"""
Pytest verification for the complete 12-test AlphaNiti Hardest Real-Time Stress Test Schedule
(Test 12 Hardest First + Tests 1..11 + LiveStrategyDaemon spread/L2/circuit proximity guards).
"""

import sys
import types
from datetime import datetime
from collections import deque
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
    gw.GrowwAPI = _GrowwAPIStub
    sys.modules["growwapi"] = gw

from openview_api import get_chart_history
from src.execution.live_safety_guards import BrokerLatencyWatchdog, TokenExpirySentinel, UpfrontMarginValidator
from src.execution.flawless_gap_dump_engine import FlawlessGapDumpEngine
from src.execution.emapi_fix import EmapiExecutionFSM
from src.validation.hardest_stress_schedule import run_all_12_tests_hardest_first
from live_strategy_daemon import LiveStrategyDaemon


def test_complete_12_test_hardest_real_time_schedule():
    ov = get_chart_history(symbol="SBIN.NS", interval="1m", period="5d")
    raw_1m = ov.get("candles", [])
    assert len(raw_1m) >= 300

    report = run_all_12_tests_hardest_first(raw_1m)
    assert report["execution_order"] == [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    assert report["total_tests"] == 12
    assert report["all_passed"] is True, f"Some stress tests failed: {report}"

    for r in report["results"]:
        assert r["passed"] is True
        assert r["zero_future_leakage"] is True
        assert r["zero_invalid_fills"] is True
        assert r["zero_duplicate_orders"] is True
        assert r["zero_risk_violations"] is True
        assert r["ending_capital_rs"] >= 2500.0


def test_daemon_spread_l2_and_circuit_proximity_guards():
    d = LiveStrategyDaemon.__new__(LiveStrategyDaemon)
    d.tz = ZoneInfo("Asia/Kolkata")
    d.log_ring_buffer = deque(maxlen=20)
    d.latency_watchdog = BrokerLatencyWatchdog()
    d.token_sentinel = TokenExpirySentinel()
    d.margin_validator = UpfrontMarginValidator()
    d.engine = FlawlessGapDumpEngine(capital=8382.07)
    d.emapi_fsm = EmapiExecutionFSM()
    d.starting_cash = 8382.07
    d.daily_loss_limit_pct = 0.032
    d.last_exit_minute_key = None
    d.daily_loss_halted = False
    d.state = {
        "account": {"clear_cash": 8382.07, "available_margin": 8382.07, "mis_margin_used": 0.0, "position_status": "FLAT"},
        "strategy": {"macro_regime": "ROTATIONAL_CHOP", "active_phase": "LIVE"},
        "safety_guards": {"token_sentinel": {"valid": True}},
        "market_status": {"is_open": True},
        "market_data": {
            "SBIN": {
                "ltp": 1000.0,
                "open": 1010.0,
                "day_high": 1012.0,
                "day_low": 998.0,
                "volume": 100000,
                "l2_imbalance_pct": -35.0,
                "spread_bps": 45.0,  # Wide spread spike > 25 bps
                "l2_live_available": True,
                "buy_depth": 0,      # Zero bid vacuum
                "sell_depth": 50000,
            }
        },
        "lunch_bases": {},
        "active_trade": None,
        "trade_history": [],
        "today_net_pnl": 0.0,
    }
    d.scan_for_setups(datetime(2026, 11, 9, 10, 5, tzinfo=d.tz))
    assert d.state["active_trade"] is None, "Daemon must block entries when spread > 25 bps or L2 bid depth == 0"
