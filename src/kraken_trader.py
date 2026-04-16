"""
Kraken trading execution - BTC perpetual futures via Kraken Futures API.
Uses HMAC-SHA512 authentication.
"""

import os
import hmac
import hashlib
import base64
import time
import requests
import json
from enum import Enum
from typing import Optional, List
from dataclasses import dataclass
from datetime import datetime
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class Position(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class Trade:
    position: Position
    size: float
    entry_price: float
    timestamp: datetime
    post_id: str
    sentiment: str
    order_id: str = None
    instrument_id: str = None
    close_at: float = None


class KrakenAPIError(Exception):
    pass


class KrakenTrader:
    """
    Executes BTC perpetual futures trades on Kraken based on sentiment signals.
    Uses HMAC-SHA512 signed requests to the Kraken Futures API.
    """

    DEFAULT_SIZE = 100  # USD
    MAX_SIZE = 1000     # USD
    API_BASE_URL = "https://futures.kraken.com"
    BTC_PERP_PRODUCT_ID = "PI_XBTUSD"  # Kraken perpetual XBT/USD

    def __init__(self, api_key: str = None, api_secret: str = None):
        self.api_key = os.getenv("KRAKEN_API_KEY") or api_key
        self.api_secret = os.getenv("KRAKEN_API_SECRET") or api_secret
        self.simulation_mode = not (self.api_key and self.api_secret)
        self._session = requests.Session()

        if not self.simulation_mode:
            try:
                # Test with a simpler endpoint - get available contracts
                self._make_request("GET", "/instruments", params={"type": "future"})
                logger.info("✅ Kraken credentials validated")
            except KrakenAPIError as e:
                logger.warning(f"⚠️ Kraken credentials invalid: {e}")
                logger.warning("Running in SIMULATION MODE")
                self.simulation_mode = True
        else:
            logger.warning("⚠️ Running in SIMULATION MODE - no real trades")

    def _sign(self, path: str, post_data: str = "") -> str:
        """Generate HMAC-SHA512 signature for Kraken API."""
        # Kraken uses SHA512 of path + post_data as the message
        secret = base64.b64decode(self.api_secret)
        message = path.encode('utf-8') + post_data.encode('utf-8')
        
        h = hmac.new(secret, message, hashlib.sha512)
        signature = base64.b64encode(h.digest()).decode('utf-8')
        return signature

    def _make_request(self, method: str, endpoint: str, data: dict = None, params: dict = None) -> dict:
        path = f"/derivatives/api/v4{endpoint}"
        
        # Add query string for GET requests
        if params:
            query_string = "&".join(f"{k}={v}" for k, v in params.items())
            path = f"{path}?{query_string}"
        
        post_data = json.dumps(data) if data else ""
        
        headers = {
            "Content-Type": "application/json",
            "APIKey": self.api_key,
        }
        
        if not self.simulation_mode:
            headers["Auth"] = self._sign(path, post_data)

        url = f"{self.API_BASE_URL}{path}"

        try:
            response = self._session.request(
                method, url, headers=headers,
                data=post_data if data else None,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            try:
                error_data = e.response.json()
                raise KrakenAPIError(f"Kraken API error: {error_data}")
            except KrakenAPIError:
                raise
            except Exception:
                raise KrakenAPIError(f"HTTP error: {e}")

    def get_current_btc_price(self) -> float:
        try:
            resp = self._session.get(
                f"{self.API_BASE_URL}/derivatives/api/v3/tickers",
                params={"symbol": self.BTC_PERP_PRODUCT_ID},
                timeout=10
            )
            data = resp.json()
            if data.get("tickers") and len(data["tickers"]) > 0:
                return float(data["tickers"][0].get("last", 0))
        except Exception:
            pass
        # Fallback to spot price
        try:
            resp = self._session.get(
                "https://api.kraken.com/0/public/Ticker",
                params={"pair": "XBTUSD"},
                timeout=10
            )
            data = resp.json()
            if data.get("error") == [] and data.get("result"):
                result = list(data["result"].values())[0]
                return float(result["c"][0])  # Close price
        except Exception:
            pass
        return 67000.0

    def get_account_balance(self) -> dict:
        return self._make_request("GET", "/accounts")

    def execute_trade(self, sentiment: str, post_id: str, size_usd: float = None, trade_type: str = "perpetual") -> Optional[Trade]:
        if sentiment == "neutral":
            print("Neutral sentiment - no trade")
            return None

        size_usd = min(size_usd or self.DEFAULT_SIZE, self.MAX_SIZE)
        current_price = self.get_current_btc_price()

        if sentiment == "bellicose":
            side = "sell"
            position = Position.SHORT
            action = "SHORT (sell)"
        else:
            side = "buy"
            position = Position.LONG
            action = "LONG (buy)"

        # Calculate quantity in contracts (1 contract = 1 USD of notional)
        quantity = size_usd  # Kraken uses USD notional

        if self.simulation_mode:
            order_id = f"SIM-{int(time.time())}"
            print(f"📋 [SIMULATED] {action} {quantity} contracts of {self.BTC_PERP_PRODUCT_ID}")
            print(f"   Post: {post_id}, Sentiment: {sentiment}")
        else:
            try:
                result = self._make_request("POST", "/orders", {
                    "symbol": self.BTC_PERP_PRODUCT_ID,
                    "side": side,
                    "orderType": "market",
                    "size": str(int(quantity))
                })
                order_id = result.get("orderId", f"SIM-{int(time.time())}")
                print(f"🔒 [LIVE] {action} {quantity} contracts of {self.BTC_PERP_PRODUCT_ID}")
                print(f"   Order ID: {order_id}")
            except KrakenAPIError as e:
                logger.error(f"Failed to place order: {e}")
                return None

        return Trade(
            position=position,
            size=quantity,
            entry_price=current_price,
            timestamp=datetime.now(),
            post_id=post_id,
            sentiment=sentiment,
            order_id=order_id,
            instrument_id=self.BTC_PERP_PRODUCT_ID,
        )

    def close_position(self, trade: Trade) -> float:
        current_price = self.get_current_btc_price()

        if trade.position == Position.LONG:
            pnl = (current_price - trade.entry_price) * trade.size
            side = "sell"
        else:
            pnl = (trade.entry_price - current_price) * trade.size
            side = "buy"

        if self.simulation_mode:
            print(f"📋 [SIMULATED] Closed at ${current_price:,.2f}, PnL: ${pnl:.2f}")
        else:
            try:
                self._make_request("POST", "/orders", {
                    "symbol": trade.instrument_id,
                    "side": side,
                    "orderType": "market",
                    "size": str(int(trade.size))
                })
                print(f"🔒 [LIVE] Closed at ${current_price:,.2f}, PnL: ${pnl:.2f}")
            except KrakenAPIError as e:
                logger.error(f"Failed to close position: {e}")

        return pnl


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    trader = KrakenTrader()
    print(f"\nBTC price: ${trader.get_current_btc_price():,.2f}")

    print("\n--- Test Perpetual Trades ---")
    trader.execute_trade("bellicose", "test-1", 100)
    trader.execute_trade("conciliatory", "test-2", 100)

    print("\n--- Test Neutral ---")
    trader.execute_trade("neutral", "test-3")