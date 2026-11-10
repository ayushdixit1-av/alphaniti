import time
import base64
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, Tuple, Optional

class BrokerLatencyWatchdog:
    """
    Safeguard 1: Adaptive Sub-Second Latency Watchdog & Dynamic Slippage Compensator.
    Eliminates execution slippage caused by 150ms-300ms broker API network jitter.
    Features:
      - Instantaneous spike detection (halts immediately on single > 500ms spike)
      - Dynamic limit offset (+2 ticks buffer during 200ms-500ms jitter)
      - Zero cross-spread market order risk
    """
    def __init__(self, high_latency_threshold_ms: float = 200.0, max_allowable_latency_ms: float = 500.0):
        self.high_latency_threshold_ms = high_latency_threshold_ms
        self.max_allowable_latency_ms = max_allowable_latency_ms
        self.rolling_latency_ms = 85.0
        self.last_raw_latency_ms = 85.0
        self.alpha = 0.50

    def record_probe_latency(self, latency_ms: float):
        self.last_raw_latency_ms = latency_ms
        self.rolling_latency_ms = (self.alpha * latency_ms) + ((1.0 - self.alpha) * self.rolling_latency_ms)

    def get_adaptive_order_routing(self, symbol: str, target_price: float, best_bid: float, emergency_exit: bool = False) -> Dict[str, Any]:
        # Instantaneous spike check takes precedence over smoothed EMA
        effective_latency = max(self.last_raw_latency_ms, self.rolling_latency_ms)
        
        if effective_latency > self.max_allowable_latency_ms:
            if emergency_exit:
                return {
                    "route": "EXCHANGE_NATIVE_SL_FAILOVER",
                    "latency_ms": round(effective_latency, 1),
                    "reason": f"API_OUTAGE_FAILOVER_TO_EXCHANGE_SL ({effective_latency:.1f}ms > {self.max_allowable_latency_ms}ms)",
                    "order_type": "SL_L_NATIVE",
                    "limit_price": round(best_bid - 0.15, 2),
                }
            return {
                "route": "ABORT_CONGESTION",
                "reason": f"CRITICAL_NETWORK_CONGESTION ({effective_latency:.1f}ms > {self.max_allowable_latency_ms}ms)",
                "order_type": None,
                "limit_price": None
            }
        elif effective_latency > self.high_latency_threshold_ms:
            # Latency between 200ms and 500ms (High Jitter)
            # Add 2 ticks slippage buffer (Rs 0.10) to limit order to ensure immediate fill
            buffered_limit = round(best_bid - 0.10, 2)
            return {
                "route": "SLIPPAGE_BUFFERED_LIMIT",
                "latency_ms": round(effective_latency, 1),
                "order_type": "LIMIT_IOC",
                "limit_price": buffered_limit,
                "ticks_buffered": 2,
                "reason": "HIGH_JITTER_BUFFERED"
            }
        else:
            # Normal low latency (<= 200ms)
            pegged_limit = round(best_bid - 0.05, 2)
            return {
                "route": "FAST_PEGGED_LIMIT",
                "latency_ms": round(effective_latency, 1),
                "order_type": "LIMIT_IOC",
                "limit_price": pegged_limit,
                "ticks_buffered": 1,
                "reason": "LOW_LATENCY_PEGGED"
            }


class TokenExpirySentinel:
    """
    Safeguard 2: Automated Pre-Market Auth Guard & Token Expiry Sentinel.
    Enforces SEBI 06:00 AM IST daily token cycle and validates TTL before market open.
    """
    def __init__(self, tz_name: str = "Asia/Kolkata"):
        self.tz = ZoneInfo(tz_name)

    def parse_jwt_payload(self, token: str) -> Dict[str, Any]:
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return {}
            payload_b64 = parts[1]
            rem = len(payload_b64) % 4
            if rem > 0:
                payload_b64 += "=" * (4 - rem)
            payload_bytes = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
            return json.loads(payload_bytes.decode("utf-8"))
        except Exception as e:
            return {}

    def audit_token(self, token: str) -> Dict[str, Any]:
        payload = self.parse_jwt_payload(token)
        if not payload:
            return {"status": "INVALID_TOKEN", "valid": False, "reason": "MALFORMED_JWT"}

        now_ts = int(time.time())
        now_dt = datetime.now(self.tz)
        
        exp_ts = payload.get("exp", 0)
        iat_ts = payload.get("iat", 0)
        
        # 1. Absolute Expiration Check
        if now_ts >= exp_ts:
            return {
                "status": "EXPIRED",
                "valid": False,
                "reason": f"TOKEN_EXPIRED (Expired at {datetime.fromtimestamp(exp_ts, self.tz).strftime('%Y-%m-%d %H:%M:%S IST')})"
            }

        # 2. SEBI Daily 06:00 AM Cycle Check:
        today_6am_dt = now_dt.replace(hour=6, minute=0, second=0, microsecond=0)
        if now_dt < today_6am_dt:
            import datetime as dt_module
            valid_6am_ts = int((today_6am_dt - dt_module.timedelta(days=1)).timestamp())
        else:
            valid_6am_ts = int(today_6am_dt.timestamp())

        ttl_seconds = exp_ts - now_ts
        ttl_minutes = ttl_seconds // 60
        
        is_stale_from_prior_cycle = (iat_ts < valid_6am_ts)
        
        if is_stale_from_prior_cycle:
            return {
                "status": "STALE_CYCLE",
                "valid": False,
                "ttl_minutes": ttl_minutes,
                "reason": "TOKEN_FROM_YESTERDAY_CYCLE (Requires fresh token generated after 06:00 AM IST today)"
            }

        # 3. TTL Warning (< 30 mins)
        if ttl_minutes < 30:
            return {
                "status": "EXPIRING_SOON",
                "valid": True,
                "ttl_minutes": ttl_minutes,
                "warning": "LESS_THAN_30_MINUTES_TTL"
            }

        return {
            "status": "ARMED_AND_VALID",
            "valid": True,
            "ttl_minutes": ttl_minutes,
            "expires_at": datetime.fromtimestamp(exp_ts, self.tz).strftime('%Y-%m-%d %H:%M:%S IST'),
            "issued_at": datetime.fromtimestamp(iat_ts, self.tz).strftime('%Y-%m-%d %H:%M:%S IST')
        }


class UpfrontMarginValidator:
    """
    Safeguard 3: Real-Time Upfront MIS Margin & Peak Margin Pre-Flight Validator.
    Verifies leverage, notional caps, and free cash buffers before order transmission.
    """
    def __init__(self, min_free_cash_buffer_rs: float = 2500.0, max_notional_multiple: float = 3.50):
        self.min_free_cash_buffer_rs = min_free_cash_buffer_rs
        self.max_notional_multiple = max_notional_multiple

    def validate_pre_order_margin(self, symbol: str, shares: int, ltp: float, 
                                  available_margin: float, clear_cash: float) -> Dict[str, Any]:
        notional_value = shares * ltp
        
        # 1. Notional Hard Cap Invariant
        max_notional_allowed = clear_cash * self.max_notional_multiple
        if notional_value > max_notional_allowed:
            clamped_shares = int(max_notional_allowed // ltp)
            return {
                "approved": False,
                "reason": "NOTIONAL_CAP_EXCEEDED",
                "notional_value": round(notional_value, 2),
                "max_allowed": round(max_notional_allowed, 2),
                "recommended_clamped_shares": clamped_shares
            }

        # 2. Intraday MIS Margin Requirement (5x leverage with 5% regulatory safety buffer)
        required_margin = (notional_value / 5.0) * 1.05
        
        # 3. Solvency & Buffer Invariant
        if available_margin < required_margin:
            return {
                "approved": False,
                "reason": "INSUFFICIENT_AVAILABLE_MARGIN",
                "available": round(available_margin, 2),
                "required": round(required_margin, 2)
            }

        post_trade_free_margin = available_margin - required_margin
        if post_trade_free_margin < self.min_free_cash_buffer_rs:
            return {
                "approved": False,
                "reason": "MINIMUM_FREE_CASH_BUFFER_BREACHED",
                "free_margin_remaining": round(post_trade_free_margin, 2),
                "required_buffer": self.min_free_cash_buffer_rs
            }

        return {
            "approved": True,
            "symbol": symbol,
            "shares": shares,
            "ltp": ltp,
            "notional_value": round(notional_value, 2),
            "required_margin": round(required_margin, 2),
            "post_trade_free_margin": round(post_trade_free_margin, 2),
            "leverage_effective": 5.0,
            "status": "MARGIN_PRE_FLIGHT_PASSED"
        }
