"""
Run the Iran Sentiment Trader agent continuously.
"""

import os
import sys
import time
import signal
from datetime import datetime

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from src.fetcher import PresidentialPostFetcher
from src.truthsocial_fetcher import TruthSocialFetcher
from src.sentiment import IranSentimentClassifier
from src.okx_trader import OKXTrader


class TradingAgent:
    """Continuous trading agent."""

    def __init__(self, poll_interval: int = 60, hold_time: int = 300, trade_type: str = "perpetual"):
        self.poll_interval = poll_interval
        self.hold_time = hold_time
        self.trade_type = trade_type

        # Components
        self.twitter_fetcher = PresidentialPostFetcher()
        self.ts_fetcher = TruthSocialFetcher()
        self.classifier = IranSentimentClassifier()
        self.trader = OKXTrader()

        # State
        self.trades = []
        self.running = True

        print("=" * 60)
        print("🇺🇸 Iran Sentiment Trader")
        print("=" * 60)
        print(f"Poll interval: {poll_interval}s")
        print(f"Hold time: {hold_time}s")
        print(f"Trade type: {trade_type}")
        print(f"Trader mode: {'LIVE' if not self.trader.simulation_mode else 'SIMULATION'}")
        print(f"BTC price: ${self.trader.get_current_btc_price():,.2f}")
        print("=" * 60)

    def fetch_posts(self):
        """Fetch from all sources."""
        posts = []

        # Twitter
        try:
            twitter_posts = self.twitter_fetcher.fetch_recent_posts()
            if twitter_posts:
                print(f"📱 Twitter: {len(twitter_posts)} Iran posts")
                posts.extend(twitter_posts)
        except Exception as e:
            print(f"⚠️ Twitter error: {e}")

        # Truth Social
        try:
            ts_posts = self.ts_fetcher.fetch_recent_posts()
            if ts_posts:
                print(f"📣 Truth Social: {len(ts_posts)} Iran posts (sample)")
                from src.fetcher import PresidentialPost
                for p in ts_posts:
                    posts.append(PresidentialPost(
                        id=p.id, text=p.text, source="truthsocial", timestamp=p.timestamp
                    ))
        except Exception as e:
            print(f"⚠️ Truth Social error: {e}")

        # Sort by time
        posts.sort(key=lambda p: p.timestamp, reverse=True)
        return posts

    def process_posts(self, posts):
        """Process posts and execute trades."""
        for post in posts:
            print(f"\n📰 [{post.source}] {post.text[:60]}...")

            sentiment = self.classifier.classify(post.text)

            emoji = "🔥" if sentiment.value == "bellicose" else ("🕊️" if sentiment.value == "conciliatory" else "😐")
            print(f"   → {emoji} {sentiment.value}")

            if sentiment.value != "neutral":
                trade = self.trader.execute_trade(sentiment.value, post.id, trade_type=self.trade_type)
                if trade:
                    trade.close_at = datetime.now().timestamp() + self.hold_time
                    self.trades.append(trade)
                    print(f"   ✅ Trade: {trade.position.value}")

    def check_positions(self):
        """Close expired positions."""
        now = datetime.now().timestamp()
        closed = []

        for trade in self.trades[:]:
            if trade.close_at and now >= trade.close_at:
                pnl = self.trader.close_position(trade)
                self.trades.remove(trade)
                closed.append((trade, pnl))

        return closed

    def run(self):
        """Main loop."""
        iteration = 0

        while self.running:
            iteration += 1
            print(f"\n{'='*60}")
            print(f"🔄 Iteration {iteration} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * 60)

            try:
                # Fetch and process
                posts = self.fetch_posts()
                self.process_posts(posts)

                # Check positions
                closed = self.check_positions()
                for trade, pnl in closed:
                    emoji = "💚" if pnl > 0 else "❤️"
                    print(f"   {emoji} Closed {trade.position.value}: PnL ${pnl:.2f}")

                print(f"\n   Active positions: {len(self.trades)}")

            except Exception as e:
                print(f"❌ Error: {e}")

            # Sleep
            print(f"\n💤 Sleeping for {self.poll_interval}s...")
            for _ in range(self.poll_interval):
                if not self.running:
                    break
                time.sleep(1)

    def stop(self):
        """Stop the agent."""
        print("\n🛑 Stopping...")
        self.running = False
        # Close open positions
        for trade in self.trades:
            self.trader.close_position(trade)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Iran Sentiment Trader")
    parser.add_argument("--poll", type=int, default=60, help="Poll interval in seconds")
    parser.add_argument("--hold", type=int, default=300, help="Position hold time in seconds")
    parser.add_argument("--type", choices=["perpetual", "option"], default="perpetual", help="Trade type")
    args = parser.parse_args()

    agent = TradingAgent(poll_interval=args.poll, hold_time=args.hold, trade_type=args.type)

    # Handle Ctrl+C
    def signal_handler(sig, frame):
        agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # Run for a limited number of iterations for testing
    print("\n📝 Running 3 iterations for testing (Ctrl+C to stop)...")

    iteration = 0
    while iteration < 3 and agent.running:
        iteration += 1
        print(f"\n{'='*60}")
        print(f"🔄 Iteration {iteration} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

        try:
            posts = agent.fetch_posts()
            agent.process_posts(posts)

            closed = agent.check_positions()
            for trade, pnl in closed:
                emoji = "💚" if pnl > 0 else "❤️"
                print(f"   {emoji} Closed: PnL ${pnl:.2f}")

            print(f"\n   Active: {len(agent.trades)}")

        except Exception as e:
            print(f"❌ Error: {e}")

        time.sleep(agent.poll_interval)

    print("\n✅ Test run complete!")
    print(f"   Total trades executed: {len(agent.trades)}")


if __name__ == "__main__":
    main()