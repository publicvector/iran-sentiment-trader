"""
OKX trading execution - supports both perpetuals and options.
OKX is US-friendly and has a full API.
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
    LONG = "long"   # Buy / Long
    SHORT = "short" # Sell / Short


@dataclass
class Trade:
    """Represents a trade execution."""
    position: Position
    size: float
    entry_price: float
    timestamp: datetime
    post_id: str
    sentiment: str
    order_id: str = None
    instrument_id: str = None


class OKXAPIError(Exception):
    """Custom exception for OKX API errors."""
    pass


class OKXTrader:
    """
    Executes BTC trades on OKX - supports both perpetuals and options.
    OKX is US-friendly and has a full trading API.
    """

    DEFAULT_SIZE = 100  # USD
    MAX_SIZE = 1000

    API_BASE_URL = "https://www.okx.com/api/v5"

    # Instrument IDs for BTC perpetuals
    BTC_PERPETUAL = "BTC-USD_UM-SWAP"  # Linear perpetual (USDC settled)

    def __init__(self, api_key: str = None, api_secret: str = None, passphrase: str = None):
        self.api_key = api_key or os.getenv("OKX_API_KEY")
        self.api_secret = api_secret or os.getenv("OKX_API_SECRET")
        self.passphrase = passphrase or os.getenv("OKX_PASSPHRASE")
        self.simulation_mode = not (self.api_key and self.api_secret)

        self._session = requests.Session()

        if self.simulation_mode:
            logger.warning("⚠️ Running in SIMULATION MODE - no real trades")
        else:
            # Verify credentials
            try:
                self._get_account_balance()
                logger.info("✅ OKX credentials validated")
            except OKXAPIError as e:
                logger.warning(f"⚠️ OKX credentials invalid: {e}")
                self.simulation_mode = True

    def _sign(self, method: str, path: str, body: str = "") -> dict:
        """Generate OKX authentication signature."""
        timestamp = str(int(time.time()))
        message = timestamp + method + path + body

        signature = base64.b64encode(
            hmac.new(
                self.api_secret.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode('utf-8')

        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json"
        }

    def _make_request(self, method: str, endpoint: str, params: dict = None, body: dict = None) -> dict:
        """Make authenticated request to OKX API."""
        if self.simulation_mode:
            return {}

        headers = self._sign(method, endpoint, json.dumps(body) if body else "")
        url = f"{self.API_BASE_URL}{endpoint}"

        try:
            response = self._session.request(method, url, headers=headers, json=body, params=params, timeout=30)
            data = response.json()

            if data.get("code") != "0":
                raise OKXAPIError(f"OKX error: {data.get('msg', 'Unknown')}")

            return data.get("data", [])

        except requests.exceptions.RequestException as e:
            raise OKXAPIError(f"Request failed: {e}")

    def _get_account_balance(self) -> dict:
        """Get account balance."""
        return self._make_request("GET", "/account/balance")

    def get_current_btc_price(self) -> float:
        """Get current BTC price."""
        try:
            # Use public ticker endpoint
            resp = requests.get(
                f"{self.API_BASE_URL}/market/ticker",
                params={"instId": self.BTC_PERPETUAL},
                timeout=10
            )
            data = resp.json()
            if data.get("data"):
                return float(data["data"][0]["last"])
        except Exception as e:
            logger.warning(f"Failed to get BTC price: {e}")

        # Fallback
        return 67000.0

    def get_perpetual_price(self) -> float:
        """Get BTC perpetual price."""
        return self.get_current_btc_price()

    def get_option_price(self, inst_id: str) -> float:
        """Get option price."""
        try:
            resp = requests.get(
                f"{self.API_BASE_URL}/market/ticker",
                params={"instId": inst_id},
                timeout=10
            )
            data = resp.json()
            if data.get("data"):
                return float(data["data"][0]["last"])
        except Exception as e:
            logger.warning(f"Failed to get option price: {e}")
        return 0.0

    def get_available_options(self, expiry_hours: int = 24) -> List[dict]:
        """Get available BTC options expiring within specified hours."""
        try:
            resp = requests.get(
                f"{self.API_BASE_URL}/public/instruments",
                params={"instType": "OPTION", "uly": "BTC-USD"},
                timeout=10
            )
            data = resp.json()

            if not data.get("data"):
                return []

            options = []
            now = time.time() * 1000  # milliseconds

            for opt in data["data"]:
                exp_time = opt.get("expTime")
                if exp_time:
                    hours_until = (int(exp_time) - now) / (1000 * 60 * 60)
                    if 0 < hours_until <= expiry_hours:
                        options.append(opt)

            return options

        except Exception as e:
            logger.warning(f"Failed to get options: {e}")
            return []

    def execute_perpetual_trade(
        self,
        sentiment: str,
        post_id: str,
        size_usd: float = None
    ) -> Optional[Trade]:
        """
        Execute a perpetual trade.

        Args:
            sentiment: "bellicose" or "conciliatory"
            post_id: ID of the post that triggered the trade
            size_usd: Trade size in USD

        Returns:
            Trade object if executed
        """
        if sentiment == "neutral":
            print("Neutral sentiment - no trade")
            return None

        if size_usd is None:
            size_usd = self.DEFAULT_SIZE

        size_usd = min(size_usd, self.MAX_SIZE)

        # Get current price
        current_price = self.get_current_btc_price()

        # Determine direction
        if sentiment == "bellicose":
            # Bearish - SELL / SHORT
            side = "sell"
            position = Position.SHORT
            action = "SHORT (sell)"
        else:
            # Bullish - BUY / LONG
            side = "buy"
            position = Position.LONG
            action = "LONG (buy)"

        logger.info(f"Executing perpetual {action} {size_usd} USD at ${current_price:,.2f}")

        # Calculate quantity in contracts (1 contract = 0.01 BTC for linear)
        quantity = size_usd / current_price / 0.01
        quantity = round(quantity, 2)

        if self.simulation_mode:
            order_id = f"SIM-{int(time.time())}"
            print(f"📋 [SIMULATED] {action} {quantity} contracts of {self.BTC_PERPETUAL}")
            print(f"   Post: {post_id}, Sentiment: {sentiment}")
        else:
            try:
                result = self._make_request(
                    "POST",
                    "/trade/order",
                    body={
                        "instId": self.BTC_PERPETUAL,
                        "tdMode": "cross",
                        "side": side,
                        "ordType": "market",
                        "sz": str(quantity)
                    }
                )
                order_id = result[0].get("ordId", "UNKNOWN") if result else "UNKNOWN"
                print(f"🔒 [LIVE] {action} {quantity} contracts of {self.BTC_PERPETUAL}")
                print(f"   Order ID: {order_id}")
            except OKXAPIError as e:
                logger.error(f"Failed to place order: {e}")
                return None

        trade = Trade(
            position=position,
            size=size_usd,
            entry_price=current_price,
            timestamp=datetime.now(),
            post_id=post_id,
            sentiment=sentiment,
            order_id=order_id,
            instrument_id=self.BTC_PERPETUAL
        )

        return trade

    def execute_option_trade(
        self,
        sentiment: str,
        post_id: str,
        size_usd: float = None
    ) -> Optional[Trade]:
        """
        Execute an option trade.

        Args:
            sentiment: "bellicose" or "conciliatory"
            post_id: ID of the post that triggered the trade
            size_usd: Trade size in USD

        Returns:
            Trade object if executed
        """
        if sentiment == "neutral":
            print("Neutral sentiment - no trade")
            return None

        if size_usd is None:
            size_usd = self.DEFAULT_SIZE

        size_usd = min(size_usd, self.MAX_SIZE)

        # Get current BTC price
        current_price = self.get_current_btc_price()

        # Get available options
        options = self.get_available_options()

        if not options:
            logger.warning("No options available")
            return None

        # Select option based on sentiment
        if sentiment == "bellicose":
            # Bearish - prefer puts
            puts = [o for o in options if o["instId"].endswith("-P")]
            if puts:
                puts.sort(key=lambda x: abs(float(x.get("stk", 0)) - current_price))
                selected = puts[0]
            else:
                selected = options[0]
            side = "buy"
            position = Position.SHORT
            action = "BUY PUT"
        else:
            # Bullish - prefer calls
            calls = [o for o in options if o["instId"].endswith("-C")]
            if calls:
                calls.sort(key=lambda x: abs(float(x.get("stk", 0)) - current_price))
                selected = calls[0]
            else:
                selected = options[0]
            side = "buy"
            position = Position.LONG
            action = "BUY CALL"

        inst_id = selected["instId"]
        option_price = self.get_option_price(inst_id)

        logger.info(f"Executing option {action} {inst_id}")
        logger.info(f"  Strike: ${selected.get('stk')} Exp: {selected.get('expTime')}")

        if self.simulation_mode:
            order_id = f"SIM-{int(time.time())}"
            print(f"📋 [SIMULATED] {action} {inst_id}")
            print(f"   Strike: ${selected.get('stk')} | Exp: {selected.get('expTime')}")
            print(f"   Post: {post_id}, Sentiment: {sentiment}")
        else:
            try:
                # Options size is in contracts
                result = self._make_request(
                    "POST",
                    "/trade/order",
                    body={
                        "instId": inst_id,
                        "tdMode": "cross",
                        "side": side,
                        "ordType": "market",
                        "sz": "1"
                    }
                )
                order_id = result[0].get("ordId", "UNKNOWN") if result else "UNKNOWN"
                print(f"🔒 [LIVE] {action} {inst_id}")
                print(f"   Order ID: {order_id}")
            except OKXAPIError as e:
                logger.error(f"Failed to place order: {e}")
                return None

        trade = Trade(
            position=position,
            size=size_usd,
            entry_price=option_price,
            timestamp=datetime.now(),
            post_id=post_id,
            sentiment=sentiment,
            order_id=order_id,
            instrument_id=inst_id
        )

        return trade

    def execute_trade(
        self,
        sentiment: str,
        post_id: str,
        size_usd: float = None,
        trade_type: str = "perpetual"  # "perpetual" or "option"
    ) -> Optional[Trade]:
        """
        Execute a trade - wrapper method.

        Args:
            sentiment: "bellicose" or "conciliatory"
            post_id: ID of the post that triggered the trade
            size_usd: Trade size in USD
            trade_type: "perpetual" or "option"

        Returns:
            Trade object if executed
        """
        if trade_type == "option":
            return self.execute_option_trade(sentiment, post_id, size_usd)
        else:
            return self.execute_perpetual_trade(sentiment, post_id, size_usd)

    def close_position(self, trade: Trade) -> float:
        """
        Close an existing position.

        Returns:
            PnL in USD
        """
        if not trade.instrument_id:
            logger.warning("No instrument ID to close")
            return 0.0

        current_price = self.get_current_btc_price()

        # Determine close side
        close_side = "buy" if trade.position == Position.SHORT else "sell"

        if self.simulation_mode:
            if trade.position == Position.LONG:
                pnl = (current_price - trade.entry_price) * (trade.size / trade.entry_price)
            else:
                pnl = (trade.entry_price - current_price) * (trade.size / trade.entry_price)

            print(f"📋 [SIMULATED] Closed position at ${current_price:,.2f}, PnL: ${pnl:.2f}")
            return pnl
        else:
            try:
                # Would need to calculate quantity and close
                logger.warning("Close position not fully implemented for live trading")
            except OKXAPIError as e:
                logger.error(f"Failed to close position: {e}")

        return 0.0


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    trader = OKXTrader()

    print(f"\nCurrent BTC price: ${trader.get_current_btc_price():,.2f}\n")

    print("=" * 50)
    print("Testing perpetual trades")
    print("=" * 50)

    print("\nTesting bellicose (SHORT):")
    trader.execute_trade("bellicose", "post-123", trade_type="perpetual")

    print("\nTesting conciliatory (LONG):")
    trader.execute_trade("conciliatory", "post-456", trade_type="perpetual")

    print("\n" + "=" * 50)
    print("Testing option trades")
    print("=" * 50)

    print("\nTesting bellicose (BUY PUT):")
    trader.execute_trade("bellicose", "post-789", trade_type="option")

    print("\nTesting neutral (should skip):")
    trader.execute_trade("neutral", "post-000", trade_type="perpetual")