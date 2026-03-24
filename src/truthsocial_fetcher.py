"""
Truth Social fetcher for presidential posts.
Note: Truth Social does not have a public API - this uses sample data for development.
To access real Truth Social data, you would need:
1. Official API access from Truth Social (unlikely to be available)
2. A scraping service (e.g., Bright Data, ScrapingBee)
3. A server that can bypass Cloudflare protection
"""

import requests
from typing import List
from datetime import datetime
import os
import logging

logger = logging.getLogger(__name__)


class TruthSocialPost:
    """Represents a post from Truth Social."""
    def __init__(self, id: str, text: str, timestamp: datetime):
        self.id = id
        self.text = text
        self.timestamp = timestamp

    def __repr__(self):
        return f"<TruthSocialPost {self.id} at {self.timestamp.isoformat()}>"


class TruthSocialFetcher:
    """
    Fetches posts from Truth Social (Trump's platform).

    WARNING: Truth Social has no public API and blocks automated access.
    This implementation returns sample data for development purposes.
    """

    # Iran-related keywords
    IRAN_KEYWORDS = [
        "iran", "iranian", "tehran", "nuclear", "uranium",
        "middle east", "gulf", "sanction", "war", "military",
        "attack", "diplomacy", "deal", "negotiations"
    ]

    # Known Truth Social accounts to monitor
    ACCOUNTS = [
        "realDonaldTrump",
        "TeamTrump",
        "WhiteHouse"
    ]

    def __init__(self):
        self.bearer_token = os.getenv("TRUTH_SOCIAL_TOKEN")
        # Truth Social is heavily protected - no public API available
        # Use sample data for now

    def filter_iran_related(self, post: TruthSocialPost) -> bool:
        """Check if post is related to Iran."""
        text_lower = post.text.lower()
        return any(keyword in text_lower for keyword in self.IRAN_KEYWORDS)

    def fetch_recent_posts(self, limit: int = 20) -> List[TruthSocialPost]:
        """
        Fetch recent posts from Truth Social.

        Due to API restrictions, this returns sample data.
        """
        logger.warning("⚠️ Truth Social has no public API - using sample data")
        return self._get_sample_posts()

    def _get_sample_posts(self) -> List[TruthSocialPost]:
        """Sample Truth Social posts for development."""
        sample_posts = [
            TruthSocialPost(
                id="ts-1",
                text="Iran is a nation of terror. They have spoken about the destruction of Israel. We will not let that happen!",
                timestamp=datetime.now()
            ),
            TruthSocialPost(
                id="ts-2",
                text="Good news coming out of Iran negotiations. They want to make a deal, and we want one too.",
                timestamp=datetime.now()
            ),
            TruthSocialPost(
                id="ts-3",
                text="The Iranian regime must stop supporting terrorism. We have imposed the highest level of sanctions. All options on the table!",
                timestamp=datetime.now()
            ),
            TruthSocialPost(
                id="ts-4",
                text="We are having very productive talks with Iran. A deal is possible if they are willing to negotiate in good faith.",
                timestamp=datetime.now()
            ),
        ]

        return [p for p in sample_posts if self.filter_iran_related(p)]


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    fetcher = TruthSocialFetcher()
    posts = fetcher.fetch_recent_posts()

    print(f"Found {len(posts)} Iran-related Truth Social posts:")
    for p in posts:
        print(f"  [{p.id}] {p.text[:60]}...")