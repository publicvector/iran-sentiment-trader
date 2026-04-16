"""
Truth Social fetcher — scrapes real Trump posts from trumpstruth.org.

Truth Social has no public API, but trumpstruth.org archives all posts
with full text and timestamps. We scrape this to get real-time data.
"""

import re
import requests
from typing import List, Optional
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
    Fetches Trump's Truth Social posts by scraping trumpstruth.org.
    Filters for Iran-related content.
    """

    ARCHIVE_URL = "https://trumpstruth.org"

    IRAN_KEYWORDS = [
        "iran", "iranian", "tehran", "nuclear", "uranium",
        "middle east", "gulf", "sanction", "war", "military",
        "attack", "diplomacy", "deal", "negotiations", "hormuz",
        "strike", "bomb", "troops", "ceasefire", "peace",
        "oil", "missile", "regime", "khamenei",
    ]

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        self._seen_ids: set = set()

    def filter_iran_related(self, post: TruthSocialPost) -> bool:
        text_lower = post.text.lower()
        return any(kw in text_lower for kw in self.IRAN_KEYWORDS)

    def _scrape_archive(self) -> List[TruthSocialPost]:
        """Scrape recent posts from trumpstruth.org homepage."""
        posts = []
        try:
            resp = self._session.get(self.ARCHIVE_URL, timeout=15)
            resp.raise_for_status()
            html = resp.text

            # trumpstruth.org has posts with links like /statuses/NNNNN
            # Extract post IDs from the page
            post_ids = re.findall(r'/statuses/(\d+)', html)
            # Deduplicate, keep order
            seen = set()
            unique_ids = []
            for pid in post_ids:
                if pid not in seen:
                    seen.add(pid)
                    unique_ids.append(pid)

            # Fetch each post page for full text
            for pid in unique_ids[:10]:  # limit to 10 most recent
                post = self._fetch_post(pid)
                if post:
                    posts.append(post)

        except Exception as e:
            logger.warning(f"Failed to scrape trumpstruth.org: {e}")

        return posts

    def _fetch_post(self, post_id: str) -> Optional[TruthSocialPost]:
        """Fetch a single post from trumpstruth.org."""
        try:
            url = f"{self.ARCHIVE_URL}/statuses/{post_id}"
            resp = self._session.get(url, timeout=10)
            resp.raise_for_status()
            html = resp.text

            # Extract post text — it's in the page content between tags
            # Look for the main content area
            text = ""

            # Try meta description first (often has the full text)
            desc_match = re.search(
                r'<meta\s+(?:name="description"|property="og:description")\s+content="([^"]*)"',
                html, re.IGNORECASE
            )
            if desc_match:
                text = desc_match.group(1)
                # Unescape HTML entities
                text = text.replace("&amp;", "&").replace("&quot;", '"')
                text = text.replace("&#x27;", "'").replace("&lt;", "<").replace("&gt;", ">")

            # If no description, try to find text in the page body
            if not text or len(text) < 10:
                # Look for article or main content
                content_match = re.search(
                    r'<article[^>]*>(.*?)</article>', html, re.DOTALL | re.IGNORECASE
                )
                if content_match:
                    raw = content_match.group(1)
                    # Strip HTML tags
                    text = re.sub(r'<[^>]+>', ' ', raw).strip()
                    text = re.sub(r'\s+', ' ', text)

            if not text or len(text) < 5:
                return None

            # Extract timestamp
            timestamp = datetime.now()
            time_match = re.search(
                r'datetime="([^"]+)"', html
            )
            if time_match:
                try:
                    timestamp = datetime.fromisoformat(time_match.group(1).replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
            else:
                # Try to find date text like "April 4, 2026"
                date_match = re.search(
                    r'((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})',
                    html
                )
                if date_match:
                    try:
                        timestamp = datetime.strptime(date_match.group(1), "%B %d, %Y")
                    except ValueError:
                        pass

            return TruthSocialPost(
                id=f"ts-{post_id}",
                text=text[:1000],  # cap length
                timestamp=timestamp,
            )

        except Exception as e:
            logger.debug(f"Failed to fetch post {post_id}: {e}")
            return None

    def fetch_recent_posts(self, limit: int = 20) -> List[TruthSocialPost]:
        """
        Fetch recent Iran-related posts from Truth Social.

        Scrapes trumpstruth.org for real posts, filters for Iran content.
        Returns only NEW posts not seen in previous calls.
        """
        all_posts = self._scrape_archive()

        if not all_posts:
            logger.warning("No posts from trumpstruth.org — site may be down")
            return []

        # Filter for Iran-related
        iran_posts = [p for p in all_posts if self.filter_iran_related(p)]

        # Only return posts we haven't seen before
        new_posts = []
        for p in iran_posts:
            if p.id not in self._seen_ids:
                self._seen_ids.add(p.id)
                new_posts.append(p)

        # Sort newest first
        new_posts.sort(key=lambda p: p.timestamp, reverse=True)

        if new_posts:
            logger.info(f"Truth Social: {len(new_posts)} new Iran posts (of {len(all_posts)} total)")
        else:
            logger.debug(f"Truth Social: no new Iran posts (checked {len(all_posts)})")

        return new_posts[:limit]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    fetcher = TruthSocialFetcher()

    print("Fetching Truth Social posts from trumpstruth.org...")
    posts = fetcher._scrape_archive()
    print(f"Got {len(posts)} total posts\n")

    for p in posts:
        iran = "IRAN" if fetcher.filter_iran_related(p) else "    "
        print(f"  [{iran}] {p.id}: {p.text[:80]}...")
        print(f"         {p.timestamp}")
        print()

    iran_posts = [p for p in posts if fetcher.filter_iran_related(p)]
    print(f"\n{len(iran_posts)} Iran-related posts out of {len(posts)} total")
