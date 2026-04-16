"""
Hormuz Strait incident monitor — detects reports of attacks on shipping.

This complements HormuzMonitor (which tracks vessel counts). Where vessel
counts are a slow-moving structural signal, incident reports are fast,
discrete events: tanker strikes, drone attacks, mining, boarding.

Sources:
  - UKMTO advisory page (authoritative — UK Navy maritime incident desk)
  - Google News RSS (authoritative — news wire coverage)
  - OSINT Twitter accounts (fast but noisy: TankerTrackers, Aurora_Intel, etc.)

Firing rule — trigger a signal when EITHER:
  (a) any authoritative source (UKMTO or news wire) publishes a matching item, OR
  (b) ≥2 independent OSINT accounts report an incident within a 30-minute window.

Cooldown after firing prevents multiple trades on the same underlying event.
"""

import re
import time
import logging
import hashlib
import requests
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import List, Optional, Set

logger = logging.getLogger(__name__)


ATTACK_KEYWORDS = [
    "attack", "attacked", "struck", "hit by", "missile", "drone",
    "explosion", "explosive", "boarded", "seized", "mine", "mines",
    "distress", "sos", "mayday", "ablaze", "hijack", "hijacked",
    "usv", "uav", "limpet", "detonation", "torpedo",
]

LOCATION_KEYWORDS = [
    "hormuz", "gulf of oman", "persian gulf", "arabian gulf",
    "bandar abbas", "fujairah", "kharg", "strait",
]

OSINT_MARITIME_USERNAMES = [
    "TankerTrackers",
    "Aurora_Intel",
    "MT_Anderson",
]


@dataclass
class IncidentReport:
    timestamp: datetime
    source: str          # "ukmto", "googlenews", or "twitter:<username>"
    source_type: str     # "authoritative" or "osint"
    text: str
    fingerprint: str = ""

    def __post_init__(self):
        if not self.fingerprint:
            norm = re.sub(r"\s+", " ", self.text.lower().strip())[:80]
            self.fingerprint = hashlib.md5(norm.encode()).hexdigest()[:12]


class HormuzIncidentMonitor:
    """
    Polls UKMTO, news wires, and OSINT Twitter accounts for reports of
    attacks on shipping in the Strait of Hormuz region. Fires a single
    signal per confirmed event.
    """

    UKMTO_URL = "https://www.ukmto.org/indian-ocean/ukmto-warnings"
    NEWS_RSS_URL = (
        "https://news.google.com/rss/search?"
        "q=%22Strait+of+Hormuz%22+(tanker+OR+ship+OR+vessel)+"
        "(attack+OR+drone+OR+missile+OR+struck)&hl=en-US&gl=US&ceid=US:en"
    )

    CHECK_INTERVAL = 300      # 5 minutes between polls
    CONFIRM_WINDOW = 1800     # 30 min window for OSINT confirmation
    COOLDOWN = 7200           # 2 hours between signals (same event dedup)
    OSINT_REQUIRED = 2        # need ≥2 independent OSINT accounts without auth source

    def __init__(self, twitter_fetcher=None):
        self.twitter_fetcher = twitter_fetcher
        self.last_check: Optional[datetime] = None
        self.last_signal: Optional[datetime] = None
        self.seen_fingerprints: Set[str] = set()
        self.recent_osint: List[IncidentReport] = []
        self._osint_account_ids: dict = {}

        if twitter_fetcher and getattr(twitter_fetcher, "bearer_token", None):
            try:
                self._osint_account_ids = twitter_fetcher._resolve_usernames(
                    OSINT_MARITIME_USERNAMES
                )
                logger.info(
                    f"Resolved OSINT maritime accounts: {list(self._osint_account_ids.keys())}"
                )
            except Exception as e:
                logger.warning(f"Could not resolve OSINT maritime accounts: {e}")

    def _is_incident_text(self, text: str) -> bool:
        """True if text mentions an attack keyword AND a Hormuz-region location."""
        t = text.lower()
        has_attack = any(k in t for k in ATTACK_KEYWORDS)
        has_location = any(k in t for k in LOCATION_KEYWORDS)
        return has_attack and has_location

    def _fetch_ukmto(self) -> List[IncidentReport]:
        """Scrape UKMTO advisory page, extract incident-shaped blocks."""
        reports = []
        try:
            resp = requests.get(
                self.UKMTO_URL,
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (compatible; IranSentimentBot/1.0)"},
            )
            html = resp.text
            chunks = re.split(r"<(?:div|article|li|p|section)[^>]*>", html)
            for chunk in chunks:
                clean = re.sub(r"<[^>]+>", " ", chunk)
                clean = re.sub(r"\s+", " ", clean).strip()
                if 30 < len(clean) < 600 and self._is_incident_text(clean):
                    reports.append(IncidentReport(
                        timestamp=datetime.now(timezone.utc),
                        source="ukmto",
                        source_type="authoritative",
                        text=clean,
                    ))
        except Exception as e:
            logger.warning(f"UKMTO fetch failed: {e}")
        return reports

    def _fetch_news_rss(self) -> List[IncidentReport]:
        """Pull Google News headlines matching Hormuz attack keywords."""
        reports = []
        try:
            resp = requests.get(self.NEWS_RSS_URL, timeout=15)
            text = resp.text
            for m in re.finditer(
                r"<item>.*?<title>(.*?)</title>.*?<pubDate>(.*?)</pubDate>.*?</item>",
                text, re.DOTALL,
            ):
                title = re.sub(r"<[^>]+>", "", m.group(1))
                title = (title.replace("&quot;", '"')
                              .replace("&amp;", "&")
                              .replace("&#39;", "'")
                              .strip())
                if self._is_incident_text(title):
                    reports.append(IncidentReport(
                        timestamp=datetime.now(timezone.utc),
                        source="googlenews",
                        source_type="authoritative",
                        text=title,
                    ))
        except Exception as e:
            logger.warning(f"News RSS fetch failed: {e}")
        return reports

    def _fetch_osint_twitter(self) -> List[IncidentReport]:
        """Pull recent tweets from maritime OSINT accounts."""
        if not self.twitter_fetcher or not self._osint_account_ids:
            return []

        reports = []
        for username, user_id in self._osint_account_ids.items():
            try:
                tweets = self.twitter_fetcher.get_user_tweets(user_id, max_results=20)
                for tweet in tweets:
                    if self._is_incident_text(tweet.text):
                        reports.append(IncidentReport(
                            timestamp=tweet.timestamp,
                            source=f"twitter:{username}",
                            source_type="osint",
                            text=tweet.text,
                        ))
            except Exception as e:
                logger.warning(f"Failed to fetch @{username}: {e}")
            time.sleep(0.5)
        return reports

    def check(self) -> Optional[IncidentReport]:
        """
        Poll all sources. Returns the triggering IncidentReport when a signal
        fires, otherwise None.
        """
        now = datetime.now(timezone.utc)

        if self.last_check and (now - self.last_check).total_seconds() < self.CHECK_INTERVAL:
            return None
        self.last_check = now

        if self.last_signal and (now - self.last_signal).total_seconds() < self.COOLDOWN:
            return None

        all_reports = []
        all_reports.extend(self._fetch_ukmto())
        all_reports.extend(self._fetch_news_rss())
        all_reports.extend(self._fetch_osint_twitter())

        fresh = [r for r in all_reports if r.fingerprint not in self.seen_fingerprints]
        for r in fresh:
            self.seen_fingerprints.add(r.fingerprint)

        if not fresh:
            return None

        # Authoritative source → fire immediately
        auth = [r for r in fresh if r.source_type == "authoritative"]
        if auth:
            self.last_signal = now
            return auth[0]

        # OSINT: slide into window, require N distinct sources
        self.recent_osint.extend(r for r in fresh if r.source_type == "osint")
        cutoff = now - timedelta(seconds=self.CONFIRM_WINDOW)
        self.recent_osint = [r for r in self.recent_osint if r.timestamp > cutoff]

        unique_sources = {r.source for r in self.recent_osint}
        if len(unique_sources) >= self.OSINT_REQUIRED:
            self.last_signal = now
            return self.recent_osint[-1]

        return None


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)

    from src.fetcher import PresidentialPostFetcher
    tf = PresidentialPostFetcher()
    monitor = HormuzIncidentMonitor(twitter_fetcher=tf)

    print("Running one-shot Hormuz incident check...")
    print(f"  UKMTO reports:   {len(monitor._fetch_ukmto())}")
    print(f"  News RSS reports:{len(monitor._fetch_news_rss())}")
    print(f"  OSINT reports:   {len(monitor._fetch_osint_twitter())}")
    # Reset rate limit and run full check
    monitor.last_check = None
    result = monitor.check()
    print(f"  Signal: {result}")
