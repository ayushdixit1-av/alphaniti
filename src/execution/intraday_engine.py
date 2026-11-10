"""
AlphaNiti v26.0 Institutional Intraday Execution Engine
Implements:
1. Exact 68-Bar Truncation (09:15 AM to 02:55 PM), excising Bars 69-75 (Pre-Auction & CAS)
2. Zero-Leakage 15-Minute ATR Synchronization on 5-Minute Candlesticks
3. 09:45:00 AM Execution (Bar 6 Closed -> Bar 7 Open)
4. Volatility-Adaptive Brackets: Stop = -2.50 * ATR_15m, Target = +4.40 * ATR_15m (1.76:1 R:R)
5. Volatility-Parity Position Sizing (SEBI 5x Peak Margin Compliant)
6. Continuous 4-Tier Gate State Machine
7. Finance Act 2026 Penny-Accurate Cost Accounting (Groww + NSE + IPFT + GST + 1.5 bps Slippage)
"""

import sys
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import numpy as np
import pandas as pd
import polars as pl
import torch

from src.portfolio.statutory_costs import IndianStatutoryCostModel
from src.portfolio.integer_sizer import compute_volatility_parity_shares
from src.risk.decision_score import (
    compute_composite_decision_score,
    resolve_gate_tier_and_multiplier,
    compute_volatility_adaptive_brackets,
)


def compute_true_range(high: np.ndarray, low: np.ndarray, close_prev: np.ndarray) -> np.ndarray:
    tr1 = high - low
    tr2 = np.abs(high - close_prev)
    tr3 = np.abs(low - close_prev)
    return np.maximum(tr1, np.maximum(tr2, tr3))


def calculate_15m_atr_aligned_to_5m(df_5m: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Computes ATR_15m strictly on closed 15-minute intervals (bars 3, 6, 9, ...)
    and forward-fills intermediate 5-minute bars to guarantee zero intra-bar lookahead leakage.
    """
    df = df_5m.copy().sort_values("time_str").reset_index(drop=True)
    n_bars = len(df)
    
    # Identify 15m closed boundaries (every 3rd 5m bar: 09:30, 09:45, 10:00, etc.)
    # Bar index 0, 1, 2 -> Bar 2 is 09:30 (15m close 1)
    # Bar index 3, 4, 5 -> Bar 5 is 09:45 (15m close 2)
    # Bar index 6 is Bar 7 (09:45 Open)
    
    # Resample to 15m
    resampled_rows = []
    for i in range(2, n_bars, 3):
        slice_3 = df.iloc[i-2:i+1]
        h_15 = slice_3["high"].max()
        l_15 = slice_3["low"].min()
        c_15 = slice_3["close"].iloc[-1]
        t_15 = slice_3["time_str"].iloc[-1]
        resampled_rows.append({
            "bar_idx_5m": i,
            "time_str": t_15,
            "high_15m": h_15,
            "low_15m": l_15,
            "close_15m": c_15
        })
        
    df_15 = pd.DataFrame(resampled_rows)
    if len(df_15) < 2:
        df["atr_15m"] = df["close"] * 0.003  # default fallback 0.3%
        return df
        
    # True Range for 15m
    prev_close = np.roll(df_15["close_15m"].values, 1)
    prev_close[0] = df_15["low_15m"].values[0]
    tr_15 = compute_true_range(df_15["high_15m"].values, df_15["low_15m"].values, prev_close)
    
    # Wilder's Smoothing / Rolling ATR
    atr_15_vals = pd.Series(tr_15).ewm(alpha=1.0/period, adjust=False).mean().values
    df_15["atr_15m"] = atr_15_vals
    
    # Map back to 5m DataFrame and forward fill
    df["atr_15m"] = np.nan
    for _, row in df_15.iterrows():
        b_idx = int(row["bar_idx_5m"])
        if b_idx < n_bars:
            df.loc[b_idx, "atr_15m"] = row["atr_15m"]
            
    df["atr_15m"] = df["atr_15m"].ffill()
    # Strict causal initialization for bars 0..1 before the first 15m bar closes (zero .bfill() leakage)
    first_valid_mask = df["atr_15m"].isna()
    if first_valid_mask.any():
        causal_tr = np.maximum(df["high"] - df["low"], df["close"] * 0.003)
        df.loc[first_valid_mask, "atr_15m"] = causal_tr.expanding().mean()[first_valid_mask]
    return df


class IntradayExecutionEngine:
    """
    AlphaNiti v26.0 Production Intraday Trading Engine.
    """
    def __init__(
        self,
        cost_model: Optional[IndianStatutoryCostModel] = None,
        k_target: float = 4.40,
        k_stop: float = 2.50,
        risk_budget_pct: float = 0.01,  # 1% rupee risk per trade
        max_leverage: float = 5.0,      # SEBI Peak Margin 5x cap
    ):
        self.cost_model = cost_model or IndianStatutoryCostModel()
        self.k_target = k_target
        self.k_stop = k_stop
        self.risk_budget_pct = risk_budget_pct
        self.max_leverage = max_leverage

    def simulate_day(
        self,
        date: str,
        capital: float,
        stock_5m_data: Dict[str, pd.DataFrame],
        ensemble_signals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Executes a single day's trading workflow:
        1. 68-Bar truncation (Bars 1-68: 09:15 AM to 02:55 PM).
        2. Market Gate evaluation at 09:45:00 AM.
        3. Volatility-parity discrete integer sizing.
        4. Bar-by-bar progression from Bar 7 (09:45 AM) to Bar 68 (02:55 PM).
        5. Hard 02:55 PM exit.
        """
        # 1. Evaluate Gate Regime
        mkt_drift = float(np.mean([s["predicted_return_pct"] for s in ensemble_signals])) / 100.0 if ensemble_signals else 0.0
        mkt_crash_risk = float(np.mean([s["drawdown_crash_prob"] for s in ensemble_signals])) if ensemble_signals else 0.0
        
        gate_tier, gate_multiplier, regime = resolve_gate_tier_and_multiplier(mkt_drift, mkt_crash_risk)
        
        if gate_tier == 3 or gate_multiplier == 0.0:
            return {
                "date": date,
                "regime": regime,
                "gate_tier": gate_tier,
                "action": "CASH_SHELTER",
                "symbol": "CASH",
                "shares": 0,
                "entry_price": 0.0,
                "exit_price": 0.0,
                "gross_pnl": 0.0,
                "friction": 0.0,
                "net_pnl": 0.0,
                "ending_capital": round(capital, 2),
                "exit_reason": "CASH_SHELTER (Tier 3 Gate Active)",
            }
            
        # 2. Score Candidates
        qualified = []
        for s in ensemble_signals:
            score_res = compute_composite_decision_score(
                ticker=s["symbol"],
                gamma_bar_horizon=s["predicted_return_pct"] / 100.0,
                sigma_alea_horizon=s["aleatoric_vol_pct"] / 100.0,
                sigma_epis=s["epistemic_uncertainty"],
                p_breach=s["drawdown_crash_prob"],
                gate_tier=gate_tier,
                gate_multiplier=gate_multiplier,
                breach_ceiling=0.48,
            )
            if ("BUY" in score_res.verdict or "ACCUMULATE" in score_res.verdict) and s["predicted_return_pct"] > 0:
                s_copy = dict(s)
                s_copy["composite_score"] = score_res.raw_score
                s_copy["final_score"] = score_res.final_score
                s_copy["verdict"] = score_res.verdict
                s_copy["gate_multiplier"] = score_res.gate_multiplier
                qualified.append(s_copy)
                
        if not qualified:
            return {
                "date": date,
                "regime": regime,
                "gate_tier": gate_tier,
                "action": "NO_QUALIFIED_STOCK",
                "symbol": "-",
                "shares": 0,
                "entry_price": 0.0,
                "exit_price": 0.0,
                "gross_pnl": 0.0,
                "friction": 0.0,
                "net_pnl": 0.0,
                "ending_capital": round(capital, 2),
                "exit_reason": "No stock cleared alpha and gate hurdles",
            }
            
        qualified.sort(key=lambda x: (x["composite_score"], x["sharpe_score"]), reverse=True)
        top_pick = qualified[0]
        sym = top_pick["symbol"]
        
        df_sym = stock_5m_data.get(sym)
        if df_sym is None or len(df_sym) < 7:
            return {
                "date": date,
                "regime": regime,
                "gate_tier": gate_tier,
                "action": "INSUFFICIENT_INTRADAY_BARS",
                "symbol": sym,
                "shares": 0,
                "entry_price": 0.0,
                "exit_price": 0.0,
                "gross_pnl": 0.0,
                "friction": 0.0,
                "net_pnl": 0.0,
                "ending_capital": round(capital, 2),
                "exit_reason": "Insufficient 5-minute bars on execution date",
            }
            
        # Ensure 68-Bar Truncation (09:15 AM to 02:55 PM, bars index 0 to 67)
        df_sym_68 = df_sym.iloc[:68].copy().reset_index(drop=True)
        
        # Calculate synchronized 15m ATR aligned to 5m
        df_sym_68 = calculate_15m_atr_aligned_to_5m(df_sym_68)
        
        # 3. Bar 6 Closed (09:45:00 AM) -> Entry at Bar 7 Open
        bar_6_idx = 5
        bar_7_idx = 6
        
        entry_price = float(df_sym_68.loc[bar_7_idx, "open"])
        atr_15m = float(df_sym_68.loc[bar_6_idx, "atr_15m"])
        
        # Compute Volatility-Adaptive Brackets (1.76:1 R:R)
        brackets = compute_volatility_adaptive_brackets(
            entry_price=entry_price,
            atr_15m=atr_15m,
            k_target=self.k_target,
            k_stop=self.k_stop,
        )
        target_price = brackets["target_price"]
        stop_price = brackets["stop_price"]
        
        # Volatility-Parity Discrete Sizing
        shares, size_info = compute_volatility_parity_shares(
            capital=capital,
            entry_price=entry_price,
            atr_15m=atr_15m,
            risk_budget_pct=self.risk_budget_pct,
            gate_multiplier=gate_multiplier,
            max_leverage=self.max_leverage,
            k_stop=self.k_stop,
        )
        
        if shares <= 0:
            return {
                "date": date,
                "regime": regime,
                "gate_tier": gate_tier,
                "action": "INSUFFICIENT_MARGIN_OR_RISK",
                "symbol": sym,
                "shares": 0,
                "entry_price": entry_price,
                "exit_price": entry_price,
                "gross_pnl": 0.0,
                "friction": 0.0,
                "net_pnl": 0.0,
                "ending_capital": round(capital, 2),
                "exit_reason": "Position size rounded to 0 shares under risk budget",
            }
            
        # 4. Bar-by-Bar Progression from Bar 7 to Bar 68 (09:45 AM to 02:55 PM)
        exit_price = entry_price
        exit_reason = "EOD_SQUAREOFF_0255PM"
        exit_bar_idx = len(df_sym_68) - 1
        
        for b in range(bar_7_idx, len(df_sym_68)):
            bar_o = float(df_sym_68.loc[b, "open"]) if "open" in df_sym_68.columns else float(df_sym_68.loc[b, "close"])
            bar_h = float(df_sym_68.loc[b, "high"])
            bar_l = float(df_sym_68.loc[b, "low"])

            # Gap-down open past stop: fill conservatively inside the bar range near bar_open (never above bar_h)
            if bar_o <= stop_price:
                exit_price = max(bar_l, min(bar_h, round(bar_o * (1.0 - 0.00015), 2)))
                exit_reason = f"GAP_THROUGH_STOP_LOSS ({round(((exit_price / entry_price) - 1.0) * 100.0, 2):+.2f}%)"
                exit_bar_idx = b
                break

            hit_target = bar_h >= target_price
            hit_stop = bar_l <= stop_price
            
            if hit_target and not hit_stop:
                exit_price = max(bar_l, min(bar_h, target_price))
                exit_reason = f"TARGET_HIT (+{brackets['target_pct']}%)"
                exit_bar_idx = b
                break
            elif hit_stop and not hit_target:
                exit_price = max(bar_l, min(bar_h, stop_price))
                exit_reason = f"STOP_LOSS ({brackets['stop_pct']}%)"
                exit_bar_idx = b
                break
            elif hit_target and hit_stop:
                # Conservative rule: stop triggered first on dual-wick intra-candle
                exit_price = max(bar_l, min(bar_h, stop_price))
                exit_reason = f"STOP_LOSS_DUAL_WICK ({brackets['stop_pct']}%)"
                exit_bar_idx = b
                break
        else:
            # Reached Bar 68 (02:55 PM Close) without hitting target or stop
            bar_68_close = float(df_sym_68.loc[len(df_sym_68) - 1, "close"])
            exit_price = bar_68_close
            ret_pct = round(((exit_price / entry_price) - 1.0) * 100.0, 2)
            exit_reason = f"EOD_SQUAREOFF_0255PM ({ret_pct:+.2f}%)"
            
        gross_pnl = shares * (exit_price - entry_price)
        friction_dict = self.cost_model.compute_intraday_roundtrip(entry_price, exit_price, shares)
        total_friction = friction_dict["total_friction"]
        net_pnl = gross_pnl - total_friction
        
        new_capital = capital + net_pnl
        
        return {
            "date": date,
            "regime": regime,
            "gate_tier": gate_tier,
            "action": "TRADE_EXECUTED",
            "symbol": sym,
            "shares": shares,
            "entry_price": round(entry_price, 2),
            "target_price": target_price,
            "stop_price": stop_price,
            "exit_price": round(exit_price, 2),
            "exit_reason": exit_reason,
            "exit_bar_time": str(df_sym_68.loc[exit_bar_idx, "time_str"]),
            "gross_pnl": round(gross_pnl, 2),
            "friction": round(total_friction, 2),
            "friction_breakdown": friction_dict,
            "net_pnl": round(net_pnl, 2),
            "ending_capital": round(new_capital, 2),
            "target_pct": brackets["target_pct"],
            "stop_pct": brackets["stop_pct"],
            "leverage_used": size_info["leverage_used"],
        }
