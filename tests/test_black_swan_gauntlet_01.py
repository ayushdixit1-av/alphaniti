"""
AlphaNiti Black Swan Gauntlet — Test 01 (F4 Severe Shock: -5.00% over 30m on SBIN.NS 1m Primary).
Verifies detection, explanation, zero future-data leakage, valid gap fills,
zero duplicate orders, zero risk-limit violations, and strict replay isolation.
"""

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from openview_api import get_chart_history
from src.execution.intraday_engine import calculate_15m_atr_aligned_to_5m
from src.validation.black_swan_gauntlet import (
    _compute_causal_bar_metrics,
    extract_session_1m_candles,
    generate_f4_shock_series,
    run_black_swan_test_01,
    run_isolated_posture_replay,
)


def test_01_live_1m_sbin_f4_linear_and_jagged_gauntlet():
    ov = get_chart_history(symbol="SBIN.NS", interval="1m", period="5d")
    raw_1m = ov.get("candles", [])
    assert len(raw_1m) >= 100, "Expected sufficient 1-minute historical SBIN.NS candles"

    resolved_date, session_bars = extract_session_1m_candles(raw_1m)
    assert len(session_bars) >= 60

    # Verify exact deterministic linear formula P_k = P_0 * (1 - 0.05 * k / 30)
    combined_lin, meta_lin = generate_f4_shock_series(
        session_bars, anchor_time_ist="14:30", duration_minutes=30, shock_pct=-0.05, path_variant="linear"
    )
    p0 = meta_lin["p0"]
    synth_lin = [b for b in combined_lin if b.get("is_synthetic")]
    assert len(synth_lin) == 30
    for k, b in enumerate(synth_lin, start=1):
        assert b["close"] == round(p0 * (1.0 - 0.05 * (k / 30.0)), 2)
    assert synth_lin[-1]["close"] == round(p0 * 0.95, 2)
    assert meta_lin["shock_pct"] == -5.0

    # Verify jagged variant has gaps & intrabar wicks while hitting exact -5.00% endpoint
    combined_jag, meta_jag = generate_f4_shock_series(
        session_bars, anchor_time_ist="14:30", duration_minutes=30, shock_pct=-0.05, path_variant="jagged"
    )
    synth_jag = [b for b in combined_jag if b.get("is_synthetic")]
    assert len(synth_jag) == 30
    assert synth_jag[-1]["close"] == round(p0 * 0.95, 2)
    gap_downs = sum(
        1 for i in range(1, len(synth_jag)) if synth_jag[i]["open"] < synth_jag[i - 1]["close"]
    )
    assert gap_downs >= 5, "Jagged shock must inject real opening air-pocket gaps"

    # Full 6-case Gauntlet Matrix run
    report = run_black_swan_test_01(
        raw_1m_candles=raw_1m,
        symbol="SBIN",
        anchor_time_ist="14:30",
        duration_minutes=30,
        shock_pct=-0.05,
        path_variant="jagged",
        posture="EXPOSED_LONG_INTO_CRASH",
    )
    assert report["overall_verdict"] == "PASS_ALL_INVARIANTS", json.dumps(report["gauntlet_matrix"], indent=2)

    for row in report["gauntlet_matrix"]:
        assert row["passed"] is True, f"Scenario failed: {row}"
        assert row["invariants"]["zero_future_leakage"] is True
        assert row["invariants"]["zero_invalid_fills"] is True
        assert row["invariants"]["zero_duplicate_orders"] is True
        assert row["invariants"]["zero_risk_limit_violations"] is True


def test_02_strict_causal_atr_and_prefix_zero_leakage():
    # Ensure calculate_15m_atr_aligned_to_5m never backfills bar 2 into bar 0..1
    rows_a = [
        {"time_str": "09:15", "open": 1000.0, "high": 1002.0, "low": 999.0, "close": 1001.0},
        {"time_str": "09:20", "open": 1001.0, "high": 1003.0, "low": 1000.0, "close": 1002.0},
        {"time_str": "09:25", "open": 1002.0, "high": 1004.0, "low": 1001.0, "close": 1003.0},
        {"time_str": "09:30", "open": 1003.0, "high": 1005.0, "low": 1002.0, "close": 1004.0},
    ]
    rows_b = [
        {"time_str": "09:15", "open": 1000.0, "high": 1002.0, "low": 999.0, "close": 1001.0},
        {"time_str": "09:20", "open": 1001.0, "high": 1003.0, "low": 1000.0, "close": 1002.0},
        {"time_str": "09:25", "open": 1002.0, "high": 1190.0, "low": 810.0, "close": 950.0},
        {"time_str": "09:30", "open": 950.0, "high": 955.0, "low": 940.0, "close": 945.0},
    ]
    out_a = calculate_15m_atr_aligned_to_5m(pd.DataFrame(rows_a))
    out_b = calculate_15m_atr_aligned_to_5m(pd.DataFrame(rows_b))
    assert out_a.loc[0, "atr_15m"] == out_b.loc[0, "atr_15m"], "Future bar 2 leaked into bar 0 ATR!"
    assert out_a.loc[1, "atr_15m"] == out_b.loc[1, "atr_15m"], "Future bar 2 leaked into bar 1 ATR!"


def test_03_gap_through_stop_produces_conservative_ohlc_valid_fill():
    # Construct synthetic 1m sequence with clean anchor then violent -3% single-bar air-pocket gap
    base_ts = 1794213000
    bars = []
    for i in range(25):
        bars.append(
            {
                "time": base_ts + i * 60,
                "timestamp": base_ts + i * 60,
                "open": 1040.0,
                "high": 1041.0,
                "low": 1039.0,
                "close": 1040.0,
                "volume": 25000,
                "is_synthetic": False,
            }
        )
    # Inject immediate -3% gap-down at t=22 (after LONG entry at t=17)
    bars.append({"time": base_ts + 25 * 60, "timestamp": base_ts + 25 * 60, "open": 1008.0, "high": 1009.5, "low": 1005.0, "close": 1006.0, "volume": 180000, "is_synthetic": True, "shock_k": 1, "cum_shock_pct": -3.27})
    for k in range(2, 31):
        p = round(1040.0 * (1.0 - 0.05 * k / 30.0), 2)
        bars.append({"time": base_ts + (24 + k) * 60, "timestamp": base_ts + (24 + k) * 60, "open": p + 0.5, "high": p + 1.0, "low": p - 0.5, "close": p, "volume": 90000, "is_synthetic": True, "shock_k": k, "cum_shock_pct": round((p / 1040.0 - 1.0) * 100, 2)})

    meta = {"anchor_index": 24, "p0": 1040.0}
    res = run_isolated_posture_replay(bars, meta, posture="EXPOSED_LONG_INTO_CRASH", symbol="SBIN")
    assert res["invariants"]["zero_invalid_fills"] is True
    assert len(res["trades"]) == 1
    tr = res["trades"][0]
    assert tr["exit_reason"] == "GAP_THROUGH_STOP_LOSS"
    assert tr["gap_through"] is True
    assert tr["exit_price"] <= 1009.5 and tr["exit_price"] >= 1005.0
    assert res["summary"]["reserve_floor_intact"] is True
