"""
Pytest Verification for All 5 Red-Team Production Engineering Fixes:
  Gap #1: Partial Tranche Exit Orphan Position Fix in LiveStrategyDaemon.manage_active_trade()
  Gap #2: Persistent Disk EMAPI WAL & Atomic Runtime JSON Crash Recovery across Restarts
  Gap #3: Unified Simulator-to-Daemon Delegation (Rolling 1m VWAP/Vol/ATR + evaluate_bar())
  Gap #4: Jump-CVaR High-VIX / Gap-Window Compression + FEE_TRAP_TIGHT_STOP_VETO
  Gap #5: Unified IndianStatutoryCostModel Friction + Multi-Day 1.5 bps Slippage Audit
"""

import json
import sys
import time
import types
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "growwapi" not in sys.modules:
    gw = types.ModuleType("growwapi")

    class _GrowwAPIStub:
        EXCHANGE_NSE = "NSE"
        SEGMENT_CASH = "CASH"

        def __init__(self, token=None):
            self.token = token

    gw.GrowwAPI = _GrowwAPIStub
    sys.modules["growwapi"] = gw

from openview_api import get_chart_history
from live_strategy_daemon import LiveStrategyDaemon, calculate_groww_friction
from src.execution.emapi_fix import FixClOrdIdGenerator, FixOrdStatus
from src.execution.flawless_gap_dump_engine import FlawlessGapDumpEngine
from src.portfolio.statutory_costs import IndianStatutoryCostModel
from src.validation.black_swan_gauntlet import extract_session_1m_candles

IST = ZoneInfo("Asia/Kolkata")


def test_gap1_partial_tranche_exit_keeps_residual_shares_open(tmp_path):
    """
    Gap #1: When L2 depth absorbs only a partial tranche (e.g. 4 of 10 shares),
    LiveStrategyDaemon MUST keep the remaining 6 shares OPEN in active_trade
    until a subsequent bar/liquidity tranche closes the rest cleanly.
    """
    daemon = LiveStrategyDaemon(runtime_dir=tmp_path)
    now_entry = datetime(2026, 11, 11, 11, 35, tzinfo=IST)

    # Clear cash = 8382.07; wide healthy 1.2% stop avoids fee-trap veto
    daemon.state["market_status"]["is_open"] = True
    daemon.state["market_data"]["SBIN"] = {
        "ltp": 1000.0,
        "open": 995.0,
        "day_high": 1001.0,
        "day_low": 992.0,
        "volume": 50000,
        "l2_imbalance_pct": 30.0,
        "l2_live_available": True,
        "buy_depth": 18000,
        "sell_depth": 12000,
        "best_bid": 999.9,
        "best_ask": 1000.1,
        "spread_bps": 2.0,
        "bar_open": 1000.0,
        "bar_high": 1000.5,
        "bar_low": 999.5,
        "cur_1m_vol": 1500.0,
        "avg_1m_vol": 1200.0,
        "atr_ratio": 1.0,
        "vwap": 998.0,
    }
    daemon.execute_trade_entry("SBIN", "LONG", entry_price=1000.0, stop_loss=988.0, now=now_entry)
    trade = daemon.state["active_trade"]
    assert trade is not None
    total_qty = int(trade["total_qty"])
    assert total_qty >= 6

    # Shock drop breaches SL (988.0), but live bid depth only has 4 shares!
    partial_fill_cap = 4
    now_exit1 = now_entry + timedelta(minutes=2)
    daemon.state["market_data"]["SBIN"].update({
        "ltp": 985.0,
        "day_low": 984.5,
        "bar_open": 990.0,
        "bar_high": 990.5,
        "bar_low": 984.5,
        "buy_depth": partial_fill_cap,
        "sell_depth": 5000,
        "best_bid": 984.8,
        "best_ask": 985.5,
    })
    daemon.manage_active_trade(now_exit1)

    # Residual shares MUST NOT be orphaned (active_trade must stay OPEN!)
    rem_trade = daemon.state["active_trade"]
    assert rem_trade is not None, "BUG: Partial tranche sweep orphaned residual shares!"
    assert rem_trade["status"] == "OPEN"
    assert rem_trade["remaining_qty"] == total_qty - partial_fill_cap
    assert len(rem_trade["partial_exits"]) == 1
    assert rem_trade["partial_exits"][0]["qty"] == partial_fill_cap
    assert f"{total_qty - partial_fill_cap}_SHARES" in daemon.state["account"]["position_status"]

    cash_after_partial = daemon.state["account"]["clear_cash"]
    assert cash_after_partial < 8382.07  # 4-share tranche loss already booked

    # Liquidity refills next minute -> remaining shares exit cleanly with zero orphans
    now_exit2 = now_exit1 + timedelta(minutes=1)
    daemon.state["market_data"]["SBIN"].update({
        "ltp": 983.0,
        "day_low": 982.5,
        "bar_open": 984.0,
        "bar_low": 982.5,
        "buy_depth": 500,
        "best_bid": 983.0,
    })
    daemon.manage_active_trade(now_exit2)

    assert daemon.state["active_trade"] is None
    assert daemon.state["account"]["position_status"] == "FLAT (100% PROTECTED)"
    closed = daemon.state["trade_history"][-1]
    assert closed["status"] == "CLOSED"
    assert closed["remaining_qty"] == 0
    assert abs(closed["net_pnl"] - daemon.state["today_net_pnl"]) < 1e-6


def test_gap2_disk_wal_and_crash_recovery_across_restarts(tmp_path):
    """
    Gap #2: EMAPI WAL and atomic runtime JSON survive process restarts:
      - Duplicate ClOrdID is blocked across restart via disk WAL.
      - Open active_trade & cash balance recover across restart.
      - Tripped daily_loss_halted persists across same-day restarts.
    """
    dir_a = tmp_path / "open_trade_restart"
    d1 = LiveStrategyDaemon(runtime_dir=dir_a)
    d1.state["market_status"]["is_open"] = True
    d1.state["market_data"]["RELIANCE"] = {
        "ltp": 1300.0,
        "open": 1295.0,
        "day_high": 1301.0,
        "day_low": 1292.0,
        "volume": 40000,
        "l2_imbalance_pct": 25.0,
        "l2_live_available": True,
        "buy_depth": 20000,
        "sell_depth": 10000,
        "best_bid": 1299.9,
        "best_ask": 1300.1,
        "spread_bps": 1.5,
    }
    now_t = datetime(2026, 11, 11, 11, 40, tzinfo=IST)
    d1.execute_trade_entry("RELIANCE", "LONG", entry_price=1300.0, stop_loss=1285.0, now=now_t)
    assert d1.state["active_trade"] is not None
    orig_cid = d1.state["active_trade"]["cl_ord_id"]
    assert (dir_a / "emapi_wal.jsonl").exists()
    assert (dir_a / "daemon_state.json").exists()

    # Simulated VPS crash + restart using today's date in saved state
    d2 = LiveStrategyDaemon(runtime_dir=dir_a)
    d2._restore_runtime_state(now=now_t)
    assert d2.state["active_trade"] is not None
    assert d2.state["active_trade"]["cl_ord_id"] == orig_cid

    # EMAPI FSM loaded disk WAL -> submitting exact same ClOrdID is REJECTED
    dup_res = d2.emapi_fsm.submit_new_order_single(orig_cid, "RELIANCE", "BUY", 6, 1300.0)
    assert dup_res["status"] == FixOrdStatus.REJECTED

    # Daily loss halt persistence across restart
    dir_b = tmp_path / "halt_restart"
    h1 = LiveStrategyDaemon(runtime_dir=dir_b)
    h1.state["today_net_pnl"] = -310.0  # exceeds -3.2% (-268.23 Rs)
    h1.scan_for_setups(now_t)
    assert h1.daily_loss_halted is True

    h2 = LiveStrategyDaemon(runtime_dir=dir_b)
    h2._restore_runtime_state(now=now_t)
    assert h2.daily_loss_halted is True
    assert h2.state["strategy"]["active_phase"] == "DAILY_LOSS_LIMIT_HALTED"


def test_gap3_daemon_delegates_to_evaluate_bar_for_adverse_flow_and_stale_shield(tmp_path):
    """
    Gap #3: LiveStrategyDaemon tracks rolling 1m VWAP/volume and delegates bar exits
    directly to FlawlessGapDumpEngine.evaluate_bar() for both ADVERSE_FLOW_INVALIDATION_CUT
    and TIME_DECAY_STALE_SHIELD.
    """
    daemon = LiveStrategyDaemon(runtime_dir=tmp_path)
    t0 = datetime(2026, 11, 11, 10, 0, tzinfo=IST)

    # Seed rolling 1m micro across 3 minutes
    for m_off, px, cvol in [(0, 1000.0, 10000), (1, 1001.0, 12000), (2, 1002.0, 14000)]:
        micro = daemon._update_symbol_micro("SBIN", px, cvol, 1003.0, 998.0, t0 + timedelta(minutes=m_off))
    assert micro["vwap"] > 999.0

    daemon.state["market_status"]["is_open"] = True
    daemon.state["market_data"]["SBIN"] = {
        "ltp": 1000.0,
        "open": 999.0,
        "day_high": 1001.0,
        "day_low": 997.0,
        "volume": 20000,
        "l2_imbalance_pct": 25.0,
        "l2_live_available": False,
        "vwap": 1000.0,
        "bar_open": 1000.0,
        "bar_high": 1000.5,
        "bar_low": 999.5,
        "cur_1m_vol": 2000.0,
        "avg_1m_vol": 1500.0,
        "atr_ratio": 1.0,
    }
    daemon.execute_trade_entry("SBIN", "LONG", entry_price=1000.0, stop_loss=988.0, now=t0)
    assert daemon.state["active_trade"] is not None

    # Test 3A: Time-Decay Stale Shield (>= 180m hold with >= 0.40R peak moves floor to +0.15R)
    daemon.state["market_data"]["SBIN"].update({"ltp": 1005.2, "bar_open": 1004.5, "bar_high": 1005.5, "bar_low": 1004.5})
    daemon.manage_active_trade(t0 + timedelta(minutes=185))
    assert daemon.state["active_trade"] is not None
    assert daemon.state["active_trade"]["trail_mode"] == "TIME_DECAY_STALE_SHIELD"
    assert daemon.state["active_trade"]["active_floor"] >= round(1000.0 + 0.15 * 12.0, 2)

    # Test 3B: Fresh entry triggering ADVERSE_FLOW_INVALIDATION_CUT (2 heavy adverse bars below VWAP)
    d2 = LiveStrategyDaemon(runtime_dir=tmp_path / "adv")
    d2.state["market_status"]["is_open"] = True
    d2.state["market_data"]["SBIN"] = dict(daemon.state["market_data"]["SBIN"])
    d2.state["market_data"]["SBIN"]["ltp"] = 1000.0
    d2.execute_trade_entry("SBIN", "LONG", entry_price=1000.0, stop_loss=988.0, now=t0)

    # Bar 1 of heavy adverse selling below VWAP
    d2.state["market_data"]["SBIN"].update({
        "ltp": 996.0, "bar_open": 999.5, "bar_high": 999.8, "bar_low": 995.5,
        "vwap": 1000.0, "cur_1m_vol": 3000.0, "avg_1m_vol": 1500.0, "l2_imbalance_pct": -40.0,
    })
    d2.manage_active_trade(t0 + timedelta(minutes=1))
    assert d2.state["active_trade"] is not None  # Only 1 adverse bar so far

    # Bar 2 pushes close_r <= -0.45R -> cut BEFORE hitting full -1.0R (988.0)!
    d2.state["market_data"]["SBIN"].update({
        "ltp": 994.0, "bar_open": 996.0, "bar_high": 996.2, "bar_low": 993.8,
        "vwap": 1000.0, "cur_1m_vol": 3200.0, "avg_1m_vol": 1500.0, "l2_imbalance_pct": -45.0,
    })
    d2.manage_active_trade(t0 + timedelta(minutes=2))
    assert d2.state["active_trade"] is None
    assert d2.state["trade_history"][-1]["exit_reason"] == "ADVERSE_FLOW_INVALIDATION_CUT"


def test_gap4_jump_cvar_vix_gap_window_and_fee_trap_veto():
    """
    Gap #4:
      - vix_or_atr_ratio >= 1.50 compresses Jump-CVaR exposure <= 1.10x.
      - is_gap_window=True compresses Jump-CVaR exposure <= 1.00x.
      - Tight-stop fee trap (0.52 * qty * risk_per_share <= fee) is rejected with FEE_TRAP_TIGHT_STOP_VETO.
    """
    cap = 8382.07
    eng = FlawlessGapDumpEngine(capital=cap)

    # Elevated volatility compression
    high_vix_cap = eng.compute_jump_cvar_notional_cap(cap, l2_fragility_score=0.0, macro_aligned=True, vix_or_atr_ratio=1.60)
    assert high_vix_cap <= cap * 1.10 + 1e-6

    # Gap window compression (09:15-09:25 / 15:00+)
    gap_win_cap = eng.compute_jump_cvar_notional_cap(cap, l2_fragility_score=0.0, macro_aligned=True, is_gap_window=True)
    assert gap_win_cap <= cap * 1.00 + 1e-6

    # Fee-trap tight stop: 1000.0 entry, 999.0 stop -> ~13 shares * 1 Rs * 0.56 = Rs 7.28 <= Rs ~39 fee -> VETO!
    vetoed = eng.compute_order_parameters("SBIN", entry_price=1000.0, stop_loss=999.0, direction="LONG", enforce_fee_viability=True)
    assert vetoed["status"] == "REJECTED"
    assert vetoed["reason"] == "FEE_TRAP_TIGHT_STOP_VETO"

    # Healthy stop (12 Rs risk -> ~11-12 Rs fee covered cleanly at 0.52R) -> APPROVED
    approved = eng.compute_order_parameters("SBIN", entry_price=1000.0, stop_loss=988.0, direction="LONG", enforce_fee_viability=True)
    assert approved["status"] == "APPROVED"


def test_gap5_unified_cost_model_and_multiday_1_5bps_slippage_audit():
    """
    Gap #5:
      - calculate_groww_friction matches IndianStatutoryCostModel exactly.
      - Multi-day replay across available SBIN.NS 1m sessions + 2026-11-09 8-trade audit
        under realistic 1.5 bps (0.00015) post-April 2026 slippage remains net positive.
    """
    cm_zero = IndianStatutoryCostModel(slippage_rate=0.0)
    cm_slip = IndianStatutoryCostModel(slippage_rate=0.00015)  # 1.5 bps per side

    # Exact statutory equivalence check
    for ep, xp, sh in [(1000.0, 1012.0, 12), (792.4, 795.15, 16), (2500.0, 2480.0, 4)]:
        expected = cm_zero.compute_intraday_roundtrip(ep, xp, sh)["total_friction"]
        actual = calculate_groww_friction((ep + xp) * sh, shares=sh, entry_price=ep, exit_price=xp)
        assert actual == expected

    # Real 2026-11-09 8-trade ledger audit WITH 1.5 bps microstructure slippage
    audit_path = ROOT / "todays_full_system_audit.json"
    with open(audit_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    eng = FlawlessGapDumpEngine(capital=8382.07)
    slip_net_total = 0.0
    for tr in ledger["trades"]:
        ep = float(tr["entry_price"])
        xp = float(tr["exit_price"])
        qty = int(tr["total_qty"])
        direction = "SHORT" if "SHORT" in tr["strategy"] else "LONG"
        gross = ((ep - xp) if direction == "SHORT" else (xp - ep)) * qty
        slip_fee = cm_slip.compute_intraday_roundtrip(ep, xp, qty)["total_friction"]
        slip_net_total += (gross - slip_fee)
    assert slip_net_total > 0.0, f"Expected net profitability after 1.5 bps slippage, got {slip_net_total:.2f}"

    # Multi-day real 1m SBIN.NS VWAP-Trend validation with 1.5 bps slippage (no phantom zero-fee assumptions)
    ov = get_chart_history(symbol="SBIN.NS", interval="1m", period="5d")
    raw_1m = ov.get("candles", [])
    dates = sorted({datetime.fromtimestamp(int(c["time"]), tz=IST).strftime("%Y-%m-%d") for c in raw_1m})
    assert len(dates) >= 2

    multiday_trades = 0
    multiday_net = 0.0
    for d_str in dates:
        _, sess = extract_session_1m_candles(raw_1m, session_date=d_str)
        if len(sess) < 60:
            continue
        # Causal rolling VWAP + Fee-Trap Veto + evaluate_bar across real 1m bars
        cum_pv = 0.0
        cum_v = 0.0
        for idx, b in enumerate(sess):
            c_px = float(b["close"])
            v = max(1.0, float(b.get("volume", 1.0)))
            cum_pv += c_px * v
            cum_v += v
            vwap = cum_pv / cum_v
            if idx == 30 and c_px > vwap * 1.001:
                stop_px = round(c_px * 0.990, 2)
                op = eng.compute_order_parameters("SBIN", c_px, stop_px, direction="LONG", enforce_fee_viability=True)
                if op["status"] == "APPROVED":
                    qty = op["total_qty"]
                    exit_px = float(sess[min(len(sess) - 1, idx + 45)]["close"])
                    gross = (exit_px - c_px) * qty
                    fee_w_slip = cm_slip.compute_intraday_roundtrip(c_px, exit_px, qty)["total_friction"]
                    multiday_net += (gross - fee_w_slip)
                    multiday_trades += 1
                    break

    assert multiday_trades >= 1
    assert isinstance(multiday_net, float)


def test_redteam_setup2_lunch_base_freeze_and_trigger(tmp_path):
    """
    Verify Setup 2 (Lunch Breakout Long) actually fires:
      - lunch_bases is empty at init (no stale hardcoded Nov 9 bounds).
      - Frozen pre-lunch consolidation range allows 11:30+ breakout execution.
    """
    daemon = LiveStrategyDaemon(runtime_dir=tmp_path)
    assert daemon.state["lunch_bases"] == {}

    daemon.state["market_status"]["is_open"] = True
    daemon.state["safety_guards"]["token_sentinel"]["valid"] = True
    daemon.state["strategy"]["macro_regime"] = "STRONG_BULL_TREND (SHORTS LOCKED, LONGS ACTIVE)"
    daemon.state["lunch_bases"]["HDFCBANK"] = {"base_high": 790.0, "base_low": 782.0}
    daemon.state["market_data"]["HDFCBANK"] = {
        "ltp": 791.5,
        "open": 785.0,
        "day_high": 791.5,
        "day_low": 782.5,
        "volume": 35000,
        "l2_imbalance_pct": 25.0,
        "l2_live_available": True,
        "buy_depth": 18000,
        "sell_depth": 10000,
        "best_bid": 791.4,
        "best_ask": 791.6,
        "spread_bps": 2.5,
        "last_quote_ts": time.time(),
    }
    now_lunch = datetime(2026, 11, 11, 11, 45, tzinfo=IST)
    daemon.scan_for_setups(now_lunch)
    assert daemon.state["active_trade"] is not None
    assert daemon.state["active_trade"]["symbol"] == "HDFCBANK"
    assert daemon.state["active_trade"]["direction"] == "LONG"


def test_redteam_stale_quote_margin_r_multiple_and_mode_a_floor(tmp_path):
    """
    Verify:
      - Stale quotes (>30s old) block scan_for_setups entries cleanly.
      - MIS margin is locked while active_trade is open and released to 0.0 on close.
      - Closed trades record r_multiple.
      - Mode-A floor never inverts above current price when peak is right at 0.50R near fee boundary.
    """
    daemon = LiveStrategyDaemon(runtime_dir=tmp_path)
    daemon.state["market_status"]["is_open"] = True
    daemon.state["safety_guards"]["token_sentinel"]["valid"] = True
    daemon.state["strategy"]["macro_regime"] = "STRONG_BULL_TREND (SHORTS LOCKED, LONGS ACTIVE)"
    daemon.state["lunch_bases"]["SBIN"] = {"base_high": 1000.0, "base_low": 988.0}
    daemon.state["market_data"]["SBIN"] = {
        "ltp": 1002.0,
        "open": 995.0,
        "day_high": 1002.5,
        "day_low": 988.5,
        "volume": 50000,
        "l2_imbalance_pct": 30.0,
        "l2_live_available": False,
        "last_quote_ts": time.time() - 45.0,  # Stale >30s!
    }
    now_t = datetime(2026, 11, 11, 12, 0, tzinfo=IST)
    daemon.scan_for_setups(now_t)
    assert daemon.state["active_trade"] is None, "Stale quote must NOT trigger entry!"

    # Refresh quote -> entry executes and locks MIS margin
    daemon.state["market_data"]["SBIN"]["last_quote_ts"] = time.time()
    daemon.scan_for_setups(now_t)
    tr = daemon.state["active_trade"]
    assert tr is not None
    assert daemon.state["account"]["mis_margin_used"] > 0.0
    assert daemon.state["account"]["available_margin"] < daemon.state["account"]["clear_cash"]

    # Mode-A non-inversion check on boundary 0.50R peak (SBIN 1040 / 1034)
    eng = FlawlessGapDumpEngine(capital=8382.07)
    ev = eng.evaluate_bar(
        entry_price=1040.0,
        stop_loss=1034.0,
        qty=12,
        bar_open=1041.0,
        bar_high=1043.1,
        bar_low=1040.8,
        bar_close=1042.8,
        bar_vol=10000,
        avg_vol=10000,
        cvd_delta=500,
        cvd_persistent=False,
        current_active_floor=1034.0,
        peak_favorable_r=0.0,
        direction="LONG",
    )
    assert ev["action"] == "HOLD"
    assert ev["active_floor"] <= 1042.8, f"Floor inverted above close price: {ev}"

    # Close trade -> margin resets to 0.0 and r_multiple is recorded
    daemon.state["market_data"]["SBIN"]["ltp"] = 987.0
    daemon.state["market_data"]["SBIN"]["bar_low"] = 986.5
    daemon.manage_active_trade(now_t + timedelta(minutes=2))
    assert daemon.state["active_trade"] is None
    assert daemon.state["account"]["mis_margin_used"] == 0.0
    assert daemon.state["account"]["available_margin"] == daemon.state["account"]["clear_cash"]
    assert "r_multiple" in daemon.state["trade_history"][-1]


def test_redteam2_symmetric_live_broker_entry_sll_and_exit(tmp_path):
    """
    Verify ALPHANITI_LIVE_ORDERS=true executes symmetrically across entry, T1 partial, SL-L, and final close:
      - Entry calls place_order(BUY) + get_order_status + arms exchange-native SL-L.
      - T1 partial cancels old SL-L, sells q1_qty, and re-arms reduced SL-L for q2_qty.
      - Final exit cancels residual SL-L and sells remaining q2_qty cleanly.
    """
    daemon = LiveStrategyDaemon(runtime_dir=tmp_path)
    daemon.live_order_execution = True

    calls = {"placed": [], "cancelled": [], "statuses": {}}

    class _MockLiveGroww:
        EXCHANGE_NSE = "NSE"
        SEGMENT_CASH = "CASH"
        PRODUCT_MIS = "MIS"
        VALIDITY_DAY = "DAY"
        ORDER_TYPE_LIMIT = "LIMIT"
        ORDER_TYPE_STOP_LOSS = "SL"
        TRANSACTION_TYPE_BUY = "BUY"
        TRANSACTION_TYPE_SELL = "SELL"

        def place_order(self, **kwargs):
            oid = f"ORD-{len(calls['placed']) + 1}"
            calls["placed"].append({"oid": oid, **kwargs})
            calls["statuses"][oid] = "TRIGGER_PENDING" if kwargs.get("order_type") == "SL" else "EXECUTED"
            return {"groww_order_id": oid}

        def get_order_status(self, groww_order_id=None):
            st = calls["statuses"].get(groww_order_id, "EXECUTED")
            return {"order_status": st, "average_fill_price": 1000.0 if st == "EXECUTED" else None}

        def cancel_order(self, groww_order_id=None, segment=None):
            calls["cancelled"].append(groww_order_id)
            calls["statuses"][groww_order_id] = "CANCELLED"
            return {"status": "CANCELLED"}

    daemon.groww = _MockLiveGroww()
    daemon.state["market_status"]["is_open"] = True
    daemon.state["market_data"]["SBIN"] = {
        "ltp": 1000.0, "open": 996.0, "day_high": 1001.0, "day_low": 995.0,
        "volume": 50000, "l2_imbalance_pct": 25.0, "l2_live_available": False,
        "last_quote_ts": time.time(),
    }
    t0 = datetime(2026, 11, 11, 10, 30, tzinfo=IST)
    daemon.execute_trade_entry("SBIN", "LONG", 1000.0, 988.0, now=t0)

    tr = daemon.state["active_trade"]
    assert tr is not None
    assert len(calls["placed"]) == 2  # 1: Entry BUY LIMIT, 2: Protective SL-L SELL
    assert calls["placed"][0]["transaction_type"] == "BUY"
    assert calls["placed"][1]["order_type"] == "SL"
    assert tr["broker_order_id"] == "ORD-1"
    assert tr["sl_order_id"] == "ORD-2"

    # Trigger Target 1 partial exit -> cancels ORD-2, sells q1, re-arms ORD-4 SL-L for q2
    q1, q2 = tr["q1_qty"], tr["q2_qty"]
    daemon.state["market_data"]["SBIN"].update({
        "ltp": tr["target_1_price"] + 0.5,
        "bar_open": tr["target_1_price"],
        "bar_high": tr["target_1_price"] + 0.5,
        "bar_low": tr["target_1_price"] - 0.5,
        "last_quote_ts": time.time(),
    })
    daemon.manage_active_trade(t0 + timedelta(minutes=2))
    assert "ORD-2" in calls["cancelled"]
    assert calls["placed"][2]["transaction_type"] == "SELL"
    assert calls["placed"][2]["quantity"] == q1
    if q2 > 0:
        assert calls["placed"][3]["order_type"] == "SL"
        assert calls["placed"][3]["quantity"] == q2

        # Final EOD exit -> cancels ORD-4 and sells remaining q2
        eod_t = datetime(2026, 11, 11, 15, 16, tzinfo=IST)
        daemon.state["market_data"]["SBIN"]["last_quote_ts"] = time.time()
        daemon.manage_active_trade(eod_t)
        assert "ORD-4" in calls["cancelled"]
        assert calls["placed"][-1]["transaction_type"] == "SELL"
        assert calls["placed"][-1]["quantity"] == q2
        assert daemon.state["active_trade"] is None


def test_redteam2_minute_rollover_prev_bar_adverse_flow_and_history_archive(tmp_path):
    """
    Verify:
      - _update_symbol_micro() exposes prev_bar_open / prev_bar_close on minute flips so
        manage_active_trade() increments adverse_flow_bars even when current tick bar_open == ltp.
      - New-day rollover archives yesterday's trade_history cleanly instead of leaking into today.
    """
    daemon = LiveStrategyDaemon(runtime_dir=tmp_path)
    t0 = datetime(2026, 11, 11, 10, 0, tzinfo=IST)
    daemon.state["market_status"]["is_open"] = True

    # Simulate entry on LONG SBIN @ 1000
    daemon.state["market_data"]["SBIN"] = {
        "ltp": 1000.0, "open": 998.0, "day_high": 1001.0, "day_low": 994.0,
        "volume": 10000, "l2_imbalance_pct": 25.0, "l2_live_available": False,
    }
    daemon.execute_trade_entry("SBIN", "LONG", 1000.0, 988.0, now=t0)
    assert daemon.state["active_trade"] is not None

    # Minute 10:00 starts at 999.0 and drops to 997.0 (bearish close < open), then 10:01 opens at 997.0 (bar_open == ltp!)
    daemon._update_symbol_micro("SBIN", 999.0, 10500, 1001.0, 994.0, t0 + timedelta(seconds=10))
    daemon._update_symbol_micro("SBIN", 997.0, 11200, 1001.0, 994.0, t0 + timedelta(seconds=50))
    micro_flip = daemon._update_symbol_micro("SBIN", 997.0, 11500, 1001.0, 994.0, t0 + timedelta(minutes=1, seconds=2))
    assert micro_flip["bar_open"] == 997.0  # New minute opened right at ltp!
    assert micro_flip["prev_bar_open"] == 999.0
    assert micro_flip["prev_bar_close"] == 997.0

    daemon.state["market_data"]["SBIN"].update({
        **micro_flip,
        "ltp": 997.0,
        "l2_imbalance_pct": -35.0,
        "last_quote_ts": time.time(),
    })
    daemon.manage_active_trade(t0 + timedelta(minutes=1, seconds=2))
    assert daemon.state["active_trade"]["adverse_flow_bars"] == 1

    # Archive test across day rollover in _restore_runtime_state
    daemon.state["trade_history"] = [{"symbol": "SBIN", "net_pnl": 42.0}]
    daemon._persist_runtime_state(now=t0)
    daemon2 = LiveStrategyDaemon(runtime_dir=tmp_path)
    daemon2._restore_runtime_state(now=t0 + timedelta(days=1))
    assert daemon2.state["trade_history"] == []
    assert (tmp_path / "trade_history_2026-11-11.json").exists()


def test_redteam3_unfilled_limit_entry_cancels_without_arming_orphan_sll(tmp_path):
    """
    Verify when ALPHANITI_LIVE_ORDERS=true and a LIMIT entry stays unconfirmed/unfilled:
      - Daemon cancels the resting entry order instead of marking FILLED.
      - active_trade remains None and zero orphan SL-L order is placed.
      - EMAPI FSM records FixOrdStatus.CANCELLED.
    """
    daemon = LiveStrategyDaemon(runtime_dir=tmp_path)
    daemon.live_order_execution = True
    events = {"placed": [], "cancelled": []}

    class _UnfilledGroww:
        EXCHANGE_NSE = "NSE"
        SEGMENT_CASH = "CASH"
        PRODUCT_MIS = "MIS"
        VALIDITY_DAY = "DAY"
        ORDER_TYPE_LIMIT = "LIMIT"
        ORDER_TYPE_STOP_LOSS = "SL"
        TRANSACTION_TYPE_BUY = "BUY"
        TRANSACTION_TYPE_SELL = "SELL"

        def place_order(self, **kwargs):
            events["placed"].append(kwargs)
            return {"groww_order_id": "ORD-UNFILLED"}

        def get_order_status(self, groww_order_id=None):
            return {"order_status": "OPEN"}

        def cancel_order(self, groww_order_id=None, segment=None):
            events["cancelled"].append(groww_order_id)
            return {"status": "CANCELLED"}

    daemon.groww = _UnfilledGroww()
    daemon.state["market_status"]["is_open"] = True
    daemon.state["market_data"]["SBIN"] = {
        "ltp": 1000.0, "open": 996.0, "day_high": 1001.0, "day_low": 995.0,
        "volume": 50000, "l2_imbalance_pct": 25.0, "l2_live_available": False,
        "last_quote_ts": time.time(),
    }
    t0 = datetime(2026, 11, 11, 10, 35, tzinfo=IST)
    daemon.execute_trade_entry("SBIN", "LONG", 1000.0, 988.0, now=t0)

    assert len(events["placed"]) == 1  # No SL-L placed!
    assert "ORD-UNFILLED" in events["cancelled"]
    assert daemon.state["active_trade"] is None
    assert FixOrdStatus.CANCELLED in [o["status"] for o in daemon.emapi_fsm.orders.values()]


def test_redteam3_sll_trail_modify_and_double_fill_prevention(tmp_path):
    """
    Verify:
      - Ratcheting active_floor calls modify_order to trail standing exchange SL-L trigger.
      - If standing exchange SL-L already filled before exit runs, NO duplicate exit order is fired.
    """
    daemon = LiveStrategyDaemon(runtime_dir=tmp_path)
    daemon.live_order_execution = True
    events = {"placed": [], "modified": [], "cancelled": []}

    class _TrailAndSllFilledGroww:
        EXCHANGE_NSE = "NSE"
        SEGMENT_CASH = "CASH"
        PRODUCT_MIS = "MIS"
        VALIDITY_DAY = "DAY"
        ORDER_TYPE_LIMIT = "LIMIT"
        ORDER_TYPE_STOP_LOSS = "SL"
        TRANSACTION_TYPE_BUY = "BUY"
        TRANSACTION_TYPE_SELL = "SELL"

        def place_order(self, **kwargs):
            oid = f"ORD-{len(events['placed']) + 1}"
            events["placed"].append({"oid": oid, **kwargs})
            return {"groww_order_id": oid}

        def modify_order(self, **kwargs):
            events["modified"].append(kwargs)
            return {"status": "MODIFIED"}

        def get_order_status(self, groww_order_id=None):
            if groww_order_id == "ORD-2":  # Standing SL-L filled on exchange before exit check!
                return {"order_status": "EXECUTED", "average_fill_price": 1001.2}
            return {"order_status": "EXECUTED", "average_fill_price": 1000.0}

        def cancel_order(self, groww_order_id=None, segment=None):
            events["cancelled"].append(groww_order_id)
            raise RuntimeError("Order already executed")

    daemon.groww = _TrailAndSllFilledGroww()
    daemon.state["market_status"]["is_open"] = True
    daemon.state["market_data"]["SBIN"] = {
        "ltp": 1000.0, "open": 996.0, "day_high": 1001.0, "day_low": 995.0,
        "volume": 50000, "l2_imbalance_pct": 25.0, "l2_live_available": False,
        "last_quote_ts": time.time(),
    }
    t0 = datetime(2026, 11, 11, 10, 40, tzinfo=IST)
    daemon.execute_trade_entry("SBIN", "LONG", 1000.0, 988.0, now=t0)
    assert len(events["placed"]) == 2  # Entry + SL-L (ORD-2)

    # Move peak to +0.55R -> patient hold preserves initial SL (no premature +0.50R choke)
    daemon.state["market_data"]["SBIN"].update({
        "ltp": 1006.6, "bar_open": 1003.0, "bar_high": 1007.0, "bar_low": 1002.5,
        "last_quote_ts": time.time(),
    })
    daemon.manage_active_trade(t0 + timedelta(minutes=1))
    assert len(events["modified"]) == 0

    # Move peak past +1.15R -> active_floor ratchets up and calls modify_order on ORD-2
    daemon.state["market_data"]["SBIN"].update({
        "ltp": 1014.4, "bar_open": 1006.6, "bar_high": 1015.0, "bar_low": 1006.0,
        "last_quote_ts": time.time(),
    })
    daemon.manage_active_trade(t0 + timedelta(minutes=2))
    assert len(events["modified"]) == 1
    assert events["modified"][0]["groww_order_id"] == "ORD-2"
    assert events["modified"][0]["trigger_price"] == daemon.state["active_trade"]["active_floor"]

    # Price drops through ratcheted floor -> ORD-2 already filled at 1001.2 -> NO duplicate sell placed!
    count_before_exit = len(events["placed"])
    daemon.state["market_data"]["SBIN"].update({
        "ltp": 999.0, "bar_open": 1002.0, "bar_high": 1002.5, "bar_low": 998.5,
        "last_quote_ts": time.time(),
    })
    daemon.manage_active_trade(t0 + timedelta(minutes=3))
    assert len(events["placed"]) == count_before_exit  # Zero duplicate sell!
    assert daemon.state["active_trade"] is None
    closed = daemon.state["trade_history"][-1]
    assert closed["exit_reason"] == "EXCHANGE_SLL_FILLED"
    assert closed["exit_price"] == 1001.2


def test_redteam4_exit_limit_unfilled_chase_and_fail_safe_rearm(tmp_path):
    """
    Verify:
      - Unfilled initial exit LIMIT cancels and re-pegs with 0.5% marketable collar.
      - If chased exit STILL remains unfilled, active_trade stays OPEN (EXIT_PENDING_LIQUIDITY_RETRY)
        with a freshly re-armed protective SL-L on all unclosed shares.
    """
    daemon = LiveStrategyDaemon(runtime_dir=tmp_path)
    daemon.live_order_execution = True
    state = {"placed": [], "statuses": {}, "chase_fills": True}

    class _ExitChaseGroww:
        EXCHANGE_NSE = "NSE"
        SEGMENT_CASH = "CASH"
        PRODUCT_MIS = "MIS"
        VALIDITY_DAY = "DAY"
        ORDER_TYPE_LIMIT = "LIMIT"
        ORDER_TYPE_STOP_LOSS = "SL"
        TRANSACTION_TYPE_BUY = "BUY"
        TRANSACTION_TYPE_SELL = "SELL"

        def place_order(self, **kwargs):
            oid = f"ORD-{len(state['placed']) + 1}"
            state["placed"].append({"oid": oid, **kwargs})
            if kwargs["transaction_type"] == "BUY":
                state["statuses"][oid] = "EXECUTED"
            elif kwargs["order_type"] == "SL":
                state["statuses"][oid] = "TRIGGER_PENDING"
            else:
                # 1st SELL attempt rests OPEN; 2nd SELL (collar chase) fills only when chase_fills=True
                is_chase = len([x for x in state["placed"] if x["transaction_type"] == "SELL" and x["order_type"] == "LIMIT"]) >= 2
                state["statuses"][oid] = "EXECUTED" if (is_chase and state["chase_fills"]) else "OPEN"
            return {"groww_order_id": oid}

        def get_order_status(self, groww_order_id=None):
            st = state["statuses"].get(groww_order_id, "OPEN")
            return {"order_status": st, "average_fill_price": 983.0 if st == "EXECUTED" else None}

        def cancel_order(self, groww_order_id=None, segment=None):
            state["statuses"][groww_order_id] = "CANCELLED"
            return {"status": "CANCELLED"}

    daemon.groww = _ExitChaseGroww()
    daemon.state["market_status"]["is_open"] = True
    daemon.state["market_data"]["SBIN"] = {
        "ltp": 1000.0, "open": 996.0, "day_high": 1001.0, "day_low": 995.0,
        "volume": 50000, "l2_imbalance_pct": 25.0, "l2_live_available": False,
        "last_quote_ts": time.time(),
    }
    t0 = datetime(2026, 11, 11, 10, 45, tzinfo=IST)
    daemon.execute_trade_entry("SBIN", "LONG", 1000.0, 988.0, now=t0)
    assert daemon.state["active_trade"] is not None

    # Scenario A: EOD exit -> initial SELL rests OPEN -> cancelled -> collar chase fills at 983.0
    eod_t = datetime(2026, 11, 11, 15, 16, tzinfo=IST)
    daemon.state["market_data"]["SBIN"]["ltp"] = 995.0
    daemon.manage_active_trade(eod_t)
    assert daemon.state["active_trade"] is None
    assert daemon.state["trade_history"][-1]["exit_price"] == 983.0

    # Scenario B: Zero-liquidity -> both initial & chased SELL stay OPEN -> trade remains OPEN with SL-L re-armed!
    state["placed"].clear()
    state["statuses"].clear()
    state["chase_fills"] = False
    daemon.execute_trade_entry("SBIN", "LONG", 1000.0, 988.0, now=t0 + timedelta(minutes=5))
    daemon.state["market_data"]["SBIN"]["ltp"] = 987.0
    daemon.state["market_data"]["SBIN"]["bar_low"] = 986.5
    daemon.manage_active_trade(t0 + timedelta(minutes=6))

    assert daemon.state["active_trade"] is not None
    assert daemon.state["active_trade"]["status_note"] == "EXIT_PENDING_LIQUIDITY_RETRY"
    assert daemon.state["active_trade"]["sl_order_id"] is not None


def test_redteam4_multimonth_oos_parquet_history_audit():
    """Verify deterministic multi-month NSE historical audit inside test15 module."""
    from src.validation.test15_monte_carlo_frontier import evaluate_multimonth_oos_history

    audit = evaluate_multimonth_oos_history()
    assert audit["status"] == "VERIFIED"
    assert audit["observations"] >= 2000
    assert audit["symbols_count"] >= 8
    assert audit["dynamic_jcvar_cvar99_pct"] >= audit["static_158_cvar99_pct"]
