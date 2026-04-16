"""
Main trading agent - fetches from Twitter + Truth Social, classifies sentiment, trades on Kraken (or Coinbase/OKX).
"""

import time
import os
from datetime import datetime, timedelta
from typing import List, Optional

from src.fetcher import PresidentialPostFetcher, PresidentialPost
from src.truthsocial_fetcher import TruthSocialFetcher, TruthSocialPost
from src.sentiment import IranSentimentClassifier, Sentiment
from src.hormuz_monitor import HormuzMonitor
from src.hormuz_incident_monitor import HormuzIncidentMonitor
from src.supply_shock_monitor import SupplyShockMonitor, SPEC_BY_NAME
from src.wires_fetcher import WiresFetcher
from src.journal import TradeJournal

import config.config as cfg

# Support multiple exchanges
from src.trader import CoinbasePerpsTrader, Trade
from src.okx_trader import OKXTrader
from src.kraken_trader import KrakenTrader
from src.kalshi_trader import KalshiTrader
from src.dydx_trader import DYDXTrader


class IranSentimentTrader:
    """
    Main trading agent that:
    1. Fetches presidential posts about Iran (Twitter + Truth Social)
    2. Classifies sentiment using LLM
    3. Executes trades on Coinbase (perpetual futures)
    """

    DEFAULT_POLL_INTERVAL = 60  # seconds
    MAX_HOLD_TIME = 28800        # 8 hours max hold (backtested optimal)
    MAX_TRADES_PER_HOUR = 3

    def __init__(
        self,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        hold_time: int = MAX_HOLD_TIME,
        trade_type: str = "perpetual",  # "perpetual" or "option"
        exchange: str = "kraken"  # "kraken", "coinbase", or "okx"
    ):
        # Initialize fetchers
        self.twitter_fetcher = PresidentialPostFetcher()
        self.truthsocial_fetcher = TruthSocialFetcher()
        self.classifier = IranSentimentClassifier()
        self.wires_fetcher = WiresFetcher() if cfg.ENABLE_WIRES else None

        # Initialize the specified exchange trader
        if exchange == "kraken":
            self.trader = KrakenTrader()
            exchange_name = "Kraken"
        elif exchange == "coinbase":
            self.trader = CoinbasePerpsTrader()
            exchange_name = "Coinbase"
        elif exchange == "okx":
            self.trader = OKXTrader()
            exchange_name = "OKX"
        elif exchange == "kalshi":
            self.trader = KalshiTrader()
            exchange_name = "Kalshi"
        elif exchange == "dydx":
            self.trader = DYDXTrader()
            exchange_name = "dYdX"
        else:
            self.trader = KrakenTrader()
            exchange_name = "Kraken"

        self.poll_interval = poll_interval
        self.hold_time = hold_time
        self.trade_type = trade_type
        self.exchange = exchange

        self.active_trades: List[Trade] = []
        self.trade_timestamps: List[datetime] = []
        self.current_bias: Optional[str] = None  # tracks latest signal direction
        self.hormuz_monitor = HormuzMonitor()
        self.hormuz_incident_monitor = HormuzIncidentMonitor(
            twitter_fetcher=self.twitter_fetcher
        )
        self.supply_shock_monitor = SupplyShockMonitor(
            twitter_fetcher=self.twitter_fetcher,
            enable_osint=True,  # wires only — see OSINT_WIRES in supply_shock_monitor
        )
        self.hormuz_trades: List[Trade] = []    # trades opened by Hormuz signal
        self.supply_trades: List[Trade] = []    # trades opened by supply-shock signal
        self.longterm_trades: List[Trade] = []  # long-term contract positions
        self._seen_post_ids: set = set()  # prevents trading on old posts after restart
        # Recent log-only supply-shock events — used as confirmation boost for
        # the oil trade path (e.g., a Qatar LNG force-majeure log within 24h of
        # a bellicose Iran post raises oil trade size). (ts, commodity, direction)
        self.recent_log_only_signals: List[tuple] = []

        # Ops + config
        self._last_heartbeat: datetime = datetime.min
        self.heartbeat_interval = cfg.HEARTBEAT_INTERVAL_SECONDS
        self.snapshot_cutoff_minutes = cfg.STARTUP_SNAPSHOT_CUTOFF_MINUTES
        self.trade_mixed = cfg.TRADE_MIXED_SIGNALS
        self.mixed_trade_size_usd = cfg.MIXED_TRADE_SIZE_USD
        self._last_counts = {"twitter": 0, "truthsocial": 0, "wires": 0}
        # Trade journal
        self.journal = TradeJournal(cfg.TRADE_JOURNAL_PATH, cfg.TRADE_JOURNAL_STDOUT) if cfg.TRADE_JOURNAL_ENABLED else None

    def _journal_open(self, trade: Trade, category: str, source: str, text: str):
        if not self.journal or not trade:
            return
        self.journal.log_open(
            exchange=self.exchange,
            trader_class=self.trader.__class__.__name__,
            trade=trade,
            category=category,
            source=source,
            text_snippet=text,
            simulated=getattr(self.trader, 'simulation_mode', True),
        )

    def _journal_close(self, trade: Trade, pnl: float, reason: str):
        if not self.journal or not trade:
            return
        self.journal.log_close(
            exchange=self.exchange,
            trader_class=self.trader.__class__.__name__,
            trade=trade,
            pnl=pnl,
            reason=reason,
            simulated=getattr(self.trader, 'simulation_mode', True),
        )

        sim = "SIMULATION" if getattr(self.trader, 'simulation_mode', True) else "LIVE"
        print(f"Iran Sentiment Trader initialized")
        print(f"   Sources: Twitter + Truth Social + Hormuz Strait traffic")
        print(f"   Exchange: {exchange_name} ({trade_type}) [{sim}]")
        print(f"   Poll interval: {poll_interval}s")
        print(f"   Max hold: {hold_time // 3600}h (closes early on contrary signal)")
        print(f"   Max trades/hour: {self.MAX_TRADES_PER_HOUR}")

    def can_trade(self) -> bool:
        """Check if we can execute a new trade (rate limiting)."""
        cutoff = datetime.now() - timedelta(hours=1)
        self.trade_timestamps = [t for t in self.trade_timestamps if t > cutoff]
        return len(self.trade_timestamps) < self.MAX_TRADES_PER_HOUR

    def fetch_all_posts(self, quiet: bool = False) -> List[PresidentialPost]:
        """Fetch posts from all sources."""
        all_posts = []

        # Fetch from Twitter
        try:
            twitter_posts = self.twitter_fetcher.fetch_recent_posts()
            all_posts.extend(twitter_posts)
            if not quiet:
                print(f"   Twitter: {len(twitter_posts)} Iran posts")
        except Exception as e:
            if not quiet:
                print(f"   Twitter error: {e}")

        # Fetch from Truth Social
        try:
            ts_posts = self.truthsocial_fetcher.fetch_recent_posts()
            for p in ts_posts:
                all_posts.append(PresidentialPost(
                    id=p.id,
                    text=p.text,
                    source="truthsocial",
                    timestamp=p.timestamp
                ))
            if not quiet:
                print(f"   Truth Social: {len(ts_posts)} Iran posts")
        except Exception as e:
            if not quiet:
                print(f"   Truth Social error: {e}")

        # Fetch from news wires (optional)
        wires_count = 0
        if self.wires_fetcher:
            try:
                wire_items = self.wires_fetcher.fetch_recent_items()
                wires_count = len(wire_items)
                for w in wire_items:
                    all_posts.append(PresidentialPost(
                        id=w.id,
                        text=w.text,
                        source=f"wire:{w.source}",
                        timestamp=w.timestamp
                    ))
                if not quiet:
                    print(f"   Wires: {wires_count} Iran headlines")
            except Exception as e:
                if not quiet:
                    print(f"   Wires error: {e}")

        # Sort by timestamp (newest first) — normalise to UTC so aware/naive don't collide
        from datetime import timezone as _tz
        def _ts_key(p):
            ts = p.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_tz.utc)
            return ts
        all_posts.sort(key=_ts_key, reverse=True)
        # Save counts for heartbeat
        self._last_counts = {
            "twitter": len([p for p in all_posts if p.source not in ("truthsocial",) and not str(p.source).startswith("wire:")]),
            "truthsocial": len([p for p in all_posts if p.source == "truthsocial"]),
            "wires": wires_count,
        }
        return all_posts

    def _close_contrary_positions(self, new_sentiment: str):
        """
        Close any open positions that conflict with the new signal.

        If we're short (bellicose) and get a conciliatory signal → close the short.
        If we're long (conciliatory) and get a bellicose signal → close the long.
        Bellicose also closes any Hormuz-based bullish positions.
        """
        all_trades = self.active_trades + self.hormuz_trades

        if not all_trades:
            return

        contrary = []
        for trade in all_trades[:]:
            is_contrary = (
                (trade.sentiment == "bellicose" and new_sentiment == "conciliatory") or
                (trade.sentiment == "conciliatory" and new_sentiment == "bellicose") or
                # Bellicose tweet kills any bullish Hormuz positions (BTC + oil)
                (trade.sentiment in ("hormuz_bullish", "hormuz_bullish_oil") and new_sentiment == "bellicose") or
                # Conciliatory tweet kills any bearish Hormuz positions
                (trade.sentiment in ("hormuz_bearish", "hormuz_bearish_oil") and new_sentiment == "conciliatory")
            )
            if is_contrary:
                contrary.append(trade)

        if contrary:
            print(f"   Contrary signal — closing {len(contrary)} position(s)")

        for trade in contrary:
            pnl = self.trader.close_position(trade)
            if trade in self.active_trades:
                self.active_trades.remove(trade)
            if trade in self.hormuz_trades:
                self.hormuz_trades.remove(trade)
            age_min = (datetime.now() - trade.timestamp).total_seconds() / 60
            print(f"   Closed {trade.sentiment} position after {age_min:.0f}m, PnL: ${pnl:.2f}")
            self._journal_close(trade, pnl, reason="contrary_signal")

    def process_post(self, post: PresidentialPost) -> Optional[Trade]:
        """
        Process a single post: classify sentiment and potentially trade.

        On contrary signals, closes existing positions before opening new ones.
        Skips mixed and neutral signals (but mixed still triggers position review).
        """
        print(f"\n Processing post from {post.source}:")
        print(f"   {post.text[:80]}...")

        # Classify sentiment
        sentiment = self.classifier.classify(post.text)
        icon = {"bellicose": "[BELL]", "conciliatory": "[CONC]",
                "mixed": "[MIXD]", "neutral": "[NEUT]"}.get(sentiment.value, "?")
        print(f"   -> Sentiment: {icon} {sentiment.value}")

        # On any actionable or mixed signal, check for contrary positions to close
        if sentiment.value in ("bellicose", "conciliatory"):
            self._close_contrary_positions(sentiment.value)
            self.current_bias = sentiment.value

        # Only open new trades on pure bellicose or pure conciliatory
        if sentiment.value in ("bellicose", "conciliatory"):
            if not self.can_trade():
                print(f"   Rate limited ({self.MAX_TRADES_PER_HOUR}/hr) — skipping trade")
                return None

            # Guard: don't stack another trade in the same direction if we already
            # hold an active position on the same thesis. Wait for it to close first.
            existing_directions = {t.sentiment for t in self.active_trades}
            if sentiment.value in existing_directions:
                print(f"   Already holding {sentiment.value} position — skipping duplicate")
                return None

            # 1) Short-term directional trade (8h hold)
            trade = self.trader.execute_trade(
                sentiment=sentiment.value,
                post_id=post.id,
                trade_type=self.trade_type
            )

            if trade:
                self.active_trades.append(trade)
                self.trade_timestamps.append(datetime.now())
                trade.close_at = datetime.now() + timedelta(seconds=self.hold_time)
                print(f"   Short-term hold: up to {self.hold_time // 3600}h (or until contrary signal)")
                # Journal open
                category = (
                    "wire" if str(post.source).startswith("wire:") else (
                        "truthsocial" if post.source == "truthsocial" else "twitter"
                    )
                )
                self._journal_open(trade, category=category, source=str(post.source), text=post.text)

            # 2) Long-term position management
            self._manage_longterm_position(sentiment.value, post.id)

            return trade

        if sentiment.value == "mixed":
            if self.trade_mixed and self.current_bias in ("bellicose", "conciliatory"):
                if not self.can_trade():
                    print(f"   Mixed signal — rate limited, skipping small trade")
                    return None
                print(f"   Mixed signal — following current bias ({self.current_bias}) with reduced size")
                trade = self.trader.execute_trade(
                    sentiment=self.current_bias,
                    post_id=post.id,
                    size_usd=self.mixed_trade_size_usd,
                    trade_type=self.trade_type,
                )
                if trade:
                    self.active_trades.append(trade)
                    self.trade_timestamps.append(datetime.now())
                    trade.close_at = datetime.now() + timedelta(seconds=self.hold_time)
                    self._journal_open(trade, category="mixed_follow_bias", source=str(post.source), text=post.text)
                return trade
            else:
                print(f"   Mixed signal — no new trade, monitoring positions")

        return None

    def _manage_longterm_position(self, sentiment: str, post_id: str):
        """
        Manage long-term contract positions based on sentiment.

        Strategy: buy fear, sell hope.
          BELLICOSE → market scared, long-term BTC contracts are cheap → BUY YES
          CONCILIATORY → market relieved, contracts recover → SELL for profit

        We accumulate on bellicose signals and take profit on conciliatory signals.
        """
        if not hasattr(self.trader, "buy_long_term"):
            return

        if sentiment == "bellicose":
            # Contracts are cheap — accumulate if we don't have too many
            if len(self.longterm_trades) >= 3:
                print(f"   Long-term: already holding {len(self.longterm_trades)} positions, skipping")
                return

            print(f"   Long-term: buying fear (BTC contracts cheap on bellicose)")
            trade = self.trader.buy_long_term(
                side="yes",
                target_strike=150000,  # "BTC above $150k by Jan 2027" — $0.06, huge volume
                reason=f"bellicose-accumulate-{post_id}",
            )
            if trade:
                self.longterm_trades.append(trade)
                self._journal_open(trade, category="longterm", source="kalshi_yearly", text="accumulate fear on bellicose")

        elif sentiment == "conciliatory" and self.longterm_trades:
            # Market recovering — sell long-term positions for profit
            print(f"   Long-term: selling hope ({len(self.longterm_trades)} position(s) to close)")
            for trade in self.longterm_trades[:]:
                pnl = self.trader.sell_long_term(trade)
                self.longterm_trades.remove(trade)
                print(f"   Long-term closed: PnL ${pnl:.2f}")
                self._journal_close(trade, pnl, reason="longterm_take_profit")

    def check_hormuz_traffic(self):
        """
        Check Hormuz Strait traffic and trade accordingly.

        Bullish (traffic up):  long BTC, short oil
        Bearish (traffic down/zero): short BTC, long oil
        All Hormuz positions close on contrary tweet signals.
        """
        signal = self.hormuz_monitor.check()
        if not signal:
            return

        reading = self.hormuz_monitor.get_latest()
        vessels = reading.vessels_detected if reading else "?"
        trend = self.hormuz_monitor.get_trend() or "unknown"

        post_id = f"hormuz-{int(datetime.now().timestamp())}"

        # Close any tweet-based positions that conflict with this Hormuz signal
        if signal in ("bullish", "very_bullish"):
            # Hormuz bullish → close any bearish tweet positions
            self._close_contrary_positions("conciliatory")
        elif signal in ("bearish", "very_bearish"):
            # Hormuz bearish → close any bullish tweet positions
            self._close_contrary_positions("bellicose")

        # Guard: skip if we already hold a same-direction Hormuz BTC position
        existing_hormuz_btc_sentiments = {
            t.sentiment for t in self.hormuz_trades
            if t.sentiment in ("hormuz_bullish", "hormuz_bearish")
        }

        if signal in ("bullish", "very_bullish") and self.can_trade():
            if "hormuz_bullish" in existing_hormuz_btc_sentiments:
                print(f"   [HORMUZ] Already holding bullish BTC position — skipping duplicate")
            else:
                # Traffic increasing → de-escalation → long BTC + short oil
                print(f"\n [HORMUZ] Traffic UP → long BTC, short oil")

                btc_trade = self.trader.execute_trade(
                    sentiment="conciliatory", post_id=post_id, trade_type=self.trade_type
                )
                if btc_trade:
                    btc_trade.sentiment = "hormuz_bullish"
                    btc_trade.close_at = datetime.now() + timedelta(seconds=self.hold_time)
                    self.hormuz_trades.append(btc_trade)
                    self.trade_timestamps.append(datetime.now())
                    self._journal_open(btc_trade, category="hormuz", source="hormuz_monitor", text=f"Traffic up; vessels={vessels}, trend={trend}")

                if hasattr(self.trader, "execute_oil_trade"):
                    oil_trade = self.trader.execute_oil_trade("short", post_id)
                    if oil_trade:
                        oil_trade.sentiment = "hormuz_bullish_oil"
                        self.hormuz_trades.append(oil_trade)
                        self._journal_open(oil_trade, category="hormuz_oil", source="hormuz_monitor", text=f"Traffic up; vessels={vessels}, trend={trend}")

        elif signal in ("bearish", "very_bearish") and self.can_trade():
            if "hormuz_bearish" in existing_hormuz_btc_sentiments:
                print(f"   [HORMUZ] Already holding bearish BTC position — skipping duplicate")
            else:
                # Traffic dropping → escalation → short BTC + long oil
                print(f"\n [HORMUZ] Traffic DOWN/ZERO → short BTC, long oil")

                btc_trade = self.trader.execute_trade(
                    sentiment="bellicose", post_id=post_id, trade_type=self.trade_type
                )
                if btc_trade:
                    btc_trade.sentiment = "hormuz_bearish"
                    btc_trade.close_at = datetime.now() + timedelta(seconds=self.hold_time)
                    self.hormuz_trades.append(btc_trade)
                    self.trade_timestamps.append(datetime.now())
                    self._journal_open(btc_trade, category="hormuz", source="hormuz_monitor", text=f"Traffic down/zero; vessels={vessels}, trend={trend}")

                if hasattr(self.trader, "execute_oil_trade"):
                    size_usd = self._oil_size_usd()
                    oil_trade = self.trader.execute_oil_trade("long", post_id, size_usd=size_usd)
                    if oil_trade:
                        oil_trade.sentiment = "hormuz_bearish_oil"
                        if size_usd:
                            print(f"   [BOOST] oil size doubled via log-only nat gas confirmation")
                        self.hormuz_trades.append(oil_trade)
                        self._journal_open(oil_trade, category="hormuz_oil", source="hormuz_monitor", text=f"Traffic down/zero; vessels={vessels}, trend={trend}")

    def check_hormuz_incidents(self):
        """
        Check for reported attacks on shipping in the Strait of Hormuz.
        On a confirmed incident → long oil (supply shock expectation).
        """
        if not hasattr(self.trader, "execute_oil_trade"):
            return

        report = self.hormuz_incident_monitor.check()
        if not report:
            return

        if not self.can_trade():
            print(f"\n [INCIDENT] Hormuz attack report but rate-limited — skipping")
            return

        post_id = f"hormuz-incident-{int(datetime.now().timestamp())}"
        size_usd = self._oil_size_usd()
        print(f"\n [INCIDENT] Hormuz ship attack reported via {report.source}")
        print(f"   {report.text[:120]}")
        print(f"   → long oil{' (BOOSTED)' if size_usd else ''}")

        oil_trade = self.trader.execute_oil_trade("long", post_id, size_usd=size_usd)
        if oil_trade:
            oil_trade.sentiment = "hormuz_incident_oil"
            oil_trade.close_at = datetime.now() + timedelta(seconds=self.hold_time)
            self.hormuz_trades.append(oil_trade)
            self.trade_timestamps.append(datetime.now())
            self._journal_open(oil_trade, category="hormuz_incident", source=report.source, text=report.text)

    def _prune_log_only_signals(self):
        """Drop log-only confirmations older than 24h from the buffer."""
        cutoff = datetime.now() - timedelta(hours=24)
        self.recent_log_only_signals = [
            s for s in self.recent_log_only_signals if s[0] > cutoff
        ]

    def _oil_size_usd(self) -> Optional[float]:
        """
        Size for an oil trade. Doubles default when a recent log-only
        bullish nat gas signal (e.g., Qatar LNG force majeure) confirms
        the Iran/supply-shock thesis.
        """
        self._prune_log_only_signals()
        base = getattr(self.trader, "DEFAULT_SIZE", 15)
        for _, commodity, direction in self.recent_log_only_signals:
            if commodity == "natgas" and direction == "bullish":
                max_size = getattr(self.trader, "MAX_SIZE", 50)
                return min(base * 2, max_size)
        return None  # None → trader uses its own default

    def check_supply_shocks(self):
        """
        Check for supply-shock headlines across all configured commodities.
        Routes to the right Kalshi series based on the report's commodity.
        Bullish (supply cut)  → long the commodity.
        Bearish (resolution)  → short the commodity (unless spec disables it).

        Log-only tier signals feed the oil-trade confirmation buffer instead
        of firing a trade directly (e.g., Qatar LNG events reinforce Iran oil
        thesis via _oil_size_usd()).
        """
        if not hasattr(self.trader, "execute_commodity_trade"):
            return

        report = self.supply_shock_monitor.check()
        if not report:
            return

        spec = SPEC_BY_NAME.get(report.commodity)
        if not spec or not spec.kalshi_series:
            print(f"\n [SUPPLY] Signal for {report.commodity} — no Kalshi series configured, skipping")
            return

        # Log-only tier: real event but wrong benchmark for Kalshi (e.g., Qatar
        # events move TTF/JKM, not Henry Hub via KXNATGASD). Still useful as
        # confirmation for the oil trade path.
        if not report.tradeable:
            print(f"\n [SUPPLY:LOG] {report.direction.upper()} {spec.display} (global tier, no trade)")
            print(f"   {report.text[:120]}")
            print(f"   source: {report.source}")
            self.recent_log_only_signals.append(
                (datetime.now(), report.commodity, report.direction)
            )
            return

        # Per-spec bearish gate (nat gas: backtest showed bearish path loses)
        if report.direction == "bearish" and not spec.allow_bearish:
            print(f"\n [SUPPLY] BEARISH {spec.display} signal suppressed (allow_bearish=False)")
            print(f"   {report.text[:120]}")
            return

        if not self.can_trade():
            print(f"\n [SUPPLY] {spec.display} signal but rate-limited — skipping")
            return

        direction = "long" if report.direction == "bullish" else "short"
        post_id = f"{spec.name}-{int(datetime.now().timestamp())}"
        print(f"\n [SUPPLY] {report.direction.upper()} {spec.display} event via {report.source}")
        print(f"   {report.text[:120]}")
        print(f"   → {direction} {spec.display}")

        trade = self.trader.execute_commodity_trade(
            series_ticker=spec.kalshi_series,
            direction=direction,
            post_id=post_id,
            label=spec.display,
        )
        if trade:
            trade.sentiment = f"{spec.name}_{report.direction}"
            hold_secs = spec.hold_seconds if spec.hold_seconds is not None else self.hold_time
            trade.close_at = datetime.now() + timedelta(seconds=hold_secs)
            self.supply_trades.append(trade)
            self.trade_timestamps.append(datetime.now())
            self._journal_open(trade, category="supply", source=report.source, text=report.text)

    def close_expired_positions(self):
        """Close any positions that have passed their hold time."""
        now = datetime.now()
        closed = []

        for trade in self.active_trades[:]:
            if trade.close_at and now >= trade.close_at:
                pnl = self.trader.close_position(trade)
                self.active_trades.remove(trade)
                closed.append((trade, pnl))
                self._journal_close(trade, pnl, reason="expired")

        for trade in self.hormuz_trades[:]:
            if trade.close_at and now >= trade.close_at:
                pnl = self.trader.close_position(trade)
                self.hormuz_trades.remove(trade)
                closed.append((trade, pnl))
                self._journal_close(trade, pnl, reason="expired")

        for trade in self.supply_trades[:]:
            if trade.close_at and now >= trade.close_at:
                pnl = self.trader.close_position(trade)
                self.supply_trades.remove(trade)
                closed.append((trade, pnl))
                self._journal_close(trade, pnl, reason="expired")

        return closed

    def _snapshot_existing_posts(self):
        """
        On startup, fetch all current posts and mark them as seen.
        This prevents trading on old posts after a restart/deploy.
        """
        print("Snapshotting existing posts (trading only NEW; recent window allowed)...")
        try:
            posts = self.fetch_all_posts()
            from datetime import timezone
            cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=self.snapshot_cutoff_minutes)
            seen_older = 0
            for post in posts:
                ts = post.timestamp if post.timestamp.tzinfo else post.timestamp.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    self._seen_post_ids.add(post.id)
                    seen_older += 1
            print(f"   Marked {seen_older} older posts as seen (cutoff {self.snapshot_cutoff_minutes}m)")
        except Exception as e:
            import traceback
            print(f"   Snapshot error (non-fatal): {e}")
            traceback.print_exc()

    def run(self):
        """Main agent loop."""
        print("=" * 60)
        print("Iran Sentiment Trader Starting...")
        print(f"   Exchange: {self.exchange}")
        print(f"   Poll interval: {self.poll_interval}s")
        print(f"   Position hold time: {self.hold_time}s")
        print(f"   Trade type: {self.trade_type}")
        print("=" * 60)

        # Mark all current posts as seen — only trade on NEW posts
        self._snapshot_existing_posts()
        print("Waiting for new posts...\n")

        try:
            while True:
                # Fetch recent posts (quiet — only log when something new appears)
                try:
                    posts = self.fetch_all_posts(quiet=True)
                except Exception as e:
                    print(f"Error fetching posts: {e}")
                    time.sleep(self.poll_interval)
                    continue

                # Only process posts we haven't seen before
                new_posts = []
                for post in posts:
                    if post.id not in self._seen_post_ids:
                        self._seen_post_ids.add(post.id)
                        new_posts.append(post)

                if new_posts:
                    print(f"\n*** {len(new_posts)} NEW POST(S) DETECTED ***")

                for post in new_posts:
                    self.process_post(post)

                # Check Hormuz Strait traffic (hourly; disabled by default)
                if cfg.ENABLE_HORMUZ_MONITOR:
                    self.check_hormuz_traffic()

                # Check for reported attacks on shipping in Hormuz (→ long oil)
                if cfg.ENABLE_HORMUZ_MONITOR:
                    self.check_hormuz_incidents()

                # Check for supply-shock events across commodities
                self.check_supply_shocks()

                # Check for expired positions
                closed = self.close_expired_positions()
                for trade, pnl in closed:
                    print(f"💰 Closed trade, PnL: ${pnl:.2f}")

                # Heartbeat
                now = datetime.now()
                if (now - self._last_heartbeat).total_seconds() >= self.heartbeat_interval:
                    tc = self._last_counts
                    print(f"[HB] posts(tw={tc.get('twitter',0)}, ts={tc.get('truthsocial',0)}, wires={tc.get('wires',0)})"
                          f" active={len(self.active_trades)} hormuz={len(self.hormuz_trades)} supply={len(self.supply_trades)}")
                    self._last_heartbeat = now

                # Sleep until next poll
                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            print("\n🛑 Shutting down...")
            for trade in self.active_trades:
                self.trader.close_position(trade)


if __name__ == "__main__":
    import sys

    # Allow specifying trade type and exchange from command line
    trade_type = "perpetual"
    exchange = "kraken"
    if len(sys.argv) > 1:
        if sys.argv[1] in ["perpetual", "option"]:
            trade_type = sys.argv[1]
    if len(sys.argv) > 2:
        if sys.argv[2] in ["kraken", "coinbase", "okx", "kalshi", "dydx"]:
            exchange = sys.argv[2]

    agent = IranSentimentTrader(trade_type=trade_type, exchange=exchange)
    # For testing, just run once instead of loop
    print("\n--- Running single iteration for testing ---\n")
    posts = agent.fetch_all_posts()
    for post in posts:
        agent.process_post(post)
