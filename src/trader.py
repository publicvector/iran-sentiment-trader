"""
Coinbase trading execution - spot BTC trading via Advanced Trade API.
"""

import os
import hmac
import hashlib
import base64
import time
import requests
from enum import Enum
from typing import Optional
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
    """Represents a trade execution."""
    position: Position
    size_usd: float
    entry_price: float
    timestamp: datetime
    post_id: str
    sentiment: str
    order_id: str = None
    close_at: datetime = None


class CoinbaseAPIError(Exception):
    """Custom exception for Coinbase API errors."""
    pass


class CoinbaseOptionsTrader:
    """
    Executes Bitcoin trades on Coinbase based on sentiment signals.
    Uses Coinbase Advanced Trade API (spot trading).

    Note: For options trading, you'd need specific options permissions.
    This implementation uses spot BTC trading.
    """

    DEFAULT_POSITION_SIZE = 100  # USD
    MAX_POSITION_SIZE = 1000    # USD

    API_BASE_URL = "https://api.coinbase.com"
    BTC_PRODUCT_ID = "BTC-USD"

    def __init__(self, api_key: str = None, api_secret: str = None):
        self.api_key = api_key or os.getenv("COINBASE_API_KEY")
        self.api_secret = api_secret or os.getenv("COINBASE_API_SECRET")
        self.simulation_mode = not (self.api_key and self.api_secret)

        self._session = requests.Session()

        if not self.simulation_mode:
            # Verify credentials work
            try:
                self._get_accounts()
                logger.info("✅ Coinbase credentials validated")
            except CoinbaseAPIError as e:
                logger.warning(f"⚠️ Coinbase credentials invalid: {e}")
                self.simulation_mode = True
        else:
            logger.warning("⚠️ Running in SIMULATION MODE - no real trades")

    def _sign_request(self, method: str, path: str, body: str = "") -> dict:
        """Generate authentication headers for Coinbase API."""
        if not self.api_key or not self.api_secret:
            raise CoinbaseAPIError("API key or secret not set")

        timestamp = str(int(time.time()))
        message = timestamp + method + path + body

        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()

        return {
            "CB-ACCESS-KEY": self.api_key,
            "CB-ACCESS-SIGN": base64.b64encode(signature).decode('utf-8'),
            "CB-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

    def _make_request(self, method: str, endpoint: str, data: dict = None) -> dict:
        """Make authenticated request to Coinbase API."""
        path = f"/api/v3/brokerage{endpoint}"
        body = ""

        if data:
            import json
            body = json.dumps(data)

        headers = self._sign_request(method, path, body)
        url = f"{self.API_BASE_URL}{path}"

        try:
            response = self._session.request(method, url, headers=headers, json=data, timeout=30)
            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            try:
                error_data = e.response.json()
                raise CoinbaseAPIError(f"Coinbase API error: {error_data}")
            except:
                raise CoinbaseAPIError(f"HTTP error: {e}")

    def _get_accounts(self) -> list:
        """Get user accounts."""
        return self._make_request("GET", "/accounts")

    def get_current_btc_price(self) -> float:
        """Get current Bitcoin price from Coinbase."""
        try:
            # Use public API v2 for price (no auth required)
            response = self._session.get(
                f"{self.API_BASE_URL}/v2/prices/BTC-USD/spot",
                timeout=10
            )
            data = response.json()
            return float(data.get("data", {}).get("amount", 0))
        except Exception as e:
            logger.warning(f"Failed to get BTC price: {e}")
            # Fallback to a reasonable default
            return 67000.0

    def place_order(
        self,
        side: str,  # "BUY" or "SELL"
        order_type: str = "MARKET",
        product_id: str = "BTC-USD",
        quote_size: float = None  # USD amount
    ) -> dict:
        """
        Place an order on Coinbase.

        Args:
            side: "BUY" or "SELL"
            order_type: "MARKET" or "LIMIT"
            product_id: Product ID (default: BTC-USD)
            quote_size: Amount in USD to spend

        Returns:
            Order response dict
        """
        if self.simulation_mode:
            return {"order_id": "SIMULATED", "side": side, "product_id": product_id}

        order_config = {
            "quote_size": f"{quote_size:.2f}"
        }

        if order_type == "LIMIT":
            order_config["limit_price"] = f"{self.get_current_btc_price():.2f}"

        order = {
            "side": side,
            "product_id": product_id,
            "order_configuration": order_config
        }

        return self._make_request("POST", "/orders", order)

    def execute_trade(
        self,
        sentiment: str,
        post_id: str,
        size_usd: float = None
    ) -> Optional[Trade]:
        """
        Execute a trade based on sentiment.

        Args:
            sentiment: "bellicose" or "conciliatory"
            post_id: ID of the post that triggered the trade
            size_usd: Trade size in USD (default: DEFAULT_POSITION_SIZE)

        Returns:
            Trade object if executed, None if skipped
        """
        if sentiment == "neutral":
            print(f"Neutral sentiment - no trade")
            return None

        if size_usd is None:
            size_usd = self.DEFAULT_POSITION_SIZE

        # Clamp position size
        size_usd = min(size_usd, self.MAX_POSITION_SIZE)

        # Determine position based on sentiment
        if sentiment == "bellicose":
            position = Position.SHORT
            side = "SELL"
            action = "SHORT (SELL)"
        else:  # conciliatory
            position = Position.LONG
            side = "BUY"
            action = "LONG (BUY)"

        # Get current price
        btc_price = self.get_current_btc_price()

        logger.info(f"Executing {action} {size_usd} USD of BTC at ${btc_price:,.2f}")

        if self.simulation_mode:
            order_id = "SIMULATED-ORDER"
            print(f"📋 [SIMULATED] {action} {size_usd} USD of BTC at ${btc_price:,.2f}")
            print(f"   Post: {post_id}, Sentiment: {sentiment}")
        else:
            try:
                result = self.place_order(side=side, quote_size=size_usd)
                order_id = result.get("order_id", "UNKNOWN")
                print(f"🔒 [LIVE] {action} {size_usd} USD of BTC at ${btc_price:,.2f}")
                print(f"   Order ID: {order_id}")
            except CoinbaseAPIError as e:
                logger.error(f"Failed to place order: {e}")
                return None

        trade = Trade(
            position=position,
            size_usd=size_usd,
            entry_price=btc_price,
            timestamp=datetime.now(),
            post_id=post_id,
            sentiment=sentiment,
            order_id=order_id
        )

        return trade

    def close_position(self, trade: Trade) -> float:
        """
        Close an existing position.

        Returns:
            PnL in USD
        """
        current_price = self.get_current_btc_price()

        if trade.position == Position.LONG:
            # Close long by selling
            side = "SELL"
            pnl = (current_price - trade.entry_price) * (trade.size_usd / trade.entry_price)
        else:  # SHORT
            # Close short by buying
            side = "BUY"
            pnl = (trade.entry_price - current_price) * (trade.size_usd / trade.entry_price)

        if self.simulation_mode:
            print(f"📋 [SIMULATED] Closed position at ${current_price:,.2f}, PnL: ${pnl:.2f}")
        else:
            try:
                self.place_order(side=side, quote_size=trade.size_usd)
                print(f"🔒 [LIVE] Closed position at ${current_price:,.2f}, PnL: ${pnl:.2f}")
            except CoinbaseAPIError as e:
                logger.error(f"Failed to close position: {e}")
                print(f"⚠️ Failed to close position: {e}")

        return pnl


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    trader = CoinbaseOptionsTrader()

    print(f"\nCurrent BTC price: ${trader.get_current_btc_price():,.2f}\n")

    # Test trades
    print("Testing bellicose trade (SHORT):")
    trader.execute_trade("bellicose", "post-123", 100)

    print("\nTesting conciliatory trade (LONG):")
    trader.execute_trade("conciliatory", "post-456", 100)

    print("\nTesting neutral (should skip):")
    trader.execute_trade("neutral", "post-789")