"""
dYdX v4 trading execution - BTC-USD perpetual futures.
Decentralized exchange on Cosmos, no KYC, accessible from US.

Uses dydx-v4-client Python SDK for order placement
and the Indexer REST API for market data.
"""

import os
import time
import json
import requests
from enum import Enum
from typing import Optional
from dataclasses import dataclass
from datetime import datetime
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Try importing dYdX v4 client
try:
    from dydx_v4_client import NodeClient, Wallet
    from dydx_v4_client.indexer.rest import IndexerClient
    from dydx_v4_client.node.market import Market
    from dydx_v4_client.indexer.rest.constants import TimePeriod
    HAS_DYDX = True
except ImportError:
    HAS_DYDX = False


class Position(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class Trade:
    position: Position
    size: float        # size in USD
    entry_price: float
    timestamp: datetime
    post_id: str
    sentiment: str
    order_id: str = None
    instrument_id: str = None
    close_at: float = None


class DYDXAPIError(Exception):
    pass


class DYDXTrader:
    """
    Executes BTC-USD perpetual futures trades on dYdX v4.
    Uses the Indexer API for market data and the chain client for trading.
    """

    DEFAULT_SIZE = 100   # USD
    MAX_SIZE = 1000      # USD
    BTC_MARKET = "BTC-USD"

    INDEXER_MAINNET = "https://indexer.dydx.trade/v4"
    INDEXER_TESTNET = "https://indexer.v4testnet.dydx.exchange/v4"

    CHAIN_MAINNET = "dydx-mainnet-1"
    CHAIN_TESTNET = "dydx-testnet-4"

    NODE_MAINNET = "https://dydx-dao-rpc.polkachu.com:443"
    NODE_TESTNET = "https://dydx-testnet-rpc.polkachu.com:443"

    def __init__(
        self,
        mnemonic: str = None,
        testnet: bool = None,
    ):
        self.mnemonic = mnemonic or os.getenv("DYDX_MNEMONIC")
        self.testnet = testnet if testnet is not None else os.getenv("DYDX_TESTNET", "true").lower() == "true"

        self.indexer_url = self.INDEXER_TESTNET if self.testnet else self.INDEXER_MAINNET
        self._session = requests.Session()
        self.simulation_mode = True

        self._node_client = None
        self._wallet = None
        self._subaccount = None

        if not HAS_DYDX:
            logger.warning("dydx-v4-client not installed - run: pip install dydx-v4-client")
            logger.warning("Running in SIMULATION MODE")
            return

        if self.mnemonic:
            try:
                self._init_client()
                self.simulation_mode = False
                mode = "TESTNET" if self.testnet else "MAINNET"
                logger.info(f"dYdX v4 client initialized ({mode})")
            except Exception as e:
                logger.warning(f"dYdX initialization failed: {e}")
                logger.warning("Running in SIMULATION MODE")
        else:
            logger.warning("Running in SIMULATION MODE - no dYdX mnemonic")

    def _init_client(self):
        """Initialize the dYdX v4 chain client and wallet."""
        try:
            from dydx_v4_client.node.client import NodeClient
            from dydx_v4_client.indexer.rest import IndexerClient
            from dydx_v4_client import Wallet

            if self.testnet:
                self._node_client = NodeClient.connect(self.NODE_TESTNET)
                chain_id = self.CHAIN_TESTNET
            else:
                self._node_client = NodeClient.connect(self.NODE_MAINNET)
                chain_id = self.CHAIN_MAINNET

            self._wallet = Wallet.from_mnemonic(self.mnemonic, chain_id=chain_id)
            self._subaccount = self._wallet.subaccount(0)

            logger.info(f"dYdX wallet address: {self._wallet.address}")
        except Exception as e:
            raise DYDXAPIError(f"Failed to initialize dYdX client: {e}")

    def _indexer_get(self, endpoint: str, params: dict = None) -> dict:
        """Make GET request to dYdX Indexer API (public, no auth needed)."""
        url = f"{self.indexer_url}{endpoint}"
        try:
            response = self._session.get(url, params=params, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise DYDXAPIError(f"Indexer API error: {e}")
        except Exception as e:
            raise DYDXAPIError(f"Indexer request failed: {e}")

    def get_current_btc_price(self) -> float:
        """Get BTC-USD price from dYdX indexer."""
        try:
            data = self._indexer_get("/perpetualMarkets", params={"ticker": self.BTC_MARKET})
            markets = data.get("markets", {})
            btc = markets.get(self.BTC_MARKET, {})
            oracle_price = btc.get("oraclePrice")
            if oracle_price:
                return float(oracle_price)
        except Exception as e:
            logger.warning(f"Failed to get BTC price from dYdX: {e}")

        # Fallback: try CoinGecko
        try:
            resp = self._session.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
                timeout=10,
            )
            return float(resp.json()["bitcoin"]["usd"])
        except Exception:
            return 67000.0

    def get_market_info(self) -> dict:
        """Get BTC-USD perpetual market info."""
        try:
            data = self._indexer_get("/perpetualMarkets", params={"ticker": self.BTC_MARKET})
            return data.get("markets", {}).get(self.BTC_MARKET, {})
        except DYDXAPIError:
            return {}

    def get_orderbook(self) -> dict:
        """Get BTC-USD orderbook."""
        try:
            return self._indexer_get(f"/orderbooks/perpetualMarket/{self.BTC_MARKET}")
        except DYDXAPIError:
            return {}

    def _place_order(self, side: str, size: float, price: float) -> str:
        """Place an order on dYdX v4 chain."""
        if not self._node_client or not self._subaccount:
            raise DYDXAPIError("Client not initialized")

        try:
            from dydx_v4_client.node.market import Market
            from dydx_v4_client.indexer.rest.constants import OrderSide, OrderType, OrderTimeInForce

            market_info = self.get_market_info()
            step_size = float(market_info.get("stepSize", "0.001"))
            tick_size = float(market_info.get("tickSize", "1"))

            # Quantize size and price
            quantized_size = round(size / step_size) * step_size
            quantized_price = round(price / tick_size) * tick_size

            # Set price with slippage for market-like execution
            if side == "BUY":
                limit_price = quantized_price * 1.01  # 1% slippage
            else:
                limit_price = quantized_price * 0.99

            limit_price = round(limit_price / tick_size) * tick_size

            order_id = f"dydx-{int(time.time() * 1000)}"

            # Place short-term order (IOC for market-like execution)
            tx = self._node_client.place_order(
                self._subaccount,
                market=self.BTC_MARKET,
                side=side,
                price=limit_price,
                size=quantized_size,
                client_id=int(time.time() * 1000) % (2**32),
                time_in_force="IOC",
                reduce_only=False,
            )

            logger.info(f"Order placed: {tx}")
            return order_id

        except Exception as e:
            raise DYDXAPIError(f"Order placement failed: {e}")

    def execute_trade(self, sentiment: str, post_id: str, size_usd: float = None, trade_type: str = "perpetual") -> Optional[Trade]:
        """
        Execute a BTC-USD perpetual trade based on sentiment.

        BELLICOSE -> SHORT BTC (conflict = risk-off)
        CONCILIATORY -> LONG BTC (peace = risk-on)
        """
        if sentiment == "neutral":
            print("Neutral sentiment - no trade")
            return None

        size_usd = min(size_usd or self.DEFAULT_SIZE, self.MAX_SIZE)
        current_price = self.get_current_btc_price()

        # Calculate BTC size
        btc_size = size_usd / current_price

        if sentiment == "bellicose":
            side = "SELL"
            position = Position.SHORT
            action = "SHORT (sell)"
        else:
            side = "BUY"
            position = Position.LONG
            action = "LONG (buy)"

        if self.simulation_mode:
            order_id = f"SIM-{int(time.time())}"
            print(f"[SIMULATED] {action} {btc_size:.6f} BTC (${size_usd}) on dYdX")
            print(f"   Price: ${current_price:,.2f}")
            print(f"   Post: {post_id}, Sentiment: {sentiment}")
        else:
            try:
                order_id = self._place_order(side, btc_size, current_price)
                print(f"[LIVE] {action} {btc_size:.6f} BTC (${size_usd}) on dYdX")
                print(f"   Price: ${current_price:,.2f} | Order: {order_id}")
            except DYDXAPIError as e:
                logger.error(f"Failed to place order: {e}")
                return None

        return Trade(
            position=position,
            size=size_usd,
            entry_price=current_price,
            timestamp=datetime.now(),
            post_id=post_id,
            sentiment=sentiment,
            order_id=order_id,
            instrument_id=self.BTC_MARKET,
        )

    def close_position(self, trade: Trade) -> float:
        """Close an open position."""
        current_price = self.get_current_btc_price()

        if trade.position == Position.LONG:
            pnl = (current_price - trade.entry_price) * (trade.size / trade.entry_price)
            close_side = "SELL"
        else:
            pnl = (trade.entry_price - current_price) * (trade.size / trade.entry_price)
            close_side = "BUY"

        btc_size = trade.size / trade.entry_price

        if self.simulation_mode:
            print(f"[SIMULATED] Closed {trade.position.value} at ${current_price:,.2f}, PnL: ${pnl:.2f}")
        else:
            try:
                self._place_order(close_side, btc_size, current_price)
                print(f"[LIVE] Closed {trade.position.value} at ${current_price:,.2f}, PnL: ${pnl:.2f}")
            except DYDXAPIError as e:
                logger.error(f"Failed to close position: {e}")

        return pnl

    def get_positions(self) -> list:
        """Get open positions from indexer."""
        if self.simulation_mode or not self._wallet:
            return []
        try:
            data = self._indexer_get(
                f"/addresses/{self._wallet.address}/subaccountNumber/0/perpetualPositions",
                params={"status": "OPEN"},
            )
            return data.get("positions", [])
        except DYDXAPIError:
            return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    trader = DYDXTrader()
    print(f"\nBTC price (dYdX): ${trader.get_current_btc_price():,.2f}")

    print("\n--- Market Info ---")
    info = trader.get_market_info()
    if info:
        print(f"  Oracle price: ${float(info.get('oraclePrice', 0)):,.2f}")
        print(f"  24h volume: ${float(info.get('volume24H', 0)):,.2f}")

    print("\n--- Test Trades ---")
    trader.execute_trade("bellicose", "test-1", 100)
    trader.execute_trade("conciliatory", "test-2", 100)
    trader.execute_trade("neutral", "test-3")
