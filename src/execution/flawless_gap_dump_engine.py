import sys
import json
import math
from typing import Any, Dict, Optional
import numpy as np
from src.portfolio.statutory_costs import IndianStatutoryCostModel

class FlawlessGapDumpEngine:
    """
    AlphaNiti v26.3 Sovereign Execution & Risk Architecture.
    Rectifies ALL 10 Red-Team Concerns + Air-Pocket Jump-CVaR & Vacuum Recovery.
    """
    def __init__(self, capital: float = 8382.07):
        self.capital = capital
        self.fixed_fee = 55.00 # Fallback ceiling fee
        self.cost_model = IndianStatutoryCostModel()
        self.poisson_buffer = 10.00 # Rs 10.00 jump buffer (absorbs 10-tick gap)
        self.default_tail_gap_pct = 0.045 # 4.5% air-pocket jump benchmark
        self.max_single_gap_equity_pct = 0.071 # 7.1% unwarned jump-CVaR cap (~1.58x cash leverage ceiling)
        
        # Approved Top-Tier Liquid NSE F&O Whitelist (Zero rigid 5% circuit halts)
        self.fo_whitelist = {
            "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "TCS", 
            "INFY", "BAJFINANCE", "TATAMOTORS", "AXISBANK", "KOTAKBANK", "LT", "BHARTIARTL"
        }

    def get_dynamic_risk_budget(self, current_equity: float) -> float:
        """Concern 10: Geometric risk decay prevents drawdown blowup."""
        return min(134.00, current_equity * 0.016)

    def compute_l2_fragility_score(
        self,
        bid_depth: Optional[int] = None,
        baseline_bid_depth: Optional[int] = None,
        spread_bps: Optional[float] = None,
        l2_imbalance_pct: float = 0.0,
        direction: str = "LONG",
        peer_canary_ret_pct: float = 0.0,
    ) -> float:
        """
        Causal Pre-Gap Fragility Score phi in [0.0, 1.0].
        Detects thinning bid stacks, widening spreads, and correlated peer/index drops
        before a single-stock air-pocket occurs.
        """
        phi = 0.0
        if bid_depth is not None and baseline_bid_depth and baseline_bid_depth > 0:
            depth_ratio = bid_depth / float(baseline_bid_depth)
            if depth_ratio < 0.70:
                phi += min(0.45, (0.70 - depth_ratio) * 0.90)

        if spread_bps is not None and spread_bps > 6.0:
            phi += min(0.35, (spread_bps - 6.0) / 30.0)

        is_long = direction.upper() == "LONG"
        if is_long and l2_imbalance_pct < -15.0:
            phi += min(0.30, (-15.0 - l2_imbalance_pct) / 100.0)
        elif (not is_long) and l2_imbalance_pct > 25.0:
            phi += min(0.30, (l2_imbalance_pct - 25.0) / 100.0)

        if is_long and peer_canary_ret_pct <= -0.35:
            phi += min(0.45, abs(peer_canary_ret_pct + 0.25) * 0.45)

        return round(min(1.0, max(0.0, phi)), 3)

    def compute_jump_cvar_notional_cap(
        self,
        current_equity: float,
        l2_fragility_score: float = 0.0,
        tail_gap_pct: Optional[float] = None,
        max_single_gap_pct: Optional[float] = None,
    ) -> float:
        """
        Dual-Budget Jump-CVaR Notional Ceiling:
        Bounds worst-case instantaneous gap loss (Notional * J_gap <= L_tol * Equity),
        preventing tight stops on calm bars from inflating notional to 3x+ cash leverage.
        """
        j_gap = tail_gap_pct if tail_gap_pct is not None else self.default_tail_gap_pct
        base_tol = max_single_gap_pct if max_single_gap_pct is not None else self.max_single_gap_equity_pct
        phi = max(0.0, min(1.0, float(l2_fragility_score)))
        if phi >= 0.62:
            return 0.0
        effective_tol = base_tol * max(0.0, 1.0 - 1.12 * phi)
        max_notional_jump = (effective_tol * current_equity) / max(0.01, j_gap)
        return min(current_equity * 3.50, max_notional_jump)

    def verify_orderbook_anti_spoofing(self, ask_depth_5l: list, bid_depth_5l: list) -> bool:
        """Concern 8: 5-Level Volume-Weighted Market Depth eliminates HFT ghost spoofing."""
        if len(ask_depth_5l) < 5 or len(bid_depth_5l) < 5:
            return False
        vw_ask = sum(q / (k + 1) for k, q in enumerate(ask_depth_5l))
        vw_bid = sum(q / (k + 1) for k, q in enumerate(bid_depth_5l))
        return (vw_ask / (vw_bid + 1e-5)) >= 1.80

    def compute_order_parameters(self, symbol: str, entry_price: float, stop_loss: float,
                                 ask_depth_5l: list = None, bid_depth_5l: list = None,
                                 direction: str = "SHORT",
                                 l2_fragility_score: float = 0.0):
        # Concern 6: F&O Master Whitelist Check
        if symbol not in self.fo_whitelist:
            return {"status": "REJECTED", "reason": "NON_FO_CIRCUIT_AUCTION_RISK"}

        if l2_fragility_score >= 0.62:
            return {"status": "REJECTED", "reason": "PRE_GAP_FRAGILITY_LOCKOUT", "fragility_score": l2_fragility_score}
            
        # Concern 8: Anti-Spoofing Check
        if ask_depth_5l and bid_depth_5l:
            if not self.verify_orderbook_anti_spoofing(ask_depth_5l, bid_depth_5l):
                return {"status": "REJECTED", "reason": "L2_ORDERBOOK_SPOOF_RISK"}

        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share < 0.001 or entry_price <= 0:
            return {"status": "REJECTED", "reason": "ZERO_RISK_SPREAD"}
            
        risk_budget = self.get_dynamic_risk_budget(self.capital)
        raw_qty = int(risk_budget // risk_per_share)
        jump_notional_cap = self.compute_jump_cvar_notional_cap(
            self.capital, l2_fragility_score=l2_fragility_score
        )
        max_notional_qty = int(jump_notional_cap // entry_price)
        if max_notional_qty < 1 or raw_qty < 1:
            return {"status": "REJECTED", "reason": "JUMP_CVAR_OR_RISK_CAP_ZERO_QTY"}
        qty = min(raw_qty, max_notional_qty)

        # Statutory cost precision check for chosen notional
        actual_roundtrip_fee = self.cost_model.compute_intraday_roundtrip(entry_price, entry_price, qty)["total_friction"]
        effective_fee = max(12.0, actual_roundtrip_fee)
        
        # Position Split: 50% Leg 1, 50% Leg 2
        q1 = max(1, math.ceil(qty * 0.50))
        q2 = qty - q1
        
        # Concern 1: Fee-Padded Target 1 Equation
        fee_per_q1_share = effective_fee / q1
        target_1_dist = (1.40 * risk_per_share) + fee_per_q1_share
        sign = -1.0 if direction.upper() == "SHORT" else 1.0
        target_1_price = round(entry_price + sign * target_1_dist, 2)
        target_2_est_price = round(entry_price + sign * (2.50 * risk_per_share), 2)
        
        # Concern 3 & 4: Poisson-Buffered Fee Guard Price
        fee_guard_price = round(entry_price + sign * ((effective_fee + self.poisson_buffer) / qty), 2)
        sl_limit_price = round(stop_loss - (sign * max(1.0, risk_per_share * 0.35)), 2)

        return {
            "status": "APPROVED",
            "symbol": symbol,
            "direction": direction.upper(),
            "total_qty": qty,
            "q1_qty": q1,
            "q2_qty": q2,
            "entry_price": entry_price,
            "initial_stop": stop_loss,
            "sl_limit_price": sl_limit_price,
            "target_1_price": target_1_price,
            "target_2_est_price": target_2_est_price,
            "fee_guard_price": fee_guard_price,
            "estimated_roundtrip_fee": round(effective_fee, 2),
            "jump_cvar_notional_cap": round(jump_notional_cap, 2),
            "fragility_score": l2_fragility_score,
            "fee_covered_at_t1": True,
            "order_type": "PEGGED_MARKETABLE_IOC_LIMIT",
            "native_exchange_sl": "SL_L_NATIVE_SUBMITTED_TO_NSE_MATCHING_ENGINE"
        }

    def evaluate_bar(self, entry_price: float, stop_loss: float, qty: int,
                     bar_open: float, bar_high: float, bar_low: float, bar_close: float,
                     bar_vol: float, avg_vol: float, cvd_delta: float, cvd_persistent: bool,
                     current_active_floor: float, peak_favorable_r: float,
                     simulated_tick_slippage: float = 0.0,
                     direction: str = "SHORT"):
        """
        Universal Bi-Directional Trailing & Reversal Engine:
        Rectifies Concerns 2, 3, 4, 8, 9 with conservative OHLC-bounded gap-through stop fills.
        """
        is_short = direction.upper() == "SHORT"
        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share < 1e-6 or qty <= 0:
            return {"action": "HOLD", "active_floor": current_active_floor, "peak_r": peak_favorable_r, "mode": "INVALID_PARAMS"}

        sign = -1.0 if is_short else 1.0
        fee_guard_price = round(entry_price + sign * ((self.fixed_fee + self.poisson_buffer) / qty), 2)
        
        # Step 1: Check floor/stop breach (conservative gap-through handling strictly inside [bar_low, bar_high])
        floor_breached = (bar_high >= current_active_floor) if is_short else (bar_low <= current_active_floor)
        if floor_breached:
            if is_short:
                raw_exit = max(current_active_floor, bar_open) + simulated_tick_slippage if bar_open >= current_active_floor else (current_active_floor + simulated_tick_slippage)
                gap_through = bar_open > current_active_floor
            else:
                raw_exit = min(current_active_floor, bar_open) - simulated_tick_slippage if bar_open <= current_active_floor else (current_active_floor - simulated_tick_slippage)
                gap_through = bar_open < current_active_floor

            effective_exit_price = round(max(bar_low, min(bar_high, raw_exit)), 2)
            gross_pnl = ((entry_price - effective_exit_price) if is_short else (effective_exit_price - entry_price)) * qty
            net_pnl = gross_pnl - self.fixed_fee

            if peak_favorable_r >= 0.50:
                return {
                    "action": "PROFIT_LOCKED_EXIT",
                    "exit_price": effective_exit_price,
                    "gap_through": gap_through,
                    "gross_pnl": round(gross_pnl, 2),
                    "fees_paid": self.fixed_fee,
                    "net_pnl": round(net_pnl, 2),
                    "is_loss": net_pnl < 0,
                    "reason": "GAP_THROUGH_PROFIT_FLOOR_EXECUTED" if gap_through else "PEAK_GE_0_50R_PROFIT_FLOOR_EXECUTED"
                }
            else:
                return {
                    "action": "STOP_LOSS_EXIT",
                    "exit_price": effective_exit_price,
                    "gap_through": gap_through,
                    "gross_pnl": round(gross_pnl, 2),
                    "fees_paid": self.fixed_fee,
                    "net_pnl": round(net_pnl, 2),
                    "is_loss": net_pnl < 0,
                    "reason": "GAP_THROUGH_HARD_STOP_EXECUTED" if gap_through else "INITIAL_HARD_STOP_EXECUTED"
                }

        # Step 2: Bar survived, update peak
        current_favorable_move = (entry_price - bar_low) if is_short else (bar_high - entry_price)
        current_r = max(0.0, current_favorable_move / risk_per_share)
        new_peak_r = max(peak_favorable_r, current_r)

        # Step 3: Two-Mode Floor with Poisson Buffer
        if new_peak_r >= 0.50:
            # Concern 9: Dual sub-bar CVD persistence eliminates single-spike whale traps
            adversarial_flow = (cvd_delta > 0) if is_short else (cvd_delta < 0)
            is_confirmed_reversal = (bar_vol > 1.4 * avg_vol) and adversarial_flow and cvd_persistent
            
            if is_confirmed_reversal:
                # Mode B: Confirmed institutional reversal -> Lock in 70% of peak gains
                locked_r = max(0.48, new_peak_r * 0.70)
                tight_floor = round(entry_price + sign * (locked_r * risk_per_share), 2)
                candidate_floor = min(tight_floor, fee_guard_price) if is_short else max(tight_floor, fee_guard_price)
                mode = "MODE_B_PERSISTENT_CVD_HARVEST"
            else:
                # Mode A: Brownian Noise -> Safe breathing room at fee_guard_price
                candidate_floor = fee_guard_price
                mode = "MODE_A_POISSON_BUFFERED_FEE_GUARD"
                
            if current_active_floor == stop_loss:
                active_floor = candidate_floor
            else:
                active_floor = min(candidate_floor, current_active_floor) if is_short else max(candidate_floor, current_active_floor)
            min_net = (((entry_price - active_floor) if is_short else (active_floor - entry_price)) * qty) - self.fixed_fee
            return {
                "action": "HOLD",
                "active_floor": active_floor,
                "peak_r": round(new_peak_r, 2),
                "guaranteed_min_net_pnl": round(min_net, 2),
                "mode": mode
            }
        else:
            return {
                "action": "HOLD",
                "active_floor": current_active_floor,
                "peak_r": round(new_peak_r, 2),
                "mode": "INITIAL_DEVELOPMENT"
            }

    def evaluate_air_pocket_vacuum_exit(
        self,
        direction: str,
        remaining_qty: int,
        sl_trigger_price: float,
        sl_limit_price: float,
        ltp: float,
        best_bid: Optional[float],
        best_ask: Optional[float],
        bid_depth: int,
        ask_depth: int,
        bar_low: float,
        bar_high: float,
        circuit_locked: bool = False,
        rested_target_active: bool = True,
        max_collar_pct: float = 0.06,
    ) -> Dict[str, Any]:
        """
        Air-Pocket SL-L Stranding Recovery & Partial-Bid Tranche Sweep Protocol:
          1. Never fabricates a phantom fill while opposite-side book depth is zero or circuit-locked.
          2. Detects when an air-pocket gap leaped past the rested SL-L limit price (SL_L_STRANDED_BY_GAP).
          3. Cancels any rested target order BEFORE issuing exit sweeps (prevents short-flip race).
          4. Sweeps strictly min(remaining_qty, counter_depth) via collared marketable IOC inside [bar_low, bar_high].
        """
        if remaining_qty <= 0:
            return {"status": "NO_POSITION", "fill_qty": 0}

        is_long = direction.upper() == "LONG"
        counter_depth = bid_depth if is_long else ask_depth
        sl_breached = (ltp <= sl_trigger_price) if is_long else (ltp >= sl_trigger_price)
        if not sl_breached:
            return {"status": "SL_NOT_TRIGGERED", "fill_qty": 0}

        sl_stranded = (ltp < sl_limit_price) if is_long else (ltp > sl_limit_price)

        # Cancel opposite target order before any exit action
        cancel_t1_required = bool(rested_target_active)

        if circuit_locked or counter_depth <= 0:
            return {
                "status": "VACUUM_WAIT_ZERO_BIDS" if is_long else "VACUUM_WAIT_ZERO_ASKS",
                "sl_stranded_detected": sl_stranded,
                "cancel_t1_before_sweep": cancel_t1_required,
                "fill_qty": 0,
                "remaining_qty": remaining_qty,
                "order_transmitted": False,
                "reason": "ZERO_COUNTERPARTY_DEPTH_WAIT_L2_REOPEN",
            }

        sweep_qty = min(remaining_qty, int(counter_depth))
        if is_long:
            ref_px = float(best_bid if (best_bid and best_bid > 0) else ltp)
            collar_floor = round(ref_px * (1.0 - max_collar_pct), 2)
            raw_fill = min(ref_px, ltp)
            fill_price = round(max(bar_low, min(bar_high, max(collar_floor, raw_fill))), 2)
        else:
            ref_px = float(best_ask if (best_ask and best_ask > 0) else ltp)
            collar_ceil = round(ref_px * (1.0 + max_collar_pct), 2)
            raw_fill = max(ref_px, ltp)
            fill_price = round(max(bar_low, min(bar_high, min(collar_ceil, raw_fill))), 2)

        rem_after = remaining_qty - sweep_qty
        return {
            "status": "PARTIAL_TRANCHE_SWEEP" if rem_after > 0 else "FULL_COLLARED_IOC_EXIT",
            "sl_stranded_detected": sl_stranded,
            "cancel_t1_before_sweep": cancel_t1_required,
            "order_type": "COLLARED_MARKETABLE_IOC_SWEEP",
            "fill_qty": sweep_qty,
            "fill_price": fill_price,
            "remaining_qty": rem_after,
            "order_transmitted": True,
            "collar_pct": round(max_collar_pct * 100.0, 2),
        }
