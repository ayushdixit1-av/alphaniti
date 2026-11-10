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
from src.strategies.gap_orb_trend_strategy import GapOrbTrendStrategy

IST = ZoneInfo("Asia/Kolkata")


def test_sanitize_intraday_bars_rejects_100x_feed_glitch():
    normal_bars = [
        (555, "2025-05-13T09:15:00", 1500.0, 1505.0, 1498.0, 1503.0, 10000.0),
        (556, "2025-05-13T09:16:00", 1503.0, 1508.0, 1501.0, 1506.0, 12000.0),
    ]
    assert len(GapOrbTrendStrategy.sanitize_intraday_bars(normal_bars)) == 2

    glitched_bars = [
        (555, "2025-05-12T10:31:00", 1560.0, 1563.0, 1559.0, 1562.8, 10000.0),
        (556, "2025-05-12T10:32:00", 1562.8, 156240.0, 1562.8, 156230.0, 12000.0),
    ]
    assert GapOrbTrendStrategy.sanitize_intraday_bars(glitched_bars) == []


def test_gap_orb_filters_splits_extreme_atr_and_countertrend():
    strat = GapOrbTrendStrategy(risk_pct=0.016, max_leverage=1.58)
    now_ts = time.time()
    base_md = {
        "RELIANCE": {
            "open": 1025.0, "ltp": 1030.0, "vwap": 1024.0, "ema5": 1029.0, "ema15": 1026.0,
            "rvol": 2.2, "orb15_high": 1027.0, "orb15_low": 1019.0, "bar_count": 18,
            "spread_bps": 2.0, "l2_live_available": True, "buy_depth": 5000, "sell_depth": 5000,
            "last_quote_ts": now_ts,
        }
    }
    # 1) Counter-trend macro: NIFTY < SMA20 blocks Long gap continuation
    sig_bear_macro = strat.evaluate_universe(
        time_min=580, market_data=base_md,
        daily_context={"RELIANCE": {"prev_close": 1000.0, "atr14_pct": 0.022}},
        nifty_prev_close=23900.0, nifty_sma20=24200.0, nifty_ret_pct=0.004,
        breadth_pct=70.0, equity=10000.0,
    )
    assert sig_bear_macro is None

    # 2) Extreme ATR regime (> 3.5%) blocks VIX panic chop
    sig_high_atr = strat.evaluate_universe(
        time_min=580, market_data=base_md,
        daily_context={"RELIANCE": {"prev_close": 1000.0, "atr14_pct": 0.048}},
        nifty_prev_close=24300.0, nifty_sma20=24000.0, nifty_ret_pct=0.004,
        breadth_pct=70.0, equity=10000.0,
    )
    assert sig_high_atr is None

    # 3) Unadjusted corporate bonus/split gap (> 12%) rejected
    sig_split = strat.evaluate_universe(
        time_min=580, market_data=base_md,
        daily_context={"RELIANCE": {"prev_close": 2050.0, "atr14_pct": 0.020}},
        nifty_prev_close=24300.0, nifty_sma20=24000.0, nifty_ret_pct=0.004,
        breadth_pct=70.0, equity=10000.0,
    )
    assert sig_split is None

    # 4) Valid aligned Long setup passes with <= 1.6% risk & <= 1.58x leverage
    sig_ok = strat.evaluate_universe(
        time_min=580, market_data=base_md,
        daily_context={"RELIANCE": {"prev_close": 1000.0, "atr14_pct": 0.022}},
        nifty_prev_close=24300.0, nifty_sma20=24000.0, nifty_ret_pct=0.004,
        breadth_pct=70.0, equity=10000.0,
    )
    assert sig_ok is not None
    assert sig_ok["symbol"] == "RELIANCE"
    assert sig_ok["direction"] == "LONG"
    assert sig_ok["qty"] * abs(sig_ok["entry_price"] - sig_ok["stop_loss"]) <= 160.0 + 1e-6
    assert sig_ok["qty"] * sig_ok["entry_price"] <= 10000.0 * 1.58 + 1e-6


def test_daemon_a8_10k_eod_hold_beats_groww_without_choking(tmp_path):
    daemon = LiveStrategyDaemon(runtime_dir=tmp_path, capital=10000.0)
    daemon.state["account"]["clear_cash"] = 10000.0
    daemon.state["account"]["available_margin"] = 10000.0
    daemon.state["market_status"]["is_open"] = True
    daemon.state["safety_guards"]["token_sentinel"]["valid"] = True
    daemon.state["indices"]["NIFTY"] = {
        "symbol": "NIFTY",
        "prev_close": 24300.0,
        "sma20": 24000.0,
        "intraday_ret_pct": 0.0045,
        "day_range_pct": 0.0055,
    }
    daemon.daily_context["SBIN"] = {"prev_close": 750.0, "atr14_pct": 0.021}
    now_ts = time.time()
    daemon.state["market_data"]["SBIN"] = {
        "open": 766.0, "ltp": 770.0, "day_high": 770.5, "day_low": 761.0,
        "vwap": 766.5, "ema5": 769.2, "ema15": 766.0, "rvol": 2.4,
        "orb15_high": 768.0, "orb15_low": 761.0, "bar_count": 20,
        "volume": 200000, "l2_imbalance_pct": 10.0, "l2_live_available": False,
        "last_quote_ts": now_ts,
    }

    t_entry = datetime(2026, 11, 11, 9, 45, tzinfo=IST)
    daemon.scan_for_setups(t_entry)
    tr = daemon.state["active_trade"]
    assert tr is not None
    assert tr["symbol"] == "SBIN"
    assert tr["eod_hold_only"] is True
    assert tr["strategy_tag"] == "A8_GAP_ORB15_EOD"

    # Mid-day dip to -0.45R below VWAP with temporary sell imbalance must NOT trigger premature adverse cut on EOD-hold A8
    mid_px = round(tr["entry_price"] - 0.45 * abs(tr["entry_price"] - tr["stop_loss"]), 2)
    daemon.state["market_data"]["SBIN"].update({
        "ltp": mid_px, "bar_open": mid_px + 2.0, "bar_high": mid_px + 2.5, "bar_low": mid_px - 0.5,
        "l2_imbalance_pct": -30.0, "cur_1m_vol": 25000.0, "avg_1m_vol": 10000.0, "last_quote_ts": time.time(),
    })
    daemon.manage_active_trade(datetime(2026, 11, 11, 13, 0, tzinfo=IST))
    assert daemon.state["active_trade"] is not None, "A8 must hold to EOD unless hard 1.2% SL hits"

    # EOD squareoff at +2.8% gain captures full institutional continuation net profit after Groww fees
    close_px = round(tr["entry_price"] * 1.028, 2)
    daemon.state["market_data"]["SBIN"].update({
        "ltp": close_px, "bar_open": close_px - 2.0, "bar_high": close_px + 0.5, "bar_low": close_px - 2.5,
        "last_quote_ts": time.time(),
    })
    daemon.manage_active_trade(datetime(2026, 11, 11, 15, 16, tzinfo=IST))
    assert daemon.state["active_trade"] is None
    closed = daemon.state["trade_history"][-1]
    assert closed["net_pnl"] > 250.0
    assert closed["fees"] > 0.0
