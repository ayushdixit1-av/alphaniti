import pickle
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.analytics.strategy_chart_service import (
    FEATURED_PRESETS,
    analyze_intraday_strategy_bars,
    get_strategy_candlestick_payload,
    normalize_nse_symbol,
)


def test_normalize_nse_symbol():
    assert normalize_nse_symbol("reliance.ns") == "RELIANCE"
    assert normalize_nse_symbol(" NSE-TATAMOTORS ") == "TATAMOTORS"
    assert normalize_nse_symbol("M&M.NS") == "M&M"
    assert normalize_nse_symbol("BAJAJ-AUTO") == "BAJAJ-AUTO"
    assert normalize_nse_symbol("!!!") == ""


def test_analyze_long_gap_orb15_eod_signal():
    # Previous close = 100.0, Day opens +2.5% gap at 102.5 (passes >=1.8% gap)
    bars = []
    # ORB15 (09:15..09:29 -> min 555..569): stays inside [102.0, 103.5]
    for idx, t_min in enumerate(range(555, 570)):
        c = 102.5 + (0.04 * (idx % 4))
        bars.append((t_min, c - 0.1, 103.4, 102.1, c, 1000))

    # 09:30 (t=570): Breakout with RVOL >= 1.8x (vol=5000 vs avg 1000), close above 103.4 ORB15 high & VWAP & EMA5>EMA15
    bars.append((570, 103.4, 104.2, 103.3, 104.0, 5000))
    # Continuation through 15:15 (t=915) without hitting 1.2% stop
    for t_min in range(571, 916):
        c = 104.0 + (t_min - 570) * 0.004
        bars.append((t_min, c - 0.05, c + 0.1, c - 0.1, c, 1100))

    res = analyze_intraday_strategy_bars(
        symbol="RELIANCE",
        session_date="2025-04-28",
        raw_bars=bars,
        prev_close=100.0,
        atr14_pct=0.02,
        nifty_prev_close=24000.0,
        nifty_sma20=23500.0,
        nifty_day_ret_pct=0.005,
        capital=10000.0,
    )

    assert res["status"] == "SUCCESS"
    assert res["orb15"]["high"] == 103.4
    assert res["orb15"]["low"] == 102.1
    assert res["trade"] is not None
    tr = res["trade"]
    assert tr["direction"] == "LONG"
    assert tr["entry_time"] == "09:30"
    assert tr["exit_reason"] == "EOD_1515"
    assert tr["net_pnl"] > 0.0
    assert res["diagnostics"]["a8_triggered"] is True
    # Verify per-bar and session liquidity factor telemetry
    c0 = res["candles"][15]
    assert 0.0 <= c0["liquidity_score"] <= 100.0
    assert c0["turnover_lakhs"] > 0.0
    assert c0["amihud_impact"] >= 0.0
    assert c0["liquidity_tier"] in ("DEEP", "NORMAL", "THIN")
    assert 0.0 <= res["diagnostics"]["avg_liquidity_score"] <= 100.0
    assert res["diagnostics"]["liquidity_regime"] in ("DEEP", "NORMAL", "THIN")
    assert "liquidity_ge_45_ok" in res["diagnostics"]["gates"]


def test_analyze_short_gap_orb15_and_hard_stop():
    # Previous close = 1000.0, Day gaps down -2.5% to 975.0
    bars = []
    for idx, t_min in enumerate(range(555, 570)):
        c = 975.0 - (0.3 * (idx % 3))
        bars.append((t_min, c + 0.5, 978.0, 972.0, c, 1000))

    # 09:32 breakdown below 972.0 with heavy RVOL
    bars.append((570, 973.0, 974.0, 972.2, 972.5, 1100))
    bars.append((571, 972.5, 973.0, 972.1, 972.3, 1200))
    bars.append((572, 972.2, 972.5, 969.0, 970.0, 6000))
    # Trigger 1.2% hard SL bounce later (sl = 970 * 1.012 = 981.64)
    bars.append((585, 974.0, 983.0, 973.5, 982.0, 2000))

    res = analyze_intraday_strategy_bars(
        symbol="ADANIENT",
        session_date="2024-11-21",
        raw_bars=bars,
        prev_close=1000.0,
        atr14_pct=0.025,
        nifty_prev_close=23400.0,
        nifty_sma20=23800.0,
        capital=10000.0,
    )

    assert res["status"] == "SUCCESS"
    assert res["trade"] is not None
    assert res["trade"]["direction"] == "SHORT"
    assert res["trade"]["exit_reason"] == "SL_1.2%"
    assert res["trade"]["net_pnl"] < 0.0


def test_quarantines_100x_decimal_glitch_day():
    # Simulate May 12, 2025 corrupted 100x feed day (>25% intraday span)
    bars = [
        (555, 2500.0, 2510.0, 2495.0, 2505.0, 1000),
        (556, 25.0, 25.2, 24.8, 25.1, 1000),
    ]
    res = analyze_intraday_strategy_bars(
        symbol="RELIANCE",
        session_date="2025-05-12",
        raw_bars=bars,
        prev_close=2500.0,
        atr14_pct=0.02,
    )
    assert res["status"] == "SANITIZED_REJECTED"
    assert res["sanitized_rejected"] is True
    assert res["candles"] == []


def test_cache_7tuple_pkl_and_fastapi_endpoint(tmp_path: Path):
    # Write sample cache_1m_7y schema {date: [(t_min, iso_str, o, h, l, c, v), ...]}
    stock_pkl = tmp_path / "TRENT.pkl"
    nifty_pkl = tmp_path / "NIFTY.pkl"
    d1_bars = [(555 + i, f"2026-11-08T09:{15+i:02d}:00", 5000.0, 5010.0, 4990.0, 5000.0, 500) for i in range(15)]
    d2_bars = [(555 + i, f"2026-11-09T09:{15+i:02d}:00", 5020.0, 5030.0, 5015.0, 5025.0, 700) for i in range(20)]
    with open(stock_pkl, "wb") as f:
        pickle.dump({"2026-11-08": d1_bars, "2026-11-09": d2_bars}, f)
    with open(nifty_pkl, "wb") as f:
        pickle.dump({"2026-11-08": [(555, "2026-11-08T09:15:00", 24000.0, 24050.0, 23950.0, 24020.0, 100)]}, f)

    payload = get_strategy_candlestick_payload(
        symbol="TRENT.NS",
        date_str="2026-11-09",
        groww_client=None,
        cache_dir=tmp_path,
    )
    assert payload["status"] == "SUCCESS"
    assert payload["symbol"] == "TRENT"
    assert payload["session_date"] == "2026-11-09"
    assert payload["source"] == "7y_groww_1m_cache"
    assert len(payload["candles"]) == 20
    assert payload["diagnostics"]["prev_close"] == 5000.0

    # Test shared FastAPI strategy chart router cleanly with mocked payload
    from fastapi import FastAPI
    from src.analytics.strategy_chart_service import build_strategy_chart_router

    app = FastAPI()
    app.include_router(build_strategy_chart_router())
    client = TestClient(app)
    r_presets = client.get("/api/strategy_chart/presets")
    assert r_presets.status_code == 200
    assert len(r_presets.json()["presets"]) == len(FEATURED_PRESETS)

    with patch("src.analytics.strategy_chart_service.get_strategy_candlestick_payload", return_value=payload):
        r_chart = client.get("/api/strategy_chart?symbol=TRENT&date=2026-11-09")
        assert r_chart.status_code == 200
        body = r_chart.json()
        assert body["symbol"] == "TRENT"
        assert body["status"] == "SUCCESS"


def test_latest_session_prefers_live_groww_over_older_cache(tmp_path: Path):
    # Cache stops on 2026-11-09, while live Groww has newer 2026-11-11 session bars
    with open(tmp_path / "RELIANCE.pkl", "wb") as f:
        pickle.dump(
            {"2026-11-09": [(555, "2026-11-09T09:15:00", 1310.0, 1315.0, 1308.0, 1312.0, 1000)]},
            f,
        )

    class _FakeGroww:
        def get_historical_candles(self, candle_interval="1minute", **kwargs):
            if candle_interval == "1day":
                return {"candles": [["2026-11-09T09:15:00+05:30", 1310.0, 1315.0, 1308.0, 1312.0, 50000]]}
            return {
                "candles": [
                    ["2026-11-11T09:15:00+05:30", 1333.5, 1337.0, 1330.1, 1336.0, 25000],
                    ["2026-11-11T09:16:00+05:30", 1336.0, 1340.0, 1335.0, 1339.0, 30000],
                ]
            }

    payload = get_strategy_candlestick_payload(
        symbol="RELIANCE",
        date_str=None,
        groww_client=_FakeGroww(),
        cache_dir=tmp_path,
    )
    assert payload["status"] == "SUCCESS"
    assert payload["session_date"] == "2026-11-11"
    assert payload["source"] == "groww_live_api_1m"
    assert len(payload["candles"]) == 2
