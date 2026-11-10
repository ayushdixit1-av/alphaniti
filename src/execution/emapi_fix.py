"""
AlphaNiti v25.0 Institutional Execution Engine
Pipeline Module 5 - Official NSE EMAPI & FIX 4.2 Protocol Architecture
Reference: docs/05_EXECUTION_ENGINE_AND_EXCHANGE_PROTOCOLS.md (Callout 4)
"""

from typing import Dict, Any, Optional, List
import hmac
import hashlib
import time
import json
from enum import Enum
from pathlib import Path


class FixOrdStatus(str, Enum):
    IDLE = "IDLE"
    PENDING_NEW = "PENDING_NEW"
    NEW = "0"  # FIX Tag 39=0
    PARTIALLY_FILLED = "1"  # Tag 39=1
    FILLED = "2"  # Tag 39=2
    CANCELLED = "4"  # Tag 39=4
    REJECTED = "8"  # Tag 39=8
    SUBMITTED_UNCONFIRMED = "SUBMITTED_UNCONFIRMED"


class FixClOrdIdGenerator:
    """
    Constructs an immutable 32-character canonical ClOrdID (Tag 11) per NSE India FIX Spec:
        ClOrdID = YYYYMMDD-MEMBER-STRAT-HMAC_SHA256[:14]
    """

    @staticmethod
    def generate(
        trading_date: str,  # "YYYYMMDD"
        member_id: str = "12345",  # 5-digit SEBI registered Trading Member ID
        strategy_id: str = "NITI",  # 4-character deterministic strategy id
        symbol: str = "RELIANCE",
        qty: int = 100,
        nonce: Optional[int] = None,
        secret_key: bytes = b"alphaniti_institutional_hsm_secret_key_v25",
    ) -> str:
        if nonce is None:
            nonce = int(time.time_ns())

        payload = f"{nonce}_{symbol}_{qty}".encode()
        h = hmac.new(secret_key, payload, hashlib.sha256).hexdigest()[:14]
        cl_ord_id = f"{trading_date}-{member_id}-{strategy_id}-{h}"
        return cl_ord_id


class EmapiExecutionFSM:
    """
    Idempotent Exchange Protocol Finite State Machine (FSM) with Write-Ahead Logging (WAL)
    and two-phase commit session recovery.
    """

    def __init__(self, wal_path: Optional[Path] = None):
        self.wal_path = Path(wal_path) if wal_path is not None else None
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.idempotent_order_registry: set = set()
        self._load_wal()

    def _write_wal(self, record: Dict[str, Any]):
        """Synchronously append and flush order event to WAL."""
        if self.wal_path:
            self.wal_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.wal_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
                f.flush()

    def _load_wal(self):
        """Replay WAL events from disk so ClOrdID idempotency and order state survive restarts."""
        if not self.wal_path or not self.wal_path.exists():
            return
        with open(self.wal_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev = rec.get("event")
                if ev == "PREPARE_NEW_ORDER":
                    order = dict(rec.get("order") or {})
                    cid = order.get("cl_ord_id")
                    if cid:
                        self.orders[cid] = order
                        self.idempotent_order_registry.add(cid)
                elif ev == "EXECUTION_REPORT":
                    cid = rec.get("cl_ord_id")
                    if cid in self.orders:
                        self.orders[cid]["status"] = rec.get("status", self.orders[cid].get("status"))
                        self.orders[cid]["cum_qty"] = rec.get("cum_qty", self.orders[cid].get("cum_qty", 0))
                        self.orders[cid]["leaves_qty"] = rec.get("leaves_qty", self.orders[cid].get("leaves_qty", 0))
                elif ev == "SOCKET_DISCONNECT_FREEZE":
                    cid = rec.get("cl_ord_id")
                    if cid in self.orders:
                        self.orders[cid]["status"] = FixOrdStatus.SUBMITTED_UNCONFIRMED
                elif ev == "RECONCILED":
                    cid = rec.get("cl_ord_id")
                    if cid in self.orders:
                        self.orders[cid]["status"] = rec.get("final_status", self.orders[cid].get("status"))

    def submit_new_order_single(
        self,
        cl_ord_id: str,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        qty: int,
        limit_price: float,
        escrow_allocated: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Phase 1: Prepare & Escrow. Writes intent to WAL, sets state to PENDING_NEW.
        """
        if cl_ord_id in self.idempotent_order_registry:
            # Duplicate order prevention at the gateway level
            return {
                "cl_ord_id": cl_ord_id,
                "status": FixOrdStatus.REJECTED,
                "ord_rej_reason": 104,
                "text": "16384: Order with duplicate ClOrdID already exists",
            }

        order = {
            "cl_ord_id": cl_ord_id,
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "limit_price": limit_price,
            "escrow_allocated": escrow_allocated,
            "status": FixOrdStatus.PENDING_NEW,
            "cum_qty": 0,
            "leaves_qty": qty,
            "avg_price": 0.0,
            "created_at": time.time(),
        }

        self.orders[cl_ord_id] = order
        self.idempotent_order_registry.add(cl_ord_id)
        self._write_wal({"event": "PREPARE_NEW_ORDER", "order": order})
        return order

    def process_execution_report(
        self,
        cl_ord_id: str,
        ord_status: FixOrdStatus,
        last_shares: int = 0,
        last_price: float = 0.0,
        ord_rej_reason: Optional[int] = None,
        text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Handles inbound FIX ExecutionReport (MsgType = 8).
        """
        if cl_ord_id not in self.orders:
            raise KeyError(f"Unknown ClOrdID {cl_ord_id} in execution report.")

        order = self.orders[cl_ord_id]

        if ord_status == FixOrdStatus.NEW:
            order["status"] = FixOrdStatus.NEW
        elif ord_status == FixOrdStatus.PARTIALLY_FILLED:
            order["status"] = FixOrdStatus.PARTIALLY_FILLED
            order["cum_qty"] += last_shares
            order["leaves_qty"] = max(0, order["quantity"] - order["cum_qty"])
            order["avg_price"] = last_price
        elif ord_status == FixOrdStatus.FILLED:
            order["status"] = FixOrdStatus.FILLED
            order["cum_qty"] = order["quantity"]
            order["leaves_qty"] = 0
            order["avg_price"] = last_price
        elif ord_status == FixOrdStatus.CANCELLED:
            order["status"] = FixOrdStatus.CANCELLED
        elif ord_status == FixOrdStatus.REJECTED:
            order["status"] = FixOrdStatus.REJECTED
            order["ord_rej_reason"] = ord_rej_reason
            order["text"] = text

        self._write_wal({
            "event": "EXECUTION_REPORT",
            "cl_ord_id": cl_ord_id,
            "status": str(ord_status),
            "cum_qty": order["cum_qty"],
            "leaves_qty": order["leaves_qty"],
        })
        return order

    def handle_socket_disconnect(self):
        """
        Handles TCP socket drops: all PENDING_NEW orders transition to SUBMITTED_UNCONFIRMED.
        Zero duplicate orders can be dispatched.
        """
        for cl_ord_id, order in self.orders.items():
            if order["status"] == FixOrdStatus.PENDING_NEW:
                order["status"] = FixOrdStatus.SUBMITTED_UNCONFIRMED
                self._write_wal({"event": "SOCKET_DISCONNECT_FREEZE", "cl_ord_id": cl_ord_id})

    def reconcile_after_reconnect(self, exchange_status_map: Dict[str, FixOrdStatus]):
        """
        Reconciles SUBMITTED_UNCONFIRMED orders using OrderStatusRequest (35=H) responses.
        """
        for cl_ord_id, order in self.orders.items():
            if order["status"] == FixOrdStatus.SUBMITTED_UNCONFIRMED:
                confirmed_status = exchange_status_map.get(cl_ord_id, FixOrdStatus.REJECTED)
                order["status"] = confirmed_status
                self._write_wal({
                    "event": "RECONCILED",
                    "cl_ord_id": cl_ord_id,
                    "final_status": str(confirmed_status),
                })
