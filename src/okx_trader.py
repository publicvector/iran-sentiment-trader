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


class OKXAPIError(Exception):
    pass


class OKXTrader:
    DEFAULT_SIZE = 100
    MAX_SIZE = 1000
    API_BASE_URL = "https://www.okx.com/api/v5"
    BTC_PERPETUAL = "BTC-USD_UM-SWAP"

    def __init__(self, api_key: str = None, api_secret: str = None, passphrase: str = None):
        self.api_key = api_key or os.getenv("OKX_API_KEY")
        self.api_secret = api_secret or os.getenv("OKX_API_SECRET")
        self.passphrase = passphrase or os.getenv("OKX_PASSPHRASE")
        self.simulation_mode = not (self.api_key and self.api_secret and self.passphrase)

        self._session = requests.Session()
        self._time_offset = 0  # Will sync with OKX server time

        if self.simulation_mode:
            logger.warning("⚠️ Running in SIMULATION MODE - no real trades")
        else:
            # Sync time with OKX server first
            try:
                self._sync_time()
                self._get_account_balance()
                logger.info("✅ OKX credentials validated")
            except OKXAPIError as e:
                logger.warning(f"⚠️ OKX credentials invalid: {e}")
                logger.warning("Running in SIMULATION MODE")
                self.simulation_mode = True

    def _sync_time(self):
        """Sync local time with OKX server to fix timestamp errors."""
        try:
            # Get server time
            resp = requests.get(f"{self.API_BASE_URL}/public/time", timeout=10)
            data = resp.json()
            if data.get("data"):
                server_time = int(data["data"][0]["ts"])
                local_time = int(time.time() * 1000)
                self._time_offset = server_time - local_time
                logger.info(f"OKX time sync: offset = {self._time_offset}ms")
        except Exception as e:
            logger.warning(f"Time sync failed: {e}")
            self._time_offset = 0

    def _sign(self, method: str, path: str, body: str = "") -> dict:
        # Use time synced with OKX server (in seconds, not milliseconds)
        timestamp = str(int((time.time() * 1000 + self._time_offset) // 1000))
        message = timestamp + method + path + body

        mac = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        )
        signature = base64.b64encode(mac.digest()).decode('utf-8')

        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json"
        }

    def _make_request(self, method: str, endpoint: str, params: dict = None, body: dict = None) -> dict:
        if self.simulation_mode:
            return {}

        body_str = json.dumps(body) if body else ""
        headers = self._sign(method, endpoint, body_str)
        url = f"{self.API_BASE_URL}{endpoint}"

        try:
            json_body = body if (method == "POST" and body) else None
            response = self._session.request(method, url, headers=headers, json=json_body, params=params, timeout=30)
            data = response.json()

            if data.get("code") != "0":
                raise OKXAPIError(f"OKX error: {data.get('msg', 'Unknown')}")

            return data.get("data", [])
        except OKXAPIError:
            raise
        except Exception as e:
            raise OKXAPIError(f"Request failed: {e}")

    def _get_account_balance(self) -> dict:
        return self._make_request("GET", "/account/balance", params={"ccy": "BTC"})

    def get_current_btc_price(self) -> float:
        try:
            resp = requests.get(
                f"{self.API_BASE_URL}/market/ticker",
                params={"instId": self.BTC_PERPETUAL},
                timeout=10
            )
            data = resp.json()
            if data.get("data"):
                return float(data["data"][0]["last"])
        except:
            pass
        return 67000.0

    def get_perpetual_price(self) -> float:
        return self.get_current_btc_price()

    def get_option_price(self, inst_id: str) -> float:
        try:
            resp = requests.get(f"{self.API_BASE_URL}/market/ticker", params={"instId": inst_id}, timeout=10)
            data = resp.json()
            if data.get("data"):
                return float(data["data"][0]["last"])
        except:
            pass
        return 0.0

    def get_available_options(self, expiry_hours: int = 24) -> List[dict]:
        try:
            resp = requests.get(f"{self.API_BASE_URL}/public/instruments", params={"instType": "OPTION", "uly": "BTC-USD"}, timeout=10)
            data = resp.json()

            if not data.get("data"):
                return []

            options = []
            now = time.time() * 1000
            for opt in data["data"]:
                exp_time = opt.get("expTime")
                if exp_time:
                    hours_until = (int(exp_time) - now) / (1000 * 60 * 60)
                    if 0 < hours_until <= expiry_hours:
                        options.append(opt)
            return options
        except:
            return []

    def execute_perpetual_trade(self, sentiment: str, post_id: str, size_usd: float = None) -> Optional[Trade]:
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

        logger.info(f"Executing perpetual {action} {size_usd} USD at ${current_price:,.2f}")
        quantity = round(size_usd / current_price / 0.01, 2)

        if self.simulation_mode:
            order_id = f"SIM-{int(time.time())}"
            print(f"📋 [SIMULATED] {action} {quantity} contracts of {self.BTC_PERPETUAL}")
            print(f"   Post: {post_id}, Sentiment: {sentiment}")
        else:
            try:
                result = self._make_request("POST", "/trade/order", body={
                    "instId": self.BTC_PERPETUAL, "tdMode": "cross", "side": side, "ordType": "market", "sz": str(quantity)
                })
                order_id = result[0].get("ordId", "UNKNOWN") if result else "UNKNOWN"
                print(f"🔒 [LIVE] {action} {quantity} contracts")
                print(f"   Order ID: {order_id}")
            except OKXAPIError as e:
                logger.error(f"Failed to place order: {e}")
                return None

        return Trade(position, size_usd, current_price, datetime.now(), post_id, sentiment, order_id, self.BTC_PERPETUAL)

    def execute_option_trade(self, sentiment: str, post_id: str, size_usd: float = None) -> Optional[Trade]:
        if sentiment == "neutral":
            print("Neutral sentiment - no trade")
            return None

        size_usd = min(size_usd or self.DEFAULT_SIZE, self.MAX_SIZE)
        current_price = self.get_current_btc_price()
        options = self.get_available_options()

        if not options:
            logger.warning("No options available")
            return None

        if sentiment == "bellicose":
            puts = [o for o in options if o["instId"].endswith("-P")]
            selected = puts[0] if puts else options[0]
            action = "BUY PUT"
        else:
            calls = [o for o in options if o["instId"].endswith("-C")]
            selected = calls[0] if calls else options[0]
            action = "BUY CALL"

        inst_id = selected["instId"]
        option_price = self.get_option_price(inst_id)

        logger.info(f"Executing option {action} {inst_id}")

        if self.simulation_mode:
            order_id = f"SIM-{int(time.time())}"
            print(f"📋 [SIMULATED] {action} {inst_id}")
            print(f"   Strike: ${selected.get('stk')} | Exp: {selected.get('expTime')}")
            print(f"   Post: {post_id}, Sentiment: {sentiment}")
        else:
            try:
                result = self._make_request("POST", "/trade/order", body={
                    "instId": inst_id, "tdMode": "cross", "side": "buy", "ordType": "market", "sz": "1"
                })
                order_id = result[0].get("ordId", "UNKNOWN") if result else "UNKNOWN"
                print(f"🔒 [LIVE] {action} {inst_id}")
                print(f"   Order ID: {order_id}")
            except OKXAPIError as e:
                logger.error(f"Failed to place order: {e}")
                return None

        return Trade(Position.LONG if sentiment == "conciliatory" else Position.SHORT, size_usd, option_price, datetime.now(), post_id, sentiment, order_id, inst_id)

    def execute_trade(self, sentiment: str, post_id: str, size_usd: float = None, trade_type: str = "perpetual") -> Optional[Trade]:
        if trade_type == "option":
            return self.execute_option_trade(sentiment, post_id, size_usd)
        else:
            return self.execute_perpetual_trade(sentiment, post_id, size_usd)

    def close_position(self, trade: Trade) -> float:
        if not trade.instrument_id:
            return 0.0

        current_price = self.get_current_btc_price()

        if self.simulation_mode:
            if trade.position == Position.LONG:
                pnl = (current_price - trade.entry_price) * (trade.size / trade.entry_price)
            else:
                pnl = (trade.entry_price - current_price) * (trade.size / trade.entry_price)
            print(f"📋 [SIMULATED] Closed at ${current_price:,.2f}, PnL: ${pnl:.2f}")
            return pnl
        return 0.0


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    trader = OKXTrader()
    print(f"\nBTC price: ${trader.get_current_btc_price():,.2f}")

    print("\n--- Test Perpetual Trades ---")
    trader.execute_trade("bellicose", "test-1", 100, "perpetual")
    trader.execute_trade("conciliatory", "test-2", 100, "perpetual")

    print("\n--- Test Option Trades ---")
    trader.execute_trade("bellicose", "test-3", 100, "option")

    print("\n--- Test Neutral ---")
    trader.execute_trade("neutral", "test-4", 100, "perpetual")