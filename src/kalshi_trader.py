"""
Kalshi trading execution - trades BTC directional contracts based on Iran sentiment.
Kalshi is CFTC-regulated and fully US-legal.

Strategy (backtested Feb 28 - Apr 4, 2026 → 71.4% win rate, +7.6% cumulative):
  BELLICOSE → BTC drops → buy NO on "BTC above X" (directional, 8h hold)
  PURE CONCILIATORY → BTC rises → buy YES on "BTC above X" (directional, 8h hold)
  MIXED → skip (conciliatory + bellicose language = unreliable signal)
  NEUTRAL → skip

Auth: RSA-PSS signed requests with API key pair.
"""

import os
import time
import json
import base64
import requests
from enum import Enum
from typing import Optional, List
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from dotenv import load_dotenv

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, utils
    from cryptography.exceptions import InvalidSignature
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

load_dotenv()

logger = logging.getLogger(__name__)


class Position(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class Trade:
    position: Position
    size: float       # number of contracts
    entry_price: float  # cost in dollars per contract
    timestamp: datetime
    post_id: str
    sentiment: str
    order_id: str = None
    instrument_id: str = None
    close_at: datetime = None


class KalshiAPIError(Exception):
    pass


class KalshiTrader:
    """
    Trades BTC directional contracts on Kalshi based on Iran sentiment.

    Uses KXBTCD series ("BTC above $X on [date]?") for directional bets:
      BELLICOSE → buy NO (bearish, bet BTC stays below strike)
      PURE CONCILIATORY → buy YES (bullish, bet BTC goes above strike)
      MIXED / NEUTRAL → no trade
    """

    DEFAULT_SIZE = 5     # USD per trade
    MAX_SIZE = 10        # USD max
    HOLD_SECONDS = 8 * 3600  # 8 hour hold (backtested optimal)
    PROD_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
    DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"

    # Market series
    BTC_DIRECTIONAL = "KXBTCD"  # "BTC above $X on [date]?" (daily)
    BTC_RANGE = "KXBTC"         # "BTC in $X-$Y range on [date]?" (daily)
    BTC_YEARLY = "KXBTCY"       # "BTC price on Jan 1, 2027?" (long-term)
    WTI_OIL = "KXWTI"           # "WTI above $X on [date]?" (daily)
    COPPER = "KXCOPPERD"        # "Copper above $X on [date]?" (daily)
    NATGAS = "KXNATGASD"        # "Natural gas above $X on [date]?" (daily)
    COBALT = "KXCOBALTMON"      # "Cobalt above $X on [date]?" (monthly)
    LITHIUM = "KXLITHIUMW"      # "Lithium above $X on [date]?" (weekly)

    def __init__(
        self,
        api_key: str = None,
        private_key_path: str = None,
        demo: bool = None,
    ):
        self.api_key = api_key or os.getenv("KALSHI_API_KEY")
        self.private_key_path = private_key_path or os.getenv("KALSHI_PRIVATE_KEY_PATH")
        self.demo = demo if demo is not None else os.getenv("KALSHI_DEMO", "true").lower() == "true"
        self.base_url = self.DEMO_BASE_URL if self.demo else self.PROD_BASE_URL

        self._private_key = None
        self._session = requests.Session()
        self.simulation_mode = True

        if not HAS_CRYPTO:
            logger.warning("cryptography package not installed - run: pip install cryptography")
            logger.warning("Running in SIMULATION MODE")
            return

        has_key = self.private_key_path or os.getenv("KALSHI_PRIVATE_KEY")
        if self.api_key and has_key:
            try:
                self._load_private_key()
                self._get_balance()
                self.simulation_mode = False
                mode = "DEMO" if self.demo else "PRODUCTION"
                logger.info(f"Kalshi credentials validated ({mode})")
            except Exception as e:
                logger.warning(f"Kalshi credentials invalid: {e}")
                logger.warning("Running in SIMULATION MODE")
        else:
            logger.warning("Running in SIMULATION MODE - no Kalshi credentials")

    # ── Auth ───────────────���───────────────────────────────────────────────

    def _load_private_key(self):
        # Support inline PEM via KALSHI_PRIVATE_KEY env var (for cloud deploy)
        inline_key = os.getenv("KALSHI_PRIVATE_KEY")
        if inline_key:
            pem_data = inline_key.replace("\\n", "\n").encode("utf-8")
            self._private_key = serialization.load_pem_private_key(pem_data, password=None)
            return

        # Fall back to file path
        key_path = os.path.expanduser(self.private_key_path)
        with open(key_path, "rb") as f:
            self._private_key = serialization.load_pem_private_key(f.read(), password=None)

    def _sign(self, method: str, path: str) -> dict:
        timestamp = str(int(time.time() * 1000))
        sign_path = path.split("?")[0]
        message = timestamp + method.upper() + sign_path

        signature = self._private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

        return {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

    def _make_request(self, method: str, endpoint: str, params: dict = None, data: dict = None) -> dict:
        api_prefix = "/trade-api/v2"
        full_path = f"{api_prefix}{endpoint}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            full_path = f"{api_prefix}{endpoint}?{query}"

        headers = self._sign(method, full_path)
        url = f"{self.base_url}{endpoint}"

        try:
            response = self._session.request(
                method, url, headers=headers,
                params=params,
                json=data if method == "POST" else None,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            try:
                error_data = e.response.json()
                raise KalshiAPIError(f"Kalshi API error: {error_data}")
            except KalshiAPIError:
                raise
            except Exception:
                raise KalshiAPIError(f"HTTP error: {e}")

    # ── Account ──────���─────────────────────────────────────────────────────

    def _get_balance(self) -> dict:
        return self._make_request("GET", "/portfolio/balance")

    def get_balance(self) -> float:
        if self.simulation_mode:
            return 10000.0
        data = self._get_balance()
        return data.get("balance", 0) / 100.0

    def get_current_btc_price(self) -> float:
        try:
            resp = requests.get(
                "https://api.coinbase.com/v2/prices/BTC-USD/spot",
                timeout=10,
            )
            return float(resp.json()["data"]["amount"])
        except Exception:
            return 85000.0

    # ── Market search ────────────────────────────���─────────────────────────

    def search_directional_markets(self) -> List[dict]:
        """Search for BTC directional markets ('BTC above $X on [date]?')."""
        if self.simulation_mode:
            btc = self.get_current_btc_price()
            return [
                {"ticker": f"KXBTCD-SIM-T{btc + 500:.2f}", "subtitle": f"${btc + 500:,.0f} or above",
                 "yes_ask_dollars": "0.4000", "no_ask_dollars": "0.6200",
                 "status": "active", "floor_strike": btc + 500, "strike_price": btc + 500},
                {"ticker": f"KXBTCD-SIM-T{btc - 500:.2f}", "subtitle": f"${btc - 500:,.0f} or above",
                 "yes_ask_dollars": "0.6200", "no_ask_dollars": "0.4000",
                 "status": "active", "floor_strike": btc - 500, "strike_price": btc - 500},
            ]

        markets = []
        try:
            data = self._make_request("GET", "/markets", params={
                "series_ticker": self.BTC_DIRECTIONAL,
                "status": "open",
                "limit": 100,
            })
            if data.get("markets"):
                markets.extend(data["markets"])
        except KalshiAPIError:
            pass

        # Parse strike prices from tickers
        for m in markets:
            ticker = m.get("ticker", "")
            if "-T" in ticker:
                try:
                    m["strike_price"] = float(ticker.split("-T")[-1])
                except ValueError:
                    pass

        return markets

    def _select_directional_market(self, markets: List[dict], sentiment: str) -> Optional[dict]:
        """
        Select a directional market near current BTC price.

        For BELLICOSE: pick strike slightly ABOVE current price → buy NO
          (cheap NO contract, pays $1 if BTC stays below — high win rate OTM)
        For CONCILIATORY: pick strike slightly BELOW current price → buy YES
          (cheap YES contract, pays $1 if BTC stays above — high win rate OTM)
        """
        if not markets:
            return None

        btc_price = self.get_current_btc_price()
        priced = [m for m in markets if m.get("strike_price")]
        if not priced:
            return markets[0]

        if sentiment == "bellicose":
            # Strike above current → NO is cheap, wins if BTC drops or stays flat
            # Filter for markets with a reasonable NO ask (not $1.00)
            above = [m for m in priced
                     if m["strike_price"] > btc_price
                     and float(m.get("no_ask_dollars", "1.00")) < 0.95]
            if above:
                # Pick ~1-2% above current (OTM sweet spot from backtest)
                target = btc_price * 1.01
                above.sort(key=lambda m: abs(m["strike_price"] - target))
                return above[0]
        else:  # conciliatory
            # Strike below current → YES is cheap-ish, wins if BTC rises or stays flat
            below = [m for m in priced
                     if m["strike_price"] < btc_price
                     and float(m.get("yes_ask_dollars", "1.00")) < 0.95]
            if below:
                target = btc_price * 0.99
                below.sort(key=lambda m: abs(m["strike_price"] - target))
                return below[0]

        # Fallback: closest to current with reasonable pricing
        reasonable = [m for m in priced
                      if float(m.get("yes_ask_dollars", "1.00")) < 0.95
                      and float(m.get("no_ask_dollars", "1.00")) < 0.95]
        if reasonable:
            reasonable.sort(key=lambda m: abs(m["strike_price"] - btc_price))
            return reasonable[0]

        priced.sort(key=lambda m: abs(m["strike_price"] - btc_price))
        return priced[0]

    # ── Trade execution ────────────��───────────────────────────────────────

    def execute_trade(self, sentiment: str, post_id: str, size_usd: float = None, trade_type: str = "directional") -> Optional[Trade]:
        """
        Execute a BTC directional trade on Kalshi.

        BELLICOSE → buy NO on 'BTC above [current+1%]' (bearish, 8h hold)
        CONCILIATORY → buy YES on 'BTC above [current-1%]' (bullish, 8h hold)
        MIXED / NEUTRAL → no trade
        """
        if sentiment in ("neutral", "mixed"):
            print(f"{sentiment.capitalize()} sentiment - no trade (mixed signals unreliable)")
            return None

        if sentiment not in ("bellicose", "conciliatory"):
            print(f"Unknown sentiment '{sentiment}' - no trade")
            return None

        size_usd = min(size_usd or self.DEFAULT_SIZE, self.MAX_SIZE)

        # Find directional markets
        markets = self.search_directional_markets()
        if not markets:
            logger.warning("No BTC directional markets found on Kalshi")
            return None

        market = self._select_directional_market(markets, sentiment)
        if not market:
            logger.warning("No suitable directional market found")
            return None

        ticker = market.get("ticker", "UNKNOWN")
        strike = market.get("strike_price", 0)
        btc_price = self.get_current_btc_price()

        if sentiment == "bellicose":
            # Buy NO on "BTC above strike" → profits if BTC < strike at settlement
            side = "no"
            position = Position.SHORT
            try:
                price_dollars = float(market.get("no_ask_dollars", "0.50"))
            except (ValueError, TypeError):
                price_dollars = 0.50
            action = f"BUY NO on 'BTC above ${strike:,.0f}' (bearish)"
        else:  # conciliatory
            # Buy YES on "BTC above strike" → profits if BTC >= strike at settlement
            side = "yes"
            position = Position.LONG
            try:
                price_dollars = float(market.get("yes_ask_dollars", "0.50"))
            except (ValueError, TypeError):
                price_dollars = 0.50
            action = f"BUY YES on 'BTC above ${strike:,.0f}' (bullish)"

        price_dollars = round(max(price_dollars, 0.01), 2)

        # Skip trades where the contract is already deep in-the-money.
        # Paying >$0.70 leaves <$0.30 upside — terrible risk/reward.
        if price_dollars > 0.70:
            print(f"   Skipping {action} — contract too expensive (${price_dollars:.2f} > $0.70 cap)")
            return None

        price_cents = int(price_dollars * 100)
        num_contracts = max(1, int(size_usd / price_dollars))
        close_at = datetime.now() + timedelta(seconds=self.HOLD_SECONDS)

        if self.simulation_mode:
            order_id = f"SIM-{int(time.time())}"
            print(f"[SIMULATED] {action}")
            print(f"   {num_contracts} contracts @ ${price_dollars:.2f} = ${num_contracts * price_dollars:.2f}")
            print(f"   BTC now: ${btc_price:,.0f} | Strike: ${strike:,.0f} | Hold: 8h")
            print(f"   Ticker: {ticker} | Post: {post_id}")
        else:
            try:
                # Kalshi requires a price — use limit order at ask price
                # Use cents (integer) for price — simpler, no precision issues
                price_key = "no_price" if side == "no" else "yes_price"
                order_data = {
                    "ticker": ticker,
                    "action": "buy",
                    "side": side,
                    "type": "limit",
                    "count": num_contracts,
                    price_key: price_cents,
                }
                print(f"   ORDER: {order_data}")
                result = self._make_request("POST", "/portfolio/orders", data=order_data)
                order_id = result.get("order", {}).get("order_id", "UNKNOWN")
                print(f"[LIVE] {action}")
                print(f"   {num_contracts} contracts @ ${price_dollars:.2f} = ${num_contracts * price_dollars:.2f}")
                print(f"   BTC now: ${btc_price:,.0f} | Strike: ${strike:,.0f} | Hold: 8h")
                print(f"   Ticker: {ticker} | Order ID: {order_id}")
            except KalshiAPIError as e:
                logger.error(f"Failed to place order: {e}")
                return None

        return Trade(
            position=position,
            size=num_contracts,
            entry_price=price_dollars,
            timestamp=datetime.now(),
            post_id=post_id,
            sentiment=sentiment,
            order_id=order_id,
            instrument_id=ticker,
            close_at=close_at,
        )

    def close_position(self, trade: Trade) -> float:
        """Close a position by selling the contracts."""
        if not trade.instrument_id:
            return 0.0

        if self.simulation_mode:
            import random
            price_change = random.uniform(-0.10, 0.10)
            current_price = trade.entry_price + price_change
            pnl = (current_price - trade.entry_price) * trade.size
            print(f"[SIMULATED] Closed {trade.instrument_id}: ${trade.entry_price:.2f} → ${current_price:.2f}, PnL: ${pnl:.2f}")
            return pnl

        # Sell the contracts at a competitive price to fill quickly
        side = "no" if trade.position == Position.SHORT else "yes"
        # Sell at 1 cent below entry to ensure fill (willing to take small loss for speed)
        # Sell at 1 cent below entry to fill quickly
        sell_cents = max(1, int(trade.entry_price * 100) - 1)
        price_key = "no_price" if side == "no" else "yes_price"
        try:
            result = self._make_request("POST", "/portfolio/orders", data={
                "ticker": trade.instrument_id,
                "action": "sell",
                "side": side,
                "type": "limit",
                "count": int(trade.size),
                price_key: sell_cents,
            })

            fill_price = float(result.get("order", {}).get("avg_price", trade.entry_price))
            pnl = (fill_price - trade.entry_price) * trade.size
            print(f"[LIVE] Closed {trade.instrument_id}: ${trade.entry_price:.2f} → ${fill_price:.2f}, PnL: ${pnl:.2f}")
            return pnl

        except KalshiAPIError as e:
            logger.error(f"Failed to close position: {e}")
            return 0.0

    def list_positions(self) -> List[dict]:
        if self.simulation_mode:
            return []
        try:
            data = self._make_request("GET", "/portfolio/positions")
            return data.get("market_positions", [])
        except KalshiAPIError as e:
            logger.error(f"Failed to list positions: {e}")
            return []

    # ── Long-term position trading ─────────────────────────────────────────
    # Buy contracts when sentiment makes them cheap, sell when sentiment reverses.
    # "BTC above $100k by Jan 2027" gets cheaper on bellicose posts → buy.
    # Conciliatory posts push it back up → sell for profit.

    def search_yearly_markets(self) -> List[dict]:
        """Search for long-term BTC price markets (KXBTCY)."""
        if self.simulation_mode:
            return [
                {"ticker": "KXBTCY-27JAN0100-T149999.99", "subtitle": "BTC >= $150k on Jan 1, 2027",
                 "yes_ask_dollars": "0.0640", "no_ask_dollars": "0.9370",
                 "yes_bid_dollars": "0.0600", "no_bid_dollars": "0.9300",
                 "status": "active", "strike_price": 150000,
                 "close_time": "2027-01-01T05:00:00Z"},
            ]

        markets = []
        try:
            data = self._make_request("GET", "/markets", params={
                "series_ticker": self.BTC_YEARLY,
                "status": "open",
                "limit": 100,
            })
            if data.get("markets"):
                markets.extend(data["markets"])
        except KalshiAPIError:
            pass

        # Parse strike from tickers
        for m in markets:
            ticker = m.get("ticker", "")
            for prefix in ["-T", "-B"]:
                if prefix in ticker:
                    try:
                        m["strike_price"] = float(ticker.split(prefix)[-1])
                    except ValueError:
                        pass
                    break

        return markets

    def select_yearly_market(self, target_strike: float = None) -> Optional[dict]:
        """
        Select a yearly market to trade.

        Default: "BTC above $150k by Jan 2027" — the flagship long-term BTC bet.
        Can also target a specific strike.
        """
        markets = self.search_yearly_markets()
        if not markets:
            return None

        # Look for threshold markets (T prefix = "above X")
        thresholds = [m for m in markets if "-T" in m.get("ticker", "")]

        if target_strike and thresholds:
            # Find closest to target
            thresholds.sort(key=lambda m: abs(m.get("strike_price", 0) - target_strike))
            return thresholds[0]

        # Default: the $150k threshold (most liquid, good for long-term bullish bet)
        if thresholds:
            t150 = [m for m in thresholds if abs(m.get("strike_price", 0) - 150000) < 1000]
            if t150:
                return t150[0]
            # Fallback: highest volume threshold
            thresholds.sort(key=lambda m: float(m.get("volume_fp", "0")), reverse=True)
            return thresholds[0]

        return markets[0]

    def buy_long_term(self, side: str, ticker: str = None, target_strike: float = None,
                      size_usd: float = None, reason: str = "") -> Optional[Trade]:
        """
        Buy a long-term contract to hold and sell later when odds shift.

        side: "yes" or "no"
        ticker: specific market ticker, or auto-select if None
        target_strike: desired strike price for auto-selection
        """
        size_usd = min(size_usd or self.DEFAULT_SIZE, self.MAX_SIZE)

        if ticker:
            # Find specific market
            markets = self.search_yearly_markets()
            market = next((m for m in markets if m.get("ticker") == ticker), None)
        else:
            market = self.select_yearly_market(target_strike)

        if not market:
            logger.warning("No suitable yearly market found")
            return None

        mkt_ticker = market.get("ticker", "UNKNOWN")
        strike = market.get("strike_price", 0)

        if side == "yes":
            try:
                price_dollars = round(float(market.get("yes_ask_dollars", "0.10")), 2)
            except (ValueError, TypeError):
                price_dollars = 0.10
            position = Position.LONG
        else:
            try:
                price_dollars = round(float(market.get("no_ask_dollars", "0.90")), 2)
            except (ValueError, TypeError):
                price_dollars = 0.90
            position = Position.SHORT

        price_dollars = round(max(price_dollars, 0.01), 2)
        price_cents = int(price_dollars * 100)
        num_contracts = max(1, int(size_usd / price_dollars))

        action = f"BUY {side.upper()} on 'BTC {'above' if '-T' in mkt_ticker else 'in range'} ${strike:,.0f}' (long-term)"

        if self.simulation_mode:
            order_id = f"SIM-LT-{int(time.time())}"
            print(f"[SIMULATED] {action}")
            print(f"   {num_contracts} contracts @ ${price_dollars:.2f} = ${num_contracts * price_dollars:.2f}")
            print(f"   Reason: {reason}")
        else:
            price_key = "no_price" if side == "no" else "yes_price"
            order_data = {
                "ticker": mkt_ticker,
                "action": "buy",
                "side": side,
                "type": "limit",
                "count": num_contracts,
                price_key: price_cents,
            }
            try:
                print(f"   ORDER: {order_data}")
                result = self._make_request("POST", "/portfolio/orders", data=order_data)
                order_id = result.get("order", {}).get("order_id", "UNKNOWN")
                fill_cost = result.get("order", {}).get("taker_fill_cost_dollars", "?")
                print(f"[LIVE] {action}")
                print(f"   {num_contracts} contracts @ ${price_dollars:.2f} | Fill cost: ${fill_cost}")
                print(f"   Reason: {reason}")
            except KalshiAPIError as e:
                logger.error(f"Failed to place long-term order: {e}")
                return None

        return Trade(
            position=position,
            size=num_contracts,
            entry_price=price_dollars,
            timestamp=datetime.now(),
            post_id=reason,
            sentiment=f"longterm_{side}",
            order_id=order_id,
            instrument_id=mkt_ticker,
        )

    # ── Oil trading ─────────────────────────────────────────────────────────
    # Hormuz disruption → oil up. Used alongside BTC shorts on bearish signals.

    def search_oil_markets(self) -> List[dict]:
        """Search for WTI oil directional markets."""
        if self.simulation_mode:
            return [
                {"ticker": "KXWTI-SIM-T99.99", "subtitle": "100.0 or above",
                 "yes_ask_dollars": "0.3500", "no_ask_dollars": "0.7000",
                 "status": "active", "strike_price": 100,
                 "floor_strike": 100},
            ]

        markets = []
        try:
            data = self._make_request("GET", "/markets", params={
                "series_ticker": self.WTI_OIL,
                "status": "open",
                "limit": 100,
            })
            if data.get("markets"):
                markets.extend(data["markets"])
        except KalshiAPIError:
            pass

        for m in markets:
            ticker = m.get("ticker", "")
            if "-T" in ticker:
                try:
                    m["strike_price"] = float(ticker.split("-T")[-1])
                except ValueError:
                    pass

        return markets

    def execute_oil_trade(self, direction: str, post_id: str, size_usd: float = None) -> Optional[Trade]:
        """
        Trade WTI oil directional contracts.

        direction: "long" (oil going up) or "short" (oil going down)
        Hormuz blocked → long oil (buy YES on "WTI above X")
        Hormuz reopening → short oil (buy NO on "WTI above X")
        """
        size_usd = min(size_usd or self.DEFAULT_SIZE, self.MAX_SIZE)

        markets = self.search_oil_markets()
        if not markets:
            logger.warning("No WTI oil markets found")
            return None

        # Filter for reasonable prices
        priced = [m for m in markets if m.get("strike_price")]

        if direction == "long":
            # Oil going up → buy YES on a strike slightly above current
            # Pick OTM ~1-2% for leverage
            candidates = [m for m in priced
                          if float(m.get("yes_ask_dollars", "1.00")) < 0.95
                          and float(m.get("yes_ask_dollars", "1.00")) > 0.05]
            if candidates:
                # Sort by YES price ascending — cheapest reasonable bet
                candidates.sort(key=lambda m: float(m.get("yes_ask_dollars", "1.00")))
                # Pick one in the mid-range (not too deep ITM, not too far OTM)
                market = candidates[len(candidates) // 2]
            else:
                return None
            side = "yes"
            position = Position.LONG
            try:
                price_dollars = round(float(market.get("yes_ask_dollars", "0.50")), 2)
            except (ValueError, TypeError):
                price_dollars = 0.50
        else:
            # Oil going down → buy NO on a strike
            candidates = [m for m in priced
                          if float(m.get("no_ask_dollars", "1.00")) < 0.95
                          and float(m.get("no_ask_dollars", "1.00")) > 0.05]
            if candidates:
                candidates.sort(key=lambda m: float(m.get("no_ask_dollars", "1.00")))
                market = candidates[len(candidates) // 2]
            else:
                return None
            side = "no"
            position = Position.SHORT
            try:
                price_dollars = round(float(market.get("no_ask_dollars", "0.50")), 2)
            except (ValueError, TypeError):
                price_dollars = 0.50

        ticker = market.get("ticker", "UNKNOWN")
        strike = market.get("strike_price", 0)
        price_dollars = round(max(price_dollars, 0.01), 2)
        price_cents = int(price_dollars * 100)
        num_contracts = max(1, int(size_usd / price_dollars))

        action = f"OIL: BUY {side.upper()} on 'WTI above ${strike:.0f}' ({'bullish' if direction == 'long' else 'bearish'} oil)"

        if self.simulation_mode:
            order_id = f"SIM-OIL-{int(time.time())}"
            print(f"[SIMULATED] {action}")
            print(f"   {num_contracts} contracts @ ${price_dollars:.2f} = ${num_contracts * price_dollars:.2f}")
        else:
            price_key = "no_price" if side == "no" else "yes_price"
            try:
                order_data = {
                    "ticker": ticker,
                    "action": "buy",
                    "side": side,
                    "type": "limit",
                    "count": num_contracts,
                    price_key: price_cents,
                }
                result = self._make_request("POST", "/portfolio/orders", data=order_data)
                order_id = result.get("order", {}).get("order_id", "UNKNOWN")
                print(f"[LIVE] {action}")
                print(f"   {num_contracts} contracts @ ${price_dollars:.2f} | Ticker: {ticker}")
            except KalshiAPIError as e:
                logger.error(f"Failed to place oil order: {e}")
                return None

        return Trade(
            position=position,
            size=num_contracts,
            entry_price=price_dollars,
            timestamp=datetime.now(),
            post_id=post_id,
            sentiment=f"oil_{direction}",
            order_id=order_id,
            instrument_id=ticker,
            close_at=datetime.now() + timedelta(seconds=self.HOLD_SECONDS),
        )

    # ── Generalized commodity trading ───────────────────────────────────────
    # Same mid-OTM reprice pattern as oil/copper, parameterized by series
    # ticker. Use this for nat gas, cobalt, lithium, and any future commodity
    # Kalshi adds that follows the "X above $Y on [date]" structure.

    def search_commodity_markets(self, series_ticker: str) -> List[dict]:
        """Search for open directional markets in an arbitrary Kalshi series."""
        if self.simulation_mode:
            return [
                {"ticker": f"{series_ticker}-SIM-T1.00", "subtitle": "1.00 or above",
                 "yes_ask_dollars": "0.4500", "no_ask_dollars": "0.5800",
                 "status": "active", "strike_price": 1.00, "floor_strike": 1.00},
                {"ticker": f"{series_ticker}-SIM-T1.10", "subtitle": "1.10 or above",
                 "yes_ask_dollars": "0.3000", "no_ask_dollars": "0.7200",
                 "status": "active", "strike_price": 1.10, "floor_strike": 1.10},
            ]

        markets = []
        try:
            data = self._make_request("GET", "/markets", params={
                "series_ticker": series_ticker,
                "status": "open",
                "limit": 100,
            })
            if data.get("markets"):
                markets.extend(data["markets"])
        except KalshiAPIError:
            pass

        for m in markets:
            ticker = m.get("ticker", "")
            if "-T" in ticker:
                try:
                    m["strike_price"] = float(ticker.split("-T")[-1])
                except ValueError:
                    pass
        return markets

    def execute_commodity_trade(
        self,
        series_ticker: str,
        direction: str,
        post_id: str,
        label: str = None,
        size_usd: float = None,
    ) -> Optional[Trade]:
        """
        Buy a directional Kalshi contract in any commodity series.

        series_ticker: e.g. self.NATGAS, self.COPPER
        direction:     "long" (supply cut → price up) or "short" (resolution)
        label:         display name ("Nat Gas", "Copper") for log output
        """
        size_usd = min(size_usd or self.DEFAULT_SIZE, self.MAX_SIZE)
        label = label or series_ticker

        markets = self.search_commodity_markets(series_ticker)
        if not markets:
            logger.warning(f"No {label} markets found on Kalshi ({series_ticker})")
            return None

        priced = [m for m in markets if m.get("strike_price")]
        if not priced:
            return None

        if direction == "long":
            candidates = [m for m in priced
                          if 0.05 < float(m.get("yes_ask_dollars", "1.00")) < 0.95]
            if not candidates:
                return None
            candidates.sort(key=lambda m: float(m.get("yes_ask_dollars", "1.00")))
            market = candidates[len(candidates) // 2]
            side = "yes"
            position = Position.LONG
            try:
                price_dollars = round(float(market.get("yes_ask_dollars", "0.50")), 2)
            except (ValueError, TypeError):
                price_dollars = 0.50
        else:
            candidates = [m for m in priced
                          if 0.05 < float(m.get("no_ask_dollars", "1.00")) < 0.95]
            if not candidates:
                return None
            candidates.sort(key=lambda m: float(m.get("no_ask_dollars", "1.00")))
            market = candidates[len(candidates) // 2]
            side = "no"
            position = Position.SHORT
            try:
                price_dollars = round(float(market.get("no_ask_dollars", "0.50")), 2)
            except (ValueError, TypeError):
                price_dollars = 0.50

        ticker = market.get("ticker", "UNKNOWN")
        strike = market.get("strike_price", 0)
        price_dollars = round(max(price_dollars, 0.01), 2)
        price_cents = int(price_dollars * 100)
        num_contracts = max(1, int(size_usd / price_dollars))

        action = f"{label.upper()}: BUY {side.upper()} on strike {strike} ({'bullish' if direction == 'long' else 'bearish'})"

        if self.simulation_mode:
            order_id = f"SIM-{series_ticker}-{int(time.time())}"
            print(f"[SIMULATED] {action}")
            print(f"   {num_contracts} contracts @ ${price_dollars:.2f} = ${num_contracts * price_dollars:.2f}")
        else:
            price_key = "no_price" if side == "no" else "yes_price"
            try:
                order_data = {
                    "ticker": ticker,
                    "action": "buy",
                    "side": side,
                    "type": "limit",
                    "count": num_contracts,
                    price_key: price_cents,
                }
                result = self._make_request("POST", "/portfolio/orders", data=order_data)
                order_id = result.get("order", {}).get("order_id", "UNKNOWN")
                print(f"[LIVE] {action}")
                print(f"   {num_contracts} contracts @ ${price_dollars:.2f} | Ticker: {ticker}")
            except KalshiAPIError as e:
                logger.error(f"Failed to place {label} order: {e}")
                return None

        return Trade(
            position=position,
            size=num_contracts,
            entry_price=price_dollars,
            timestamp=datetime.now(),
            post_id=post_id,
            sentiment=f"{label.lower().replace(' ', '_')}_{direction}",
            order_id=order_id,
            instrument_id=ticker,
            close_at=datetime.now() + timedelta(seconds=self.HOLD_SECONDS),
        )

    # ── Copper trading (legacy wrapper — kept for compat with Hormuz/oil path idioms) ──

    def search_copper_markets(self) -> List[dict]:
        """Search for copper directional markets (KXCOPPERD)."""
        if self.simulation_mode:
            return [
                {"ticker": "KXCOPPERD-SIM-T4.50", "subtitle": "4.50 or above",
                 "yes_ask_dollars": "0.4500", "no_ask_dollars": "0.5800",
                 "status": "active", "strike_price": 4.50, "floor_strike": 4.50},
                {"ticker": "KXCOPPERD-SIM-T4.60", "subtitle": "4.60 or above",
                 "yes_ask_dollars": "0.3000", "no_ask_dollars": "0.7200",
                 "status": "active", "strike_price": 4.60, "floor_strike": 4.60},
            ]

        markets = []
        try:
            data = self._make_request("GET", "/markets", params={
                "series_ticker": self.COPPER,
                "status": "open",
                "limit": 100,
            })
            if data.get("markets"):
                markets.extend(data["markets"])
        except KalshiAPIError:
            pass

        for m in markets:
            ticker = m.get("ticker", "")
            if "-T" in ticker:
                try:
                    m["strike_price"] = float(ticker.split("-T")[-1])
                except ValueError:
                    pass

        return markets

    def execute_copper_trade(self, direction: str, post_id: str, size_usd: float = None) -> Optional[Trade]:
        """
        Trade copper directional contracts.

        direction: "long" (supply cut → price up) or "short" (resolution → price down)
        Picks the mid-priced reasonable candidate for leverage without being too deep OTM.
        """
        size_usd = min(size_usd or self.DEFAULT_SIZE, self.MAX_SIZE)

        markets = self.search_copper_markets()
        if not markets:
            logger.warning("No copper markets found on Kalshi")
            return None

        priced = [m for m in markets if m.get("strike_price")]
        if not priced:
            return None

        if direction == "long":
            candidates = [m for m in priced
                          if 0.05 < float(m.get("yes_ask_dollars", "1.00")) < 0.95]
            if not candidates:
                return None
            candidates.sort(key=lambda m: float(m.get("yes_ask_dollars", "1.00")))
            market = candidates[len(candidates) // 2]
            side = "yes"
            position = Position.LONG
            try:
                price_dollars = round(float(market.get("yes_ask_dollars", "0.50")), 2)
            except (ValueError, TypeError):
                price_dollars = 0.50
        else:
            candidates = [m for m in priced
                          if 0.05 < float(m.get("no_ask_dollars", "1.00")) < 0.95]
            if not candidates:
                return None
            candidates.sort(key=lambda m: float(m.get("no_ask_dollars", "1.00")))
            market = candidates[len(candidates) // 2]
            side = "no"
            position = Position.SHORT
            try:
                price_dollars = round(float(market.get("no_ask_dollars", "0.50")), 2)
            except (ValueError, TypeError):
                price_dollars = 0.50

        ticker = market.get("ticker", "UNKNOWN")
        strike = market.get("strike_price", 0)
        price_dollars = round(max(price_dollars, 0.01), 2)
        price_cents = int(price_dollars * 100)
        num_contracts = max(1, int(size_usd / price_dollars))

        action = f"COPPER: BUY {side.upper()} on 'Cu above ${strike:.2f}' ({'bullish' if direction == 'long' else 'bearish'} Cu)"

        if self.simulation_mode:
            order_id = f"SIM-CU-{int(time.time())}"
            print(f"[SIMULATED] {action}")
            print(f"   {num_contracts} contracts @ ${price_dollars:.2f} = ${num_contracts * price_dollars:.2f}")
        else:
            price_key = "no_price" if side == "no" else "yes_price"
            try:
                order_data = {
                    "ticker": ticker,
                    "action": "buy",
                    "side": side,
                    "type": "limit",
                    "count": num_contracts,
                    price_key: price_cents,
                }
                result = self._make_request("POST", "/portfolio/orders", data=order_data)
                order_id = result.get("order", {}).get("order_id", "UNKNOWN")
                print(f"[LIVE] {action}")
                print(f"   {num_contracts} contracts @ ${price_dollars:.2f} | Ticker: {ticker}")
            except KalshiAPIError as e:
                logger.error(f"Failed to place copper order: {e}")
                return None

        return Trade(
            position=position,
            size=num_contracts,
            entry_price=price_dollars,
            timestamp=datetime.now(),
            post_id=post_id,
            sentiment=f"copper_{direction}",
            order_id=order_id,
            instrument_id=ticker,
            close_at=datetime.now() + timedelta(seconds=self.HOLD_SECONDS),
        )

    def sell_long_term(self, trade: Trade) -> float:
        """Sell a long-term position to take profit or cut losses."""
        if not trade.instrument_id:
            return 0.0

        side = "no" if trade.position == Position.SHORT else "yes"

        if self.simulation_mode:
            import random
            price_change = random.uniform(-0.02, 0.04)
            current_price = trade.entry_price + price_change
            pnl = (current_price - trade.entry_price) * trade.size
            print(f"[SIMULATED] Sold long-term {trade.instrument_id}: ${trade.entry_price:.2f} → ${current_price:.2f}, PnL: ${pnl:.2f}")
            return pnl

        # Sell at 1 cent below current ask to fill fast
        sell_cents = max(1, int(trade.entry_price * 100) - 1)
        price_key = "no_price" if side == "no" else "yes_price"
        try:
            result = self._make_request("POST", "/portfolio/orders", data={
                "ticker": trade.instrument_id,
                "action": "sell",
                "side": side,
                "type": "limit",
                "count": int(trade.size),
                price_key: sell_cents,
            })
            fill_price = float(result.get("order", {}).get("avg_price", trade.entry_price))
            pnl = (fill_price - trade.entry_price) * trade.size
            print(f"[LIVE] Sold long-term {trade.instrument_id}: ${trade.entry_price:.2f} → ${fill_price:.2f}, PnL: ${pnl:.2f}")
            return pnl
        except KalshiAPIError as e:
            logger.error(f"Failed to sell long-term position: {e}")
            return 0.0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    trader = KalshiTrader()
    print(f"\nBalance: ${trader.get_balance():,.2f}")
    btc = trader.get_current_btc_price()
    print(f"BTC Price: ${btc:,.2f}")

    print("\n--- Directional Markets (near current price) ---")
    markets = trader.search_directional_markets()
    near = sorted([m for m in markets if m.get("strike_price")],
                  key=lambda m: abs(m["strike_price"] - btc))
    for m in near[:6]:
        strike = m.get("strike_price", 0)
        yes = m.get("yes_ask_dollars", "?")
        no = m.get("no_ask_dollars", "?")
        print(f"  'BTC above ${strike:,.0f}': YES=${yes} NO=${no}")

    print("\n--- Test Trades ---")
    trader.execute_trade("bellicose", "test-bellicose", 100)
    print()
    trader.execute_trade("conciliatory", "test-conciliatory", 100)
    print()
    trader.execute_trade("mixed", "test-mixed", 100)
    print()
    trader.execute_trade("neutral", "test-neutral", 100)
