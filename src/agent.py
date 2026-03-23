"""
Main trading agent that orchestrates fetching, sentiment analysis, and trading.
"""

import time
from datetime import datetime, timedelta
from typing import List, Optional

from src.fetcher import PresidentialPostFetcher, PresidentialPost
from src.sentiment import IranSentimentClassifier, Sentiment
from src.trader import CoinbaseOptionsTrader, Trade
from src.deribit_trader import DeribitTrader


class IranSentimentTrader:
    """
    Main trading agent that:
    1. Fetches presidential posts about Iran
    2. Classifies sentiment using LLM
    3. Executes trades on Coinbase options
    """

    DEFAULT_POLL_INTERVAL = 60  # seconds
    POSITION_HOLD_TIME = 300    # seconds (5 minutes)
    MAX_TRADES_PER_HOUR = 3

    def __init__(
        self,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        hold_time: int = POSITION_HOLD_TIME
    ):
        self.fetcher = PresidentialPostFetcher()
        self.classifier = IranSentimentClassifier()
        self.trader = CoinbaseOptionsTrader()

        self.poll_interval = poll_interval
        self.hold_time = hold_time

        self.active_trades: List[Trade] = []
        self.trade_timestamps: List[datetime] = []

    def can_trade(self) -> bool:
        """Check if we can execute a new trade (rate limiting)."""
        # Reset hour window
        cutoff = datetime.now() - timedelta(hours=1)
        self.trade_timestamps = [t for t in self.trade_timestamps if t > cutoff]

        return len(self.trade_timestamps) < self.MAX_TRADES_PER_HOUR

    def process_post(self, post: PresidentialPost) -> Optional[Trade]:
        """
        Process a single post: classify sentiment and potentially trade.

        Returns:
            Trade if executed, None otherwise
        """
        print(f"\n📰 Processing post from {post.source}:")
        print(f"   {post.text[:100]}...")

        # Classify sentiment
        sentiment = self.classifier.classify(post.text)
        print(f"   → Sentiment: {sentiment.value}")

        # Execute trade if not neutral and within rate limits
        if sentiment.value != "neutral" and self.can_trade():
            trade = self.trader.execute_trade(
                sentiment=sentiment.value,
                post_id=post.id
            )

            if trade:
                self.active_trades.append(trade)
                self.trade_timestamps.append(datetime.now())

                # Schedule position close
                self._schedule_position_close(trade)

            return trade

        return None

    def _schedule_position_close(self, trade: Trade):
        """Close position after hold time expires."""
        # In a real implementation, this would use asyncio or threading
        # For now, we just track when to close
        trade.close_at = datetime.now() + timedelta(seconds=self.hold_time)
        print(f"   ⏱️ Will close position in {self.hold_time}s")

    def close_expired_positions(self):
        """Close any positions that have passed their hold time."""
        now = datetime.now()
        closed = []

        for trade in self.active_trades[:]:
            if hasattr(trade, 'close_at') and now >= trade.close_at:
                pnl = self.trader.close_position(trade)
                self.active_trades.remove(trade)
                closed.append((trade, pnl))

        return closed

    def run(self):
        """
        Main agent loop.
        """
        print("=" * 60)
        print("🇺🇸 Iran Sentiment Trader Starting...")
        print(f"   Poll interval: {self.poll_interval}s")
        print(f"   Position hold time: {self.hold_time}s")
        print("=" * 60)

        try:
            while True:
                # Fetch recent posts
                try:
                    posts = self.fetcher.fetch_recent_posts()
                except Exception as e:
                    print(f"Error fetching posts: {e}")
                    time.sleep(self.poll_interval)
                    continue

                # Process new posts
                for post in posts:
                    self.process_post(post)

                # Check for expired positions
                closed = self.close_expired_positions()
                for trade, pnl in closed:
                    print(f"💰 Closed trade, PnL: ${pnl:.2f}")

                # Sleep until next poll
                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            print("\n🛑 Shutting down...")
            # Close any open positions
            for trade in self.active_trades:
                self.trader.close_position(trade)


if __name__ == "__main__":
    agent = IranSentimentTrader()
    agent.run()