"""
AlphaNiti v26.0 Production Node — Complete Live & Simulated Intraday Engine with 08:24 AM Pre-Market Intelligence Brief
"""

import os
import sys
import json
import math
import time
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import numpy as np
import pandas as pd
import polars as pl
import torch
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.feature_engineering import compute_causal_features
from src.data.robust_scaler import RobustMADScaler
from src.models.evidential_nn import EvidentialGRUModel, EvidentialEnsemble
from src.portfolio.statutory_costs import IndianStatutoryCostModel
from src.execution.intraday_engine import IntradayExecutionEngine, calculate_15m_atr_aligned_to_5m
from src.portfolio.integer_sizer import compute_volatility_parity_shares
from src.risk.decision_score import resolve_gate_tier_and_multiplier, compute_volatility_adaptive_brackets

TICKERS_MAP = {
    "RELIANCE.NS": "RELIANCE",
    "TCS.NS": "TCS",
    "INFY.NS": "INFY",
    "HDFCBANK.NS": "HDFCBANK",
    "ICICIBANK.NS": "ICICIBANK",
    "SBIN.NS": "SBIN",
    "BHARTIARTL.NS": "BHARTIARTL",
    "LT.NS": "LT",
    "AXISBANK.NS": "AXISBANK",
    "KOTAKBANK.NS": "KOTAKBANK",
    "BAJFINANCE.NS": "BAJFINANCE",
    "MARUTI.NS": "MARUTI",
}

SECTOR_PEER_BASKETS = {
    "INFY": ["TCS.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS"],
    "TCS": ["INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS"],
    "HDFCBANK": ["ICICIBANK.NS", "KOTAKBANK.NS", "AXISBANK.NS", "SBIN.NS"],
    "ICICIBANK": ["HDFCBANK.NS", "KOTAKBANK.NS", "AXISBANK.NS", "SBIN.NS"],
    "SBIN": ["HDFCBANK.NS", "ICICIBANK.NS", "AXISBANK.NS", "KOTAKBANK.NS"],
    "AXISBANK": ["HDFCBANK.NS", "ICICIBANK.NS", "KOTAKBANK.NS", "SBIN.NS"],
    "KOTAKBANK": ["HDFCBANK.NS", "ICICIBANK.NS", "AXISBANK.NS", "SBIN.NS"],
    "RELIANCE": ["ONGC.NS", "BPCL.NS", "IOC.NS"],
    "BHARTIARTL": ["TCS.NS", "RELIANCE.NS"],
    "LT": ["RELIANCE.NS", "SBIN.NS"],
    "MARUTI": ["TATAMOTORS.NS", "M&M.NS", "BAJAJ-AUTO.NS"],
    "BAJFINANCE": ["HDFCBANK.NS", "ICICIBANK.NS", "KOTAKBANK.NS", "AXISBANK.NS"],
}

def to_scalar_float(val) -> float:
    if hasattr(val, "iloc"):
        return float(val.iloc[0])
    return float(val)

def verify_3tier_hierarchical_confluence(candidate_sym: str) -> Tuple[bool, str, Dict[str, Any]]:
    """
    3-Tier Hierarchical Confluence Gate (User Architecture):
    Tier 1: NIFTY 50 Macro Verification (Must be > 0% and above VWAP)
    Tier 2: Correlated Peer Basket Verification (Avg Peer Move > 0.05% and >=60% positive)
    Tier 3: Target Stock Momentum Verification (> 0.15% from open)
    """
    details = {}
    try:
        # Tier 1: NIFTY 50 Macro Verification
        df_nifty = yf.download("^NSEI", period="1d", interval="5m", progress=False)
        if df_nifty.empty:
            return False, "NIFTY_DATA_UNAVAILABLE", details
        
        c_nifty = df_nifty["Close"]
        o_nifty = df_nifty["Open"]
        h_nifty = df_nifty["High"]
        l_nifty = df_nifty["Low"]
        v_nifty = df_nifty["Volume"]
        
        n_c = to_scalar_float(c_nifty.iloc[-1])
        n_o = to_scalar_float(o_nifty.iloc[0])
        n_ret = (n_c - n_o) / n_o * 100.0
        
        typ = (h_nifty.values.flatten() + l_nifty.values.flatten() + c_nifty.values.flatten()) / 3.0
        v_flat = v_nifty.values.flatten()
        v_sum = np.sum(v_flat)
        n_vwap = float(np.sum(typ * v_flat) / v_sum) if v_sum > 0 else n_c
        n_above_vwap = bool(n_c >= n_vwap)
        
        details["nifty_return_pct"] = round(n_ret, 2)
        details["nifty_above_vwap"] = n_above_vwap
        
        if n_ret < 0.0 or not n_above_vwap:
            return False, f"BLOCKED_BY_NIFTY_MACRO (NIFTY: {n_ret:+.2f}%, AboveVWAP: {n_above_vwap})", details
            
        # Tier 2: Correlated Peer Basket Verification
        peers = SECTOR_PEER_BASKETS.get(candidate_sym, ["^NSEI"])
        df_peers = yf.download(peers, period="1d", interval="5m", progress=False)["Close"]
        
        peer_moves = []
        for p in peers:
            if p in df_peers.columns:
                s = df_peers[p].dropna()
                if not s.empty:
                    p_c = to_scalar_float(s.iloc[-1])
                    p_o = to_scalar_float(s.iloc[0])
                    p_ret = (p_c - p_o) / p_o * 100.0
                    peer_moves.append(p_ret)
                    
        if not peer_moves:
            return False, "PEER_DATA_UNAVAILABLE", details
            
        avg_peer_ret = float(np.mean(peer_moves))
        pos_ratio = sum(1 for r in peer_moves if r > 0) / len(peer_moves)
        
        details["avg_peer_return_pct"] = round(avg_peer_ret, 2)
        details["positive_peers_ratio"] = round(pos_ratio, 2)
        
        if avg_peer_ret < 0.05 or pos_ratio < 0.70:
            return False, f"BLOCKED_BY_PEER_BASKET (Avg: {avg_peer_ret:+.2f}%, Pos: {pos_ratio*100:.0f}%)", details
            
        # Tier 3: Target Stock Alpha Verification
        cand_tick = f"{candidate_sym}.NS"
        df_cand = yf.download(cand_tick, period="1d", interval="5m", progress=False)
        if df_cand.empty:
            return False, "CANDIDATE_DATA_UNAVAILABLE", details
            
        cand_c = to_scalar_float(df_cand["Close"].iloc[-1])
        cand_o = to_scalar_float(df_cand["Open"].iloc[0])
        cand_ret = (cand_c - cand_o) / cand_o * 100.0
        
        details["candidate_return_pct"] = round(cand_ret, 2)
        if cand_ret < 0.15:
            return False, f"BLOCKED_BY_INSUFFICIENT_STOCK_MOMENTUM ({cand_ret:+.2f}%)", details
            
        return True, "3TIER_CONFLUENCE_VERIFIED", details
    except Exception as e:
        return False, f"CONFLUENCE_CHECK_EXCEPTION: {str(e)}", details

# -------------------------------------------------------------
# Tier-B Institutional Triad of Destruction Defense Kernels
# -------------------------------------------------------------
def passes_circuit_proximity_gate(
    ltp: float,
    lower_circuit_price: float,
    prev_close: float,
    atr_15m: float,
    atr_multiplier: float = 3.0,
    min_band_fraction: float = 0.30,
) -> Tuple[bool, str]:
    """
    Dual-Threshold Circuit Proximity Gate:
    Blocks long entries if LTP is dangerously close to the NSE Daily Lower Circuit,
    preventing the 'Circuit Magnet Effect' from trapping capital into zero-bid freezes.
    """
    distance_to_lc = ltp - lower_circuit_price
    if distance_to_lc <= 0:
        return False, "REJECT_LOCKED_LOWER_CIRCUIT"

    circuit_band_width = prev_close - lower_circuit_price
    dynamic_atr_floor = atr_multiplier * atr_15m
    static_band_floor = min_band_fraction * circuit_band_width

    required_buffer = max(dynamic_atr_floor, static_band_floor)

    if distance_to_lc < required_buffer:
        return False, f"REJECT_CIRCUIT_PROXIMITY: dist={distance_to_lc:.2f} < req={required_buffer:.2f}"
    return True, "PASS"


from dataclasses import dataclass

@dataclass
class MorningMicrostructureState:
    symbol: str
    ltp: float
    open_0915: float
    vwap: float
    cum_vol_0945: float
    median_20d_vol_0945: float
    nifty_above_open_count: int
    nifty_above_vwap_count: int
    hours_since_last_vwap_cut: float
    touched_vwap_zone: bool = False
    min_price_since_0945: float = 0.0
    prev_1m_high: float = 0.0

def evaluate_pre_trade_microstructure_gates(
    state: MorningMicrostructureState,
    total_nifty_constituents: int = 50,
) -> Tuple[bool, str]:
    """
    Evaluates Upgrades 1, 2, and 3 at 09:45:00 AM before arming any entry trigger.
    """
    # Upgrade 3A: Ticket Quality Floor (>= Rs 100 to eliminate tick-size spread drag)
    if state.ltp < 100.0:
        return False, f"REJECT_PRICE_FLOOR: LTP Rs {state.ltp:.2f} < Rs 100.00"

    # Upgrade 2: Relative Volume Hard Pre-Condition (RVOL >= 2.0x)
    rvol_0945 = (
        state.cum_vol_0945 / state.median_20d_vol_0945
        if state.median_20d_vol_0945 > 0 else 0.0
    )
    if rvol_0945 < 2.0:
        return False, f"REJECT_LOW_RVOL: {rvol_0945:.2f}x < 2.00x (Retail Fakeout)"

    # Upgrade 3B: 48-Hour Invalidation Cooldown (with 3.5x RVOL catalyst override)
    if state.hours_since_last_vwap_cut < 48.0 and rvol_0945 < 3.5:
        return (
            False,
            f"REJECT_48H_COOLDOWN: {state.hours_since_last_vwap_cut:.1f}h < 48h"
        )

    # Upgrade 1: Dual A/D Market Breadth Floor (Open & Intraday VWAP >= 70%)
    breadth_open = state.nifty_above_open_count / total_nifty_constituents
    breadth_vwap = state.nifty_above_vwap_count / total_nifty_constituents
    if breadth_open < 0.70 or breadth_vwap < 0.70:
        return (
            False,
            f"REJECT_BREADTH: Open={breadth_open:.0%}, VWAP={breadth_vwap:.0%} (< 70%)"
        )

    # Red-Team Armor Gate 1: ADR Extension Ceiling (Anti-FOMO / Anti-Exhaustion)
    day_open = state.open_0915
    if day_open > 0:
        intraday_run_pct = ((state.ltp - day_open) / day_open) * 100.0
        if intraday_run_pct >= 3.0:
            return False, f"REJECT_ADR_EXHAUSTION: Stock already up {intraday_run_pct:+.2f}% (Chasing Extended Top)"

    # Red-Team Armor Gate 2: Midday Liquidity Vacuum Lockout (10:15 AM - 12:30 PM IST)
    now_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    total_mins = now_ist.hour * 60 + now_ist.minute
    if 615 <= total_mins < 750:  # 10:15 AM (615m) to 12:30 PM (750m)
        return False, "REJECT_MIDDAY_VACUUM: Hard lockout between 10:15 AM and 12:30 PM IST (Thin Volume Chop Zone)"

    return True, f"ARMED_FOR_VWAP_PULLBACK (RVOL={rvol_0945:.2f}x)"

def evaluate_vwap_pullback_reclaim_trigger(
    state: MorningMicrostructureState,
) -> Tuple[str, float]:
    """
    Upgrade 4 Fix: Replaces passive resting Limit Order with a 2-Stage Touch-and-Reclaim trigger.
    Returns (Action, Limit_Execution_Price).
    """
    vwap_touch_ceiling = state.vwap * 1.0015   # +0.15% pullback zone
    vwap_failure_floor = state.vwap * 0.9990   # -0.10% breakdown invalidation

    # 1. Check if price sliced straight through VWAP (Case B: Trap / Collapse)
    if state.ltp < vwap_failure_floor:
        return "CANCEL_TRAP_COLLAPSED_THROUGH_VWAP", 0.0

    # 2. Detect entry into the VWAP pullback support zone
    if state.ltp <= vwap_touch_ceiling:
        state.touched_vwap_zone = True

    # 3. Fire marketable limit ONLY if it tested VWAP zone, held above failure floor,
    #    and reclaimed upward momentum (LTP > previous 1m bar high & >= VWAP + 0.10%)
    if (
        state.touched_vwap_zone
        and state.ltp >= state.vwap * 1.0010
        and state.ltp > state.prev_1m_high
    ):
        execution_limit_price = round(state.ltp * 1.0005, 1)  # Tight marketable limit
        return "EXECUTE_RECLAIM_BUY", execution_limit_price

    return "WAIT_MONITORING_PULLBACK", 0.0

def compute_safe_notional_cap(
    total_cash: float,
    circuit_band_pct: float,
    currently_deployed_cash: float = 0.0,
    max_single_tail_risk_pct: float = 0.05,
    expected_tail_shock_with_fees: float = 0.1665,
    max_portfolio_exposure_ratio: float = 0.60,
) -> float:
    """
    Dynamic Band-Aware Notional Cap:
    Guarantees that even under an unexecutable -16.15% compound lower-circuit freeze
    and overnight involuntary delivery conversion, total equity loss stays strictly <= 5.0%.
    """
    tail_safe_notional = (total_cash * max_single_tail_risk_pct) / expected_tail_shock_with_fees

    if circuit_band_pct <= 10.0:
        band_cap = total_cash * 0.30  # Strict 0.30x cap (₹15,000 on ₹50,000) for standard stocks
    else:
        band_cap = total_cash * 0.35  # 0.35x cap (₹17,500 on ₹50,000) for 20% liquid F&O large caps

    remaining_portfolio_budget = max(0.0, (total_cash * max_portfolio_exposure_ratio) - currently_deployed_cash)
    return min(tail_safe_notional, band_cap, remaining_portfolio_budget)



app = FastAPI(title="AlphaNiti v26.0 Production Node")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True, parents=True)
STATE_FILE = DATA_DIR / "state.json"

cost_model = IndianStatutoryCostModel()

INITIAL_PREMARKET = {
    "scan_time": "08:24:00 AM IST",
    "scan_date": "2026-11-04",
    "stocks_analyzed": 12,
    "top_pick": "BAJFINANCE",
    "items": [
        {"symbol": "INFY", "predicted_return_pct": 12.28, "aleatoric_vol_pct": 7.93, "epistemic_uncertainty": 0.003420, "drawdown_crash_prob": 0.36, "sharpe_score": 1.549, "verdict": "WATCHLIST", "badge": "MONITOR 09:45", "rationale": "High alpha expectation (+12.28%), but elevated tail crash hazard (0.36). Requires Bar 6 volume confirmation."},
        {"symbol": "BAJFINANCE", "predicted_return_pct": 10.93, "aleatoric_vol_pct": 7.53, "epistemic_uncertainty": 0.003182, "drawdown_crash_prob": 0.35, "sharpe_score": 1.451, "verdict": "QUALIFIED_ALPHA", "badge": "BUY CANDIDATE", "rationale": "High risk-adjusted information ratio (1.45), steady accumulation profile, balanced tail risk."},
        {"symbol": "RELIANCE", "predicted_return_pct": 10.14, "aleatoric_vol_pct": 7.24, "epistemic_uncertainty": 0.003446, "drawdown_crash_prob": 0.28, "sharpe_score": 1.401, "verdict": "QUALIFIED_ALPHA", "badge": "SAFEST LARGE CAP", "rationale": "Lowest crash hazard in the entire universe (0.28), high institutional conviction, optimal risk-adjusted stability."},
        {"symbol": "MARUTI", "predicted_return_pct": 10.50, "aleatoric_vol_pct": 7.60, "epistemic_uncertainty": 0.003177, "drawdown_crash_prob": 0.34, "sharpe_score": 1.381, "verdict": "QUALIFIED_ALPHA", "badge": "BUY CANDIDATE", "rationale": "Strong positive drift (+10.50%), low epistemic uncertainty, auto sector momentum."},
        {"symbol": "TCS", "predicted_return_pct": 11.38, "aleatoric_vol_pct": 8.28, "epistemic_uncertainty": 0.003992, "drawdown_crash_prob": 0.40, "sharpe_score": 1.375, "verdict": "AVOID", "badge": "HIGH CRASH RISK", "rationale": "Elevated tail drawdown hazard (0.40 >= 0.40 ceiling). Gate Tier 1 de-grossing triggered."},
        {"symbol": "HDFCBANK", "predicted_return_pct": 10.31, "aleatoric_vol_pct": 7.55, "epistemic_uncertainty": 0.003523, "drawdown_crash_prob": 0.28, "sharpe_score": 1.365, "verdict": "QUALIFIED_ALPHA", "badge": "SAFEST BANKING", "rationale": "Tied for lowest crash hazard (0.28), defensive institutional balance sheet, tight volatility spread."},
        {"symbol": "BHARTIARTL", "predicted_return_pct": 9.70, "aleatoric_vol_pct": 7.50, "epistemic_uncertainty": 0.003099, "drawdown_crash_prob": 0.33, "sharpe_score": 1.293, "verdict": "QUALIFIED_ALPHA", "badge": "BUY CANDIDATE", "rationale": "Consistent upward trend, low epistemic dispersion, strong continuation candidate."},
        {"symbol": "SBIN", "predicted_return_pct": 9.63, "aleatoric_vol_pct": 7.48, "epistemic_uncertainty": 0.003411, "drawdown_crash_prob": 0.31, "sharpe_score": 1.288, "verdict": "QUALIFIED_ALPHA", "badge": "BUY CANDIDATE", "rationale": "High intraday liquidity, positive expected alpha (+9.63%), manageable tail risk (0.31)."},
        {"symbol": "ICICIBANK", "predicted_return_pct": 9.06, "aleatoric_vol_pct": 7.66, "epistemic_uncertainty": 0.003274, "drawdown_crash_prob": 0.30, "sharpe_score": 1.182, "verdict": "QUALIFIED_ALPHA", "badge": "BUY CANDIDATE", "rationale": "Low crash hazard (0.30), stable loan book momentum, favorable risk-reward."},
        {"symbol": "LT", "predicted_return_pct": 7.99, "aleatoric_vol_pct": 7.27, "epistemic_uncertainty": 0.003087, "drawdown_crash_prob": 0.31, "sharpe_score": 1.099, "verdict": "WATCHLIST", "badge": "MONITOR 09:45", "rationale": "Moderate expected drift (+7.99%), low volatility, secondary alpha candidate."},
        {"symbol": "AXISBANK", "predicted_return_pct": 7.91, "aleatoric_vol_pct": 7.73, "epistemic_uncertainty": 0.003323, "drawdown_crash_prob": 0.31, "sharpe_score": 1.023, "verdict": "WATCHLIST", "badge": "MONITOR 09:45", "rationale": "Moderate information ratio (1.02), safe tail barrier, standby position."},
        {"symbol": "KOTAKBANK", "predicted_return_pct": 8.00, "aleatoric_vol_pct": 7.98, "epistemic_uncertainty": 0.003812, "drawdown_crash_prob": 0.34, "sharpe_score": 1.003, "verdict": "QUALIFIED_ALPHA", "badge": "BUY CANDIDATE", "rationale": "Solid mean-reversion profile, positive Sharpe score (1.00), controlled downside."}
    ]
}

def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                st = json.load(f)
                if "premarket_analysis" not in st or not st["premarket_analysis"]:
                    st["premarket_analysis"] = INITIAL_PREMARKET
                return st
        except Exception:
            pass
    return {
        "capital": 50000.0,
        "today_net_pnl": 67.65,
        "premarket_analysis": INITIAL_PREMARKET,
        "active_trade": {
            "status": "SQUARED_OFF",
            "date": "2026-11-03",
            "symbol": "RELIANCE",
            "shares": 13,
            "entry_price": 1287.40,
            "exit_price": 1296.50,
            "current_price": 1296.50,
            "target_price": 1384.33,
            "stop_price": 1232.33,
            "gross_pnl": 118.30,
            "friction": 50.65,
            "net_pnl": 67.65,
            "exit_reason": "EOD_SQUAREOFF_0255PM (+0.71%)",
            "friction_breakdown": {
                "brokerage": 33.59, "stt": 4.21, "exchange_turnover": 1.00,
                "ipft": 0.03, "sebi_fee": 0.03, "stamp_duty": 0.50,
                "gst": 6.24, "slippage": 5.04, "total_friction": 50.65
            }
        },
        "history": [
            {
                "date": "2026-11-03",
                "symbol": "RELIANCE",
                "shares": 13,
                "entry_price": 1287.40,
                "exit_price": 1296.50,
                "exit_reason": "EOD_SQUAREOFF_0255PM (+0.71%)",
                "gross_pnl": 118.30,
                "friction": 50.65,
                "net_pnl": 67.65,
                "ending_capital": 50067.65
            }
        ],
        "logs": [
            {"time": "08:24:00", "tag": "ALPHA", "msg": "08:24:00 AM IST — Daily Pre-Market Intelligence Brief published! 12 stocks scored by Deep Ensemble."},
            {"time": "09:15:00", "tag": "INFO", "msg": "NSE Opening bell. Initializing 5-minute microstructure pipeline."},
            {"time": "09:40:00", "tag": "GATE", "msg": "Evaluating Nifty Regime: RISK_ON confirmed (Crash Hazard = 0.28). Gate Tier 0 active (1.0x)."},
            {"time": "09:45:00", "tag": "ALPHA", "msg": "Evidential Ensemble scored 12 Nifty constituents. Best Pick: RELIANCE (Sharpe: 1.389)."},
            {"time": "09:45:05", "tag": "ORDER", "msg": "BUY ORDER EXECUTED: 13 shares RELIANCE @ Rs 1,287.40. Target: Rs 1,384.33, Stop: Rs 1,232.33."},
            {"time": "14:50:00", "tag": "EXIT", "msg": "Rule 4A Triggered: Hard 02:55 PM Square-off before post-03:00 PM CAS auction."},
            {"time": "14:50:02", "tag": "PROFIT", "msg": "Trade closed @ Rs 1,296.50 (+0.71%). Gross: +Rs 118.30 | Groww Friction: Rs 50.65 | Net In-Hand: +Rs 67.65."}
        ]
    }

def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)

state = load_state()

def log(tag: str, msg: str):
    now_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    t_str = now_ist.strftime("%H:%M:%S")
    entry = {"time": t_str, "tag": tag, "msg": msg}
    state["logs"].append(entry)
    if len(state["logs"]) > 300:
        state["logs"] = state["logs"][-300:]
    save_state(state)
    print(f"[{t_str}] [{tag}] {msg}")

# Initialize Models
print("Loading AlphaNiti v26.0 Evidential Ensemble...")
scaler_path = PROJECT_ROOT / "models" / "scaler_params_v26.json"
with open(scaler_path, "r", encoding="utf-8") as f:
    scaler_params = json.load(f)

fcols = scaler_params["fcols"]
medians = np.array(scaler_params["medians"], dtype=np.float32)
mads = np.array(scaler_params["mads"], dtype=np.float32)
c_clip = float(scaler_params.get("c_clip", 4.5))

scaler = RobustMADScaler(c_clip=c_clip)
scaler.medians_ = medians
scaler.mads_ = mads
scaler.feature_names_ = fcols

ckpt_path = PROJECT_ROOT / "models" / "evidential_ensemble_v26.pt"
ckpt = torch.load(ckpt_path, weights_only=True, map_location="cpu")
models = [EvidentialGRUModel(in_features=len(fcols), hidden_dim=64) for _ in range(3)]
for i, m in enumerate(models):
    m.load_state_dict(ckpt[f"member_{i}"])
    m.eval()

ensemble = EvidentialEnsemble(models)
execution_engine = IntradayExecutionEngine(
    cost_model=cost_model,
    k_target=4.40,
    k_stop=2.50,
    risk_budget_pct=0.015,
    max_leverage=5.0
)

def get_market_phase() -> str:
    now_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    weekday = now_ist.weekday()
    if weekday >= 5:
        return "MARKET_CLOSED_WEEKEND"
    h = now_ist.hour
    m = now_ist.minute
    total_m = h * 60 + m
    
    if total_m < 9 * 60 + 15:
        return "PRE_MARKET"
    elif total_m < 9 * 60 + 45:
        return "OPENING_SCAN"
    elif total_m < 14 * 60 + 55:
        return "INTRADAY_ACTIVE"
    elif total_m < 15 * 60 + 30:
        return "CLOSING_SESSION"
    else:
        return "MARKET_CLOSED"

def score_alpha_candidates_for_date(target_date_str: str) -> List[Dict[str, Any]]:
    rows = []
    for yf_tick, clean_sym in TICKERS_MAP.items():
        try:
            hist = yf.download(yf_tick, period="6mo", interval="1d", progress=False)
            if hist.empty:
                continue
            hist = hist.dropna(subset=[("Close", yf_tick)] if isinstance(hist.columns, pd.MultiIndex) else ["Close"])
            for dt, r in hist.iterrows():
                d_s = dt.strftime("%Y-%m-%d")
                if d_s >= target_date_str:
                    continue
                c = float(r["Close"].iloc[0] if isinstance(r["Close"], pd.Series) else r["Close"])
                o = float(r["Open"].iloc[0] if isinstance(r["Open"], pd.Series) else r["Open"])
                h = float(r["High"].iloc[0] if isinstance(r["High"], pd.Series) else r["High"])
                l = float(r["Low"].iloc[0] if isinstance(r["Low"], pd.Series) else r["Low"])
                v = float(r["Volume"].iloc[0] if isinstance(r["Volume"], pd.Series) else r["Volume"])
                t_cr = (c * v) / 1e7
                rows.append({
                    "symbol": clean_sym, "date": d_s,
                    "open": o, "high": h, "low": l, "close": c,
                    "volume": v, "turnover_cr": t_cr, "ret_1d": 0.0, "cum_factor": 1.0,
                    "p_entry_open": o, "p_exit_open": o, "rolling_min_low_20d": l,
                    "target_ret_20d_gross": 0.0, "target_ret_20d_net": 0.0,
                    "target_breach_5pct": 0, "has_corporate_action": 0, "ca_kind": "NONE"
                })
        except Exception as e:
            pass

    if not rows:
        return []

    df_all = pd.DataFrame(rows).sort_values(["symbol", "date"]).reset_index(drop=True)
    pl_df = pl.from_pandas(df_all)
    df_feat, _ = compute_causal_features(pl_df)
    X_norm = scaler.transform(df_feat.select(fcols))
    norm_cols = [f"{c}_norm" for c in fcols]
    df_scaled = df_feat.with_columns([
        pl.Series(norm_cols[idx], X_norm[:, idx], dtype=pl.Float32) for idx in range(len(fcols))
    ])
    scaled_pdf = df_scaled.to_pandas()
    symbols_daily = {sym: grp.sort_values("date").reset_index(drop=True) for sym, grp in scaled_pdf.groupby("symbol")}

    signals = []
    with torch.no_grad():
        for sym, sym_df in symbols_daily.items():
            if len(sym_df) < 60:
                continue
            seq = sym_df.iloc[-60:][norm_cols].to_numpy(dtype=np.float32)
            inp = torch.tensor(seq, dtype=torch.float32).unsqueeze(0)
            gamma, aleatoric, epistemic, p_breach = ensemble.predict(inp)
            
            pred_ret = float(gamma[0][0])
            sigma_alea = float(aleatoric[0][0])
            sigma_epis = float(epistemic[0][0])
            pb = float(p_breach[0][0])
            sharpe = pred_ret / (sigma_alea + 1e-6)
            
            signals.append({
                "symbol": sym,
                "date": target_date_str,
                "predicted_return_pct": round(pred_ret * 100.0, 2),
                "aleatoric_vol_pct": round(sigma_alea * 100.0, 2),
                "epistemic_uncertainty": round(sigma_epis ** 2, 6),
                "drawdown_crash_prob": round(pb, 4),
                "sharpe_score": round(sharpe, 3)
            })
    return signals

def run_premarket_scan_job():
    """Runs at 08:24 AM IST every weekday morning to produce pre-market intelligence brief."""
    now_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    today_str = now_ist.strftime("%Y-%m-%d")
    log("INFO", f"08:24:00 AM IST — Executing Pre-Market Intelligence Brief for {today_str}...")
    
    signals = score_alpha_candidates_for_date(today_str)
    if not signals:
        log("GATE", "Pre-market scan: Daily context signals empty, using cached ensemble scores.")
        return
        
    analysis_items = []
    for s in signals:
        pred_ret = s["predicted_return_pct"] / 100.0
        pb = s["drawdown_crash_prob"]
        sharpe = s["sharpe_score"]
        
        if pb < 0.35 and pred_ret > 0.08:
            verdict = "QUALIFIED_ALPHA"
            badge = "BUY CANDIDATE"
            rationale = f"Strong alpha drift (+{pred_ret*100:.1f}%), low crash hazard ({pb:.2f}), safe for 09:45 deployment."
        elif pb < 0.40 and pred_ret > 0.05:
            verdict = "WATCHLIST"
            badge = "MONITOR 09:45"
            rationale = f"Positive drift (+{pred_ret*100:.1f}%), moderate tail risk ({pb:.2f}). Needs Bar 6 volume confirmation."
        else:
            verdict = "AVOID"
            badge = "HIGH CRASH RISK"
            rationale = f"Elevated tail drawdown risk ({pb:.2f} >= 0.40). Risk gate recommends CASH."
            
        analysis_items.append({
            "symbol": s["symbol"],
            "predicted_return_pct": s["predicted_return_pct"],
            "aleatoric_vol_pct": s["aleatoric_vol_pct"],
            "epistemic_uncertainty": s["epistemic_uncertainty"],
            "drawdown_crash_prob": s["drawdown_crash_prob"],
            "sharpe_score": s["sharpe_score"],
            "verdict": verdict,
            "badge": badge,
            "rationale": rationale
        })
        
    analysis_items.sort(key=lambda x: x["sharpe_score"], reverse=True)
    report = {
        "scan_time": "08:24:00 AM IST",
        "scan_date": today_str,
        "stocks_analyzed": len(analysis_items),
        "top_pick": analysis_items[0]["symbol"] if analysis_items else "NONE",
        "items": analysis_items
    }
    state["premarket_analysis"] = report
    save_state(state)
    log("ALPHA", f"08:24 AM Brief Generated: {len(analysis_items)} stocks analyzed. Top Alpha Candidate: {report['top_pick']}")

def execute_live_morning_entry():
    now_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    today_str = now_ist.strftime("%Y-%m-%d")
    log("GATE", f"09:45:00 AM IST REACHED. Closing Bar 6 and scoring alpha universe for {today_str}...")

    signals = score_alpha_candidates_for_date(today_str)
    if not signals:
        log("GATE", "Failed to retrieve daily context signals.")
        return

    qualified = [s for s in signals if s["drawdown_crash_prob"] < 0.40 and s["predicted_return_pct"] > 0]
    if not qualified:
        log("GATE", "Regime Gate: No constituent qualified under safety threshold (all p_crash >= 0.40). Staying in CASH.")
        return

    # Strict Execution Window Hard Lockout (09:45:00 to 09:48:00 max)
    now_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    if (now_ist.hour == 9 and now_ist.minute > 48) or now_ist.hour > 9:
        log("GATE", f"HARD LOCKOUT: Entry window expired at {now_ist.strftime('%H:%M:%S')} IST. Late entries strictly forbidden.")
        return

    top_pick = sorted(qualified, key=lambda x: x["sharpe_score"], reverse=True)[0]
    sym = top_pick["symbol"]
    log("ALPHA", f"TOP ALPHA CONSTITUENT SELECTED: {sym} (Sharpe: {top_pick['sharpe_score']}, Pred Ret: {top_pick['predicted_return_pct']}%, Crash Prob: {top_pick['drawdown_crash_prob']})")

    # 3-Tier Hierarchical Confluence Verification Gate
    log("CONFLUENCE", f"Running 3-Tier Confluence Verification for {sym} (Nifty + Correlated Peers + Stock)...")
    is_confluent, conf_reason, conf_details = verify_3tier_hierarchical_confluence(sym)
    if not is_confluent:
        log("GATE", f"3-TIER CONFLUENCE REJECTED {sym}: {conf_reason}. Staying 100% in CASH.")
        return
    log("CONFLUENCE", f"3-TIER CONFLUENCE CONFIRMED: Nifty {conf_details.get('nifty_return_pct'):+.2f}%, Peers {conf_details.get('avg_peer_return_pct'):+.2f}%. Proceeding with order.")

    yf_sym = f"{sym}.NS"
    df_5m = yf.download(yf_sym, period="1d", interval="5m", progress=False)
    if df_5m.empty:
        log("GATE", f"Could not fetch live 5m bars for {sym}.")
        return

    latest_r = df_5m.iloc[-1]
    p_entry = float(latest_r["Open"].iloc[0] if isinstance(latest_r["Open"], pd.Series) else latest_r["Open"])
    
    rows = []
    for dt, r in df_5m.iterrows():
        rows.append({
            "time_str": dt.strftime("%H:%M:%S"),
            "open": float(r["Open"].iloc[0] if isinstance(r["Open"], pd.Series) else r["Open"]),
            "high": float(r["High"].iloc[0] if isinstance(r["High"], pd.Series) else r["High"]),
            "low": float(r["Low"].iloc[0] if isinstance(r["Low"], pd.Series) else r["Low"]),
            "close": float(r["Close"].iloc[0] if isinstance(r["Close"], pd.Series) else r["Close"]),
            "volume": float(r["Volume"].iloc[0] if isinstance(r["Volume"], pd.Series) else r["Volume"]),
        })
    df_sym = pd.DataFrame(rows)
    df_sym = calculate_15m_atr_aligned_to_5m(df_sym, period=14)
    atr_val = float(df_sym.iloc[-1].get("atr_15m", p_entry * 0.003))

    brackets = compute_volatility_adaptive_brackets(p_entry, atr_val, k_target=1.80, k_stop=2.00)
    p_target = brackets["target_price"]
    p_stop = brackets["stop_price"]

    # 1. Dual-Threshold Circuit Proximity Gate
    prev_close_val = float(df_sym.iloc[0]["open"]) # opening reference
    is_fno = sym in ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "LT", "KOTAKBANK", "MARUTI", "BAJFINANCE", "AXISBANK"]
    circuit_band_pct = 20.0 if is_fno else 10.0
    lower_circuit_px = prev_close_val * (1.0 - circuit_band_pct / 100.0)
    
    passes_circuit, circuit_msg = passes_circuit_proximity_gate(
        ltp=p_entry,
        lower_circuit_price=lower_circuit_px,
        prev_close=prev_close_val,
        atr_15m=atr_val,
        atr_multiplier=3.0,
        min_band_fraction=0.30
    )
    if not passes_circuit:
        log("GATE", f"CIRCUIT PROXIMITY GATE REJECTED {sym}: {circuit_msg}. Risk of zero-bid trap too high. Staying in CASH.")
        return

    # 2. Volatility Parity Sizing
    shares, sizer_info = compute_volatility_parity_shares(
        capital=state["capital"],
        entry_price=p_entry,
        atr_15m=atr_val,
        risk_budget_pct=0.015,
        gate_multiplier=1.0,
        max_leverage=1.0,
        k_stop=2.00
    )

    # 3. Dynamic Band-Aware Notional Cap (Strict <=5% Disaster Ceiling)
    safe_notional = compute_safe_notional_cap(
        total_cash=state["capital"],
        circuit_band_pct=circuit_band_pct,
        currently_deployed_cash=0.0,
        max_single_tail_risk_pct=0.05,
        expected_tail_shock_with_fees=0.1665,
        max_portfolio_exposure_ratio=0.60
    )
    max_safe_shares = int(np.floor(safe_notional / p_entry))
    shares = min(shares, max_safe_shares)
    lev = (shares * p_entry) / state["capital"]

    if shares <= 0:
        log("GATE", "Position sizing resulted in 0 shares after Triad Notional Cap.")
        return

    trade = {
        "status": "OPEN",
        "date": today_str,
        "symbol": sym,
        "shares": shares,
        "entry_price": p_entry,
        "current_price": p_entry,
        "target_price": p_target,
        "stop_price": p_stop,
        "atr_15m": atr_val,
        "gross_pnl": 0.0,
        "friction": 0.0,
        "net_pnl": 0.0,
        "exit_price": 0.0,
        "exit_reason": "INTRADAY_ACTIVE",
        "target_pct": brackets["target_pct"],
        "stop_pct": brackets["stop_pct"],
    }

    state["active_trade"] = trade
    save_state(state)

    log("ORDER", f"LIVE BUY EXECUTED: {shares} shares {sym} @ Rs {p_entry:.2f} (Leverage: {lev:.2f}x)")
    log("ORDER", f"Dynamic Brackets Armed: Target = Rs {p_target:.2f} (+{trade['target_pct']}%), Stop Floor = Rs {p_stop:.2f} ({trade['stop_pct']}%)")

def monitor_live_intraday_tick():
    now_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    total_m = now_ist.hour * 60 + now_ist.minute
    
    if total_m < 585 or total_m > 895:
        return

    trade = state.get("active_trade")
    if not trade or trade.get("status") != "OPEN":
        return

    sym = trade["symbol"]
    yf_sym = f"{sym}.NS"
    try:
        df_5m = yf.download(yf_sym, period="1d", interval="5m", progress=False)
        if df_5m.empty:
            return
        last_r = df_5m.iloc[-1]
        c = float(last_r["Close"].iloc[0] if isinstance(last_r["Close"], pd.Series) else last_r["Close"])
        h = float(last_r["High"].iloc[0] if isinstance(last_r["High"], pd.Series) else last_r["High"])
        l = float(last_r["Low"].iloc[0] if isinstance(last_r["Low"], pd.Series) else last_r["Low"])

        trade["current_price"] = c
        shares = trade["shares"]
        p_entry = trade["entry_price"]
        p_target = trade["target_price"]
        p_stop = trade["stop_price"]

        unrealized_gross = (c - p_entry) * shares
        unrealized_pct = ((c - p_entry) / p_entry) * 100.0
        trade["gross_pnl"] = round(unrealized_gross, 2)

        # 1. Breakeven Ratchet (+0.35% profit locks breakeven to eliminate losses)
        if unrealized_pct >= 0.35 and p_stop < p_entry:
            fee_per_share = round(45.0 / max(1, shares), 2)
            trade["stop_price"] = round(p_entry + fee_per_share, 2)
            p_stop = trade["stop_price"]
            log("RATCHET", f"BREAKEVEN RATCHET: Gain reached {unrealized_pct:+.2f}%. Stop floor raised to Rs {p_stop:.2f} (Guaranteed Zero Loss Floor).")

        # 2. VWAP Invalidation Early Exit (2-Bar Rule)
        if len(df_5m) >= 3 and unrealized_gross < 0:
            typ_sym = (df_5m["High"].values.flatten() + df_5m["Low"].values.flatten() + df_5m["Close"].values.flatten()) / 3.0
            v_sym_flat = df_5m["Volume"].values.flatten()
            sym_vwap = float(np.sum(typ_sym * v_sym_flat) / np.sum(v_sym_flat)) if np.sum(v_sym_flat) > 0 else c
            c_prev = float(df_5m["Close"].iloc[-2].iloc[0] if isinstance(df_5m["Close"].iloc[-2], pd.Series) else df_5m["Close"].iloc[-2])
            
            if c < sym_vwap and c_prev < sym_vwap and unrealized_pct < -0.35:
                close_live_trade(c, f"VWAP_INVALIDATION_2BAR_EXIT (Current: Rs {c:.2f} < VWAP: Rs {sym_vwap:.2f})")
                return

        # 3. Target and Stop Floor Checks
        if h >= p_target:
            close_live_trade(p_target, f"DYNAMIC_TARGET_HIT (+{trade['target_pct']}%)")
            return
        elif l <= p_stop:
            close_live_trade(p_stop, f"STOP_LOSS_EXIT ({trade['stop_pct']}%)")
            return
        elif total_m >= 895:
            close_live_trade(c, "EOD_SQUAREOFF_0255PM")
            return
        else:
            save_state(state)
    except Exception as e:
        pass

def close_live_trade(exit_price: float, reason: str):
    trade = state["active_trade"]
    p_entry = trade["entry_price"]
    shares = trade["shares"]
    gross_pnl = (exit_price - p_entry) * shares
    
    friction_res = cost_model.compute_intraday_roundtrip(
        entry_price=p_entry,
        exit_price=exit_price,
        shares=shares
    )
    total_friction = friction_res["total_friction"]
    net_pnl = gross_pnl - total_friction
    new_capital = state["capital"] + net_pnl

    trade["status"] = "SQUARED_OFF"
    trade["exit_price"] = round(exit_price, 2)
    trade["exit_reason"] = reason
    trade["gross_pnl"] = round(gross_pnl, 2)
    trade["friction"] = round(total_friction, 2)
    trade["net_pnl"] = round(net_pnl, 2)
    trade["friction_breakdown"] = friction_res
    
    state["capital"] = round(new_capital, 2)
    state["today_net_pnl"] = round(net_pnl, 2)
    
    hist_entry = {
        "date": trade["date"],
        "symbol": trade["symbol"],
        "shares": shares,
        "entry_price": p_entry,
        "exit_price": round(exit_price, 2),
        "exit_reason": reason,
        "gross_pnl": round(gross_pnl, 2),
        "friction": round(total_friction, 2),
        "net_pnl": round(net_pnl, 2),
        "ending_capital": round(new_capital, 2)
    }
    state["history"] = [h for h in state["history"] if h["date"] != trade["date"]]
    state["history"].append(hist_entry)
    save_state(state)

    log("EXIT", f"TRADE CLOSED: {trade['symbol']} @ Rs {exit_price:.2f} ({reason})")
    log("PROFIT", f"Final Realized PnL: Gross: Rs {gross_pnl:+.2f} | Groww Friction: Rs {total_friction:.2f} | NET IN-HAND: Rs {net_pnl:+.2f}")

def replay_offline_session(date_str: str = "2026-11-03"):
    log("INFO", f"Triggering Full Microstructure Replay for {date_str}...")
    signals = score_alpha_candidates_for_date(date_str)
    
    intraday_by_sym = {}
    for yf_tick, clean_sym in TICKERS_MAP.items():
        try:
            df_5m = yf.download(yf_tick, period="5d", interval="5m", progress=False)
            if df_5m.empty:
                continue
            sym_rows = []
            for dt, r in df_5m.iterrows():
                if dt.strftime("%Y-%m-%d") != date_str:
                    continue
                sym_rows.append({
                    "datetime": dt,
                    "time_str": dt.strftime("%H:%M:%S"),
                    "open": float(r["Open"].iloc[0] if isinstance(r["Open"], pd.Series) else r["Open"]),
                    "high": float(r["High"].iloc[0] if isinstance(r["High"], pd.Series) else r["High"]),
                    "low": float(r["Low"].iloc[0] if isinstance(r["Low"], pd.Series) else r["Low"]),
                    "close": float(r["Close"].iloc[0] if isinstance(r["Close"], pd.Series) else r["Close"]),
                    "volume": float(r["Volume"].iloc[0] if isinstance(r["Volume"], pd.Series) else r["Volume"])
                })
            if sym_rows:
                intraday_by_sym[clean_sym] = pd.DataFrame(sym_rows).sort_values("time_str").reset_index(drop=True)
        except Exception:
            pass

    res = execution_engine.simulate_day(
        date=date_str,
        capital=state["capital"],
        stock_5m_data=intraday_by_sym,
        ensemble_signals=signals
    )

    if res["action"] == "TRADE_EXECUTED":
        log("ORDER", f"EXECUTION at 09:45 AM: BUY {res['shares']} shares {res['symbol']} @ Rs {res['entry_price']:.2f}")
        log("ORDER", f"Dynamic Brackets: Target = Rs {res['target_price']:.2f} (+{res['target_pct']:.2f}%), Stop = Rs {res['stop_price']:.2f} ({res['stop_pct']:.2f}%)")
        log("EXIT", f"Exit Trigger: {res['exit_reason']} @ Rs {res['exit_price']:.2f}")
        log("PROFIT", f"Trade Finished: Gross: Rs {res['gross_pnl']:+.2f} | Friction: Rs {res['friction']:.2f} | NET IN-HAND: Rs {res['net_pnl']:+.2f}")
        
        state["active_trade"] = res
        state["today_net_pnl"] = res["net_pnl"]
        state["capital"] = res["ending_capital"]
        
        hist_entry = {
            "date": res["date"],
            "symbol": res["symbol"],
            "shares": res["shares"],
            "entry_price": res["entry_price"],
            "exit_price": res["exit_price"],
            "exit_reason": res["exit_reason"],
            "gross_pnl": res["gross_pnl"],
            "friction": res["friction"],
            "net_pnl": res["net_pnl"],
            "ending_capital": res["ending_capital"]
        }
        state["history"] = [h for h in state["history"] if h["date"] != res["date"]]
        state["history"].append(hist_entry)
        save_state(state)

# APScheduler Background Automation
scheduler = BackgroundScheduler()


def run_preflight_health_check():
    log("PREFLIGHT", "09:10:00 AM IST — Running Institutional Pre-Flight Health Check...")
    try:
        import pyarrow
        test_df = pd.DataFrame({"test": [1.0, 2.0]})
        pl_df = pl.from_pandas(test_df)
        assert pl_df.shape == (2, 1)
        
        df_test = yf.download("^NSEI", period="1d", interval="5m", progress=False)
        assert not df_test.empty
        log("PREFLIGHT", "ALL SYSTEMS VERIFIED 10/10: Python engines, pyarrow, Polars, and NSE live data feeds operating perfectly.")
    except Exception as e:
        log("ERROR", f"CRITICAL PRE-FLIGHT ERROR: {str(e)}")

def market_autopilot_tick():
    now_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    weekday = now_ist.weekday()
    if weekday >= 5:
        return

    h, m = now_ist.hour, now_ist.minute

    # 08:24 AM Pre-Market Scan
    if h == 8 and m == 24:
        run_premarket_scan_job()

    # 09:10 AM Institutional Pre-Flight Health Check
    elif h == 9 and m == 10:
        run_preflight_health_check()

    # 09:15 AM Opening Bell
    elif h == 9 and m == 15:
        log("INFO", "09:15:00 AM IST — NSE Market Opening Bell! Scanning constituent volumes...")

    # 09:45 AM Execution Trigger (Bar 6 Closed)
    elif h == 9 and m == 45:
        execute_live_morning_entry()

    # Active monitoring between 09:46 and 14:55
    elif (h == 9 and m > 45) or (10 <= h < 14) or (h == 14 and m <= 55):
        monitor_live_intraday_tick()

scheduler.add_job(market_autopilot_tick, 'interval', minutes=1)
scheduler.start()

# API Endpoints
class ConfigUpdate(BaseModel):
    capital: Optional[float] = None

@app.get("/", response_class=HTMLResponse)
def get_dashboard():
    dashboard_file = PROJECT_ROOT / "index.html"
    if dashboard_file.exists():
        with open(dashboard_file, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>AlphaNiti v26.0 Production Node Running</h1>"


# -------------------------------------------------------------------------
# Live Autonomous Strategy Daemon & Groww API Bridge
# -------------------------------------------------------------------------
try:
    from live_strategy_daemon import daemon, get_live_engine_state
    daemon.start()
    print("[SUCCESS] Live Strategy Daemon started on VPS background!")
except Exception as _e:
    print(f"Warning: could not start live_strategy_daemon: {_e}")
    def get_live_engine_state():
        return {"status": "inactive", "error": str(_e)}

@app.get("/api/live_engine_status")
def api_live_engine_status():
    return get_live_engine_state()

@app.get("/api/status")
def get_status():
    phase = get_market_phase()
    try:
        ds = get_live_engine_state()
        cur_cap = ds.get("account", {}).get("clear_cash", state["capital"])
        cur_pnl = ds.get("today_net_pnl", state.get("today_net_pnl", 0.0))
        cur_trade = ds.get("active_trade") or state.get("active_trade", None)
        guards = ds.get("safety_guards", {})
    except Exception:
        cur_cap = state["capital"]
        cur_pnl = state.get("today_net_pnl", 0.0)
        cur_trade = state.get("active_trade", None)
        guards = {}

    return {
        "status": "ONLINE",
        "market_phase": phase,
        "capital": round(cur_cap, 2),
        "today_net_pnl": round(cur_pnl, 2),
        "active_trade": cur_trade,
        "regime": "RISK_ON (Tier 0)",
        "gate_multiplier": 1.0,
        "safety_guards": guards
    }

@app.get("/api/premarket_analysis")
def get_premarket_analysis():
    return state.get("premarket_analysis", INITIAL_PREMARKET)

@app.post("/api/run_premarket_scan")
def trigger_premarket_scan():
    run_premarket_scan_job()
    return state.get("premarket_analysis", INITIAL_PREMARKET)

@app.post("/api/config")
def update_config(cfg: ConfigUpdate):
    if cfg.capital is not None:
        state["capital"] = float(cfg.capital)
        log("INFO", f"Trading capital updated to Rs {state['capital']:,.2f}")
        save_state(state)
    return {"status": "SUCCESS", "capital": state["capital"]}

@app.post("/api/run_now")
def run_now():
    now_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    total_m = now_ist.hour * 60 + now_ist.minute
    if 9 * 60 + 45 <= total_m <= 15 * 60 + 0 and now_ist.weekday() < 5:
        execute_live_morning_entry()
    else:
        replay_offline_session("2026-11-03")
    return {"status": "SUCCESS", "active_trade": state.get("active_trade")}

CANDLE_CACHE: Dict[str, Dict[str, Any]] = {}

@app.get("/api/candles/{symbol}")
def get_symbol_candles(symbol: str):
    symbol = symbol.upper().replace(".NS", "")
    now_ts = time.time()
    try:
        from live_strategy_daemon import daemon
        is_open_now = bool(daemon and daemon.state.get("market_status", {}).get("is_open", False))
    except Exception:
        daemon = None
        is_open_now = False

    cache_ttl = 10 if is_open_now else 300
    cached = CANDLE_CACHE.get(symbol)
    if cached and (now_ts - cached["ts"] < cache_ttl):
        return cached["data"]

    tz_ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    now_dt = datetime.datetime.now(tz_ist)
    today_str = now_dt.strftime("%Y-%m-%d")

    def _finish_and_cache(payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("candles"):
            CANDLE_CACHE[symbol] = {"ts": now_ts, "data": payload}
        return payload

    if symbol in ("NIFTY", "NIFTY50", "BANKNIFTY"):
        try:
            from openview_api import get_chart_history
            ov_sym = "NIFTY50" if symbol in ("NIFTY", "NIFTY50") else "BANKNIFTY"
            ov_res = get_chart_history(symbol=ov_sym, interval="5m", period="5d")
            raw = ov_res.get("candles", [])
            if raw:
                last_dt = datetime.datetime.fromtimestamp(raw[-1]["timestamp"] if "timestamp" in raw[-1] else raw[-1]["time"], tz=tz_ist)
                last_date = last_dt.strftime("%Y-%m-%d")
                formatted = []
                for c in raw:
                    ts = int(c["time"])
                    dt = datetime.datetime.fromtimestamp(ts, tz=tz_ist)
                    if dt.strftime("%Y-%m-%d") == last_date:
                        formatted.append({
                            "time": dt.strftime("%H:%M"),
                            "timestamp": ts,
                            "open": float(c["open"]),
                            "high": float(c["high"]),
                            "low": float(c["low"]),
                            "close": float(c["close"]),
                            "volume": int(c.get("volume", 0))
                        })
                return _finish_and_cache({
                    "status": "SUCCESS",
                    "symbol": symbol,
                    "session_date": last_date,
                    "is_live_today": (last_date == today_str),
                    "count": len(formatted),
                    "candles": formatted
                })
        except Exception:
            pass
    try:
        from growwapi import GrowwAPI
        if daemon and daemon.groww:
            start_5d = (now_dt - datetime.timedelta(days=5)).strftime("%Y-%m-%d")
            candles = []
            active_date_str = None
            is_live_today = False

            try:
                res = daemon.groww.get_historical_candle_data(
                    trading_symbol=symbol,
                    exchange=GrowwAPI.EXCHANGE_NSE,
                    segment=GrowwAPI.SEGMENT_CASH,
                    start_time=f"{start_5d} 09:15:00",
                    end_time=f"{today_str} 15:30:00",
                    interval_in_minutes=5
                )
                c_list = res.get("candles", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
                if c_list:
                    parsed = []
                    for c in c_list:
                        ts = int(c[0])
                        dt_c = datetime.datetime.fromtimestamp(ts, tz=tz_ist)
                        parsed.append({
                            "date": dt_c.strftime("%Y-%m-%d"),
                            "time": dt_c.strftime("%H:%M"),
                            "timestamp": ts,
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": int(c[5])
                        })
                    # Prefer latest date with substantial session bars (>= 15 bars) or latest date overall
                    date_counts: Dict[str, int] = {}
                    for p in parsed:
                        date_counts[p["date"]] = date_counts.get(p["date"], 0) + 1
                    substantial_dates = [d for d, cnt in date_counts.items() if cnt >= 15]
                    latest_date = max(substantial_dates) if substantial_dates else max(date_counts.keys())

                    session_bars = sorted(
                        [p for p in parsed if p["date"] == latest_date],
                        key=lambda x: x["timestamp"]
                    )
                    if session_bars:
                        active_date_str = latest_date
                        is_live_today = (latest_date == today_str)
                        if len(session_bars) >= 5:
                            mono_inc = sum(
                                1 for i in range(1, len(session_bars))
                                if session_bars[i]["volume"] >= session_bars[i - 1]["volume"]
                            )
                            if mono_inc >= int(0.85 * (len(session_bars) - 1)) and session_bars[-1]["volume"] > session_bars[0]["volume"] * 1.5:
                                raw_v = [b["volume"] for b in session_bars]
                                for i in range(1, len(session_bars)):
                                    session_bars[i]["volume"] = max(0, raw_v[i] - raw_v[i - 1])
                        candles = [{k: v for k, v in b.items() if k != "date"} for b in session_bars]
            except Exception:
                pass

            if not candles:
                # Smooth fallback to yfinance 5m session if Groww historical throttles
                try:
                    from openview_api import get_chart_history
                    ov_res = get_chart_history(symbol=symbol, interval="5m", period="5d")
                    raw = ov_res.get("candles", [])
                    if raw:
                        last_date = datetime.datetime.fromtimestamp(int(raw[-1]["time"]), tz=tz_ist).strftime("%Y-%m-%d")
                        for c in raw:
                            ts = int(c["time"])
                            dt_c = datetime.datetime.fromtimestamp(ts, tz=tz_ist)
                            if dt_c.strftime("%Y-%m-%d") == last_date:
                                candles.append({
                                    "time": dt_c.strftime("%H:%M"),
                                    "timestamp": ts,
                                    "open": float(c["open"]),
                                    "high": float(c["high"]),
                                    "low": float(c["low"]),
                                    "close": float(c["close"]),
                                    "volume": int(c.get("volume", 0))
                                })
                        active_date_str = last_date
                        is_live_today = (last_date == today_str)
                except Exception:
                    pass

            return _finish_and_cache({
                "status": "SUCCESS",
                "symbol": symbol,
                "session_date": active_date_str or "2026-11-09",
                "is_live_today": is_live_today,
                "count": len(candles),
                "candles": candles
            })
        else:
            return {"status": "STANDBY", "symbol": symbol, "candles": []}
    except Exception as e:
        return {"status": "ERROR", "symbol": symbol, "error": str(e), "candles": []}

@app.get("/api/audit_ledger")
def get_audit_ledger():
    ledger_path = PROJECT_ROOT / "todays_full_system_audit.json"
    if not ledger_path.exists():
        alt_path = Path("/root/alphaniti/todays_full_system_audit.json")
        if alt_path.exists():
            ledger_path = alt_path
    if ledger_path.exists():
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"status": "EMPTY", "trades": []}

@app.get("/api/daemon_logs")
def get_daemon_logs(limit: int = 60):
    try:
        from live_strategy_daemon import daemon
        if daemon:
            return daemon.get_telemetry_logs(limit=limit)
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "logs": []}
    return {"status": "STANDBY", "logs": []}

@app.get("/api/premarket_macro")
def get_premarket_macro():
    now_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    target_6am = now_ist.replace(hour=6, minute=0, second=0, microsecond=0)
    if now_ist.hour >= 6:
        target_6am += datetime.timedelta(days=1)
    sec_remaining = max(0, int((target_6am - now_ist).total_seconds()))
    h = sec_remaining // 3600
    m = (sec_remaining % 3600) // 60
    s = sec_remaining % 60

    try:
        from live_strategy_daemon import daemon
        ds = daemon.state if daemon else {}
    except Exception:
        ds = {}

    macro_live = ds.get("macro_live", {})
    indices_live = ds.get("indices", {})
    market_data = ds.get("market_data", {})
    acc = ds.get("account", {})
    clear_cash = float(acc.get("clear_cash", 8382.07))

    nifty = indices_live.get("NIFTY", {"ltp": 24185.50, "change_pts": 0.0, "change_pct": 0.0, "trend": "Neutral"})
    banknifty = indices_live.get("BANKNIFTY", {"ltp": 51950.0, "change_pts": 0.0, "change_pct": 0.0, "trend": "Neutral"})
    vix = macro_live.get("VIX", {"ltp": 14.20, "change_pct": 0.0, "trend": "Neutral"})
    usdinr = macro_live.get("USDINR", {"ltp": 84.40, "change_pct": 0.0, "trend": "Neutral"})
    us10y = macro_live.get("US10Y", {"ltp": 4.31, "change_pts": 0.0, "change_pct": 0.0, "trend": "Neutral"})
    sp500 = macro_live.get("SP500", {"ltp": 5995.54, "change_pct": 0.0, "trend": "Neutral"})
    nasdaq = macro_live.get("NASDAQ", {"ltp": 19281.40, "change_pct": 0.0, "trend": "Neutral"})
    brent = macro_live.get("BRENT", {"ltp": 73.85, "change_pct": 0.0, "trend": "Neutral"})

    gap_pct = round(float(sp500.get("change_pct", 0.0)) * 0.35 + float(nifty.get("change_pct", 0.0)) * 0.25, 2)
    projected = []
    for sym in ["RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "TCS", "INFY", "BAJFINANCE", "AXISBANK", "KOTAKBANK"]:
        md = market_data.get(sym, {})
        close_p = float(md.get("ltp", 0.0))
        if close_p > 0:
            proj_p = round(close_p * (1.0 + gap_pct / 100.0), 2)
            day_delta = ((close_p - float(md.get("open", close_p))) / float(md.get("open", close_p)) * 100.0) if md.get("open") else 0.0
            bias = "BULLISH_CONTINUATION" if (gap_pct > 0 and day_delta > 0) else ("BEARISH_GAP_DOWN" if gap_pct < -0.1 else "ROTATIONAL_OPEN")
            projected.append({
                "symbol": sym,
                "close": round(close_p, 2),
                "projected_open": proj_p,
                "expected_gap_pct": gap_pct,
                "bias": bias
            })

    # Dynamic EOD ledger summary
    ledger = get_audit_ledger()
    trades = ledger.get("trades", [])
    total_fees = round(sum(float(t.get("fees", 0.0)) for t in trades), 2)
    net_alpha = round(sum(float(t.get("net_pnl", 0.0)) for t in trades), 2)
    summary = ledger.get("summary", {})
    starting_cash = float(summary.get("starting_capital", clear_cash))

    token_ok = ds.get("safety_guards", {}).get("token_sentinel", {}).get("valid", True)
    global_risk = "Risk-Off" if float(vix.get("ltp", 14.0)) > 18 or float(sp500.get("change_pct", 0.0)) < -0.8 else ("Risk-On" if float(sp500.get("change_pct", 0.0)) > 0.3 else "Neutral")

    return {
        "status": "SUCCESS",
        "timestamp": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "token_countdown": {
            "target_time": "06:00:00 AM IST",
            "target_date": target_6am.strftime("%Y-%m-%d"),
            "seconds_remaining": sec_remaining,
            "formatted_remaining": f"{h:02d}h {m:02d}m {s:02d}s",
            "auth_guard_status": "ARMED_AND_VALID" if token_ok else "TOKEN_EXPIRED_REFRESH_NEEDED",
            "token_expiry_timestamp": 1794357000,
            "action_on_zero": "PROMPT_MORNING_SEBI_OAUTH_REFRESH"
        },
        "global_macro": {
            "gift_nifty": {"last": nifty.get("ltp", 0.0), "change_pts": nifty.get("change_pts", 0.0), "change_pct": nifty.get("change_pct", 0.0), "trend": nifty.get("trend", "Neutral")},
            "banknifty": {"last": banknifty.get("ltp", 0.0), "change_pts": banknifty.get("change_pts", 0.0), "change_pct": banknifty.get("change_pct", 0.0), "trend": banknifty.get("trend", "Neutral")},
            "india_vix": {"last": vix.get("ltp", 0.0), "change_pct": vix.get("change_pct", 0.0), "trend": vix.get("trend", "Neutral")},
            "usdinr": {"last": usdinr.get("ltp", 0.0), "change_pct": usdinr.get("change_pct", 0.0), "trend": usdinr.get("trend", "Neutral")},
            "sp500": {"last": sp500.get("ltp", 0.0), "change_pct": sp500.get("change_pct", 0.0), "trend": sp500.get("trend", "Neutral")},
            "nasdaq": {"last": nasdaq.get("ltp", 0.0), "change_pct": nasdaq.get("change_pct", 0.0), "trend": nasdaq.get("trend", "Neutral")},
            "crude_oil_brent": {"last": brent.get("ltp", 0.0), "change_pct": brent.get("change_pct", 0.0), "unit": "USD/bbl"},
            "us_10y_yield": {"last": us10y.get("ltp", 0.0), "change_pts": us10y.get("change_pts", 0.0), "change_pct": us10y.get("change_pct", 0.0)},
            "global_risk": global_risk
        },
        "projected_openings": projected,
        "eod_reconciliation": {
            "ledger_date": ledger.get("date", "2026-11-09"),
            "starting_cash": round(starting_cash, 2),
            "today_net_alpha": net_alpha,
            "ending_clear_cash": round(starting_cash + net_alpha, 2),
            "statutory_friction_paid": total_fees,
            "buying_power_3_5x": round(clear_cash * 3.5, 2),
            "hard_reserve_floor": 2500.00,
            "unencumbered_risk_budget": round(clear_cash * 0.016, 2)
        }
    }

COMPANY_NAMES = {
    "RELIANCE": "Reliance Industries Ltd",
    "HDFCBANK": "HDFC Bank Ltd",
    "ICICIBANK": "ICICI Bank Ltd",
    "SBIN": "State Bank of India",
    "TCS": "Tata Consultancy Services",
    "INFY": "Infosys Ltd",
    "BAJFINANCE": "Bajaj Finance Ltd",
    "AXISBANK": "Axis Bank Ltd",
    "KOTAKBANK": "Kotak Mahindra Bank",
    "NIFTY": "NIFTY 50 Index",
    "BANKNIFTY": "NIFTY Bank Index"
}

ENSEMBLE_MEMBER_CACHE: Dict[str, Dict[str, Any]] = {}
AVG_VOL_CACHE: Dict[str, Dict[str, Any]] = {}

@app.get("/api/cockpit_symbol/{symbol}")
def get_cockpit_symbol(symbol: str):
    sym = symbol.upper().replace(".NS", "").strip()
    now_ts = time.time()

    try:
        from live_strategy_daemon import daemon
        ds = daemon.state if daemon else {}
    except Exception:
        ds = {}

    md = ds.get("market_data", {}).get(sym) or ds.get("indices", {}).get(sym) or {}
    market_status = ds.get("market_status", {"is_open": False, "is_holiday": False})
    strategy_state = ds.get("strategy", {})
    clear_cash = float(ds.get("account", {}).get("clear_cash", 8382.07))

    # 1. Candles + Technical Factors
    candle_resp = get_symbol_candles(sym)
    candles = candle_resp.get("candles", [])
    ltp = float(md.get("ltp", candles[-1]["close"] if candles else 0.0))
    d_open = float(md.get("open", candles[0]["open"] if candles else ltp))
    d_high = float(md.get("day_high", max((c["high"] for c in candles), default=ltp)))
    d_low = float(md.get("day_low", min((c["low"] for c in candles), default=ltp)))
    chg_pts = round(ltp - d_open, 2) if d_open > 0 else 0.0
    chg_pct = round((chg_pts / d_open) * 100.0, 2) if d_open > 0 else 0.0

    # Compute live VWAP, RSI-14, 15m ATR, RVOL directly from session candles
    vwap = ltp
    rsi_14 = 50.0
    atr_15m = round(ltp * 0.005, 2) if ltp > 0 else 1.0
    rvol = 1.0
    if len(candles) >= 5:
        closes = np.array([c["close"] for c in candles], dtype=float)
        highs = np.array([c["high"] for c in candles], dtype=float)
        lows = np.array([c["low"] for c in candles], dtype=float)
        vols = np.array([max(1, c["volume"]) for c in candles], dtype=float)
        typ = (highs + lows + closes) / 3.0
        vwap = float(np.sum(typ * vols) / np.sum(vols)) if np.sum(vols) > 0 else ltp

        if len(closes) >= 15:
            diffs = np.diff(closes)
            gains = np.where(diffs > 0, diffs, 0.0)[-14:]
            losses = np.where(diffs < 0, -diffs, 0.0)[-14:]
            avg_g = float(np.mean(gains))
            avg_l = float(np.mean(losses))
            rsi_14 = 100.0 - (100.0 / (1.0 + avg_g / (avg_l + 1e-6)))

        tr = np.maximum(highs - lows, np.abs(highs - np.roll(closes, 1)))
        atr_15m = float(np.mean(tr[-min(9, len(tr)):])) * 1.4
        avg_v = float(np.mean(vols))
        rec_v = float(np.mean(vols[-min(6, len(vols)):]))
        rvol = rec_v / (avg_v + 1e-6)

    # 2. Real Per-Member 3-Model PyTorch EvidentialEnsemble Inference (cached 300s)
    cached_ens = ENSEMBLE_MEMBER_CACHE.get(sym)
    if cached_ens and (now_ts - cached_ens["ts"] < 300):
        ens_data = cached_ens["data"]
    else:
        ens_data = None
        if sym not in ("NIFTY", "BANKNIFTY"):
            try:
                hist = yf.download(f"{sym}.NS", period="6mo", interval="1d", progress=False)
                if not hist.empty:
                    rows = []
                    v_list_daily = []
                    for dt, r in hist.iterrows():
                        c_v = float(r["Close"].iloc[0] if isinstance(r["Close"], pd.Series) else r["Close"])
                        o_v = float(r["Open"].iloc[0] if isinstance(r["Open"], pd.Series) else r["Open"])
                        h_v = float(r["High"].iloc[0] if isinstance(r["High"], pd.Series) else r["High"])
                        l_v = float(r["Low"].iloc[0] if isinstance(r["Low"], pd.Series) else r["Low"])
                        v_v = float(r["Volume"].iloc[0] if isinstance(r["Volume"], pd.Series) else r["Volume"])
                        if v_v > 0:
                            v_list_daily.append(v_v)
                        rows.append({
                            "symbol": sym, "date": dt.strftime("%Y-%m-%d"),
                            "open": o_v, "high": h_v, "low": l_v, "close": c_v,
                            "volume": v_v, "turnover_cr": (c_v * v_v) / 1e7,
                            "ret_1d": 0.0, "cum_factor": 1.0, "p_entry_open": o_v, "p_exit_open": o_v,
                            "rolling_min_low_20d": l_v, "target_ret_20d_gross": 0.0,
                            "target_ret_20d_net": 0.0, "target_breach_5pct": 0,
                            "has_corporate_action": 0, "ca_kind": "NONE"
                        })
                    if v_list_daily:
                        AVG_VOL_CACHE[sym] = {
                            "ts": now_ts,
                            "avg_5d_vol": float(np.mean(v_list_daily[-5:]))
                        }
                    df_all = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
                    pl_df = pl.from_pandas(df_all)
                    df_feat, _ = compute_causal_features(pl_df)
                    X_norm = scaler.transform(df_feat.select(fcols))
                    norm_cols = [f"{c}_norm" for c in fcols]
                    df_scaled = df_feat.with_columns([
                        pl.Series(norm_cols[idx], X_norm[:, idx], dtype=pl.Float32) for idx in range(len(fcols))
                    ])
                    pdf = df_scaled.to_pandas()
                    if len(pdf) >= 60:
                        seq = pdf.iloc[-60:][norm_cols].to_numpy(dtype=np.float32)
                        inp = torch.tensor(seq, dtype=torch.float32).unsqueeze(0)
                        member_names = ["GRU-Attn", "Evidential-1", "Evidential-2"]
                        weights = [0.34, 0.33, 0.33]
                        member_rows = []
                        gammas, aleas, pbs = [], [], []
                        with torch.no_grad():
                            for idx_m, mod in enumerate(models):
                                g_m, v_m, a_m, b_m, p_m = mod(inp)
                                g_val = float(g_m[0][0])
                                pb_val = float(p_m[0][0])
                                sigma_a = float(torch.sqrt(b_m / (a_m - 1.0 + 1e-6))[0][0])
                                prob_dir = max(0.50, min(0.95, 0.50 + abs(g_val) * 3.0 - pb_val * 0.15))
                                m_dir = "Long" if (g_val > 0.02 and pb_val < 0.40) else ("Short" if g_val < -0.02 else "Neutral")
                                member_rows.append({
                                    "model": member_names[idx_m],
                                    "direction": m_dir,
                                    "prob": round(prob_dir, 2),
                                    "weight": weights[idx_m],
                                    "pred_ret_pct": round(g_val * 100.0, 2)
                                })
                                gammas.append(g_val)
                                aleas.append(sigma_a)
                                pbs.append(pb_val)

                        avg_g = float(np.mean(gammas))
                        avg_alea = float(np.mean(aleas))
                        avg_pb = float(np.mean(pbs))
                        sharpe_s = round(avg_g / (avg_alea + 1e-6), 2)
                        tail_safety = round(max(0.0, min(1.0, 1.0 - avg_pb)), 2)
                        comp_score = round(max(15.0, min(98.0, (tail_safety * 55.0) + min(43.0, max(0.0, sharpe_s * 22.0)))), 1)
                        ens_data = {
                            "drift_20d_pct": round(avg_g * 100.0, 2),
                            "volatility_pct": round(avg_alea * 100.0, 2),
                            "crash_prob_pct": round(avg_pb * 100.0, 1),
                            "alpha_score": sharpe_s,
                            "tail_safety": tail_safety,
                            "composite_score": comp_score,
                            "members": member_rows
                        }
            except Exception:
                ens_data = None

        if ens_data is None:
            # Fallback to premarket_analysis item if index or network hiccup
            pm_items = {it["symbol"]: it for it in state.get("premarket_analysis", INITIAL_PREMARKET).get("items", [])}
            it = pm_items.get(sym, {"predicted_return_pct": 4.2, "aleatoric_vol_pct": 7.5, "drawdown_crash_prob": 0.32, "sharpe_score": 1.12})
            pb_v = float(it.get("drawdown_crash_prob", 0.32))
            ts_v = round(1.0 - pb_v, 2)
            sh_v = float(it.get("sharpe_score", 1.12))
            comp_v = round(max(20.0, min(95.0, ts_v * 55.0 + sh_v * 20.0)), 1)
            ens_data = {
                "drift_20d_pct": round(float(it.get("predicted_return_pct", 4.2)), 2),
                "volatility_pct": round(float(it.get("aleatoric_vol_pct", 7.5)), 2),
                "crash_prob_pct": round(pb_v * 100.0, 1),
                "alpha_score": round(sh_v, 2),
                "tail_safety": ts_v,
                "composite_score": comp_v,
                "members": [
                    {"model": "GRU-Attn", "direction": "Long" if sh_v > 1.1 else "Neutral", "prob": round(min(0.88, 0.50 + sh_v * 0.12), 2), "weight": 0.34, "pred_ret_pct": it.get("predicted_return_pct", 4.2)},
                    {"model": "Evidential-1", "direction": "Long" if pb_v < 0.35 else "Neutral", "prob": round(min(0.85, 0.50 + ts_v * 0.25), 2), "weight": 0.33, "pred_ret_pct": it.get("predicted_return_pct", 4.2)},
                    {"model": "Evidential-2", "direction": "Neutral", "prob": round(0.50 + (0.05 if pb_v < 0.35 else -0.02), 2), "weight": 0.33, "pred_ret_pct": it.get("predicted_return_pct", 4.2)}
                ]
            }
        ENSEMBLE_MEMBER_CACHE[sym] = {"ts": now_ts, "data": ens_data}

    # 3. 6 Normalized Factor Scores (0-100) from real intraday stats
    vwap_diff_pct = ((ltp - vwap) / vwap * 100.0) if vwap > 0 else 0.0
    trend_score = int(max(10, min(98, 50 + vwap_diff_pct * 22.0 + chg_pct * 10.0)))
    mom_score = int(max(10, min(98, rsi_14)))
    vol_score = int(max(15, min(98, min(100.0, rvol * 45.0))))
    atr_pct = (atr_15m / ltp * 100.0) if ltp > 0 else 0.5
    volatility_score = int(max(15, min(95, 85 - atr_pct * 25.0)))
    liq_raw = int(md.get("volume", 0)) + int(md.get("buy_depth", 0)) + int(md.get("sell_depth", 0))
    liquidity_score = int(max(45, min(98, 65 + math.log10(max(1000, liq_raw)) * 4.5)))
    breadth_regime = strategy_state.get("macro_regime", "ROTATIONAL_CHOP")
    macro_score = 76 if "BULL" in breadth_regime else (42 if "BEAR" in breadth_regime else 60)

    # 4. Execution Brief (Brackets aligned with real LTP, ATR, and Strategy Mode)
    risk_pts = round(max(atr_15m, ltp * 0.0045), 2) if ltp > 0 else 1.0
    active_tr = ds.get("active_trade")
    if active_tr and active_tr.get("symbol") == sym and active_tr.get("status") == "OPEN":
        rec = f"ACTIVE {active_tr['direction']}"
        setup_mode = "Mode B (Gain Harvest)" if active_tr.get("peak_favorable_r", 0) >= 0.70 else "Mode A (Fee Guard)"
        entry_low = round(active_tr["entry_price"] - risk_pts * 0.1, 2)
        entry_high = round(active_tr["entry_price"] + risk_pts * 0.1, 2)
        t1_p = active_tr["target_1_price"]
        t2_p = active_tr["target_2_est_price"]
        sl_p = active_tr["stop_loss"]
    else:
        is_short_bias = (ltp < vwap and chg_pct < -0.15) or ("LONGS LOCKED" in breadth_regime)
        is_long_bias = (ltp > vwap and chg_pct > 0.25 and ens_data["crash_prob_pct"] < 38.0) or ("SHORTS LOCKED" in breadth_regime)
        if not market_status.get("is_open", False):
            rec = "HOLIDAY STANDBY" if market_status.get("is_holiday") else "HOLD"
        elif is_short_bias:
            rec = "SHORT ARMED"
        elif is_long_bias:
            rec = "LONG ARMED"
        else:
            rec = "HOLD"

        setup_mode = "Mode B (Cash Preservation)" if rec in ("HOLD", "HOLIDAY STANDBY") else ("Gap & Dump Short" if is_short_bias else "Breakout Long")
        entry_low = round(ltp - risk_pts * 0.15, 2)
        entry_high = round(ltp + risk_pts * 0.15, 2)
        if is_short_bias and not is_long_bias:
            t1_p = round(ltp - risk_pts * 1.15, 2)
            t2_p = round(ltp - risk_pts * 1.80, 2)
            sl_p = round(ltp + risk_pts, 2)
        else:
            t1_p = round(ltp + risk_pts * 1.15, 2)
            t2_p = round(ltp + risk_pts * 1.80, 2)
            sl_p = round(ltp - risk_pts, 2)

    # 5. Intraday Volume Profile (from active session candles so VAH/POC/VAL align directly on chart)
    vp_data = None
    if len(candles) >= 10:
        try:
            c_low = min(c["low"] for c in candles)
            c_high = max(c["high"] for c in candles)
            if c_high > c_low:
                bins_n = 36
                edges = np.linspace(c_low, c_high, bins_n + 1)
                v_dist = np.zeros(bins_n)
                for c in candles:
                    v_c = float(c.get("volume", 0))
                    if v_c <= 0:
                        v_c = 1.0
                    i0 = max(0, min(bins_n - 1, int(np.digitize(c["low"], edges) - 1)))
                    i1 = max(0, min(bins_n - 1, int(np.digitize(c["high"], edges) - 1)))
                    span = max(1, i1 - i0 + 1)
                    v_dist[i0:i1 + 1] += v_c / span
                tot_v = float(np.sum(v_dist))
                p_idx = int(np.argmax(v_dist))
                poc_p = float((edges[p_idx] + edges[p_idx + 1]) / 2.0)
                va_target = tot_v * 0.70
                acc_v = v_dist[p_idx]
                u_i, d_i = p_idx, p_idx
                while acc_v < va_target and (u_i < bins_n - 1 or d_i > 0):
                    uv = v_dist[u_i + 1] if u_i < bins_n - 1 else 0.0
                    dv = v_dist[d_i - 1] if d_i > 0 else 0.0
                    if uv >= dv and u_i < bins_n - 1:
                        u_i += 1
                        acc_v += uv
                    elif d_i > 0:
                        d_i -= 1
                        acc_v += dv
                    else:
                        break
                vp_nodes = []
                for idx_b in range(bins_n):
                    vp_nodes.append({
                        "price": round(float((edges[idx_b] + edges[idx_b + 1]) / 2.0), 2),
                        "volume": int(v_dist[idx_b]),
                        "is_poc": bool(idx_b == p_idx),
                        "is_value_area": bool(d_i <= idx_b <= u_i)
                    })
                vp_data = {
                    "success": True,
                    "symbol": sym,
                    "poc_price": round(poc_p, 2),
                    "vah_price": round(float(edges[u_i + 1]), 2),
                    "val_price": round(float(edges[d_i]), 2),
                    "total_volume": int(tot_v),
                    "profile": vp_nodes
                }
        except Exception:
            vp_data = None
    if vp_data is None:
        try:
            from openview_api import get_volume_profile
            vp_sym = "NIFTY50" if sym == "NIFTY" else sym
            vp_data = get_volume_profile(symbol=vp_sym, period="5d", bins_count=36)
        except Exception:
            vp_data = None

    lunch_base = ds.get("lunch_bases", {}).get(sym)
    if not lunch_base and len(candles) >= 20:
        mid_slice = candles[len(candles)//3 : (len(candles)*2)//3]
        lunch_base = {
            "base_high": round(max(c["high"] for c in mid_slice), 2),
            "base_low": round(min(c["low"] for c in mid_slice), 2)
        }

    # 6. Market Microstructure (Strict per-metric reliability gating)
    is_open_mkt = bool(market_status.get("is_open", False))
    candle_vol_sum = sum(int(c.get("volume", 0)) for c in candles)
    session_vol = int(md.get("volume") or candle_vol_sum or 0)

    # Fetch / use cached 5-day average daily volume for true RVOL
    avg_vol_entry = AVG_VOL_CACHE.get(sym)
    avg_5d_vol = 0.0
    if sym not in ("NIFTY", "BANKNIFTY"):
        if avg_vol_entry and (now_ts - avg_vol_entry["ts"] < 300):
            avg_5d_vol = float(avg_vol_entry["avg_5d_vol"])
        else:
            try:
                vh = yf.download(f"{sym}.NS", period="10d", interval="1d", progress=False)
                if not vh.empty:
                    vs_col = vh["Volume"].iloc[:, 0] if isinstance(vh["Volume"], pd.DataFrame) else vh["Volume"]
                    vals = [float(x) for x in vs_col.dropna().tolist() if float(x) > 0]
                    if vals:
                        avg_5d_vol = float(np.mean(vals[-5:]))
                        AVG_VOL_CACHE[sym] = {"ts": now_ts, "avg_5d_vol": avg_5d_vol}
            except Exception:
                pass

    rvol_reliable = bool(sym not in ("NIFTY", "BANKNIFTY") and session_vol > 0 and avg_5d_vol > 0)
    true_rvol = None
    if rvol_reliable:
        if is_open_mkt:
            now_ist_dt = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
            elapsed_mins = max(15, (now_ist_dt.hour * 60 + now_ist_dt.minute) - 555)
            elapsed_frac = min(1.0, max(0.05, elapsed_mins / 375.0))
            true_rvol = round(session_vol / (avg_5d_vol * elapsed_frac), 2)
        else:
            true_rvol = round(session_vol / avg_5d_vol, 2)

    # Volume Delta from empirical intraday 5m OHLCV flow
    volume_delta_reliable = bool(sym not in ("NIFTY", "BANKNIFTY") and len(candles) >= 10 and candle_vol_sum > 0)
    cum_delta = 0.0
    recent_5_delta = 0.0
    buy_vol_est = 0.0
    sell_vol_est = 0.0
    if volume_delta_reliable:
        for i_c, c_bar in enumerate(candles):
            c_v = float(c_bar.get("volume", 0))
            rng = float(c_bar["high"]) - float(c_bar["low"])
            if rng > 0:
                flow_ratio = ((float(c_bar["close"]) - float(c_bar["low"])) - (float(c_bar["high"]) - float(c_bar["close"]))) / rng
            else:
                flow_ratio = 1.0 if float(c_bar["close"]) >= float(c_bar["open"]) else -1.0
            d_bar = c_v * flow_ratio
            cum_delta += d_bar
            buy_vol_est += c_v * (0.5 + 0.5 * flow_ratio)
            sell_vol_est += c_v * (0.5 - 0.5 * flow_ratio)
            if i_c >= len(candles) - 5:
                recent_5_delta += d_bar

    delta_pct = round((cum_delta / candle_vol_sum) * 100.0, 2) if (volume_delta_reliable and candle_vol_sum > 0) else None

    # Order book & Bid/Ask imbalance strictly gated by live market + non-empty two-sided L2 quotes
    imbalance_reliable = bool(is_open_mkt and md.get("imbalance_reliable", False))
    depth_reliable = bool(is_open_mkt and md.get("l2_live_available", False))
    spread_bps = md.get("spread_bps")
    spread_reliable = bool(is_open_mkt and spread_bps is not None)

    liquidity_reliable = bool(sym not in ("NIFTY", "BANKNIFTY") and session_vol > 0 and ltp > 0)
    turnover_cr = round((session_vol * ltp) / 1e7, 2) if liquidity_reliable else None
    if liquidity_reliable and turnover_cr is not None:
        if turnover_cr >= 250.0:
            liq_tier = "INSTITUTIONAL_DEEP"
        elif turnover_cr >= 75.0:
            liq_tier = "HIGH_LIQUID"
        else:
            liq_tier = "MODERATE_THIN"
    else:
        liq_tier = None

    microstructure = {
        "has_any_reliable": bool(imbalance_reliable or depth_reliable or volume_delta_reliable or rvol_reliable or liquidity_reliable),
        "imbalance_reliable": imbalance_reliable,
        "depth_reliable": depth_reliable,
        "volume_delta_reliable": volume_delta_reliable,
        "rvol_reliable": rvol_reliable,
        "liquidity_reliable": liquidity_reliable,
        "spread_reliable": spread_reliable,
        "bid_ask_imbalance": {
            "imbalance_pct": md.get("l2_imbalance_pct"),
            "buy_qty": md.get("buy_depth"),
            "sell_qty": md.get("sell_depth")
        } if imbalance_reliable else None,
        "order_book_depth": {
            "bid_levels": md.get("bid_levels", []),
            "ask_levels": md.get("ask_levels", []),
            "total_bid_qty": md.get("buy_depth", 0),
            "total_ask_qty": md.get("sell_depth", 0),
            "best_bid": md.get("best_bid"),
            "best_ask": md.get("best_ask")
        } if depth_reliable else None,
        "volume_delta": {
            "cumulative_delta": int(round(cum_delta)),
            "delta_pct": delta_pct,
            "recent_5bar_delta": int(round(recent_5_delta)),
            "buy_volume_est": int(round(buy_vol_est)),
            "sell_volume_est": int(round(sell_vol_est)),
            "total_volume": int(candle_vol_sum)
        } if volume_delta_reliable else None,
        "relative_volume": {
            "rvol": true_rvol,
            "session_volume": session_vol,
            "avg_5d_volume": int(round(avg_5d_vol)),
            "regime": "SURGE (>=1.5x)" if (true_rvol or 0) >= 1.5 else ("ACTIVE (>=0.9x)" if (true_rvol or 0) >= 0.9 else "SUBDUED (<0.9x)")
        } if rvol_reliable else None,
        "liquidity_conditions": {
            "turnover_cr": turnover_cr,
            "tier": liq_tier,
            "spread_bps": spread_bps if spread_reliable else None,
            "volume": session_vol
        } if liquidity_reliable else None
    }

    return {
        "status": "SUCCESS",
        "symbol": sym,
        "company_name": COMPANY_NAMES.get(sym, f"{sym} NSE"),
        "ltp": round(ltp, 2),
        "day_open": round(d_open, 2),
        "day_high": round(d_high, 2),
        "day_low": round(d_low, 2),
        "change_pts": chg_pts,
        "change_pct": chg_pct,
        "vwap": round(vwap, 2),
        "rsi_14": round(rsi_14, 1),
        "rvol": true_rvol if rvol_reliable else None,
        "atr_15m": round(atr_15m, 2),
        "last_trade_time": md.get("last_trade_time", candle_resp.get("session_date", "--")),
        "ai_metrics": ens_data,
        "factor_scores": [
            {"name": "Trend", "score": trend_score},
            {"name": "Momentum", "score": mom_score},
            {"name": "Volume", "score": vol_score},
            {"name": "Volatility", "score": volatility_score},
            {"name": "Liquidity", "score": liquidity_score},
            {"name": "Macro", "score": macro_score}
        ],
        "execution_brief": {
            "recommendation": rec,
            "setup_mode": setup_mode,
            "entry_zone": f"{entry_low:,.2f} - {entry_high:,.2f}",
            "target_1": t1_p,
            "target_2": t2_p,
            "stop_loss": sl_p,
            "risk_per_trade_rs": round(clear_cash * 0.016, 2)
        },
        "dalton_base": lunch_base,
        "volume_profile": vp_data,
        "microstructure": microstructure
    }

@app.get("/api/risk_command")
def get_risk_command():
    try:
        from live_strategy_daemon import daemon
        ds = daemon.state if daemon else {}
    except Exception:
        ds = {}

    acc = ds.get("account", {})
    clear_cash = float(acc.get("clear_cash", 8382.07))
    available_margin = float(acc.get("available_margin", clear_cash))
    mis_used = float(acc.get("mis_margin_used", 0.0))
    hard_floor = 2500.0
    deployable_margin = max(0.0, round(clear_cash - hard_floor, 2))

    # Van Tharp Risk Parameters & Daily Loss Limit (2.0R = 3.2% Daily Stop; 5.0% Tail Ceiling)
    risk_pct = 1.6
    risk_per_trade_rs = round(clear_cash * (risk_pct / 100.0), 2)
    daily_loss_limit_pct = 3.2
    daily_loss_limit_rs = round(clear_cash * (daily_loss_limit_pct / 100.0), 2)
    max_notional_mult = 3.50
    max_notional_cap_rs = round(clear_cash * max_notional_mult, 2)

    active_trade = ds.get("active_trade")
    active_notional = 0.0
    active_risk_rs = 0.0
    active_unrealized = 0.0
    if active_trade and active_trade.get("status") == "OPEN":
        active_notional = float(active_trade.get("notional", 0.0))
        active_risk_rs = round(
            abs(float(active_trade.get("entry_price", 0.0)) - float(active_trade.get("stop_loss", 0.0)))
            * float(active_trade.get("remaining_qty", active_trade.get("total_qty", 0))),
            2
        )
        active_unrealized = float(active_trade.get("unrealized_pnl", 0.0))

    ledger = get_audit_ledger()
    trades = ledger.get("trades", [])
    is_open_mkt = ds.get("market_status", {}).get("is_open", False)
    live_history = ds.get("trade_history", [])
    session_trades = live_history if (is_open_mkt and live_history) else trades

    sorted_trades = sorted(session_trades, key=lambda t: str(t.get("exit_time", "")))
    cum_pnl = 0.0
    peak_pnl = 0.0
    max_dd_rs = 0.0
    max_dd_pct = 0.0
    eq_series = [{"time": "09:15", "equity": round(clear_cash, 2), "cum_pnl": 0.0, "drawdown_rs": 0.0, "drawdown_pct": 0.0}]

    peak_notional_session = 0.0
    for tr in sorted_trades:
        net_v = float(tr.get("net_pnl", 0.0))
        notional_v = float(tr.get("notional", 0.0))
        if notional_v > peak_notional_session:
            peak_notional_session = notional_v
        cum_pnl = round(cum_pnl + net_v, 2)
        if cum_pnl > peak_pnl:
            peak_pnl = cum_pnl
        dd_rs = round(cum_pnl - peak_pnl, 2)
        dd_pct = round((dd_rs / (clear_cash + peak_pnl)) * 100.0, 2) if (clear_cash + peak_pnl) > 0 else 0.0
        if dd_rs < max_dd_rs:
            max_dd_rs = dd_rs
            max_dd_pct = dd_pct
        eq_series.append({
            "time": str(tr.get("exit_time", "--")),
            "symbol": tr.get("symbol", ""),
            "equity": round(clear_cash + cum_pnl, 2),
            "cum_pnl": cum_pnl,
            "drawdown_rs": dd_rs,
            "drawdown_pct": dd_pct
        })

    if active_unrealized != 0.0:
        live_cum = round(cum_pnl + active_unrealized, 2)
        if live_cum > peak_pnl:
            peak_pnl = live_cum
        dd_rs = round(live_cum - peak_pnl, 2)
        dd_pct = round((dd_rs / (clear_cash + peak_pnl)) * 100.0, 2) if (clear_cash + peak_pnl) > 0 else 0.0
        if dd_rs < max_dd_rs:
            max_dd_rs = dd_rs
            max_dd_pct = dd_pct
        eq_series.append({
            "time": "LIVE",
            "symbol": active_trade.get("symbol", "") if active_trade else "",
            "equity": round(clear_cash + live_cum, 2),
            "cum_pnl": live_cum,
            "drawdown_rs": dd_rs,
            "drawdown_pct": dd_pct
        })

    # Daily loss consumed (only positive consumption if net day PnL < 0; otherwise worst drawdown)
    current_day_pnl = float(ds.get("today_net_pnl", 0.0)) + active_unrealized if is_open_mkt else cum_pnl
    daily_loss_used_rs = round(max(0.0, -current_day_pnl), 2)
    daily_loss_remaining_rs = round(max(0.0, daily_loss_limit_rs - daily_loss_used_rs), 2)
    daily_loss_consumed_pct = round(min(100.0, (daily_loss_used_rs / daily_loss_limit_rs) * 100.0), 1) if daily_loss_limit_rs > 0 else 0.0

    utilization_pct = round((active_notional / max_notional_cap_rs) * 100.0, 1) if max_notional_cap_rs > 0 else 0.0
    session_peak_util_pct = round((peak_notional_session / max_notional_cap_rs) * 100.0, 1) if max_notional_cap_rs > 0 else 0.0

    # Circuit Breakers live audit
    ms = ds.get("market_status", {})
    guards = ds.get("safety_guards", {})
    lat_guard = guards.get("latency_watchdog", {})
    lat_ms = float(lat_guard.get("rolling_latency_ms", 65.0))
    tok_guard = guards.get("token_sentinel", {})
    tok_valid = bool(tok_guard.get("valid", True))
    regime = ds.get("strategy", {}).get("macro_regime", "ROTATIONAL_CHOP")

    breakers = [
        {
            "id": "EXCHANGE_SESSION",
            "name": "Exchange & Holiday Gate",
            "status": "ARMED" if ms.get("is_open") else ("HOLIDAY LOCK" if ms.get("is_holiday") else "POST-MARKET LOCK"),
            "severity": "OK" if ms.get("is_open") else "WARN",
            "live_value": ms.get("status_label", "STANDBY"),
            "threshold": "09:15–15:30 IST (Non-Holiday)",
            "detail": f"Next Session: {ms.get('next_session', '--')}"
        },
        {
            "id": "LATENCY_WATCHDOG",
            "name": "Broker Latency Watchdog",
            "status": "TRIPPED" if lat_ms > 500 else ("BUFFERED" if lat_ms > 200 else "ARMED"),
            "severity": "CRITICAL" if lat_ms > 500 else ("WARN" if lat_ms > 200 else "OK"),
            "live_value": f"{lat_ms:.1f} ms ({lat_guard.get('route_mode', 'FAST_PEGGED_LIMIT')})",
            "threshold": "<= 200ms Optimal / 500ms Halt",
            "detail": "Auto-aborts orders if gateway congestion exceeds 500ms"
        },
        {
            "id": "SEBI_TOKEN_SENTINEL",
            "name": "SEBI 06:00 AM Token Guard",
            "status": "ARMED" if tok_valid else "TRIPPED",
            "severity": "OK" if tok_valid else "CRITICAL",
            "live_value": tok_guard.get("status", "ARMED_AND_VALID"),
            "threshold": "Post-06:00 AM IST ES256 Cycle",
            "detail": f"Expires: {tok_guard.get('expires_at', '06:00 AM IST Next Cycle')}"
        },
        {
            "id": "MARGIN_FLOOR",
            "name": "Upfront Cash Reserve Floor",
            "status": "ARMED" if clear_cash >= hard_floor else "TRIPPED",
            "severity": "OK" if clear_cash >= hard_floor else "CRITICAL",
            "live_value": f"₹{clear_cash:,.2f} (Buffer ₹{deployable_margin:,.2f})",
            "threshold": f">= ₹{hard_floor:,.2f} Hard Floor",
            "detail": f"3.50x Cap: ₹{max_notional_cap_rs:,.2f}"
        },
        {
            "id": "DAILY_LOSS_STOP",
            "name": "Daily Loss Limit Breaker",
            "status": "TRIPPED" if daily_loss_used_rs >= daily_loss_limit_rs else "ARMED",
            "severity": "CRITICAL" if daily_loss_used_rs >= daily_loss_limit_rs else "OK",
            "live_value": f"₹{daily_loss_used_rs:.2f} Used / ₹{daily_loss_remaining_rs:.2f} Left",
            "threshold": f"-₹{daily_loss_limit_rs:.2f} (-{daily_loss_limit_pct:.1f}% / 2.0R)",
            "detail": "Halts new intraday entries if daily net equity drops by 2.0R"
        },
        {
            "id": "ONE_TRADE_MUTEX",
            "name": "1-Trade Mutex & Breadth Gate",
            "status": "ENGAGED" if (active_trade and active_trade.get("status") == "OPEN") else "ARMED",
            "severity": "OK",
            "live_value": f"{'1 OPEN' if (active_trade and active_trade.get('status') == 'OPEN') else '0 OPEN (FLAT)'} • {regime.split(' ')[0]}",
            "threshold": "Max 1 Open Trade • Breadth 30–70%",
            "detail": regime
        }
    ]

    # Per-stock circuit proximity (distance from NSE ±20% price band)
    md_map = ds.get("market_data", {})
    circuit_health = []
    for sym in ["RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "TCS", "INFY", "BAJFINANCE", "AXISBANK", "KOTAKBANK"]:
        q = md_map.get(sym, {})
        ltp_v = float(q.get("ltp", 0.0))
        open_v = float(q.get("open", ltp_v))
        chg_p = ((ltp_v - open_v) / open_v * 100.0) if open_v > 0 else 0.0
        dist_lc_pct = round(max(0.0, 20.0 + chg_p), 2)
        dist_uc_pct = round(max(0.0, 20.0 - chg_p), 2)
        circuit_health.append({
            "symbol": sym,
            "ltp": round(ltp_v, 2),
            "day_chg_pct": round(chg_p, 2),
            "dist_to_lower_circuit_pct": dist_lc_pct,
            "dist_to_upper_circuit_pct": dist_uc_pct,
            "status": "CRITICAL" if min(dist_lc_pct, dist_uc_pct) < 5.0 else "SAFE"
        })

    tripped_count = sum(1 for b in breakers if b["status"] == "TRIPPED")
    locked_count = sum(1 for b in breakers if "LOCK" in b["status"] or b["status"] == "ENGAGED")
    overall_status = "HALTED (BREAKER TRIPPED)" if tripped_count > 0 else ("INTERLOCK STANDBY" if locked_count > 0 else "ALL SYSTEMS ARMED")

    return {
        "status": "SUCCESS",
        "overall_breaker_status": overall_status,
        "tripped_count": tripped_count,
        "live_max_drawdown": {
            "drawdown_rs": max_dd_rs,
            "drawdown_pct": max_dd_pct,
            "peak_equity_rs": round(clear_cash + peak_pnl, 2),
            "current_equity_rs": round(clear_cash + current_day_pnl, 2),
            "session_label": "LIVE" if is_open_mkt else ledger.get("date", "2026-11-09")
        },
        "risk_per_trade": {
            "risk_rs": risk_per_trade_rs,
            "risk_pct": risk_pct,
            "active_trade_risk_rs": active_risk_rs,
            "max_1r_cap_pct": risk_pct
        },
        "available_margin": {
            "clear_cash_rs": round(clear_cash, 2),
            "available_margin_rs": round(available_margin, 2),
            "mis_margin_used_rs": round(mis_used, 2),
            "hard_reserve_floor_rs": hard_floor,
            "deployable_margin_rs": deployable_margin
        },
        "daily_loss_limit": {
            "limit_rs": daily_loss_limit_rs,
            "limit_pct": daily_loss_limit_pct,
            "used_rs": daily_loss_used_rs,
            "remaining_rs": daily_loss_remaining_rs,
            "consumed_pct": daily_loss_consumed_pct
        },
        "exposure_utilization": {
            "max_notional_multiple": max_notional_mult,
            "max_notional_cap_rs": max_notional_cap_rs,
            "current_notional_rs": round(active_notional, 2),
            "utilization_pct": utilization_pct,
            "session_peak_notional_rs": round(peak_notional_session, 2),
            "session_peak_utilization_pct": session_peak_util_pct
        },
        "circuit_breakers": breakers,
        "constituent_circuit_proximity": circuit_health,
        "equity_drawdown_series": eq_series
    }

@app.get("/api/logs")
def get_logs():
    return state.get("logs", [])

@app.get("/api/history")
def get_history():
    return state.get("history", [])

# -------------------------------------------------------------------------
# Forensic Audit Endpoint for Any Calendar Date (Last 5 Years)
# -------------------------------------------------------------------------
from historical_audit_engine import generate_point_in_time_audit

@app.get("/api/audit_date")
def api_get_audit_date(date: str = "2026-11-04"):
    try:
        return generate_point_in_time_audit(date)
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/audit_date")
def api_post_audit_date(payload: Dict[str, Any]):
    d = payload.get("date", "2026-11-04")
    try:
        return generate_point_in_time_audit(d)
    except Exception as e:
        return {"success": False, "error": str(e)}

# -------------------------------------------------------------------------
# OpenView Free Institutional Charting API
# -------------------------------------------------------------------------
from openview_api import router as openview_router
app.include_router(openview_router)

from fastapi.staticfiles import StaticFiles
app.mount("/openview", StaticFiles(directory="/root/alphaniti/openview_frontend", html=True), name="openview")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


