"""
AlphaNiti v26.0 Institutional Execution Engine
Pipeline Module 4 - Indian Statutory Costs & Non-Convex Fee Relaxations
Reference: Finance Act 2026, SEBI (Trading and Clearing) Regulations, Groww Fee Schedule
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
import numpy as np


class IndianStatutoryCostModel:
    """
    Statutory taxes, exchange transaction costs, non-convex Groww brokerage fees,
    NSE IPFT charges, and post-April 2026 slippage schedules under Indian regulatory framework.
    """

    def __init__(
        self,
        stt_rate_delivery: float = 0.001,      # 0.1% on delivery equity buy & sell
        stt_rate_intraday: float = 0.00025,    # 0.025% on intraday equity sell
        stamp_duty_delivery: float = 0.00015,  # 0.015% on delivery buy
        stamp_duty_intraday: float = 0.00003,  # 0.003% on intraday buy
        nse_turnover_rate: float = 0.0000297,  # 0.00297% NSE cash segment
        sebi_turnover_rate: float = 0.000001,  # Rs 10 per crore (0.0001%)
        ipft_rate: float = 0.000001,           # Rs 10 per crore NSE Investor Protection Fund
        brokerage_rate: float = 0.001,         # Groww: 0.1% or Rs 20 max per executed order
        brokerage_max_inr: float = 20.0,       # Rs 20 cap per order
        brokerage_min_inr: float = 5.0,        # Rs 5 min per order
        gst_rate: float = 0.18,                # 18% GST on (brokerage + exchange + sebi + ipft)
        slippage_rate: float = 0.00015,        # 1.5 bps post-April 2026 wider spread
        impact_eta: float = 0.1,               # Market impact coefficient
        stt_rate: Optional[float] = None,      # Backward-compatible delivery STT alias
        stamp_duty_rate: Optional[float] = None,
    ):
        self.stt_rate_delivery = stt_rate if stt_rate is not None else stt_rate_delivery
        self.stt_rate_intraday = stt_rate_intraday
        self.stamp_duty_delivery = stamp_duty_rate if stamp_duty_rate is not None else stamp_duty_delivery
        self.stamp_duty_intraday = stamp_duty_intraday
        self.stt_rate = self.stt_rate_delivery
        self.stamp_duty_rate = self.stamp_duty_delivery
        self.nse_turnover_rate = nse_turnover_rate
        self.sebi_turnover_rate = sebi_turnover_rate
        self.ipft_rate = ipft_rate
        self.brokerage_rate = brokerage_rate
        self.brokerage_max_inr = brokerage_max_inr
        self.brokerage_min_inr = brokerage_min_inr
        self.gst_rate = gst_rate
        self.slippage_rate = slippage_rate
        self.impact_eta = impact_eta

    @classmethod
    def from_config(cls, config_path: Optional[str] = None) -> "IndianStatutoryCostModel":
        """Load statutory tax and fee parameters dynamically from Finance Act 2026 configuration."""
        if config_path is None:
            candidate = Path(__file__).resolve().parent.parent.parent / "config" / "statutory_rates_2026.json"
            if candidate.exists():
                config_path = str(candidate)
        if config_path and os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rates = data.get("rates", {})
            return cls(
                stt_rate_delivery=rates.get("stt_rate", 0.001),
                stamp_duty_delivery=rates.get("stamp_duty_rate", 0.00015),
                nse_turnover_rate=rates.get("nse_turnover_rate", 0.0000325),
                sebi_turnover_rate=rates.get("sebi_turnover_rate", 0.000001),
                brokerage_rate=rates.get("brokerage_rate", 0.0003),
                brokerage_max_inr=rates.get("brokerage_max_inr"),
                brokerage_min_inr=rates.get("brokerage_min_inr", 20.0),
                gst_rate=rates.get("gst_rate", 0.18),
                impact_eta=rates.get("impact_eta", 0.1),
            )
        return cls()

    def compute_intraday_roundtrip(
        self,
        entry_price: float,
        exit_price: float,
        shares: int,
    ) -> Dict[str, float]:
        """
        Computes exact penny-accurate intraday roundtrip friction for Groww + NSE:
        - Entry leg: Buy
        - Exit leg: Sell (Auto square-off before 02:55 PM avoids Rs 50 + GST RMS penalty)
        """
        if shares <= 0:
            return {
                "brokerage": 0.0,
                "stt": 0.0,
                "exchange_turnover": 0.0,
                "ipft": 0.0,
                "sebi_fee": 0.0,
                "stamp_duty": 0.0,
                "gst": 0.0,
                "slippage": 0.0,
                "total_friction": 0.0,
            }

        buy_turnover = shares * entry_price
        sell_turnover = shares * exit_price
        total_turnover = buy_turnover + sell_turnover

        # 1. Groww Brokerage: min(Rs 20, 0.1% of turnover), min Rs 5
        b_buy = max(self.brokerage_min_inr, min(self.brokerage_max_inr, self.brokerage_rate * buy_turnover))
        b_sell = max(self.brokerage_min_inr, min(self.brokerage_max_inr, self.brokerage_rate * sell_turnover))
        brokerage = b_buy + b_sell

        # 2. STT: 0.025% on sell leg only
        stt = self.stt_rate_intraday * sell_turnover

        # 3. NSE Turnover Charges: 0.00297% on buy + sell
        nse_charges = self.nse_turnover_rate * total_turnover

        # 4. NSE IPFT Charge: 0.0001% (Rs 10 per crore)
        ipft = self.ipft_rate * total_turnover

        # 5. SEBI Regulatory Turnover Fee: Rs 10 per crore
        sebi = self.sebi_turnover_rate * total_turnover

        # 6. Stamp Duty: 0.003% on buy leg only
        stamp = self.stamp_duty_intraday * buy_turnover

        # 7. 18% GST on (Brokerage + NSE + IPFT + SEBI)
        gst = self.gst_rate * (brokerage + nse_charges + ipft + sebi)

        # 8. Post-April 2026 Microstructure Slippage (1.5 bps on entry + exit)
        slippage = self.slippage_rate * total_turnover

        total = brokerage + stt + nse_charges + ipft + sebi + stamp + gst + slippage

        return {
            "brokerage": round(brokerage, 2),
            "stt": round(stt, 2),
            "exchange_turnover": round(nse_charges, 2),
            "ipft": round(ipft, 2),
            "sebi_fee": round(sebi, 2),
            "stamp_duty": round(stamp, 2),
            "gst": round(gst, 2),
            "slippage": round(slippage, 2),
            "total_friction": round(total, 2),
        }

    def compute_cost(
        self,
        shares: float,
        price: float,
        spread: float = 0.05,
        adv: float = 1_000_000.0,
        volatility: float = 0.02,
        is_buy: bool = True,
    ) -> float:
        """Delivery equity single-leg cost function per Finance Act 2026 (STT on buy & sell)."""
        if shares <= 0:
            return 0.0
        turnover = shares * price
        spread_cost = shares * (spread / 2.0)
        participation = np.sqrt(shares / max(1.0, adv))
        impact_cost = self.impact_eta * volatility * participation * turnover
        raw_brk = self.brokerage_rate * turnover
        capped_brk = min(self.brokerage_max_inr, raw_brk) if self.brokerage_max_inr is not None else raw_brk
        brokerage = max(self.brokerage_min_inr, capped_brk)
        exchange_charges = self.nse_turnover_rate * turnover
        ipft_charges = self.ipft_rate * turnover
        sebi_charges = self.sebi_turnover_rate * turnover
        gst = self.gst_rate * (brokerage + exchange_charges + ipft_charges + sebi_charges)
        stt = self.stt_rate_delivery * turnover
        stamp_duty = (self.stamp_duty_delivery * turnover) if is_buy else 0.0
        total_statutory = stt + stamp_duty + exchange_charges + ipft_charges + sebi_charges + gst
        return float(spread_cost + impact_cost + brokerage + total_statutory)

    def compute_detailed_breakdown(
        self,
        shares: float,
        price: float,
        spread: float = 0.05,
        adv: float = 1_000_000.0,
        volatility: float = 0.02,
        is_buy: bool = True,
    ) -> Dict[str, float]:
        """Returns itemised Finance Act 2026 delivery statutory tax & friction breakdown."""
        if shares <= 0:
            return {
                "stt": 0.0,
                "stamp_duty": 0.0,
                "exchange_nse": 0.0,
                "sebi_fee": 0.0,
                "brokerage": 0.0,
                "gst_18pct": 0.0,
                "spread_and_impact": 0.0,
                "total_charges": 0.0,
            }
        turnover = shares * price
        spread_cost = shares * (spread / 2.0)
        participation = np.sqrt(shares / max(1.0, adv))
        impact_cost = self.impact_eta * volatility * participation * turnover
        raw_brk = self.brokerage_rate * turnover
        capped_brk = min(self.brokerage_max_inr, raw_brk) if self.brokerage_max_inr is not None else raw_brk
        brokerage = max(self.brokerage_min_inr, capped_brk)
        exchange_charges = self.nse_turnover_rate * turnover
        ipft_charges = self.ipft_rate * turnover
        sebi_charges = self.sebi_turnover_rate * turnover
        gst = self.gst_rate * (brokerage + exchange_charges + ipft_charges + sebi_charges)
        stt = self.stt_rate_delivery * turnover
        stamp_duty = (self.stamp_duty_delivery * turnover) if is_buy else 0.0
        total = spread_cost + impact_cost + brokerage + stt + stamp_duty + exchange_charges + ipft_charges + sebi_charges + gst
        return {
            "stt": float(stt),
            "stamp_duty": float(stamp_duty),
            "exchange_nse": float(exchange_charges),
            "sebi_fee": float(sebi_charges),
            "brokerage": float(brokerage),
            "gst_18pct": float(gst),
            "spread_and_impact": float(spread_cost + impact_cost),
            "total_charges": float(total),
        }

    def lower_convex_envelope_slope(
        self,
        price: float,
        u_upper_bound: float = 1.0,
        spread: float = 0.05,
        adv: float = 1_000_000.0,
        volatility: float = 0.02,
        is_buy: bool = True,
    ) -> float:
        """
        Constructs a guaranteed lower convex envelope Cost_i^cvx(q) = slope * q <= Cost_i(q)
        for all q in [0, u_upper_bound] under both uncapped and Groww Rs 20-capped brokerage.
        """
        spread_slope = spread / 2.0
        u_safe = max(1.0, float(u_upper_bound))
        if self.brokerage_max_inr is not None:
            brokerage_slope = min(self.brokerage_max_inr, self.brokerage_rate * price * u_safe) / u_safe
        else:
            brokerage_slope = self.brokerage_rate * price

        exchange_slope = self.nse_turnover_rate * price
        ipft_slope = self.ipft_rate * price
        sebi_slope = self.sebi_turnover_rate * price
        gst_slope = self.gst_rate * (brokerage_slope + exchange_slope + ipft_slope + sebi_slope)
        stt_slope = self.stt_rate_delivery * price
        stamp_slope = (self.stamp_duty_delivery * price) if is_buy else 0.0

        statutory_slope = stt_slope + stamp_slope + exchange_slope + ipft_slope + sebi_slope + gst_slope
        return float(spread_slope + brokerage_slope + statutory_slope)
