"""
Deribit options trading execution.
Deribit is the largest crypto options exchange - offers BTC options.
"""

import os
import requests
import json
import time
from enum import Enum
from typing import Optional, List
from dataclasses import dataclass
from datetime import datetime
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class Position(Enum):
    LONG = "long"   # Buy call or sell put (bullish)
    SHORT = "short" # Sell call or buy put (bearish)


@dataclass
class OptionContract:
    """Represents an option contract."""
    instrument_name: str  # e.g., "BTC-25APR25-67000-C"
    strike: float
    expiration: datetime
    option_type: str  # "call" or "put"
    bid: float = 0
    ask: float = 0
    last: float = 0


@dataclass
class Trade:
    """Represents a trade execution."""
    position: Position
    size_contracts: int
    entry_price: float
    timestamp: datetime
    post_id: str
    sentiment: str
    order_id: str = None
    option_type: str = None  # "call" or "put"


class DeribitAPIError(Exception):
    """Custom exception for Deribit API errors."""
    pass


class DeribitTrader:
    """
    Executes Bitcoin options trades on Deribit.
    Deribit is the leading crypto options exchange.
    """

    DEFAULT_CONTRACTS = 1  # Number of contracts (1 BTC each for BTC options)
    MAX_CONTRACTS = 10

    API_BASE_URL = "https://api.deribit.com/api/v2"

    # BTC option instruments are denominated in BTC
    # We'll use BTC-USD for settlement

    def __init__(self, client_id: str = None, client_secret: str = None):
        self.client_id = client_id or os.getenv("DERIBIT_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("DERIBIT_CLIENT_SECRET")
        self.access_token = None
        self.simulation_mode = not (self.client_id and self.client_secret)

        self._session = requests.Session()

        if not self.simulation_mode:
            self._authenticate()
        else:
            logger.warning("⚠️ Running in SIMULATION MODE - no real trades")

    def _authenticate(self):
        """Authenticate with Deribit API."""
        if not self.client_id or not self.client_secret:
            raise DeribitAPIError("Client ID or secret not set")

        response = self._session.post(
            f"{self.API_BASE_URL}/public/auth",
            json={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret
            },
            timeout=30
        )
        data = response.json()

        if "result" in data and "access_token" in data["result"]:
            self.access_token = data["result"]["access_token"]
            logger.info("✅ Deribit authentication successful")
        else:
            raise DeribitAPIError(f"Authentication failed: {data}")

    def _make_request(self, method: str, params: dict = None) -> dict:
        """Make authenticated request to Deribit API."""
        if self.simulation_mode:
            return {"result": {}}

        headers = {"Authorization": f"Bearer {self.access_token}"}
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1}

        response = self._session.post(
            f"{self.API_BASE_URL}/private/{method}",
            json=body,
            headers=headers,
            timeout=30
        )

        data = response.json()

        if "error" in data:
            raise DeribitAPIError(f"Deribit error: {data['error']}")

        return data

    def get_current_btc_price(self) -> float:
        """Get current Bitcoin price from Deribit index."""
        try:
            response = self._session.post(
                f"{self.API_BASE_URL}/public/get_index_price",
                json={"jsonrpc": "2.0", "method": "public/get_index_price", "params": {"index_name": "btc_usd"}, "id": 1},
                timeout=30
            )
            data = response.json()
            if "result" in data and "index_price" in data["result"]:
                return float(data["result"]["index_price"])
            logger.warning(f"Unexpected price response: {data}")
        except Exception as e:
            logger.warning(f"Failed to get BTC price from Deribit: {e}")

        # Fallback to Coinbase public API
        try:
            response = self._session.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=10)
            data = response.json()
            return float(data.get("data", {}).get("amount", 0))
        except Exception as e:
            logger.warning(f"Fallback to Coinbase also failed: {e}")

        # Last resort fallback
        return 67000.0

    def get_options_instruments(self, expiration_hours: int = 24) -> List[OptionContract]:
        """
        Get available BTC options expiring within specified hours.

        Args:
            expiration_hours: Only return options expiring within this many hours

        Returns:
            List of available option contracts
        """
        # Get options for BTC-USD
        data = self._make_request(
            "public/get_instruments",
            {
                "currency": "BTC",
                "kind": "option",
                "expired": False
            }
        )

        instruments = []
        for inst in data["result"]:
            # Parse expiration from instrument name
            # Format: BTC-25APR25-67000-C
            name = inst["instrument_name"]
            # Get the expiration timestamp
            exp_timestamp = inst.get("expiration_timestamp", 0)
            exp_date = datetime.fromtimestamp(exp_timestamp / 1000)

            # Filter by expiration time
            hours_until_exp = (exp_date - datetime.now()).total_seconds() / 3600
            if hours_until_exp > 0 and hours_until_exp <= expiration_hours:
                instruments.append(OptionContract(
                    instrument_name=name,
                    strike=inst.get("strike", 0),
                    expiration=exp_date,
                    option_type="call" if "-C-" in name else "put",
                    bid=inst.get("bid", 0),
                    ask=inst.get("ask", 0),
                    last=inst.get("last", 0)
                ))

        return instruments

    def get_nearest_expiry_options(self) -> List[OptionContract]:
        """Get options with the nearest expiration."""
        try:
            # Public endpoint - no auth needed
            response = self._session.post(
                f"{self.API_BASE_URL}/public/get_instruments",
                json={"jsonrpc": "2.0", "method": "public/get_instruments", "params": {"currency": "BTC", "kind": "option", "expired": False}, "id": 1},
                timeout=30
            )
            data = response.json()
        except Exception as e:
            logger.warning(f"Failed to get instruments: {e}")
            return []

        if "result" not in data:
            logger.warning(f"No result in response: {data}")
            return []

        options = []
        now = datetime.now()

        for inst in data["result"]:
            exp_ts = inst.get("expiration_timestamp", 0)
            if exp_ts > 0:
                exp_date = datetime.fromtimestamp(exp_ts / 1000)
                hours_until = (exp_date - now).total_seconds() / 3600

                # Only include options expiring in next 24 hours
                if 0 < hours_until <= 24:
                    name = inst["instrument_name"]
                    options.append(OptionContract(
                        instrument_name=name,
                        strike=float(inst.get("strike", 0)),
                        expiration=exp_date,
                        option_type="call" if "-C-" in name else "put",
                        bid=inst.get("bid", 0),
                        ask=inst.get("ask", 0),
                        last=inst.get("last", 0)
                    ))

        # Sort by expiration, then by strike
        options.sort(key=lambda x: (x.expiration, x.strike))
        return options

    def select_option(
        self,
        sentiment: str,
        current_price: float
    ) -> Optional[OptionContract]:
        """
        Select the appropriate option contract based on sentiment.

        For BELLICOSE (bearish): Buy puts or sell calls
        For CONCILIATORY (bullish): Buy calls or sell puts

        Args:
            sentiment: "bellicose" or "conciliatory"
            current_price: Current BTC price

        Returns:
            Selected option contract or None
        """
        options = self.get_nearest_expiry_options()

        if not options:
            logger.warning("No options available")
            return None

        if sentiment == "bellicose":
            # Bearish - prefer puts
            puts = [o for o in options if o.option_type == "put"]
            if puts:
                # ATM or slightly OTM puts
                puts.sort(key=lambda x: abs(x.strike - current_price))
                return puts[0]
            return options[0]  # Fallback to any

        else:  # conciliatory - bullish
            # Bullish - prefer calls
            calls = [o for o in options if o.option_type == "call"]
            if calls:
                calls.sort(key=lambda x: abs(x.strike - current_price))
                return calls[0]
            return options[0]  # Fallback

    def execute_trade(
        self,
        sentiment: str,
        post_id: str,
        size_contracts: int = None
    ) -> Optional[Trade]:
        """
        Execute an options trade based on sentiment.

        Args:
            sentiment: "bellicose" or "conciliatory"
            post_id: ID of the post that triggered the trade
            size_contracts: Number of option contracts (default: DEFAULT_CONTRACTS)

        Returns:
            Trade object if executed, None if skipped
        """
        if sentiment == "neutral":
            print(f"Neutral sentiment - no trade")
            return None

        if size_contracts is None:
            size_contracts = self.DEFAULT_CONTRACTS

        size_contracts = min(size_contracts, self.MAX_CONTRACTS)

        # Get current BTC price
        current_price = self.get_current_btc_price()

        # Select option based on sentiment
        option = self.select_option(sentiment, current_price)

        if not option:
            logger.error("Could not select option contract")
            return None

        # Determine position and trade direction
        if sentiment == "bellicose":
            # Bearish: buy puts (profit when BTC drops)
            side = "buy"
            position = Position.SHORT
            action = "BUY PUT"
            option_type = "put"
        else:  # conciliatory
            # Bullish: buy calls (profit when BTC rises)
            side = "buy"
            position = Position.LONG
            action = "BUY CALL"
            option_type = "call"

        price = option.ask if option.ask > 0 else option.last

        logger.info(f"Executing {action} {size_contracts} contracts")
        logger.info(f"  Instrument: {option.instrument_name}")
        logger.info(f"  Strike: ${option.strike:,.0f} | Exp: {option.expiration.strftime('%d%b%y')}")
        logger.info(f"  Price: {price} BTC")

        if self.simulation_mode:
            order_id = f"SIM-{int(time.time())}"
            print(f"📋 [SIMULATED] {action} {size_contracts}x {option.instrument_name}")
            print(f"   Strike: ${option.strike:,.0f} | Exp: {option.expiration.strftime('%d%b%y')}")
            print(f"   Post: {post_id}, Sentiment: {sentiment}")
        else:
            # Place actual order
            try:
                result = self._make_request(
                    "private/buy",
                    {
                        "instrument_name": option.instrument_name,
                        "amount": size_contracts * 100,  # Deribit uses Satoshis (100 = 1 contract)
                        "type": "market"
                    }
                )
                order_id = result["result"].get("order", {}).get("order_id", "UNKNOWN")
                print(f"🔒 [LIVE] {action} {size_contracts}x {option.instrument_name}")
                print(f"   Order ID: {order_id}")
            except DeribitAPIError as e:
                logger.error(f"Failed to place order: {e}")
                return None

        trade = Trade(
            position=position,
            size_contracts=size_contracts,
            entry_price=price,
            timestamp=datetime.now(),
            post_id=post_id,
            sentiment=sentiment,
            order_id=order_id,
            option_type=option_type
        )

        return trade

    def close_position(self, trade: Trade) -> float:
        """
        Close an options position.

        Returns:
            PnL in BTC (to be converted to USD)
        """
        # In simulation mode, just calculate PnL
        if self.simulation_mode:
            print(f"📋 [SIMULATED] Closing position (no real close)")
            return 0.0

        # For real trading, would need to sell the option
        # This is simplified - real implementation needs to track the order
        print(f"⚠️ Position close not fully implemented")
        return 0.0


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    trader = DeribitTrader()

    print(f"\nCurrent BTC price: ${trader.get_current_btc_price():,.2f}\n")

    # Show available options
    print("Available options (next 24h):")
    options = trader.get_nearest_expiry_options()
    for opt in options[:5]:
        print(f"  {opt.instrument_name}: strike=${opt.strike:,.0f}, exp={opt.expiration.strftime('%d%b%y %H:%M')}")

    print("\n" + "=" * 50)
    print("Testing bellicose trade (buy puts):")
    trader.execute_trade("bellicose", "post-123")

    print("\nTesting conciliatory trade (buy calls):")
    trader.execute_trade("conciliatory", "post-456")

    print("\nTesting neutral (should skip):")
    trader.execute_trade("neutral", "post-789")