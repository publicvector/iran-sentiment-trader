"""
Simple wires/RSS fetcher for Iran-related headlines from major outlets.

Uses Google News RSS search queries to pull recent headlines from
authoritative sources (Reuters, AP). Filters to Iran-related and returns
items shaped like PresidentialPost for downstream classification.
"""

import re
import time
import requests
from typing import List, Optional
from datetime import datetime, timezone
from dataclasses import dataclass
from urllib.parse import quote_plus
import logging

logger = logging.getLogger(__name__)


@dataclass
class WireItem:
    id: str
    text: str
    source: str
    timestamp: datetime


class WiresFetcher:
    """Fetches Iran-related wire headlines via Google News RSS."""

    # Bias toward fast, authoritative sources
    QUERIES = [
        # Reuters/AP Iran + US/White House/President context
        "(Iran+US+White+House)\n when:1d site:reuters.com OR site:apnews.com",
        "(Iran+missile+OR+attack+OR+nuclear)\n when:1d site:reuters.com OR site:apnews.com",
    ]

    GNEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

    IRAN_KEYWORDS = [
        "iran", "iranian", "tehran", "nuclear", "uranium", "irgc",
        "khamenei", "jcpoa", "strait of hormuz", "hormuz", "missile", "attack",
    ]

    def __init__(self):
        self._session = requests.Session()
        self._seen_ids = set()

    def _fetch_rss(self, query: str) -> str:
        url = self.GNEWS_RSS.format(query=quote_plus(query))
        resp = self._session.get(url, timeout=15)
        resp.raise_for_status()
        return resp.text

    def _parse_items(self, xml: str) -> List[WireItem]:
        items: List[WireItem] = []
        for item_xml in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL | re.IGNORECASE):
            title_match = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", item_xml, re.DOTALL)
            title = (title_match.group(1) or title_match.group(2) or "").strip() if title_match else ""

            pub_match = re.search(r"<pubDate>(.*?)</pubDate>", item_xml)
            pub_date = pub_match.group(1).strip() if pub_match else ""
            try:
                # Example: Mon, 08 Apr 2026 14:22:00 GMT
                timestamp = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            except Exception:
                timestamp = datetime.now(timezone.utc)

            source = "wire"
            src_match = re.search(r"<source[^>]*>(.*?)</source>", item_xml)
            if src_match:
                source = src_match.group(1).strip()

            text = title
            if not text:
                continue

            # Use a stable ID by hashing the title+time
            rid = f"wire-{abs(hash((text, int(timestamp.timestamp()))))}"
            items.append(WireItem(id=rid, text=text, source=source, timestamp=timestamp))
        return items

    def _is_iran_related(self, text: str) -> bool:
        t = text.lower()
        return any(kw in t for kw in self.IRAN_KEYWORDS)

    def fetch_recent_items(self, limit: int = 20) -> List[WireItem]:
        results: List[WireItem] = []
        for q in self.QUERIES:
            try:
                xml = self._fetch_rss(q)
                items = self._parse_items(xml)
                results.extend(items)
                time.sleep(0.2)
            except Exception as e:
                logger.debug(f"Wires RSS fetch failed for query: {e}")

        # Filter
        iran_items = [i for i in results if self._is_iran_related(i.text)]

        # Deduplicate by text
        dedup: List[WireItem] = []
        seen_txt = set()
        for i in sorted(iran_items, key=lambda x: x.timestamp, reverse=True):
            key = i.text.strip().lower()
            if key in seen_txt:
                continue
            seen_txt.add(key)
            if i.id in self._seen_ids:
                continue
            self._seen_ids.add(i.id)
            dedup.append(i)

        return dedup[:limit]

