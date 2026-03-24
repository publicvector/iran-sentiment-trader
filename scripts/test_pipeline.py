"""Test script for full pipeline."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.fetcher import PresidentialPostFetcher
from src.truthsocial_fetcher import TruthSocialFetcher
from src.sentiment import IranSentimentClassifier
from src.okx_trader import OKXTrader


def main():
    print("=" * 60)
    print("Testing Full Pipeline: Twitter + Truth Social → Sentiment → OKX")
    print("=" * 60)

    # Initialize components
    twitter_fetcher = PresidentialPostFetcher()
    truthsocial_fetcher = TruthSocialFetcher()
    classifier = IranSentimentClassifier()
    trader = OKXTrader()

    print(f"\nBTC Price: ${trader.get_current_btc_price():,.2f}")
    print(f"Trader mode: {'LIVE' if not trader.simulation_mode else 'SIMULATION'}")

    # Fetch from Twitter
    print("\n--- Fetching from Twitter ---")
    try:
        twitter_posts = twitter_fetcher.fetch_recent_posts()
        print(f"Found {len(twitter_posts)} Iran-related posts from Twitter")
    except Exception as e:
        print(f"Twitter error: {e}")
        twitter_posts = []

    # Fetch from Truth Social
    print("\n--- Fetching from Truth Social ---")
    try:
        ts_posts = truthsocial_fetcher.fetch_recent_posts()
        print(f"Found {len(ts_posts)} Iran-related posts from Truth Social")
    except Exception as e:
        print(f"Truth Social error: {e}")
        ts_posts = []

    # Process all posts
    print("\n--- Processing Posts ---")
    all_posts = []

    for p in twitter_posts:
        all_posts.append(("Twitter", p))

    for p in ts_posts:
        from src.fetcher import PresidentialPost
        all_posts.append(("TruthSocial", PresidentialPost(
            id=p.id, text=p.text, source="truthsocial", timestamp=p.timestamp
        )))

    for source, post in all_posts:
        print(f"\n[{source}] {post.text[:60]}...")
        sentiment = classifier.classify(post.text)

        emoji = "🔥" if sentiment.value == "bellicose" else ("🕊️" if sentiment.value == "conciliatory" else "😐")
        print(f"   Sentiment: {emoji} {sentiment.value}")

        if sentiment.value != "neutral":
            trade = trader.execute_trade(sentiment.value, post.id, trade_type="perpetual")
            if trade:
                print(f"   Trade: {trade.position.value} at ${trade.entry_price:,.2f}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()