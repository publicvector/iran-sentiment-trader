"""
Fetcher for presidential and White House posts via Twitter/X API v2.
"""

import requests
from typing import List, Optional
from datetime import datetime
import os
import time
import logging

logger = logging.getLogger(__name__)


class PresidentialPost:
    """Represents a post from the President or White House."""
    def __init__(self, id: str, text: str, source: str, timestamp: datetime):
        self.id = id
        self.text = text
        self.source = source  # "potus", "whitehouse", "realtimepotus", etc.
        self.timestamp = timestamp

    def __repr__(self):
        return f"<PresidentialPost {self.source} at {self.timestamp.isoformat()}>"


class TwitterAPIError(Exception):
    """Custom exception for Twitter API errors."""
    pass


class PresidentialPostFetcher:
    """
    Fetches posts from the President and White House accounts via Twitter API v2.
    """

    # Keywords related to Iran conflict
    IRAN_KEYWORDS = [
        "iran", "iranian", "tehran", "nuclear", "uranium",
        "middle east", "gulf", "persian", "oil", "sanction",
        "revolutionary guard", "irgc", "khamenei", "rouhani",
        "nuclear deal", "jcpoa", "israel", "war", "military",
        "attack", "strike", "troops", "deployment", "missile",
        "hostage", "negotiations", "diplomacy", "missiles",
        "tehran", "persian gulf", "atomic", "enrichment"
    ]

    # Twitter user IDs for presidential accounts
    ACCOUNT_IDS = {
        "realdonaldtrump": "25073877",       # @realDonaldTrump — primary signal source
        "potus": "822215673726779392",       # @POTUS
        "whitehouse": "786317602383623360",  # @WhiteHouse
    }

    # Iran-side official accounts — usernames resolved to IDs at init.
    # Posts from these accounts bypass the Iran keyword filter since
    # everything they say is by definition relevant.
    IRAN_SOURCE_USERNAMES = [
        "araghchi",    # Abbas Araghchi — Iran Foreign Minister
        "IRIMFA_EN",   # Iran MFA (English)
        "IRIMFA",      # Iran MFA (Persian)
    ]

    API_BASE_URL = "https://api.twitter.com/2"
    MAX_RESULTS_PER_REQUEST = 20

    def __init__(self):
        self.bearer_token = os.getenv("TWITTER_BEARER_TOKEN")
        self._session = requests.Session()
        self.iran_account_ids: dict = {}  # lowercased username -> user_id

        if self.bearer_token:
            self._session.headers.update({
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json"
            })
            try:
                self.iran_account_ids = self._resolve_usernames(self.IRAN_SOURCE_USERNAMES)
                logger.info(f"Resolved Iran source accounts: {list(self.iran_account_ids.keys())}")
            except TwitterAPIError as e:
                logger.warning(f"Could not resolve Iran source usernames: {e}")

    @property
    def iran_source_names(self) -> set:
        """Lowercased source names whose posts skip the English keyword filter."""
        return set(self.iran_account_ids.keys())

    def _resolve_usernames(self, usernames: List[str]) -> dict:
        """Resolve Twitter usernames to numeric user IDs in one batch request."""
        if not usernames:
            return {}
        data = self._make_request("/users/by", {"usernames": ",".join(usernames)})
        return {u["username"].lower(): u["id"] for u in data.get("data", [])}

    def filter_iran_related(self, post: PresidentialPost) -> bool:
        """Check if post is related to Iran."""
        text_lower = post.text.lower()
        return any(keyword in text_lower for keyword in self.IRAN_KEYWORDS)

    def _make_request(self, endpoint: str, params: dict = None) -> dict:
        """Make a request to the Twitter API v2."""
        if not self.bearer_token:
            raise TwitterAPIError("TWITTER_BEARER_TOKEN not set")

        url = f"{self.API_BASE_URL}{endpoint}"

        try:
            response = self._session.get(url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()

            # Check for Twitter API errors
            if "errors" in data:
                errors = data["errors"]
                if errors:
                    error = errors[0]
                    raise TwitterAPIError(f"Twitter API error: {error.get('message', 'Unknown error')}")

            return data

        except requests.exceptions.RequestException as e:
            raise TwitterAPIError(f"Request failed: {e}")

    def get_user_tweets(self, user_id: str, max_results: int = 20) -> List[PresidentialPost]:
        """
        Fetch recent tweets from a specific user.

        Args:
            user_id: Twitter user ID
            max_results: Number of tweets to fetch (max 100)

        Returns:
            List of PresidentialPost objects
        """
        params = {
            "max_results": min(max_results, self.MAX_RESULTS_PER_REQUEST),
            "tweet.fields": "created_at,text,author_id",
            "expansions": "author_id",
            "user.fields": "username"
        }

        data = self._make_request(f"/users/{user_id}/tweets", params)

        tweets = []
        if "data" in data:
            includes = data.get("include", {}).get("users", [{}])
            user_map = {u["id"]: u.get("username", "unknown") for u in includes}

            for tweet in data["data"]:
                # Parse timestamp
                created_at = tweet.get("created_at")
                if created_at:
                    timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                else:
                    timestamp = datetime.now()

                tweets.append(PresidentialPost(
                    id=tweet["id"],
                    text=tweet["text"],
                    source=user_map.get(tweet["author_id"], "unknown"),
                    timestamp=timestamp
                ))

        return tweets

    def fetch_from_twitter(self) -> List[PresidentialPost]:
        """
        Fetch posts from all presidential accounts using Twitter API v2.

        Returns:
            List of PresidentialPost objects from all tracked accounts
        """
        if not self.bearer_token:
            raise TwitterAPIError("TWITTER_BEARER_TOKEN not set - cannot fetch from Twitter")

        all_posts = []

        # Combine presidential accounts + resolved Iran-side accounts
        combined = dict(self.ACCOUNT_IDS)
        combined.update(self.iran_account_ids)

        for account_name, user_id in combined.items():
            try:
                logger.info(f"Fetching tweets from @{account_name}")
                tweets = self.get_user_tweets(user_id)
                all_posts.extend(tweets)
                logger.info(f"  Got {len(tweets)} tweets from @{account_name}")

            except TwitterAPIError as e:
                logger.warning(f"  Failed to fetch from @{account_name}: {e}")
                continue

            # Rate limiting - be respectful
            time.sleep(0.5)

        return all_posts

    def fetch_recent_posts(self, limit: int = 20) -> List[PresidentialPost]:
        """
        Fetch recent posts from presidential accounts.

        If TWITTER_BEARER_TOKEN is set, uses real Twitter API.
        Otherwise returns empty list (Truth Social is primary source).
        """
        if not self.bearer_token or len(self.bearer_token) < 20:
            return []  # no valid token — Truth Social is primary source

        try:
            # Fetch all posts from Twitter
            posts = self.fetch_from_twitter()

            # Filter: keep posts from Iran-side accounts unconditionally
            # (their feeds are already Iran-focused and may be in Persian),
            # keyword-filter everything else.
            iran_sources = self.iran_source_names
            iran_posts = [
                p for p in posts
                if p.source.lower() in iran_sources or self.filter_iran_related(p)
            ]

            # Sort by timestamp (newest first)
            iran_posts.sort(key=lambda p: p.timestamp, reverse=True)

            return iran_posts[:limit]

        except TwitterAPIError as e:
            logger.error(f"Twitter API error: {e}")
            return self._get_sample_posts()

    def _get_sample_posts(self) -> List[PresidentialPost]:
        """Return sample posts for development when API is not available."""
        sample_posts = [
            PresidentialPost(
                id="sample-1",
                text="We will not allow Iran to acquire a nuclear weapon. All options are on the table.",
                source="potus",
                timestamp=datetime.now()
            ),
            PresidentialPost(
                id="sample-2",
                text="We're exploring diplomatic solutions to de-escalate tensions in the Middle East. A peaceful path is always preferable.",
                source="potus",
                timestamp=datetime.now()
            ),
            PresidentialPost(
                id="sample-3",
                text="The United States is committed to ensuring Iran never acquires a nuclear weapon. Our sanctions regime will continue to tighten until Iran changes course.",
                source="whitehouse",
                timestamp=datetime.now()
            ),
            PresidentialPost(
                id="sample-4",
                text="We remain open to direct diplomacy with Iran if they are willing to engage in good faith. A deal is possible if they take meaningful steps.",
                source="potus",
                timestamp=datetime.now()
            ),
        ]

        return [p for p in sample_posts if self.filter_iran_related(p)]


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    fetcher = PresidentialPostFetcher()

    print("Fetching recent posts...")
    posts = fetcher.fetch_recent_posts()

    print(f"\nFound {len(posts)} Iran-related posts:\n")
    for p in posts:
        print(f"[{p.source}] {p.text[:80]}...")
        print(f"  Time: {p.timestamp}\n")