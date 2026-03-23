"""
Test script to run the full pipeline: fetch -> sentiment -> display.
"""

import os
import sys
from dotenv import load_dotenv

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

load_dotenv()

# Import directly without going through __init__
from src.fetcher import PresidentialPostFetcher
from src.sentiment import IranSentimentClassifier

def main():
    print("=" * 60)
    print("Testing Iran Sentiment Trader Pipeline")
    print("=" * 60)

    fetcher = PresidentialPostFetcher()
    classifier = IranSentimentClassifier()

    print("\nFetching posts from Twitter...")
    posts = fetcher.fetch_recent_posts()

    print(f"Found {len(posts)} Iran-related posts\n")
    print("-" * 60)

    for p in posts:
        sentiment = classifier.classify(p.text)

        if sentiment.value == "bellicose":
            emoji = "🔥"
        elif sentiment.value == "conciliatory":
            emoji = "🕊️"
        else:
            emoji = "😐"

        print(f"{emoji} [{sentiment.value.upper()}] @{p.source}")
        print(f"   {p.text[:120]}...")
        print(f"   {p.timestamp}")
        print()


if __name__ == "__main__":
    main()