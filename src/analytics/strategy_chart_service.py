"""
Strategy Candlestick Chart Service (Any NSE Stock + Causal A8 Gap+ORB15 Analysis)
Provides 1-minute OHLCV candles, ORB15 box, cumulative VWAP, 1m EMA5/EMA15, RVOL,
and causal GapOrbTrendStrategy (A8) entry/1.2%-stop/EOD-exit evaluation + diagnostics
for ANY NSE cash symbol requested by the frontend dashboard.
"""

from __future__ import annotations

import datetime
import math
import pickle
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz

from src.portfolio.statutory_costs import IndianStatutoryCostModel
from src.strategies.gap_orb_trend_strategy import GapOrbTrendStrategy

IST = pytz.timezone("Asia/Kolkata")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = PROJECT_ROOT / "cache_1m_7y"

FEATURED_PRESETS: List[Dict[str, Optional[str]]] = [
    {
        "symbol": "ADANIENT",
        "date": "2024-11-21",
        "tag": "SHORT",
        "label": "ADANIENT · 2024-11-21 (A8 Short Breakdown)",
    },
    {
        "symbol": "ICICIBANK",
        "date": "2026-11-05",
        "tag": "LONG",
        "label": "ICICIBANK · 2026-11-05 (A8 Long Breakout)",
    },
    {
        "symbol": "RELIANCE",
        "date": "2025-04-28",
        "tag": "LONG",
        "label": "RELIANCE · 2025-04-28 (A8 Long ORB15)",
    },
    {
        "symbol": "BAJFINANCE",
        "date": "2026-03-30",
        "tag": "SHORT",
        "label": "BAJFINANCE · 2026-03-30 (A8 Short Breakdown)",
    },
    {
        "symbol": "TATASTEEL",
        "date": "2026-05-18",
        "tag": "LONG",
        "label": "TATASTEEL · 2026-05-18 (A8 Long Trend)",
    },
    {
        "symbol": "SBIN",
        "date": None,
        "tag": "LATEST",
        "label": "SBIN · Latest Session",
    },
    {
        "symbol": "TCS",
        "date": None,
        "tag": "LATEST",
        "label": "TCS · Latest Session",
    },
    {
        "symbol": "TRENT",
        "date": None,
        "tag": "ANY NSE",
        "label": "TRENT · Latest Session (Custom NSE)",
    },
]


def normalize_nse_symbol(raw_symbol: str) -> str:
    """Normalize user-entered symbol like 'reliance.ns' or 'NSE-TATAMOTORS' -> 'RELIANCE'."""
    sym = (raw_symbol or "").strip().upper()
    if sym.startswith("NSE-"):
        sym = sym[4:]
    if sym.endswith(".NS"):
        sym = sym[:-3]
    if sym.endswith(".BO"):
        sym = sym[:-3]
    sym = re.sub(r"[^A-Z0-9&-]", "", sym)
    return sym


def _min_to_hhmm(min_of_day: int) -> str:
    return f"{min_of_day // 60:02d}:{min_of_day % 60:02d}"


def _parse_iso_or_epoch_to_ist(raw_ts: Any) -> datetime.datetime:
    if isinstance(raw_ts, (int, float)):
        return datetime.datetime.fromtimestamp(float(raw_ts), tz=IST)
    s = str(raw_ts).strip()
    if s.isdigit():
        return datetime.datetime.fromtimestamp(float(s), tz=IST)
    dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return IST.localize(dt)
    return dt.astimezone(IST)


def _extract_candles_list(res: Any) -> List[Any]:
    if isinstance(res, dict):
        return list(res.get("candles") or [])
    if isinstance(res, list):
        return res
    return []


def _compute_daily_context_from_rows(
    daily_rows: List[Tuple[str, float, float, float, float]],
    target_date: str,
) -> Tuple[Optional[float], Optional[float]]:
    """Compute prior close and prior 14-day ATR% strictly strictly BEFORE target_date (causal)."""
    prior = [r for r in daily_rows if r[0] < target_date]
    if not prior:
        return None, None
    prior.sort(key=lambda x: x[0])
    prev_close = float(prior[-1][4])
    trs: List[float] = []
    for i in range(1, len(prior)):
        _, _, h, l, _ = prior[i]
        pc = prior[i - 1][4]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        if pc > 0:
            trs.append(tr / pc)
    atr14_pct = (sum(trs[-14:]) / min(len(trs), 14)) if trs else 0.0
    return prev_close, atr14_pct


def _normalize_bar_tuple(b: Any) -> Tuple[int, float, float, float, float, int]:
    """Normalize either 6-tuple (t_min, o, h, l, c, v) or 7-tuple cache_1m_7y (t_min, iso_ts, o, h, l, c, v)."""
    if len(b) >= 7 and isinstance(b[1], str):
        return (int(b[0]), float(b[2]), float(b[3]), float(b[4]), float(b[5]), int(float(b[6] or 0)))
    return (int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), int(float(b[5] or 0)))


def _load_from_pkl_cache(
    symbol: str,
    target_date: Optional[str],
    cache_dir: Path,
) -> Optional[Dict[str, Any]]:
    pkl_path = cache_dir / f"{symbol}.pkl"
    if not pkl_path.exists():
        return None
    try:
        with open(pkl_path, "rb") as f:
            payload = pickle.load(f)
    except Exception:
        return None

    by_day: Dict[str, List[Tuple[int, float, float, float, float, int]]] = {}
    daily_rows: List[Tuple[str, float, float, float, float]] = []

    if isinstance(payload, dict) and "by_day" in payload and isinstance(payload["by_day"], dict):
        for k, v in payload["by_day"].items():
            if isinstance(k, str) and isinstance(v, list) and v:
                by_day[k] = [_normalize_bar_tuple(b) for b in v]
        daily_rows = list(payload.get("daily_rows") or [])
    elif isinstance(payload, dict):
        # cache_1m_7y schema: { "YYYY-MM-DD": [(t_min, iso_ts, o, h, l, c, v), ...] }
        for k, v in payload.items():
            if isinstance(k, str) and len(k) == 10 and isinstance(v, list) and v:
                by_day[k] = [_normalize_bar_tuple(b) for b in v]

    if not by_day:
        return None

    if not daily_rows:
        for d_key in sorted(by_day.keys()):
            bars = by_day[d_key]
            if bars:
                d_o = float(bars[0][1])
                d_h = max(float(b[2]) for b in bars)
                d_l = min(float(b[3]) for b in bars)
                d_c = float(bars[-1][4])
                daily_rows.append((d_key, d_o, d_h, d_l, d_c))

    resolved_date = target_date if (target_date and target_date in by_day) else None
    if target_date and resolved_date is None:
        return None
    if resolved_date is None:
        resolved_date = max(by_day.keys())

    bars = by_day.get(resolved_date, [])
    prev_c, atr14 = _compute_daily_context_from_rows(daily_rows, resolved_date)
    return {
        "session_date": resolved_date,
        "bars": bars,
        "prev_close": prev_c,
        "atr14_pct": atr14,
        "source": "7y_groww_1m_cache",
        "interval_used": "1m",
        "available_dates_count": len(by_day),
    }


def _fetch_groww_candles_for_range(
    groww_client: Any,
    symbol: str,
    start_dt: str,
    end_dt: str,
    interval: str = "1minute",
) -> List[Any]:
    if groww_client is None:
        return []
    is_index = symbol in ("NIFTY", "NIFTY50", "BANKNIFTY")
    groww_sym = f"NSE-{'NIFTY' if symbol == 'NIFTY50' else symbol}"
    segment = "IDX" if is_index else "CASH"

    # Try V2 get_historical_candles first, then V1 get_historical_candle_data
    try:
        if hasattr(groww_client, "get_historical_candles"):
            res = groww_client.get_historical_candles(
                exchange="NSE",
                segment=segment,
                groww_symbol=groww_sym,
                start_time=start_dt,
                end_time=end_dt,
                candle_interval=interval,
            )
            candles = _extract_candles_list(res)
            if candles:
                return candles
    except Exception:
        pass

    try:
        if hasattr(groww_client, "get_historical_candle_data"):
            mins = 1 if interval == "1minute" else (5 if interval == "5minute" else 1440)
            res = groww_client.get_historical_candle_data(
                trading_symbol="NIFTY" if symbol == "NIFTY50" else symbol,
                exchange="NSE",
                segment=segment,
                start_time=start_dt,
                end_time=end_dt,
                interval_in_minutes=mins,
            )
            candles = _extract_candles_list(res)
            if candles:
                return candles
    except Exception:
        pass
    return []


def _parse_groww_bars_by_day(
    raw_candles: List[Any],
) -> Dict[str, List[Tuple[int, float, float, float, float, int]]]:
    by_day: Dict[str, List[Tuple[int, float, float, float, float, int]]] = {}
    for c in raw_candles:
        try:
            dt_ist = _parse_iso_or_epoch_to_ist(c[0])
            t_min = dt_ist.hour * 60 + dt_ist.minute
            if not (555 <= t_min <= 929):
                continue
            d_str = dt_ist.strftime("%Y-%m-%d")
            bar = (t_min, float(c[1]), float(c[2]), float(c[3]), float(c[4]), int(float(c[5] or 0)))
            by_day.setdefault(d_str, []).append(bar)
        except Exception:
            continue
    for d_str in list(by_day.keys()):
        dedup: Dict[int, Tuple[int, float, float, float, float, int]] = {b[0]: b for b in by_day[d_str]}
        by_day[d_str] = [dedup[k] for k in sorted(dedup.keys())]
    return by_day


def _fetch_from_groww(
    symbol: str,
    target_date: Optional[str],
    groww_client: Any,
) -> Optional[Dict[str, Any]]:
    if groww_client is None:
        return None
    anchor_d = (
        datetime.date.fromisoformat(target_date)
        if target_date
        else datetime.datetime.now(IST).date()
    )
    start_intraday = (anchor_d - datetime.timedelta(days=7 if target_date is None else 4)).strftime("%Y-%m-%d")
    end_intraday = anchor_d.strftime("%Y-%m-%d")

    raw_1m = _fetch_groww_candles_for_range(
        groww_client,
        symbol,
        f"{start_intraday} 09:15:00",
        f"{end_intraday} 15:30:00",
        interval="1minute",
    )
    interval_used = "1m"
    if not raw_1m:
        raw_1m = _fetch_groww_candles_for_range(
            groww_client,
            symbol,
            f"{start_intraday} 09:15:00",
            f"{end_intraday} 15:30:00",
            interval="5minute",
        )
        interval_used = "5m"

    by_day = _parse_groww_bars_by_day(raw_1m)
    if not by_day:
        return None

    if target_date:
        resolved_date = target_date if target_date in by_day else None
        if resolved_date is None:
            return None
    else:
        substantial = [d for d, bs in by_day.items() if len(bs) >= 15]
        resolved_date = max(substantial) if substantial else max(by_day.keys())

    # Fetch ~40 days daily candles for causal prev_close & ATR14%
    daily_start = (datetime.date.fromisoformat(resolved_date) - datetime.timedelta(days=45)).strftime("%Y-%m-%d")
    raw_daily = _fetch_groww_candles_for_range(
        groww_client,
        symbol,
        f"{daily_start} 09:15:00",
        f"{resolved_date} 15:30:00",
        interval="1day",
    )
    daily_rows: List[Tuple[str, float, float, float, float]] = []
    for c in raw_daily:
        try:
            d_str = _parse_iso_or_epoch_to_ist(c[0]).strftime("%Y-%m-%d")
            daily_rows.append((d_str, float(c[1]), float(c[2]), float(c[3]), float(c[4])))
        except Exception:
            continue
    if not any(r[0] < resolved_date for r in daily_rows):
        for d_k, bs in sorted(by_day.items()):
            if d_k < resolved_date and bs:
                daily_rows.append((
                    d_k,
                    bs[0][1],
                    max(b[2] for b in bs),
                    min(b[3] for b in bs),
                    bs[-1][4],
                ))

    prev_c, atr14 = _compute_daily_context_from_rows(daily_rows, resolved_date)
    return {
        "session_date": resolved_date,
        "bars": by_day[resolved_date],
        "prev_close": prev_c,
        "atr14_pct": atr14,
        "source": f"groww_live_api_{interval_used}",
        "interval_used": interval_used,
    }


def _fetch_from_yfinance(symbol: str, target_date: Optional[str]) -> Optional[Dict[str, Any]]:
    try:
        import yfinance as yf
    except Exception:
        return None

    yf_sym = "^NSEI" if symbol in ("NIFTY", "NIFTY50") else ("^NSEBANK" if symbol == "BANKNIFTY" else f"{symbol}.NS")
    today = datetime.datetime.now(IST).date()
    anchor = datetime.date.fromisoformat(target_date) if target_date else today
    days_ago = (today - anchor).days

    interval_used = "1m"
    df_intra = None
    try:
        if days_ago <= 6:
            df_intra = yf.download(yf_sym, period="7d", interval="1m", progress=False, auto_adjust=True)
        if (df_intra is None or df_intra.empty) and days_ago <= 58:
            interval_used = "5m"
            df_intra = yf.download(yf_sym, period="60d", interval="5m", progress=False, auto_adjust=True)
    except Exception:
        return None

    if df_intra is None or df_intra.empty:
        return None

    # Flatten MultiIndex if returned by newer yfinance
    if hasattr(df_intra.columns, "levels"):
        df_intra.columns = [c[0] if isinstance(c, tuple) else c for c in df_intra.columns]

    by_day: Dict[str, List[Tuple[int, float, float, float, float, int]]] = {}
    for idx, row in df_intra.iterrows():
        dt_ts = idx.to_pydatetime()
        if dt_ts.tzinfo is None:
            dt_ist = IST.localize(dt_ts)
        else:
            dt_ist = dt_ts.astimezone(IST)
        t_min = dt_ist.hour * 60 + dt_ist.minute
        if not (555 <= t_min <= 929):
            continue
        d_str = dt_ist.strftime("%Y-%m-%d")
        bar = (
            t_min,
            float(row["Open"]),
            float(row["High"]),
            float(row["Low"]),
            float(row["Close"]),
            int(float(row.get("Volume", 0) or 0)),
        )
        by_day.setdefault(d_str, []).append(bar)

    if not by_day:
        return None
    if target_date:
        if target_date not in by_day:
            return None
        resolved_date = target_date
    else:
        resolved_date = max(by_day.keys())

    bars = sorted(by_day[resolved_date], key=lambda x: x[0])

    # Daily bars for causal prev_close & ATR14%
    prev_c: Optional[float] = None
    atr14: Optional[float] = None
    try:
        df_d = yf.download(yf_sym, period="3mo", interval="1d", progress=False, auto_adjust=True)
        if df_d is not None and not df_d.empty:
            if hasattr(df_d.columns, "levels"):
                df_d.columns = [c[0] if isinstance(c, tuple) else c for c in df_d.columns]
            d_rows: List[Tuple[str, float, float, float, float]] = []
            for idx, r in df_d.iterrows():
                d_s = idx.strftime("%Y-%m-%d")
                d_rows.append((d_s, float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"])))
            prev_c, atr14 = _compute_daily_context_from_rows(d_rows, resolved_date)
    except Exception:
        pass

    return {
        "session_date": resolved_date,
        "bars": bars,
        "prev_close": prev_c,
        "atr14_pct": atr14,
        "source": f"yfinance_fallback_{interval_used}",
        "interval_used": interval_used,
    }


def _load_nifty_macro_context(
    target_date: str,
    groww_client: Any,
    cache_dir: Path,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (nifty_prev_close, nifty_sma20, nifty_ret_at_open_or_close)."""
    daily_rows: List[Tuple[str, float, float, float, float]] = []

    pkl = cache_dir / "NIFTY.pkl"
    if pkl.exists():
        try:
            with open(pkl, "rb") as f:
                p = pickle.load(f)
            if isinstance(p, dict) and "daily_rows" in p:
                daily_rows = list(p["daily_rows"])
            elif isinstance(p, dict):
                for d_k, bs in sorted(p.items()):
                    if isinstance(d_k, str) and len(d_k) == 10 and isinstance(bs, list) and bs:
                        norm_bs = [_normalize_bar_tuple(b) for b in bs]
                        daily_rows.append((
                            d_k,
                            float(norm_bs[0][1]),
                            max(float(b[2]) for b in norm_bs),
                            min(float(b[3]) for b in norm_bs),
                            float(norm_bs[-1][4]),
                        ))
        except Exception:
            pass

    if not daily_rows and groww_client is not None:
        start_d = (datetime.date.fromisoformat(target_date) - datetime.timedelta(days=50)).strftime("%Y-%m-%d")
        raw_d = _fetch_groww_candles_for_range(
            groww_client,
            "NIFTY",
            f"{start_d} 09:15:00",
            f"{target_date} 15:30:00",
            interval="1day",
        )
        for c in raw_d:
            try:
                d_s = _parse_iso_or_epoch_to_ist(c[0]).strftime("%Y-%m-%d")
                daily_rows.append((d_s, float(c[1]), float(c[2]), float(c[3]), float(c[4])))
            except Exception:
                continue

    if not daily_rows:
        try:
            import yfinance as yf
            df_n = yf.download("^NSEI", period="3mo", interval="1d", progress=False, auto_adjust=True)
            if df_n is not None and not df_n.empty:
                if hasattr(df_n.columns, "levels"):
                    df_n.columns = [c[0] if isinstance(c, tuple) else c for c in df_n.columns]
                for idx, r in df_n.iterrows():
                    daily_rows.append((idx.strftime("%Y-%m-%d"), float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"])))
        except Exception:
            pass

    prior = sorted([r for r in daily_rows if r[0] < target_date], key=lambda x: x[0])
    if not prior:
        return None, None, None
    nifty_prev_c = float(prior[-1][4])
    closes = [float(r[4]) for r in prior[-20:]]
    nifty_sma20 = sum(closes) / len(closes) if closes else None

    target_rows = [r for r in daily_rows if r[0] == target_date]
    nifty_day_ret = ((float(target_rows[0][4]) - nifty_prev_c) / nifty_prev_c) if (target_rows and nifty_prev_c > 0) else None
    return nifty_prev_c, nifty_sma20, nifty_day_ret


def analyze_intraday_strategy_bars(
    symbol: str,
    session_date: str,
    raw_bars: List[Tuple[int, float, float, float, float, int]],
    prev_close: Optional[float] = None,
    atr14_pct: Optional[float] = None,
    nifty_prev_close: Optional[float] = None,
    nifty_sma20: Optional[float] = None,
    nifty_day_ret_pct: Optional[float] = None,
    capital: float = 10000.0,
    source: str = "groww_1m",
    interval_used: str = "1m",
) -> Dict[str, Any]:
    """
    Pure causal analyzer turning sorted session bars [(t_min, o, h, l, c, v), ...] into
    full chart candles + ORB15 + VWAP + EMA5 + EMA15 + RVOL + A8 Signal + SL/Exit + Gate Diagnostics.
    """
    strat = GapOrbTrendStrategy()
    # Sanitize tuples: Strategy expects tuple where indices [1..4] are O,H,L,C
    # GapOrbTrendStrategy.sanitize_intraday_bars checks b[3] (High/Low index in its tuple signature)
    # Wait: verify indices in sanitize_intraday_bars -> highs = b[3], lows = b[4] OR min(low)/max(high)!
    # Note: sanitize_intraday_bars checks max(b[3])/min(b[4]) if (sym, t, o, h, l, c, v), so let's normalize clean bars & apply max_day_span_ratio check explicitly too!
    wrapped = [(symbol, b[0], b[1], b[2], b[3], b[4], b[5]) for b in raw_bars]
    cleaned_wrapped = strat.sanitize_intraday_bars(wrapped, max_day_span_ratio=1.25)
    # Double-check low/high span ratio accurately using actual Low (index 3 in raw_bars) & High (index 2)
    clean_bars: List[Tuple[int, float, float, float, float, int]] = []
    sanitized_rejected = False
    if cleaned_wrapped:
        lows = [float(b[3]) for b in raw_bars if b[3] is not None and float(b[3]) > 0]
        highs = [float(b[2]) for b in raw_bars if b[2] is not None]
        if lows and highs and (max(highs) / min(lows)) <= 1.25:
            clean_bars = sorted(raw_bars, key=lambda x: x[0])
        else:
            sanitized_rejected = True
    elif raw_bars:
        sanitized_rejected = True

    if sanitized_rejected or not clean_bars:
        return {
            "status": "SANITIZED_REJECTED" if sanitized_rejected else "NO_DATA",
            "symbol": symbol,
            "session_date": session_date,
            "source": source,
            "interval_used": interval_used,
            "sanitized_rejected": sanitized_rejected,
            "reason": "Extreme >25% intraday span / split / decimal glitch quarantined by GapOrbTrendStrategy.sanitize_intraday_bars()" if sanitized_rejected else "No intraday candles available for requested date/symbol",
            "candles": [],
            "orb15": None,
            "trade": None,
            "diagnostics": {},
            "presets": FEATURED_PRESETS,
        }

    # ORB15 (09:15..09:29 -> 555 <= t <= 569)
    orb_bars = [b for b in clean_bars if 555 <= b[0] <= 569]
    if not orb_bars:
        orb_bars = clean_bars[: min(3, len(clean_bars))]
    orb15_high = max(float(b[2]) for b in orb_bars)
    orb15_low = min(float(b[3]) for b in orb_bars)
    d_open = float(clean_bars[0][1])

    eff_prev_c = float(prev_close) if (prev_close and prev_close > 0) else d_open
    gap_pct = (d_open - eff_prev_c) / eff_prev_c if eff_prev_c > 0 else 0.0
    eff_atr14 = float(atr14_pct) if atr14_pct is not None else 0.02

    # Compute causal bar-by-bar indicators
    cum_pv = 0.0
    cum_v = 0.0
    ema5 = float(clean_bars[0][4])
    ema15 = float(clean_bars[0][4])
    k5 = 2.0 / 6.0
    k15 = 2.0 / 16.0
    vol_hist: List[int] = []

    enriched_candles: List[Dict[str, Any]] = []
    max_rvol_in_window = 0.0
    saw_orb_break_long = False
    saw_orb_break_short = False
    saw_vwap_ok = False
    saw_ema_ok = False

    n_bull = True if (nifty_sma20 is None or nifty_prev_close is None) else (nifty_prev_close > nifty_sma20)
    n_bear = True if (nifty_sma20 is None or nifty_prev_close is None) else (nifty_prev_close < nifty_sma20)

    gap_ok_long = gap_pct >= strat.min_gap_pct and abs(gap_pct) <= strat.max_gap_pct
    gap_ok_short = gap_pct <= -strat.min_gap_pct and abs(gap_pct) <= strat.max_gap_pct
    gap_ok = gap_ok_long or gap_ok_short
    atr_ok = eff_atr14 <= strat.max_atr14_pct

    trade_entry: Optional[Dict[str, Any]] = None
    entry_bar_idx: Optional[int] = None

    for idx, (t_min, o, h, l, c, v) in enumerate(clean_bars):
        tp = (h + l + c) / 3.0
        vol_pos = max(0, int(v))
        cum_pv += tp * vol_pos
        cum_v += vol_pos
        vwap = (cum_pv / cum_v) if cum_v > 0 else c

        if idx > 0:
            ema5 = c * k5 + ema5 * (1.0 - k5)
            ema15 = c * k15 + ema15 * (1.0 - k15)

        win_vols = vol_hist[-20:]
        avg_v = (sum(win_vols) / len(win_vols)) if win_vols else max(1.0, float(vol_pos))
        rvol = (float(vol_pos) / avg_v) if avg_v > 0 else 1.0
        vol_hist.append(vol_pos)

        # Causal bar Liquidity Factor [0..100]: RVOL participation + Rupee Turnover Depth - Amihud Price Impact
        turnover_lakhs = (c * float(vol_pos)) / 100_000.0
        bar_move_bps = (max(abs(c - o), h - l) / max(1e-6, c)) * 10_000.0
        amihud_impact = bar_move_bps / max(0.05, turnover_lakhs)
        vol_depth = min(1.0, rvol / 2.0)
        turnover_depth = min(1.0, math.log1p(turnover_lakhs) / math.log1p(50.0))
        impact_penalty = min(1.0, amihud_impact / 50.0)
        liquidity_score = round(
            100.0 * max(0.0, min(1.0, 0.45 * vol_depth + 0.40 * turnover_depth + 0.15 * (1.0 - impact_penalty))),
            1,
        )
        liquidity_tier = "DEEP" if liquidity_score >= 70.0 else ("NORMAL" if liquidity_score >= 40.0 else "THIN")

        hhmm = _min_to_hhmm(t_min)
        enriched_candles.append({
            "time": hhmm,
            "minute": t_min,
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": vol_pos,
            "vwap": round(vwap, 2),
            "ema5": round(ema5, 2),
            "ema15": round(ema15, 2),
            "rvol": round(rvol, 2),
            "turnover_lakhs": round(turnover_lakhs, 2),
            "amihud_impact": round(amihud_impact, 2),
            "liquidity_score": liquidity_score,
            "liquidity_tier": liquidity_tier,
        })

        # Evaluate A8 causal signal in 09:30..11:00 IST (570 <= t_min <= 660)
        if trade_entry is None and strat.entry_start_min <= t_min <= strat.entry_end_min and idx >= 10:
            if rvol > max_rvol_in_window:
                max_rvol_in_window = rvol
            if c > orb15_high:
                saw_orb_break_long = True
            if c < orb15_low:
                saw_orb_break_short = True
            if (gap_pct >= 0 and c > vwap) or (gap_pct < 0 and c < vwap):
                saw_vwap_ok = True
            if (gap_pct >= 0 and ema5 > ema15) or (gap_pct < 0 and ema5 < ema15):
                saw_ema_ok = True

            if atr_ok and rvol >= strat.min_rvol:
                # Check LONG
                if n_bull and gap_ok_long and c > orb15_high and c > vwap and ema5 > ema15:
                    sl = round(c * (1.0 - strat.stop_pct), 2)
                    entry_bar_idx = idx
                    trade_entry = {
                        "direction": "LONG",
                        "entry_idx": idx,
                        "entry_time": hhmm,
                        "entry_price": round(c, 2),
                        "stop_loss": sl,
                    }
                # Check SHORT
                elif n_bear and gap_ok_short and c < orb15_low and c < vwap and ema5 < ema15:
                    sl = round(c * (1.0 + strat.stop_pct), 2)
                    entry_bar_idx = idx
                    trade_entry = {
                        "direction": "SHORT",
                        "entry_idx": idx,
                        "entry_time": hhmm,
                        "entry_price": round(c, 2),
                        "stop_loss": sl,
                    }

    # If trade triggered, simulate hard 1.2% SL -> EOD 15:15 exit + statutory Groww fees
    trade_result: Optional[Dict[str, Any]] = None
    if trade_entry is not None and entry_bar_idx is not None:
        ep = float(trade_entry["entry_price"])
        sl = float(trade_entry["stop_loss"])
        direction = str(trade_entry["direction"])
        exit_idx = len(clean_bars) - 1
        exit_price = float(clean_bars[-1][4])
        exit_reason = "EOD_1515"

        for j in range(entry_bar_idx + 1, len(clean_bars)):
            t_j, _, h_j, l_j, c_j, _ = clean_bars[j]
            if direction == "LONG" and l_j <= sl:
                exit_idx = j
                exit_price = sl
                exit_reason = "SL_1.2%"
                break
            if direction == "SHORT" and h_j >= sl:
                exit_idx = j
                exit_price = sl
                exit_reason = "SL_1.2%"
                break
            if t_j >= 915:
                exit_idx = j
                exit_price = float(c_j)
                exit_reason = "EOD_1515"
                break

        r_sh = max(0.01, abs(ep - sl))
        cap_notional = min(capital * strat.max_leverage, max(0.0, (capital - 2500.0) * 5.0))
        qty = max(1, min(int((capital * strat.risk_pct) // r_sh), max(1, int(cap_notional // ep))))
        mult = 1.0 if direction == "LONG" else -1.0
        gross_pnl = (exit_price - ep) * qty * mult
        cost_model = IndianStatutoryCostModel(slippage_rate=0.00015)
        cost_info = cost_model.compute_intraday_roundtrip(
            entry_price=ep if direction == "LONG" else exit_price,
            exit_price=exit_price if direction == "LONG" else ep,
            shares=qty,
        )
        fees = float(cost_info["total_friction"])
        net_pnl = gross_pnl - fees

        trade_result = {
            **trade_entry,
            "exit_idx": exit_idx,
            "exit_time": enriched_candles[exit_idx]["time"],
            "exit_price": round(exit_price, 2),
            "exit_reason": exit_reason,
            "qty": qty,
            "capital_base": round(capital, 2),
            "gross_pnl": round(gross_pnl, 2),
            "fees": round(fees, 2),
            "net_pnl": round(net_pnl, 2),
            "ret_pct": round((net_pnl / capital) * 100.0, 2),
        }

    # Build transparent A8 diagnostic gates for the UI banner
    direction_bias = "LONG" if gap_pct >= 0 else "SHORT"
    macro_ok = n_bull if direction_bias == "LONG" else n_bear
    orb_ok = saw_orb_break_long if direction_bias == "LONG" else saw_orb_break_short

    liq_scores = [float(ec["liquidity_score"]) for ec in enriched_candles]
    win_liq_scores = [
        float(ec["liquidity_score"])
        for ec in enriched_candles
        if strat.entry_start_min <= int(ec["minute"]) <= strat.entry_end_min
    ]
    avg_liq = round(sum(liq_scores) / len(liq_scores), 1) if liq_scores else 0.0
    entry_win_liq = round(sum(win_liq_scores) / len(win_liq_scores), 1) if win_liq_scores else avg_liq
    total_turnover_cr = round(sum(float(ec["turnover_lakhs"]) for ec in enriched_candles) / 100.0, 2)
    avg_amihud = round(
        sum(float(ec["amihud_impact"]) for ec in enriched_candles) / len(enriched_candles), 2
    ) if enriched_candles else 0.0
    liq_regime = "DEEP" if avg_liq >= 70.0 else ("NORMAL" if avg_liq >= 40.0 else "THIN")

    diagnostics = {
        "prev_close": round(eff_prev_c, 2),
        "day_open": round(d_open, 2),
        "day_close": round(float(clean_bars[-1][4]), 2),
        "day_high": round(max(float(b[2]) for b in clean_bars), 2),
        "day_low": round(min(float(b[3]) for b in clean_bars), 2),
        "gap_pct": round(gap_pct * 100.0, 2),
        "atr14_pct": round(eff_atr14 * 100.0, 2),
        "nifty_prev_close": round(nifty_prev_close, 2) if nifty_prev_close is not None else None,
        "nifty_sma20": round(nifty_sma20, 2) if nifty_sma20 is not None else None,
        "nifty_day_ret_pct": round(nifty_day_ret_pct * 100.0, 2) if nifty_day_ret_pct is not None else None,
        "max_rvol_0930_1100": round(max_rvol_in_window, 2),
        "avg_liquidity_score": avg_liq,
        "entry_window_liquidity_score": entry_win_liq,
        "total_turnover_cr": total_turnover_cr,
        "avg_amihud_impact": avg_amihud,
        "liquidity_regime": liq_regime,
        "gates": {
            "gap_ge_1_8_pct": bool(gap_ok),
            "atr_le_3_5_pct": bool(atr_ok),
            "macro_nifty_sma20_ok": bool(macro_ok),
            "orb15_break_ok": bool(orb_ok),
            "rvol_ge_1_8x_ok": bool(max_rvol_in_window >= strat.min_rvol),
            "vwap_align_ok": bool(saw_vwap_ok),
            "ema5_15_align_ok": bool(saw_ema_ok),
            "liquidity_ge_45_ok": bool(entry_win_liq >= 45.0),
        },
        "a8_triggered": trade_result is not None,
    }

    return {
        "status": "SUCCESS",
        "symbol": symbol,
        "session_date": session_date,
        "source": source,
        "interval_used": interval_used,
        "sanitized_rejected": False,
        "bar_count": len(enriched_candles),
        "orb15": {
            "high": round(orb15_high, 2),
            "low": round(orb15_low, 2),
            "start_time": "09:15",
            "end_time": "09:29",
        },
        "candles": enriched_candles,
        "trade": trade_result,
        "diagnostics": diagnostics,
        "presets": FEATURED_PRESETS,
    }


def get_strategy_candlestick_payload(
    symbol: str,
    date_str: Optional[str] = None,
    groww_client: Any = None,
    cache_dir: Optional[Path] = None,
    capital: float = 10000.0,
) -> Dict[str, Any]:
    """
    Fetch 1m bars for ANY NSE symbol + optional date across cache -> Groww API -> yfinance,
    then run exact causal A8 strategy overlays & diagnostics.
    """
    clean_sym = normalize_nse_symbol(symbol)
    if not clean_sym:
        return {
            "status": "ERROR",
            "error": "Please enter a valid NSE stock symbol (e.g. RELIANCE, SBIN, ADANIENT, TRENT).",
            "candles": [],
            "presets": FEATURED_PRESETS,
        }

    target_date: Optional[str] = None
    if date_str and date_str.strip():
        s = date_str.strip()
        try:
            datetime.date.fromisoformat(s)
            target_date = s
        except ValueError:
            return {
                "status": "ERROR",
                "error": f"Invalid date '{date_str}'. Use YYYY-MM-DD format.",
                "candles": [],
                "presets": FEATURED_PRESETS,
            }

    eff_cache_dir = cache_dir if cache_dir is not None else DEFAULT_CACHE_DIR

    loaded: Optional[Dict[str, Any]] = None
    # 1. Local 7-year cache if present on VPS/local
    loaded = _load_from_pkl_cache(clean_sym, target_date, eff_cache_dir)

    # 2. Live/Historical Groww API (supports ALL NSE cash stocks)
    if loaded is None:
        loaded = _fetch_from_groww(clean_sym, target_date, groww_client)

    # 3. yfinance fallback (supports ALL NSE symbols via .NS for recent sessions)
    if loaded is None:
        loaded = _fetch_from_yfinance(clean_sym, target_date)

    if loaded is None:
        return {
            "status": "NOT_FOUND",
            "symbol": clean_sym,
            "session_date": target_date or "",
            "error": (
                f"No intraday candles found for NSE:{clean_sym}"
                + (f" on {target_date} (check NSE trading calendar / holiday or Groww token)" if target_date else "")
                + "."
            ),
            "candles": [],
            "presets": FEATURED_PRESETS,
        }

    resolved_date = str(loaded["session_date"])
    nifty_prev_c, nifty_sma20, nifty_day_ret = _load_nifty_macro_context(
        resolved_date, groww_client, eff_cache_dir
    )

    return analyze_intraday_strategy_bars(
        symbol=clean_sym,
        session_date=resolved_date,
        raw_bars=loaded["bars"],
        prev_close=loaded.get("prev_close"),
        atr14_pct=loaded.get("atr14_pct"),
        nifty_prev_close=nifty_prev_c,
        nifty_sma20=nifty_sma20,
        nifty_day_ret_pct=nifty_day_ret,
        capital=capital,
        source=str(loaded.get("source") or "groww"),
        interval_used=str(loaded.get("interval_used") or "1m"),
    )


def build_strategy_chart_router(get_groww_fn: Any = None):
    """Build reusable FastAPI router for /api/strategy_chart and /api/strategy_chart/presets."""
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/api/strategy_chart/presets")
    def get_strategy_chart_presets():
        return {"status": "SUCCESS", "presets": FEATURED_PRESETS}

    @router.get("/api/strategy_chart")
    def get_strategy_chart(
        symbol: str = "ADANIENT",
        date: Optional[str] = None,
        capital: float = 10000.0,
    ):
        groww_cli = get_groww_fn() if callable(get_groww_fn) else None
        return get_strategy_candlestick_payload(
            symbol=symbol,
            date_str=date,
            groww_client=groww_cli,
            capital=max(5000.0, float(capital)),
        )

    return router
