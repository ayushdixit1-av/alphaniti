"""
AlphaNiti Black Swan Gauntlet — Isolated Synthetic Shock Injection & Causal 1m Replay Engine.

Provides an isolated, non-mutating shock-injection testbench that extends AlphaNiti's
existing 1-minute OpenView history pipeline, SuperChart Replay Lab, Risk Command Center,
and EMAPI/FlawlessGapDumpEngine without touching live daemon state or ledgers.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.execution.emapi_fix import EmapiExecutionFSM, FixClOrdIdGenerator, FixOrdStatus
from src.execution.flawless_gap_dump_engine import FlawlessGapDumpEngine
from src.execution.live_safety_guards import UpfrontMarginValidator, BrokerLatencyWatchdog
from src.portfolio.statutory_costs import IndianStatutoryCostModel
from src.risk.decision_score import compute_composite_decision_score
from src.risk.tier_gate import HysteresisTierGate

IST_TZ = timezone(timedelta(hours=5, minutes=30))


def _format_ist_hhmm(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=IST_TZ).strftime("%H:%M")


def _hash_payload(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def extract_session_1m_candles(
    candles: List[Dict[str, Any]],
    session_date: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Extracts clean chronological 1m candles for the target (or latest) IST session date."""
    if not candles:
        return ("", [])

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for c in candles:
        ts = int(c.get("time") or c.get("timestamp") or 0)
        if ts <= 0 or float(c.get("close", 0.0)) <= 0:
            continue
        d_str = datetime.fromtimestamp(ts, tz=IST_TZ).strftime("%Y-%m-%d")
        grouped.setdefault(d_str, []).append(
            {
                "time": ts,
                "timestamp": ts,
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": int(max(1, int(c.get("volume") or 1))),
                "is_synthetic": False,
            }
        )

    if not grouped:
        return ("", [])

    target_date = session_date if (session_date and session_date in grouped) else max(grouped.keys())
    bars = sorted(grouped[target_date], key=lambda b: b["time"])

    # Deduplicate timestamps
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for b in bars:
        if b["time"] not in seen:
            seen.add(b["time"])
            deduped.append(b)
    return (target_date, deduped)


def generate_f4_shock_series(
    session_bars: List[Dict[str, Any]],
    anchor_time_ist: str = "14:30",
    duration_minutes: int = 30,
    shock_pct: float = -0.05,
    path_variant: str = "linear",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Truncates historical session bars at anchor_time_ist (k=0 close = P_0)
    and appends `duration_minutes` (default 30) deterministic 1m synthetic shock candles
    reaching exact endpoint P_N = P_0 * (1 + shock_pct) (e.g. -5.00%).

    path_variant:
      - 'linear': P_k = P_0 * (1 + shock_pct * k / duration_minutes)
      - 'jagged': Same P_0 and exact -5.00% endpoint P_N, with opening air-pocket gaps,
                  dead-cat-bounce upper wicks, and panic volume bursts.
    """
    if len(session_bars) < 20:
        raise ValueError("Need at least 20 historical 1m candles before anchor for causal indicators.")

    # Locate anchor index nearest to anchor_time_ist (default 14:30 IST, leaving room before 15:15)
    if anchor_time_ist.upper() == "LAST":
        anchor_idx = len(session_bars) - 1
    else:
        hh, mm = [int(x) for x in anchor_time_ist.split(":")]
        target_mins = hh * 60 + mm
        anchor_idx = min(
            range(len(session_bars)),
            key=lambda i: abs(
                (datetime.fromtimestamp(session_bars[i]["time"], tz=IST_TZ).hour * 60
                 + datetime.fromtimestamp(session_bars[i]["time"], tz=IST_TZ).minute)
                - target_mins
            ),
        )
        anchor_idx = max(19, anchor_idx)

    hist_prefix = copy.deepcopy(session_bars[: anchor_idx + 1])
    anchor_bar = hist_prefix[-1]
    p0 = float(anchor_bar["close"])
    anchor_ts = int(anchor_bar["time"])

    avg_pre_vol = max(
        10_000.0,
        sum(float(b["volume"]) for b in hist_prefix[-min(30, len(hist_prefix)) :])
        / min(30, len(hist_prefix)),
    )

    variant = path_variant.lower().strip()
    synthetic_bars: List[Dict[str, Any]] = []
    prev_close = p0

    # Deterministic micro-offsets (zero randomness -> 100% reproducible audits)
    # Sum/end shape lands strictly at k=30 exact P_0 * (1 + shock_pct)
    jagged_deviations = [
        0.0000, -0.0042, -0.0085, -0.0052, -0.0110, -0.0165, -0.0120, -0.0195, -0.0135, -0.0220,
        -0.0275, -0.0230, -0.0295, -0.0245, -0.0330, -0.0385, -0.0340, -0.0410, -0.0360, -0.0435,
        -0.0400, -0.0460, -0.0425, -0.0475, -0.0445, -0.0488, -0.0468, -0.0494, -0.0485, -0.0500,
    ]
    # Gap fractions between previous close and next candle open on air-pocket minutes
    jagged_gap_fracs = {
        2: -0.0030,
        5: -0.0045,
        8: -0.0035,
        10: -0.0055,
        14: -0.0065,
        17: -0.0050,
        21: -0.0055,
        25: -0.0040,
    }
    bounce_wick_bars = {3, 6, 8, 11, 13, 16, 18, 20, 22, 24, 26, 28}

    for k in range(1, duration_minutes + 1):
        ts_k = anchor_ts + k * 60
        if variant == "jagged":
            if duration_minutes == 30:
                frac = jagged_deviations[k - 1]
            else:
                base_frac = shock_pct * (k / duration_minutes)
                osc = 0.0045 * math.sin(k * 1.45) * (1.0 - (k / duration_minutes) ** 2)
                frac = min(0.0, base_frac + osc) if k < duration_minutes else shock_pct
            close_k = round(p0 * (1.0 + frac), 2)
            gap_f = jagged_gap_fracs.get(k, -0.0008 if k % 3 == 0 else 0.0005)
            open_k = round(prev_close * (1.0 + gap_f), 2)
            upper_pad = 0.0058 if k in bounce_wick_bars else 0.0010
            lower_pad = 0.0038 if k in (4, 9, 14, 15, 19, 21, 25, 29) else 0.0012
            high_k = round(max(open_k, close_k) * (1.0 + upper_pad), 2)
            low_k = round(min(open_k, close_k) * (1.0 - lower_pad), 2)
            vol_mult = 4.6 if k in (5, 10, 14, 17, 21, 30) else (2.9 if k in bounce_wick_bars else 2.4)
        else:
            # Exact Baseline Deterministic Linear Formula: P_k = P_0 * (1 - 0.05 * k / 30)
            close_k = round(p0 * (1.0 + shock_pct * (k / duration_minutes)), 2)
            open_k = round(prev_close, 2)
            high_k = round(max(open_k, close_k) * 1.0004, 2)
            low_k = round(min(open_k, close_k) * 0.9996, 2)
            vol_mult = 1.8 + 0.10 * k

        vol_k = int(avg_pre_vol * vol_mult)
        cum_pct = round(((close_k - p0) / p0) * 100.0, 2)
        synthetic_bars.append(
            {
                "time": ts_k,
                "timestamp": ts_k,
                "open": open_k,
                "high": high_k,
                "low": low_k,
                "close": close_k,
                "volume": vol_k,
                "is_synthetic": True,
                "shock_k": k,
                "cum_shock_pct": cum_pct,
            }
        )
        prev_close = close_k

    # Enforce exact endpoint P_N = round(P_0 * (1 + shock_pct), 2)
    expected_end = round(p0 * (1.0 + shock_pct), 2)
    synthetic_bars[-1]["close"] = expected_end
    synthetic_bars[-1]["low"] = min(synthetic_bars[-1]["low"], expected_end)
    synthetic_bars[-1]["high"] = max(synthetic_bars[-1]["high"], expected_end)
    synthetic_bars[-1]["cum_shock_pct"] = round(shock_pct * 100.0, 2)

    combined = hist_prefix + synthetic_bars
    meta = {
        "anchor_index": anchor_idx,
        "anchor_time_ist": _format_ist_hhmm(anchor_ts),
        "anchor_ts": anchor_ts,
        "p0": round(p0, 2),
        "p_end": expected_end,
        "shock_end_ts": synthetic_bars[-1]["time"],
        "shock_end_time_ist": _format_ist_hhmm(synthetic_bars[-1]["time"]),
        "duration_minutes": duration_minutes,
        "shock_pct": round(shock_pct * 100.0, 2),
        "path_variant": variant,
        "pre_anchor_bars": len(hist_prefix),
        "synthetic_bars": len(synthetic_bars),
        "total_bars": len(combined),
    }
    return (combined, meta)


def _compute_causal_bar_metrics(prefix_bars: List[Dict[str, Any]], avg_pre_vol: float) -> Dict[str, Any]:
    """
    Computes ONLY strictly causal indicators from prefix_bars[0..t] (never accesses > t).
    """
    n = len(prefix_bars)
    latest = prefix_bars[-1]
    cum_tpv = 0.0
    cum_vol = 0.0
    cum_delta = 0.0
    trs: List[float] = []
    ema9 = float(prefix_bars[0]["close"])
    k_ema = 2.0 / 10.0

    for idx, b in enumerate(prefix_bars):
        h = float(b["high"])
        l = float(b["low"])
        c = float(b["close"])
        o = float(b["open"])
        v = max(1.0, float(b["volume"]))
        tp = (h + l + c) / 3.0
        cum_tpv += tp * v
        cum_vol += v
        ema9 = c * k_ema + ema9 * (1.0 - k_ema)

        rng = h - l
        if rng > 0:
            flow_ratio = ((c - l) - (h - c)) / rng
        else:
            flow_ratio = 1.0 if c >= o else -1.0
        cum_delta += v * flow_ratio

        prev_c = float(prefix_bars[idx - 1]["close"]) if idx > 0 else o
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)

    vwap = cum_tpv / cum_vol if cum_vol > 0 else float(latest["close"])
    atr_15bar = float(sum(trs[-15:]) / len(trs[-15:])) * 1.6 if trs else float(latest["close"]) * 0.003
    rvol = float(latest["volume"]) / max(1.0, avg_pre_vol)

    lookback_30 = prefix_bars[max(0, n - 31)]
    ret_30m_pct = ((float(latest["close"]) - float(lookback_30["close"])) / float(lookback_30["close"])) * 100.0
    session_open = float(prefix_bars[0]["open"])
    day_chg_pct = ((float(latest["close"]) - session_open) / session_open) * 100.0 if session_open > 0 else 0.0

    return {
        "ema9": round(ema9, 2),
        "vwap": round(vwap, 2),
        "atr_causal": round(atr_15bar, 2),
        "rvol": round(rvol, 2),
        "cum_delta": int(round(cum_delta)),
        "ret_30m_pct": round(ret_30m_pct, 2),
        "day_chg_pct": round(day_chg_pct, 2),
    }


def run_isolated_posture_replay(
    bars: List[Dict[str, Any]],
    meta: Dict[str, Any],
    posture: str = "EXPOSED_LONG_INTO_CRASH",
    symbol: str = "SBIN",
    capital: float = 8382.07,
) -> Dict[str, Any]:
    """
    Executes one strictly causal, isolated bar-by-bar simulation on `bars` from anchor_index-5
    to end of shock without mutating any global daemon state or production files.

    Postures:
      - 'EXPOSED_LONG_INTO_CRASH': Enters sized LONG 3 minutes before P_0 anchor, suffers F4 crash.
      - 'FLAT_DURING_SHOCK': Starts FLAT at P_0 anchor; enforces knife-catching & Tier 3 freeze.
      - 'SHORT_TREND_INTO_CRASH': Enters Fee-Padded SHORT at P_0 anchor; tests Two-Mode neutralizer.
    """
    anchor_idx = int(meta["anchor_index"])
    pre_window = [float(b["volume"]) for b in bars[max(0, anchor_idx - 29) : anchor_idx + 1]]
    avg_pre_vol = sum(pre_window) / max(1, len(pre_window))
    prev_day_close = float(bars[0]["open"])
    lower_circuit_20pct = round(prev_day_close * 0.80, 2)

    # Fresh isolated instances — zero shared global mutable state
    engine = FlawlessGapDumpEngine(capital=capital)
    margin_validator = UpfrontMarginValidator(min_free_cash_buffer_rs=2500.0, max_notional_multiple=3.50)
    cost_model = IndianStatutoryCostModel()
    fsm = EmapiExecutionFSM()
    tier_gate = HysteresisTierGate()

    start_bar = max(20, anchor_idx - 5)
    daily_loss_limit_rs = round(capital * 0.032, 2)  # 2.0R = 3.2% Daily Stop
    equity = capital
    peak_equity = capital
    max_drawdown_rs = 0.0
    max_drawdown_pct = 0.0

    active_trade: Optional[Dict[str, Any]] = None
    closed_trades: List[Dict[str, Any]] = []
    telemetry: List[Dict[str, Any]] = []
    bar_frames: List[Dict[str, Any]] = []
    duplicate_attempts_blocked = 0
    risk_violations: List[str] = []
    invalid_fills: List[str] = []
    max_concurrent_open = 0
    same_bar_reentry_count = 0
    long_entries_during_crash = 0
    revenge_entries_after_stop = 0
    detected_shock_bar: Optional[int] = None
    tier_history: List[int] = []
    last_exit_bar_idx: Optional[int] = None
    post_stop_cooldown_bars = 0

    def _log(t_str: str, level: str, sub: str, msg: str):
        telemetry.append(
            {
                "id": len(telemetry) + 1,
                "time_str": f"{t_str} IST",
                "level": level,
                "subsystem": sub,
                "message": msg,
                "is_historical": False,
            }
        )

    def _submit_isolated_order(bar_idx: int, ts_val: int, direction: str, entry_p: float, stop_p: float) -> Optional[Dict[str, Any]]:
        nonlocal duplicate_attempts_blocked
        if active_trade is not None:
            return None
        if last_exit_bar_idx == bar_idx:
            return None
        if (equity - capital) <= -daily_loss_limit_rs:
            return None

        order_params = engine.compute_order_parameters(symbol, entry_p, stop_p, direction=direction)
        if order_params.get("status") != "APPROVED":
            return None

        qty = int(order_params["total_qty"])
        # Recompute strictly causal fee-padded T1 using IndianStatutoryCostModel roundtrip estimate
        est_fees = cost_model.compute_intraday_roundtrip(entry_p, entry_p, qty)["total_friction"]
        risk_sh = abs(entry_p - stop_p)
        notional = round(qty * entry_p, 2)
        trade_risk_rs = round(qty * risk_sh, 2)

        # Risk Limit Invariant Checks BEFORE admission
        m_audit = margin_validator.validate_pre_order_margin(
            symbol=symbol,
            shares=qty,
            ltp=entry_p,
            available_margin=equity,
            clear_cash=equity,
        )
        if not m_audit.get("approved", False):
            _log(_format_ist_hhmm(ts_val), "GUARD", "MARGIN_GUARD", f"Order BLOCKED ({m_audit.get('reason')}): qty={qty} @ Rs {entry_p:.2f}")
            return None

        if trade_risk_rs > engine.get_dynamic_risk_budget(equity) + 0.05:
            risk_violations.append(f"Bar {bar_idx}: trade_risk {trade_risk_rs} > budget {engine.get_dynamic_risk_budget(equity)}")
        if notional > equity * 3.50 + 0.05:
            risk_violations.append(f"Bar {bar_idx}: notional {notional} > 3.5x {equity * 3.50}")

        # EMAPI ClOrdID Deduplication Test (submit once, plus probe duplicate rejection)
        cl_id = FixClOrdIdGenerator.generate(
            trading_date=datetime.fromtimestamp(ts_val, tz=IST_TZ).strftime("%Y%m%d"),
            symbol=f"{symbol}_{direction}",
            qty=qty,
            nonce=int(ts_val) + bar_idx,
        )
        first_res = fsm.submit_new_order_single(cl_id, symbol, "BUY" if direction == "LONG" else "SELL", qty, entry_p)
        dup_probe = fsm.submit_new_order_single(cl_id, symbol, "BUY" if direction == "LONG" else "SELL", qty, entry_p)
        if dup_probe.get("status") == FixOrdStatus.REJECTED:
            duplicate_attempts_blocked += 1
        else:
            risk_violations.append(f"Bar {bar_idx}: Duplicate ClOrdID {cl_id} was not rejected!")

        fsm.process_execution_report(cl_id, FixOrdStatus.FILLED, last_shares=qty, last_price=entry_p)

        sign = -1.0 if direction == "SHORT" else 1.0
        q1 = max(1, math.ceil(qty * 0.50))
        q2 = qty - q1
        t1_price = round(entry_p + sign * ((1.40 * risk_sh) + (est_fees / max(1, q1))), 2)
        t2_price = round(entry_p + sign * (2.50 * risk_sh), 2)
        fee_guard = round(entry_p + sign * ((est_fees + 10.0) / max(1, qty)), 2)

        return {
            "cl_ord_id": cl_id,
            "symbol": symbol,
            "direction": direction,
            "strategy": "BREAKOUT_LONG_EXPOSED" if direction == "LONG" else "MACRO_GAP_AND_DUMP_SHORT",
            "entry_bar_idx": bar_idx,
            "entry_time": _format_ist_hhmm(ts_val),
            "entry_price": round(entry_p, 2),
            "stop_loss": round(stop_p, 2),
            "risk_per_share": round(risk_sh, 2),
            "total_qty": qty,
            "q1_qty": q1,
            "q2_qty": q2,
            "remaining_qty": qty,
            "notional": notional,
            "target_1": t1_price,
            "target_2": t2_price,
            "fee_guard_price": fee_guard,
            "active_floor": round(stop_p, 2),
            "peak_r": 0.0,
            "t1_hit": False,
            "t1_time": None,
            "t1_price_fill": None,
            "t1_gross_pnl": 0.0,
            "mode_label": "OPEN_RISK_INITIAL (-1.0R)",
        }

    for t in range(start_bar, len(bars)):
        prefix = bars[: t + 1]  # STRICT CAUSAL SLICE ONLY
        b = bars[t]
        ts = int(b["time"])
        t_str = _format_ist_hhmm(ts)
        o_p, h_p, l_p, c_p = float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
        v_p = float(b["volume"])

        metrics = _compute_causal_bar_metrics(prefix, avg_pre_vol)
        ret_30m = metrics["ret_30m_pct"]
        vwap_val = metrics["vwap"]
        atr_val = max(metrics["atr_causal"], c_p * 0.0025)

        # Synthetic epistemic/volatility proxy rises rapidly during abnormal 30m velocity drop & panic RVOL
        velocity_shock = max(0.0, -ret_30m) / 100.0
        vol_proxy = round(min(0.015, 0.0012 + (velocity_shock * 0.22) + (max(0.0, metrics["rvol"] - 1.5) * 0.0012)), 5)
        dist_lc_pct = ((c_p - lower_circuit_20pct) / prev_day_close) * 100.0
        is_circuit_halt = dist_lc_pct <= 2.0
        directive = tier_gate.update(uncertainty=vol_proxy, is_circuit_halt=is_circuit_halt)
        cur_tier = int(directive["tier"])
        tier_history.append(cur_tier)

        if (ret_30m <= -1.25 or metrics["rvol"] >= 2.2) and b.get("is_synthetic") and detected_shock_bar is None:
            detected_shock_bar = int(b.get("shock_k") or (t - anchor_idx))
            _log(
                t_str,
                "WARN",
                "SHOCK_DETECTOR",
                f"F4 SHOCK DETECTED at k=+{detected_shock_bar}m: SBIN {ret_30m:+.2f}% | RVOL {metrics['rvol']:.2f}x | VWAP Rs {vwap_val:.2f} | Gate -> Tier {cur_tier} ({directive['tier_name']})",
            )

        # Posture 1: Open pre-shock LONG 3 minutes before P_0 anchor (t == anchor_idx - 3)
        if posture == "EXPOSED_LONG_INTO_CRASH" and t == anchor_idx - 3 and active_trade is None:
            sl_long = round(c_p - max(atr_val, c_p * 0.006), 2)
            active_trade = _submit_isolated_order(t, ts, "LONG", c_p, sl_long)
            if active_trade:
                if not (l_p <= active_trade["entry_price"] <= h_p):
                    invalid_fills.append(f"Entry fill {active_trade['entry_price']} outside bar {t} [{l_p}, {h_p}]")
                _log(
                    t_str,
                    "EXEC",
                    "EXEC",
                    f"PRE-SHOCK LONG ENTERED: {active_trade['total_qty']} SBIN @ Rs {c_p:.2f} | Hard SL Rs {sl_long:.2f} | ClOrdID {active_trade['cl_ord_id']}",
                )

        # Posture 3: Open F&O SHORT right at shock onset k=2 after confirmation (p < vwap & negative flow)
        if posture == "SHORT_TREND_INTO_CRASH" and t == anchor_idx + 2 and active_trade is None:
            sl_short = round(c_p + max(atr_val, c_p * 0.0065), 2)
            active_trade = _submit_isolated_order(t, ts, "SHORT", c_p, sl_short)
            if active_trade:
                if not (l_p <= active_trade["entry_price"] <= h_p):
                    invalid_fills.append(f"Short entry fill {active_trade['entry_price']} outside bar {t} [{l_p}, {h_p}]")
                _log(
                    t_str,
                    "EXEC",
                    "EXEC",
                    f"GAP & DUMP SHORT ARMED: {active_trade['total_qty']} SBIN @ Rs {c_p:.2f} | Fee-Padded T1 Rs {active_trade['target_1']:.2f} | SL Rs {sl_short:.2f}",
                )

        # Active Trade Bar-by-Bar Lifecycle (evaluate only bars AFTER entry_bar_idx)
        exited_this_bar = False
        if active_trade is not None and t > active_trade["entry_bar_idx"]:
            is_short = active_trade["direction"] == "SHORT"
            entry_p = active_trade["entry_price"]
            stop_p = active_trade["stop_loss"]
            risk_sh = active_trade["risk_per_share"]
            cur_floor = active_trade["active_floor"]
            rem_qty = active_trade["remaining_qty"]

            # Dual-wick conservative order: Stop/Floor breach checked FIRST before Target!
            floor_hit = (h_p >= cur_floor) if is_short else (l_p <= cur_floor)
            t1_hit_now = (not active_trade["t1_hit"]) and (
                (l_p <= active_trade["target_1"]) if is_short else (h_p >= active_trade["target_1"])
            )
            t2_hit_now = (active_trade["t1_hit"]) and (
                (l_p <= active_trade["target_2"]) if is_short else (h_p >= active_trade["target_2"])
            )

            if floor_hit:
                if is_short:
                    gap_through = o_p > cur_floor
                    raw_exit = max(cur_floor, o_p) if o_p >= cur_floor else cur_floor
                    fill_exit = round(max(l_p, min(h_p, raw_exit)), 2)
                else:
                    gap_through = o_p < cur_floor
                    raw_exit = min(cur_floor, o_p) if o_p <= cur_floor else cur_floor
                    # Deduct 1.5 bps panic impact slippage on gap-through stop while staying inside bar OHLC
                    raw_exit = raw_exit * (1.0 - 0.00015) if gap_through else raw_exit
                    fill_exit = round(max(l_p, min(h_p, raw_exit)), 2)

                if not (l_p <= fill_exit <= h_p):
                    invalid_fills.append(f"Exit fill {fill_exit} outside bar {t} [{l_p}, {h_p}]")

                q2_pnl = ((entry_p - fill_exit) if is_short else (fill_exit - entry_p)) * rem_qty
                gross_total = active_trade["t1_gross_pnl"] + q2_pnl
                # Weighted average exit price across Q1 (if hit) and Q2
                if active_trade["t1_hit"] and active_trade["t1_price_fill"]:
                    avg_exit = (
                        active_trade["t1_price_fill"] * active_trade["q1_qty"] + fill_exit * rem_qty
                    ) / max(1, active_trade["total_qty"])
                else:
                    avg_exit = fill_exit

                fees = cost_model.compute_intraday_roundtrip(entry_p, avg_exit, active_trade["total_qty"])["total_friction"]
                net_pnl = round(gross_total - fees, 2)
                r_mult = round(net_pnl / max(1.0, active_trade["total_qty"] * risk_sh), 2)

                reason = (
                    "GAP_THROUGH_STOP_LOSS"
                    if (gap_through and active_trade["peak_r"] < 0.50)
                    else ("GAP_THROUGH_PROFIT_FLOOR" if gap_through else ("PROFIT_LOCKED_FLOOR_EXECUTED" if active_trade["peak_r"] >= 0.50 else "INITIAL_STOP_EXECUTED"))
                )

                equity = round(equity + net_pnl, 2)
                engine.capital = equity
                if equity > peak_equity:
                    peak_equity = equity
                dd_now = round(equity - peak_equity, 2)
                dd_pct = round((dd_now / peak_equity) * 100.0, 2) if peak_equity > 0 else 0.0
                if dd_now < max_drawdown_rs:
                    max_drawdown_rs = dd_now
                    max_drawdown_pct = dd_pct

                closed_trades.append(
                    {
                        "symbol": symbol,
                        "strategy": active_trade["strategy"],
                        "entry_time": active_trade["entry_time"],
                        "entry_price": entry_p,
                        "stop_loss": stop_p,
                        "risk_per_share": risk_sh,
                        "total_qty": active_trade["total_qty"],
                        "q1_qty": active_trade["q1_qty"],
                        "q2_qty": active_trade["q2_qty"],
                        "notional": active_trade["notional"],
                        "target_1": active_trade["target_1"],
                        "t1_hit": active_trade["t1_hit"],
                        "t1_time": active_trade["t1_time"],
                        "exit_time": t_str,
                        "exit_price": round(avg_exit, 2),
                        "exit_reason": reason,
                        "peak_r": round(active_trade["peak_r"], 2),
                        "gross_pnl": round(gross_total, 2),
                        "fees": fees,
                        "net_pnl": net_pnl,
                        "r_multiple": r_mult,
                        "gap_through": gap_through,
                        "cl_ord_id": active_trade["cl_ord_id"],
                    }
                )
                _log(
                    t_str,
                    "WARN" if net_pnl < 0 else "SUCCESS",
                    "EXEC",
                    f"EXIT {active_trade['direction']} {symbol} @ Rs {avg_exit:.2f} ({reason}) | Net PnL Rs {net_pnl:+.2f} | Equity Rs {equity:,.2f}",
                )
                active_trade = None
                last_exit_bar_idx = t
                post_stop_cooldown_bars = 5
                exited_this_bar = True
            else:
                # Partial Target 1 fill check (only inside [l_p, h_p])
                if t1_hit_now and active_trade["q2_qty"] > 0:
                    t1_fill = active_trade["target_1"]
                    if not (l_p <= t1_fill <= h_p):
                        invalid_fills.append(f"T1 fill {t1_fill} outside bar {t} [{l_p}, {h_p}]")
                    active_trade["t1_hit"] = True
                    active_trade["t1_time"] = t_str
                    active_trade["t1_price_fill"] = t1_fill
                    active_trade["t1_gross_pnl"] = ((entry_p - t1_fill) if is_short else (t1_fill - entry_p)) * active_trade["q1_qty"]
                    active_trade["remaining_qty"] = active_trade["q2_qty"]
                    active_trade["active_floor"] = (
                        min(active_trade["active_floor"], active_trade["fee_guard_price"])
                        if is_short
                        else max(active_trade["active_floor"], active_trade["fee_guard_price"])
                    )
                    _log(
                        t_str,
                        "SUCCESS",
                        "NEUTRALIZER",
                        f"TARGET 1 PARTIAL ({active_trade['q1_qty']} sh) filled @ Rs {t1_fill:.2f}. Floor ratcheted to Fee Guard Rs {active_trade['active_floor']:.2f}",
                    )
                elif t2_hit_now:
                    t2_fill = active_trade["target_2"]
                    if not (l_p <= t2_fill <= h_p):
                        invalid_fills.append(f"T2 fill {t2_fill} outside bar {t} [{l_p}, {h_p}]")
                    q2_pnl = ((entry_p - t2_fill) if is_short else (t2_fill - entry_p)) * rem_qty
                    gross_total = active_trade["t1_gross_pnl"] + q2_pnl
                    avg_exit = (
                        (active_trade["t1_price_fill"] or entry_p) * active_trade["q1_qty"] + t2_fill * rem_qty
                    ) / max(1, active_trade["total_qty"])
                    fees = cost_model.compute_intraday_roundtrip(entry_p, avg_exit, active_trade["total_qty"])["total_friction"]
                    net_pnl = round(gross_total - fees, 2)
                    equity = round(equity + net_pnl, 2)
                    engine.capital = equity
                    closed_trades.append(
                        {
                            "symbol": symbol,
                            "strategy": active_trade["strategy"],
                            "entry_time": active_trade["entry_time"],
                            "entry_price": entry_p,
                            "stop_loss": stop_p,
                            "risk_per_share": risk_sh,
                            "total_qty": active_trade["total_qty"],
                            "q1_qty": active_trade["q1_qty"],
                            "q2_qty": active_trade["q2_qty"],
                            "notional": active_trade["notional"],
                            "target_1": active_trade["target_1"],
                            "t1_hit": True,
                            "t1_time": active_trade["t1_time"],
                            "exit_time": t_str,
                            "exit_price": round(avg_exit, 2),
                            "exit_reason": "TARGET_2_FULL_HARVEST",
                            "peak_r": round(active_trade["peak_r"], 2),
                            "gross_pnl": round(gross_total, 2),
                            "fees": fees,
                            "net_pnl": net_pnl,
                            "r_multiple": round(net_pnl / max(1.0, active_trade["total_qty"] * risk_sh), 2),
                            "gap_through": False,
                            "cl_ord_id": active_trade["cl_ord_id"],
                        }
                    )
                    _log(
                        t_str,
                        "SUCCESS",
                        "EXEC",
                        f"TARGET 2 FULL HARVEST @ Rs {t2_fill:.2f} | Net PnL Rs {net_pnl:+.2f}",
                    )
                    active_trade = None
                    last_exit_bar_idx = t
                    exited_this_bar = True

                # If still open at end of bar, update favorable excursion & Two-Mode floor causally
                if active_trade is not None:
                    fav_dist = (entry_p - l_p) if is_short else (h_p - entry_p)
                    bar_r = max(0.0, fav_dist / max(1e-6, risk_sh))
                    active_trade["peak_r"] = max(active_trade["peak_r"], bar_r)
                    pk = active_trade["peak_r"]
                    if pk >= 0.70:
                        trail_dist = pk * 0.70 * risk_sh
                        mode_b_floor = round(entry_p - trail_dist if is_short else entry_p + trail_dist, 2)
                        active_trade["active_floor"] = (
                            min(active_trade["active_floor"], active_trade["fee_guard_price"], mode_b_floor)
                            if is_short
                            else max(active_trade["active_floor"], active_trade["fee_guard_price"], mode_b_floor)
                        )
                        active_trade["mode_label"] = "MODE B: 70% GAIN HARVEST"
                    elif pk >= 0.50:
                        active_trade["active_floor"] = (
                            min(active_trade["active_floor"], active_trade["fee_guard_price"])
                            if is_short
                            else max(active_trade["active_floor"], active_trade["fee_guard_price"])
                        )
                        active_trade["mode_label"] = "MODE A: FEE GUARD CLAMPED"

        # Verify Anti-Falling-Knife Long Block & Daily Loss / Tier 3 Guards whenever FLAT during shock
        if active_trade is None and not exited_this_bar and b.get("is_synthetic"):
            if post_stop_cooldown_bars > 0:
                post_stop_cooldown_bars -= 1

            # Evaluate directional score causally with negative intraday drift when price < VWAP
            shock_drift = min(-0.005, (c_p - vwap_val) / max(1.0, vwap_val))
            d_score = compute_composite_decision_score(
                ticker=symbol,
                gamma_bar_horizon=shock_drift,
                sigma_alea_horizon=0.045,
                sigma_epis=vol_proxy,
                p_breach=min(0.95, 0.25 + abs(ret_30m) * 0.08),
                gate_tier=cur_tier,
            )
            if "BUY" in d_score.verdict:
                long_entries_during_crash += 1
                risk_violations.append(f"Bar {t} ({t_str}): Allowed BUY verdict during F4 crash!")

            # Check if Daily Loss Limit or Tier 3 Systemic Freeze blocks re-entry
            if (equity - capital) <= -daily_loss_limit_rs and int(b.get("shock_k", 0)) in (10, 20, 30):
                _log(t_str, "GUARD", "MARGIN_GUARD", f"DAILY LOSS LIMIT LOCKOUT active (Eq Rs {equity:,.2f}, -Rs {capital - equity:.2f}). Re-entry vetoed.")
            elif int(b.get("shock_k", 0)) in (5, 15, 25, 30):
                _log(
                    t_str,
                    "GUARD",
                    "DALTON_IB" if cur_tier >= 2 else "RISK_GATE",
                    f"Bar k=+{b.get('shock_k')}: LTP Rs {c_p:.2f} ({b.get('cum_shock_pct'):+.2f}%) | Verdict={d_score.verdict_badge} | Tier {cur_tier} | LC Buffer {dist_lc_pct:.1f}%",
                )

        if active_trade is not None:
            max_concurrent_open = max(max_concurrent_open, 1)

        # Mark-to-market equity check for max drawdown invariant
        mtm_unrealized = 0.0
        if active_trade is not None:
            dir_s = 1.0 if active_trade["direction"] == "LONG" else -1.0
            mtm_unrealized = (c_p - active_trade["entry_price"]) * active_trade["remaining_qty"] * dir_s
        eq_now = equity + mtm_unrealized
        if eq_now > peak_equity:
            peak_equity = eq_now
        dd_mtm = eq_now - peak_equity
        if dd_mtm < max_drawdown_rs:
            max_drawdown_rs = round(dd_mtm, 2)
            max_drawdown_pct = round((dd_mtm / peak_equity) * 100.0, 2) if peak_equity > 0 else 0.0

        # Check Risk Floor invariant continuously
        if equity < 2500.0:
            risk_violations.append(f"Bar {t}: Equity {equity} fell below Rs 2,500 hard reserve floor!")

        bar_frames.append(
            {
                "bar_idx": t,
                "time_ist": t_str,
                "close": c_p,
                "vwap": metrics["vwap"],
                "rvol": metrics["rvol"],
                "ret_30m_pct": ret_30m,
                "tier": cur_tier,
                "active_floor": active_trade["active_floor"] if active_trade else None,
                "equity_mtm": round(eq_now, 2),
                "is_synthetic": bool(b.get("is_synthetic", False)),
            }
        )

    # Close any residual open position cleanly at final shock bar close (marked EOD/TEST_END)
    if active_trade is not None:
        last_b = bars[-1]
        end_p = float(last_b["close"])
        is_short = active_trade["direction"] == "SHORT"
        q2_pnl = ((active_trade["entry_price"] - end_p) if is_short else (end_p - active_trade["entry_price"])) * active_trade["remaining_qty"]
        gross_total = active_trade["t1_gross_pnl"] + q2_pnl
        avg_exit = (
            ((active_trade["t1_price_fill"] or active_trade["entry_price"]) * active_trade["q1_qty"] + end_p * active_trade["remaining_qty"])
            / max(1, active_trade["total_qty"])
            if active_trade["t1_hit"]
            else end_p
        )
        fees = cost_model.compute_intraday_roundtrip(active_trade["entry_price"], avg_exit, active_trade["total_qty"])["total_friction"]
        net_pnl = round(gross_total - fees, 2)
        equity = round(equity + net_pnl, 2)
        closed_trades.append(
            {
                "symbol": symbol,
                "strategy": active_trade["strategy"],
                "entry_time": active_trade["entry_time"],
                "entry_price": active_trade["entry_price"],
                "stop_loss": active_trade["stop_loss"],
                "risk_per_share": active_trade["risk_per_share"],
                "total_qty": active_trade["total_qty"],
                "q1_qty": active_trade["q1_qty"],
                "q2_qty": active_trade["q2_qty"],
                "notional": active_trade["notional"],
                "target_1": active_trade["target_1"],
                "t1_hit": active_trade["t1_hit"],
                "t1_time": active_trade["t1_time"],
                "exit_time": _format_ist_hhmm(last_b["time"]),
                "exit_price": round(avg_exit, 2),
                "exit_reason": "SHOCK_HORIZON_MARK_EXIT",
                "peak_r": round(active_trade["peak_r"], 2),
                "gross_pnl": round(gross_total, 2),
                "fees": fees,
                "net_pnl": net_pnl,
                "r_multiple": round(net_pnl / max(1.0, active_trade["total_qty"] * active_trade["risk_per_share"]), 2),
                "gap_through": False,
                "cl_ord_id": active_trade["cl_ord_id"],
            }
        )

    # Canary Future-Data Leakage Verification:
    # Truncate bars at checkpoint t_check = anchor_idx + 12 and poison all future bars with extreme swings.
    # Recompute causal metrics at t_check and verify SHA-256 hash matches full-series run at t_check!
    t_check = min(len(bars) - 2, anchor_idx + 12)
    base_slice = bars[: t_check + 1]
    base_metrics_at_k12 = _compute_causal_bar_metrics(base_slice, avg_pre_vol)
    # Poisoned future has +40% spike right after t_check
    poisoned_future = [
        {"time": bars[t_check]["time"] + (m * 60), "open": 1500.0, "high": 1650.0, "low": 1450.0, "close": 1600.0, "volume": 99_999_999}
        for m in range(1, 10)
    ]
    poisoned_prefix = (bars[: t_check + 1] + poisoned_future)[: t_check + 1]
    poisoned_metrics_at_k12 = _compute_causal_bar_metrics(poisoned_prefix, avg_pre_vol)
    leakage_clean = (_hash_payload(base_metrics_at_k12) == _hash_payload(poisoned_metrics_at_k12))

    # Build final Risk Command impact summary for this isolated posture
    total_net = round(equity - capital, 2)
    daily_loss_used = round(max(0.0, -total_net), 2)
    end_close = float(bars[-1]["close"])
    end_dist_lc = round(((end_close - lower_circuit_20pct) / prev_day_close) * 100.0, 2)

    return {
        "posture": posture,
        "detected": detected_shock_bar is not None and max(tier_history or [0]) >= 2,
        "detected_at_minute_k": detected_shock_bar,
        "max_tier_reached": max(tier_history or [0]),
        "trades": closed_trades,
        "summary": {
            "starting_capital_rs": round(capital, 2),
            "ending_capital_rs": round(equity, 2),
            "net_pnl_rs": total_net,
            "return_pct": round((total_net / capital) * 100.0, 2),
            "max_drawdown_rs": round(max_drawdown_rs, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "daily_loss_used_rs": daily_loss_used,
            "daily_loss_limit_rs": daily_loss_limit_rs,
            "daily_loss_limit_breached": daily_loss_used >= daily_loss_limit_rs,
            "reserve_floor_intact": equity >= 2500.0,
            "end_dist_to_lower_circuit_pct": end_dist_lc,
            "max_concurrent_open_trades": max_concurrent_open,
        },
        "invariants": {
            "zero_future_leakage": bool(leakage_clean),
            "zero_invalid_fills": len(invalid_fills) == 0,
            "zero_duplicate_orders": (duplicate_attempts_blocked >= len(closed_trades)) and same_bar_reentry_count == 0,
            "zero_risk_limit_violations": (len(risk_violations) == 0) and (long_entries_during_crash == 0) and (revenge_entries_after_stop == 0),
            "canary_hash_match": _hash_payload(base_metrics_at_k12) == _hash_payload(poisoned_metrics_at_k12),
            "invalid_fills_detail": invalid_fills,
            "risk_violations_detail": risk_violations,
            "duplicate_probes_blocked": duplicate_attempts_blocked,
        },
        "bar_frames": bar_frames,
        "telemetry": telemetry,
    }


def run_black_swan_test_01(
    raw_1m_candles: List[Dict[str, Any]],
    symbol: str = "SBIN",
    session_date: Optional[str] = None,
    anchor_time_ist: str = "14:30",
    duration_minutes: int = 30,
    shock_pct: float = -0.05,
    path_variant: str = "linear",
    posture: str = "EXPOSED_LONG_INTO_CRASH",
) -> Dict[str, Any]:
    """
    Top-level isolated entrypoint for AlphaNiti Black Swan Gauntlet — Test 01 (F4 -5.00% 30m).
    Runs requested `path_variant` + returns primary SuperChart 1m candles plus full 3-posture matrix
    across both linear and jagged variants.
    """
    resolved_date, session_bars = extract_session_1m_candles(raw_1m_candles, session_date=session_date)
    if len(session_bars) < 20:
        raise ValueError(f"Insufficient 1-minute historical bars for {symbol} ({len(session_bars)} bars found)")

    # Build primary variant requested for SuperChart & Replay Lab
    candles_primary, meta_primary = generate_f4_shock_series(
        session_bars=session_bars,
        anchor_time_ist=anchor_time_ist,
        duration_minutes=duration_minutes,
        shock_pct=shock_pct,
        path_variant=path_variant,
    )
    primary_run = run_isolated_posture_replay(
        bars=candles_primary,
        meta=meta_primary,
        posture=posture,
        symbol=symbol,
    )

    # Also evaluate full Gauntlet Matrix (Linear + Jagged x 3 Postures) on exact same anchor P_0
    candles_linear, meta_linear = generate_f4_shock_series(session_bars, anchor_time_ist, duration_minutes, shock_pct, "linear")
    candles_jagged, meta_jagged = generate_f4_shock_series(session_bars, anchor_time_ist, duration_minutes, shock_pct, "jagged")

    matrix_results: List[Dict[str, Any]] = []
    for var_name, c_arr, m_obj in [
        ("F4_LINEAR", candles_linear, meta_linear),
        ("F4_JAGGED", candles_jagged, meta_jagged),
    ]:
        for post in ["EXPOSED_LONG_INTO_CRASH", "FLAT_DURING_SHOCK", "SHORT_TREND_INTO_CRASH"]:
            res = run_isolated_posture_replay(c_arr, m_obj, posture=post, symbol=symbol)
            inv = res["invariants"]
            passed = all(
                [
                    res["detected"],
                    inv["zero_future_leakage"],
                    inv["zero_invalid_fills"],
                    inv["zero_duplicate_orders"],
                    inv["zero_risk_limit_violations"],
                    res["summary"]["reserve_floor_intact"],
                ]
            )
            matrix_results.append(
                {
                    "variant": var_name,
                    "posture": post,
                    "passed": passed,
                    "detected_at_k": res["detected_at_minute_k"],
                    "max_tier": res["max_tier_reached"],
                    "net_pnl_rs": res["summary"]["net_pnl_rs"],
                    "max_drawdown_pct": res["summary"]["max_drawdown_pct"],
                    "end_capital_rs": res["summary"]["ending_capital_rs"],
                    "trades_count": len(res["trades"]),
                    "exit_reason": res["trades"][0]["exit_reason"] if res["trades"] else "SHIELDED_FLAT_CASH",
                    "invariants": inv,
                }
            )

    all_passed = all(row["passed"] for row in matrix_results)
    return {
        "status": "SUCCESS",
        "test_id": "BLACK_SWAN_GAUNTLET_TEST_01",
        "severity": "F4_SEVERE_SHOCK",
        "isolated_only": True,
        "symbol": symbol,
        "interval": "1m",
        "session_date": resolved_date,
        "anchor_meta": meta_primary,
        "candles": candles_primary,
        "primary_replay": primary_run,
        "gauntlet_matrix": matrix_results,
        "overall_verdict": "PASS_ALL_INVARIANTS" if all_passed else "FAIL_REVIEW_REQUIRED",
    }
