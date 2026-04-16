"""
Trade journal utilities — append JSONL entries for opens/closes and echo to stdout.
"""

import os
import json
from datetime import datetime
from typing import Any, Dict, Optional


class TradeJournal:
    def __init__(self, path: str, echo_stdout: bool = True):
        self.path = path
        self.echo = echo_stdout
        # Ensure directory exists
        d = os.path.dirname(self.path)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)

    def _write(self, record: Dict[str, Any]):
        line = json.dumps(record, separators=(",", ":"))
        try:
            with open(self.path, "a") as f:
                f.write(line + "\n")
        except Exception:
            # If file IO fails, at least echo
            pass
        if self.echo:
            print(f"[JOURNAL] {line}")

    @staticmethod
    def _trade_fields(trade) -> Dict[str, Any]:
        return {
            "instrument_id": getattr(trade, "instrument_id", None),
            "order_id": getattr(trade, "order_id", None),
            "position": getattr(getattr(trade, "position", None), "value", None) or getattr(trade, "position", None),
            "size": getattr(trade, "size", None),
            "entry_price": getattr(trade, "entry_price", None),
            "open_ts": getattr(trade, "timestamp", None).isoformat() if getattr(trade, "timestamp", None) else None,
            "close_at": getattr(trade, "close_at", None).isoformat() if getattr(trade, "close_at", None) else None,
            "post_id": getattr(trade, "post_id", None),
            "sentiment": getattr(trade, "sentiment", None),
        }

    def log_open(self, *, exchange: str, trader_class: str, trade, category: str,
                 source: Optional[str] = None, text_snippet: Optional[str] = None,
                 simulated: Optional[bool] = None, extra: Optional[Dict[str, Any]] = None):
        rec = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "event": "open",
            "exchange": exchange,
            "trader": trader_class,
            "category": category,
            "source": source,
            "text": (text_snippet or "")[:160],
            "simulated": bool(simulated),
        }
        rec.update(self._trade_fields(trade))
        if extra:
            rec.update(extra)
        self._write(rec)

    def log_close(self, *, exchange: str, trader_class: str, trade, pnl: float,
                  reason: str, simulated: Optional[bool] = None,
                  extra: Optional[Dict[str, Any]] = None):
        rec = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "event": "close",
            "exchange": exchange,
            "trader": trader_class,
            "reason": reason,
            "pnl": round(float(pnl or 0.0), 2),
            "simulated": bool(simulated),
        }
        rec.update(self._trade_fields(trade))
        if extra:
            rec.update(extra)
        self._write(rec)

