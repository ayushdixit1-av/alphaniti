import sys
import time
import types
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

if "growwapi" not in sys.modules:
    gw = types.ModuleType("growwapi")
    gw.GrowwAPI = type("GrowwAPI", (), {"__init__": lambda self, *a, **kw: None})
    sys.modules["growwapi"] = gw

from live_strategy_daemon import LiveStrategyDaemon
from src.execution.flawless_gap_dump_engine import FlawlessGapDumpEngine


IST = ZoneInfo("Asia/Kolkata")


def _seed_expansion_daemon(tmp_path, nifty_rng=0.0085, nifty_ret=-0.0045):
    daemon = LiveStrategyDaemon(runtime_dir=tmp_path)
    daemon.state["market_status"]["is_open"] = True
    daemon.state["safety_guards"]["token_sentinel"]["valid"] = True
    daemon.state["strategy"]["macro_regime"] = "ROTATIONAL_CHOP (DUAL-DIRECTION ACTIVE)"
    daemon.state["indices"]["NIFTY"] = {
        "symbol": "NIFTY",
        "ltp": 24000.0 * (1.0 + nifty_ret),
        "day_range_pct": nifty_rng,
        "intraday_ret_pct": nifty_ret,
    }
    return daemon


def test_engine_groww_beater_t1_and_patient_ratchet():
    eng = FlawlessGapDumpEngine(capital=8382.07, target_1_r=1.85, ratchet_trigger_r=1.15, trail_capture_ratio=0.70)
    op = eng.compute_order_parameters("SBIN", entry_price=1000.0, stop_loss=990.0, direction="LONG", enforce_fee_viability=True)
    assert op["status"] == "APPROVED"
    # Risk cap strictly enforced (<= 1.6% of 8382.07 = 134.11) and leverage <= 1.58x
    assert op["total_qty"] * 10.0 <= 135.0
    assert op["total_qty"] * 1000.0 <= 8382.07 * 1.58 + 1.0
    # Target 1 padded past 1.85R + fees
    assert op["target_1_price"] > 1018.5

    # At +0.60R, engine stays patient at initial stop (no +0.50R noise choke)
    ev_early = eng.evaluate_bar(
        entry_price=1000.0, stop_loss=990.0, qty=op["total_qty"],
        bar_open=1003.0, bar_high=1006.0, bar_low=1002.0, bar_close=1005.0,
        bar_vol=10000, avg_vol=10000, cvd_delta=100, cvd_persistent=False,
        current_active_floor=990.0, peak_favorable_r=0.0, direction="LONG",
    )
    assert ev_early["action"] == "HOLD"
    assert ev_early["active_floor"] == 990.0

    # At +1.25R, fee_guard breakeven floor engages safely
    ev_be = eng.evaluate_bar(
        entry_price=1000.0, stop_loss=990.0, qty=op["total_qty"],
        bar_open=1008.0, bar_high=1012.5, bar_low=1007.5, bar_close=1012.0,
        bar_vol=12000, avg_vol=10000, cvd_delta=500, cvd_persistent=False,
        current_active_floor=990.0, peak_favorable_r=0.60, direction="LONG",
    )
    assert ev_be["active_floor"] > 1000.0
    assert ev_be["active_floor"] <= 1012.0

    # At +2.40R, runner locks 70% of peak gains (~1.68R => 1016.80)
    ev_run = eng.evaluate_bar(
        entry_price=1000.0, stop_loss=990.0, qty=op["total_qty"],
        bar_open=1020.0, bar_high=1024.0, bar_low=1019.0, bar_close=1023.0,
        bar_vol=15000, avg_vol=10000, cvd_delta=800, cvd_persistent=False,
        current_active_floor=ev_be["active_floor"], peak_favorable_r=1.25, direction="LONG",
    )
    assert ev_run["mode"] == "RUNNER_PROFIT_TRAIL"
    assert ev_run["active_floor"] >= 1016.5


def test_daemon_sits_out_low_vol_chop_and_low_rvol(tmp_path):
    # 1) Low-volatility choppy NIFTY (range 0.45% < 0.70%) must produce ZERO entries
    d_chop = _seed_expansion_daemon(tmp_path / "chop", nifty_rng=0.0045, nifty_ret=-0.0010)
    d_chop.state["market_data"]["SBIN"] = {
        "ltp": 994.0, "open": 1000.0, "day_high": 1002.0, "day_low": 993.5,
        "bar_open": 996.0, "vwap": 998.0, "ema3": 995.0, "ema8": 998.5,
        "rvol": 2.1, "bar_count": 12, "volume": 120000, "l2_imbalance_pct": -30.0,
        "l2_live_available": True, "buy_depth": 5000, "sell_depth": 10000, "spread_bps": 2.0,
        "last_quote_ts": time.time(),
    }
    now_t = datetime(2026, 11, 11, 10, 15, tzinfo=IST)
    d_chop.scan_for_setups(now_t)
    assert d_chop.state["active_trade"] is None, "Must sit out choppy low-range days to avoid Groww fee bleed"

    # 2) Even on high-range NIFTY day, weak RVOL (< 1.50x) must be rejected
    d_lowv = _seed_expansion_daemon(tmp_path / "lowv", nifty_rng=0.0095, nifty_ret=-0.0055)
    d_lowv.state["market_data"]["SBIN"] = {
        "ltp": 994.0, "open": 1000.0, "day_high": 1001.0, "day_low": 993.5,
        "bar_open": 996.0, "vwap": 998.0, "ema3": 995.0, "ema8": 998.5,
        "rvol": 1.05, "bar_count": 12, "volume": 40000, "l2_imbalance_pct": -25.0,
        "l2_live_available": True, "buy_depth": 5000, "sell_depth": 10000, "spread_bps": 2.0,
        "last_quote_ts": time.time(),
    }
    d_lowv.scan_for_setups(now_t)
    assert d_lowv.state["active_trade"] is None, "Must require RVOL >= 1.50x"


def test_daemon_expansion_entry_and_runner_beats_groww_fees(tmp_path):
    d = _seed_expansion_daemon(tmp_path / "live_win", nifty_rng=0.0095, nifty_ret=0.0055)
    d.state["lunch_bases"]["HDFCBANK"] = {"base_high": 790.0, "base_low": 782.0}
    d.state["market_data"]["HDFCBANK"] = {
        "ltp": 792.0, "open": 784.0, "day_high": 792.5, "day_low": 782.5,
        "bar_open": 789.5, "vwap": 787.0, "ema3": 790.8, "ema8": 787.2,
        "rvol": 1.95, "bar_count": 14, "volume": 150000,
        "l2_imbalance_pct": 0.0, "l2_live_available": False,  # Neutral L2 should NOT block RVOL+EMA expansion
        "last_quote_ts": time.time(),
    }
    t0 = datetime(2026, 11, 11, 12, 10, tzinfo=IST)
    d.scan_for_setups(t0)
    tr = d.state["active_trade"]
    assert tr is not None
    assert tr["symbol"] == "HDFCBANK"
    assert tr["direction"] == "LONG"
    assert tr["target_1_price"] > 792.0 + 1.80 * abs(792.0 - tr["stop_loss"])
    # Margin safety preserved (>= Rs 2500 free cash buffer + <= 1.58x leverage)
    notional = tr["total_qty"] * tr["entry_price"]
    assert notional <= 8382.07 * 1.58 + 1.0
    assert d.state["account"]["available_margin"] >= 2500.0

    # Simulate hitting T1 and trailing runner profit past Groww brokerage + slippage
    t1_px = tr["target_1_price"] + 0.5
    d.state["market_data"]["HDFCBANK"].update({
        "ltp": t1_px, "bar_open": t1_px - 2.0, "bar_high": t1_px + 1.0, "bar_low": t1_px - 2.5,
        "last_quote_ts": time.time(),
    })
    d.manage_active_trade(t0 + timedelta(minutes=5))
    assert d.state["active_trade"]["t1_hit"] is True

    # Power hour EOD squareoff at +2.6R above entry -> guaranteed positive net profit after fees
    close_px = round(tr["entry_price"] + 2.6 * abs(tr["entry_price"] - tr["stop_loss"]), 2)
    d.state["market_data"]["HDFCBANK"].update({
        "ltp": close_px, "bar_open": close_px - 1.0, "bar_high": close_px + 0.5, "bar_low": close_px - 1.5,
        "last_quote_ts": time.time(),
    })
    d.manage_active_trade(datetime(2026, 11, 11, 15, 16, tzinfo=IST))
    assert d.state["active_trade"] is None
    closed = d.state["trade_history"][-1]
    assert closed["net_pnl"] > 200.0, f"Expected strong net profit after Groww fees, got {closed['net_pnl']}"
    assert closed["fees"] > 0.0
