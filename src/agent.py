"""
Main trading agent - fetches from Twitter + Truth Social, classifies sentiment, trades on OKX.
"""

import time
from datetime import datetime, timedelta
from typing import List, Optional

from src.fetcher import PresidentialPostFetcher, PresidentialPost
from src.truthsocial_fetcher import TruthSocialFetcher, TruthSocialPost
from src.sentiment import IranSentimentClassifier, Sentiment
from src.okx_trader import OKXTrader, Trade


class IranSentimentTrader:
    """
    Main trading agent that:
    1. Fetches presidential posts about Iran (Twitter + Truth Social)
    2. Classifies sentiment using LLM
    3. Executes trades on OKX (perpetuals/options)
    """

    DEFAULT_POLL_INTERVAL = 60  # seconds
    POSITION_HOLD_TIME = 300     # seconds (5 minutes)
    MAX_TRADES_PER_HOUR = 3

    def __init__(
        self,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        hold_time: int = POSITION_HOLD_TIME,
        trade_type: str = "perpetual"  # "perpetual" or "option"
    ):
        # Initialize fetchers
        self.twitter_fetcher = PresidentialPostFetcher()
        self.truthsocial_fetcher = TruthSocialFetcher()
        self.classifier = IranSentimentClassifier()

        # Use OKX trader (US-friendly)
        self.trader = OKXTrader()

        self.poll_interval = poll_interval
        self.hold_time = hold_time
        self.trade_type = trade_type

        self.active_trades: List[Trade] = []
        self.trade_timestamps: List[datetime] = []

        print(f"📊 Iran Sentiment Trader initialized")
        print(f"   Sources: Twitter + Truth Social")
        print(f"   Exchange: OKX ({trade_type})")
        print(f"   Poll interval: {poll_interval}s")

    def can_trade(self) -> bool:
        """Check if we can execute a new trade (rate limiting)."""
        cutoff = datetime.now() - timedelta(hours=1)
        self.trade_timestamps = [t for t in self.trade_timestamps if t > cutoff]
        return len(self.trade_timestamps) < self.MAX_TRADES_PER_HOUR

    def fetch_all_posts(self) -> List[PresidentialPost]:
        """Fetch posts from all sources."""
        all_posts = []

        # Fetch from Twitter
        try:
            twitter_posts = self.twitter_fetcher.fetch_recent_posts()
            all_posts.extend(twitter_posts)
            print(f"   Twitter: {len(twitter_posts)} Iran posts")
        except Exception as e:
            print(f"   Twitter error: {e}")

        # Fetch from Truth Social (currently sample data)
        try:
            ts_posts = self.truthsocial_fetcher.fetch_recent_posts()
            # Convert to same format
            for p in ts_posts:
                all_posts.append(PresidentialPost(
                    id=p.id,
                    text=p.text,
                    source="truthsocial",
                    timestamp=p.timestamp
                ))
            print(f"   Truth Social: {len(ts_posts)} Iran posts")
        except Exception as e:
            print(f"   Truth Social error: {e}")

        # Sort by timestamp (newest first)
        all_posts.sort(key=lambda p: p.timestamp, reverse=True)
        return all_posts

    def process_post(self, post: PresidentialPost) -> Optional[Trade]:
        """Process a single post: classify sentiment and potentially trade."""
        print(f"\n📰 Processing post from {post.source}:")
        print(f"   {post.text[:80]}...")

        # Classify sentiment
        sentiment = self.classifier.classify(post.text)
        print(f"   → Sentiment: {sentiment.value}")

        # Execute trade if not neutral and within rate limits
        if sentiment.value != "neutral" and self.can_trade():
            trade = self.trader.execute_trade(
                sentiment=sentiment.value,
                post_id=post.id,
                trade_type=self.trade_type
            )

            if trade:
                self.active_trades.append(trade)
                self.trade_timestamps.append(datetime.now())
                trade.close_at = datetime.now() + timedelta(seconds=self.hold_time)
                print(f"   ⏱️ Will close position in {self.hold_time}s")

            return trade

        return None

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
        """Main agent loop."""
        print("=" * 60)
        print("🇺🇸 Iran Sentiment Trader Starting...")
        print(f"   Poll interval: {self.poll_interval}s")
        print(f"   Position hold time: {self.hold_time}s")
        print(f"   Trade type: {self.trade_type}")
        print("=" * 60)

        try:
            while True:
                # Fetch recent posts
                try:
                    posts = self.fetch_all_posts()
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
            for trade in self.active_trades:
                self.trader.close_position(trade)


if __name__ == "__main__":
    import sys

    # Allow specifying trade type from command line
    trade_type = "perpetual"
    if len(sys.argv) > 1:
        if sys.argv[1] in ["perpetual", "option"]:
            trade_type = sys.argv[1]

    agent = IranSentimentTrader(trade_type=trade_type)
    # For testing, just run once instead of loop
    print("\n--- Running single iteration for testing ---\n")
    posts = agent.fetch_all_posts()
    for post in posts:
        agent.process_post(post)