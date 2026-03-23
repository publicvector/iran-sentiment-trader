"""
Fetcher for presidential and White House posts.
"""

import requests
from typing import List, Dict
from datetime import datetime
import os


class PresidentialPost:
    """Represents a post from the President or White House."""
    def __init__(self, id: str, text: str, source: str, timestamp: datetime):
        self.id = id
        self.text = text
        self.source = source  # "potus", "whitehouse", "realtimepotus", etc.
        self.timestamp = timestamp

    def __repr__(self):
        return f"<PresidentialPost {self.source} at {self.timestamp.isoformat()}>"


class PresidentialPostFetcher:
    """
    Fetches posts from the President and White House accounts.
    """

    # Keywords related to Iran conflict
    IRAN_KEYWORDS = [
        "iran", "iranian", "tehran", "nuclear", "uranium",
        "middle east", "gulf", "persian", "oil", "sanction",
        "revolutionary guard", "irgc", "khamenei", "rouhani",
        "nuclear deal", "jcpoa", "israel", "war", "military",
        "attack", "strike", "troops", "deployment", "missile",
        "hostage", "negotiations", "diplomacy"
    ]

    WHITEHOUSE_SOURCES = [
        "potus",
        "whitehouse",
        "realtimepotus",
        "VP"
    ]

    def __init__(self):
        # Could add Twitter API credentials here
        self.twitter_bearer_token = os.getenv("TWITTER_BEARER_TOKEN")

    def filter_iran_related(self, post: PresidentialPost) -> bool:
        """Check if post is related to Iran."""
        text_lower = post.text.lower()
        return any(keyword in text_lower for keyword in self.IRAN_KEYWORDS)

    def fetch_recent_posts(self, limit: int = 20) -> List[PresidentialPost]:
        """
        Fetch recent posts from presidential accounts.

        This is a placeholder - would need Twitter API integration.
        For now, returns sample data for development.
        """
        # TODO: Implement actual Twitter API fetching
        # For now, return sample posts for testing the sentiment pipeline

        sample_posts = [
            PresidentialPost(
                id="1",
                text="We will not allow Iran to acquire a nuclear weapon. All options are on the table.",
                source="potus",
                timestamp=datetime.now()
            ),
            PresidentialPost(
                id="2",
                text="We're exploring diplomatic solutions to de-escalate tensions in the Middle East. A peaceful path is always preferable.",
                source="potus",
                timestamp=datetime.now()
            ),
        ]

        # Filter for Iran-related posts
        return [p for p in sample_posts if self.filter_iran_related(p)]

    def fetch_from_twitter(self) -> List[PresidentialPost]:
        """
        Fetch posts using Twitter API v2.
        Requires TWITTER_BEARER_TOKEN environment variable.
        """
        if not self.twitter_bearer_token:
            raise ValueError("TWITTER_BEARER_TOKEN not set")

        # Twitter API v2 implementation would go here
        # Use user_id for @potus, @whitehouse, etc.
        pass


if __name__ == "__main__":
    fetcher = PresidentialPostFetcher()
    posts = fetcher.fetch_recent_posts()
    for p in posts:
        print(f"[{p.source}] {p.text[:80]}...")