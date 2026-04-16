"""
Multi-commodity supply-shock monitor.

Polls news wires for disruption or resolution events on any configured
commodity (copper, nat gas, cobalt, lithium) and returns a structured
signal the agent can route to the right Kalshi market.

Generalization of the original CopperSupplyMonitor. Each commodity is a
CommoditySpec with its own keyword set, major facilities list, news
query, and Kalshi series ticker. Disruption/resolution keywords and the
firing/cooldown rules are shared across all commodities.

Sources (per commodity):
  - Mining.com RSS (one shared fetch, classified per commodity)
  - Google News RSS with commodity-specific query
  - Optional OSINT Twitter (wires tier only by default)

Direction:
  - "bullish" = supply cut → long the commodity
  - "bearish" = resolution  → short the commodity

Firing rule: authoritative source matching commodity + disruption/resolution
keywords fires a signal. 2-hour cooldown dedupes headlines on the same event.
"""

import re
import time
import logging
import hashlib
import requests
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import List, Optional, Set, Dict, Tuple
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)


# ── Shared disruption/resolution vocab ────────────────────────────────────

DISRUPTION_KEYWORDS = [
    "strike", "walkout", "stoppage", "halt", "halted", "suspend", "suspended",
    "shutdown", "shut down", "closure", "closed", "force majeure",
    "blockade", "blocked", "protest", "protesters",
    "collapse", "mudslide", "landslide", "flood", "flooding",
    "explosion", "fire", "accident", "fatality", "fatalities",
    "power outage", "power cut", "evacuate", "evacuated",
    "sabotage", "attack", "seized",
    "revoke", "revoked", "pulls permit", "pulled permit", "permit denied",
    "permit rejected", "permit pulled", "injunction", "court halts",
    "nationalize", "nationalized", "expropriate", "expropriated",
    # Gas-specific additions
    "leak", "rupture", "outage", "unplanned", "curtail", "curtailed",
    "export halt", "export ban", "cut supply", "supply cut",
]

RESOLUTION_KEYWORDS = [
    "resume", "resumed", "resumes", "restart", "restarted", "reopen", "reopened",
    "end of strike", "strike ends", "strike ended", "deal reached",
    "agreement reached", "lift force majeure", "back to normal",
    "return to service", "fully operational",
]


# ── Per-commodity spec ────────────────────────────────────────────────────

@dataclass
class CommoditySpec:
    """
    One commodity's classification + trading config.

    Tiering (nat gas is the motivating case):
      - tradeable_facilities: named facilities whose disruption moves the
        Kalshi-tracked benchmark. Hits here → trade on kalshi_series.
      - log_only_facilities: known facilities whose disruption moves a
        different benchmark (e.g., Qatar events move TTF/JKM, not Henry
        Hub). Hits here → log + alert, no trade.
      - exclude_patterns: noise filters applied to the headline text
        before classification (residential/lawsuit/political language).
      - default_tradeable: for keyword-only matches with no facility
        named, whether to treat as tradeable by default.

    For single-tier commodities (copper, cobalt, lithium), omit the
    tiering fields; the defaults make every facility tradeable and no
    exclude patterns apply.
    """
    name: str                           # "copper", "natgas", "cobalt", "lithium"
    display: str                        # user-facing name
    keywords: List[str]                 # words that identify the commodity
    facilities: List[str]               # union of tradeable + log_only
    kalshi_series: Optional[str]        # Kalshi series for the tradeable tier
    news_queries: List[str]             # Google News RSS query strings
    tradeable_facilities: Optional[List[str]] = None
    log_only_facilities: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None
    default_tradeable: bool = True
    # Strategy config
    allow_bearish: bool = True          # if False, resolution signals are dropped
    hold_seconds: Optional[int] = None  # override agent default; None = inherit

    def __post_init__(self):
        if self.tradeable_facilities is None:
            self.tradeable_facilities = list(self.facilities)
        if self.log_only_facilities is None:
            self.log_only_facilities = []
        if self.exclude_patterns is None:
            self.exclude_patterns = []


COPPER = CommoditySpec(
    name="copper",
    display="Copper",
    keywords=["copper", "cobre"],
    facilities=[
        # Chile
        "escondida", "collahuasi", "chuquicamata", "el teniente",
        "los pelambres", "centinela", "quebrada blanca", "radomiro tomic",
        "mantoverde", "andina",
        # Peru
        "las bambas", "antamina", "cerro verde", "toquepala", "cuajone",
        "antapaccay", "tia maria",
        # Elsewhere
        "grasberg",               # Indonesia
        "cobre panama",           # Panama
        "kamoa", "kakula", "katanga", "tenke fungurume", "mutanda",  # DRC
        "oyu tolgoi",             # Mongolia
        "kansanshi", "sentinel",  # Zambia
        "olympic dam",            # Australia
    ],
    kalshi_series="KXCOPPERD",
    news_queries=[
        "copper+(mine+OR+smelter+OR+concentrate)+"
        "(strike+OR+halt+OR+suspend+OR+%22force+majeure%22+OR+collapse+OR+resume)",
    ],
)

# Nat gas tradeable universe = US LNG export terminals + major US pipelines
# where a disruption tightens Henry Hub directly (the benchmark behind
# Kalshi's KXNATGASD). Events at Qatari, Russian, Australian, or European
# facilities move TTF/JKM, not Henry Hub, and so are log-only.
_NATGAS_US = [
    # LNG export terminals (post-FID or operating)
    "sabine pass", "freeport lng", "corpus christi lng", "corpus christi",
    "cameron lng", "cove point", "elba island", "plaquemines lng", "plaquemines",
    "calcasieu pass", "rio grande lng", "port arthur lng", "delfin",
    # Major interstate pipelines
    "transco", "tennessee gas pipeline", "el paso natural gas", "epng",
    "rockies express", "permian highway", "kinder morgan pipeline",
    "williams companies pipeline", "enterprise products pipeline",
    # Production basins (for hurricane/freeze-off context)
    "haynesville", "permian basin gas", "marcellus",
]
_NATGAS_GLOBAL = [
    # Qatar
    "ras laffan", "qatar lng", "qatarenergy", "north field",
    # Russia / Europe
    "nord stream", "yamal", "bovanenkovo", "groningen",
    "turkstream", "transcaspian",
    # Australia / Asia-Pacific
    "gorgon", "wheatstone", "ichthys", "prelude",
]
_NATGAS_EXCLUDES = [
    # Residential / distribution / local noise
    "hundreds without", "customers without", "customers could be without",
    "customers face", "energy customers", "multi-day natural gas outage",
    "neighborhood", "residents near", "local residents", "residents voice",
    "house explosion", "home explosion", "house was", "homes evacuated",
    # False positives on named US facilities used as place names
    "sabine pass students", "sabine pass school", "sabine pass isd",
    "pine county", "willow river",
    # Aftermath reporting
    "sues", "lawsuit", "seeks damages", "seeks over", "catastrophic injuries",
    "dangers of",
    # Political commentary / press releases
    "press release", "calls on", "letter to", "calls for halt",
    "halt natural gas export plan",
    # Historical recaps
    "ntsb", "years ago", "blast from the past", "unrepaired leaks led",
    # Anniversary / memorial / community events — not live disruptions
    "holds event", "remember the", "remind people", "anniversary of",
    "memorial", "commemorate", "marks anniversary", "one year since",
    "two years since", "years since the", "looks back",
    # Early-stage project news (not operating disruption)
    "project restart", "restarts construction", "begin construction",
]

NATGAS = CommoditySpec(
    name="natgas",
    display="Natural Gas",
    keywords=["natural gas", "lng", "nat gas", "natgas"],
    facilities=_NATGAS_US + _NATGAS_GLOBAL,
    tradeable_facilities=_NATGAS_US,
    log_only_facilities=_NATGAS_GLOBAL,
    exclude_patterns=_NATGAS_EXCLUDES,
    # Keyword-only hits ("LNG pipeline explosion" with no facility name)
    # default to log-only — require an explicit tradeable facility to trade.
    default_tradeable=False,
    # Backtest: bearish resolution path was 33% WR, -15% avg at 5d — drop it.
    allow_bearish=False,
    # Backtest: 3d hold was best (+21% cum); nat gas moves bigger/faster than BTC.
    hold_seconds=72 * 3600,
    kalshi_series="KXNATGASD",
    news_queries=[
        # US-focused: named tradeable terminals
        "(%22Sabine+Pass%22+OR+%22Freeport+LNG%22+OR+%22Cameron+LNG%22+"
        "OR+%22Corpus+Christi+LNG%22+OR+%22Cove+Point%22+OR+%22Plaquemines+LNG%22+"
        "OR+%22Calcasieu+Pass%22+OR+%22Rio+Grande+LNG%22)+"
        "(halt+OR+suspend+OR+%22force+majeure%22+OR+explosion+OR+fire+OR+outage+OR+resume)",
        # Global: for log-only tier
        "(%22natural+gas%22+OR+LNG)+"
        "(pipeline+OR+terminal+OR+export)+"
        "(halt+OR+suspend+OR+%22force+majeure%22+OR+explosion+OR+leak+OR+outage+OR+resume)",
    ],
)

COBALT = CommoditySpec(
    name="cobalt",
    display="Cobalt",
    keywords=["cobalt"],
    facilities=[
        # DRC (makes up ~70% of world supply)
        # Note: "kcc" removed — 3-letter acronym matches newspaper codes (e.g. "KCCI")
        "tenke fungurume", "mutanda", "kamoto", "metalkol", "kisanfu",
        "katanga", "kolwezi",
        # Other
        "ambatovy",         # Madagascar
        "nkamouna",         # Cameroon
        "ravensthorpe",     # Australia
    ],
    kalshi_series="KXCOBALTMON",
    news_queries=[
        "cobalt+(mine+OR+DRC+OR+Congo+OR+smelter)+"
        "(strike+OR+halt+OR+suspend+OR+%22export+ban%22+OR+%22force+majeure%22+OR+resume)",
    ],
)

LITHIUM = CommoditySpec(
    name="lithium",
    display="Lithium",
    keywords=["lithium"],
    facilities=[
        # Australia (hard rock)
        "greenbushes", "pilbara", "pilgangoora", "mt marion", "mt cattlin",
        "wodgina", "finniss",
        # Chile / Argentina (brine, "lithium triangle")
        "salar de atacama", "salar del hombre muerto", "cauchari", "olaroz",
        "sal de vida", "rincon",
        # China
        "yichun", "jiangxi",
    ],
    kalshi_series="KXLITHIUMW",
    news_queries=[
        "lithium+(mine+OR+brine+OR+refinery)+"
        "(strike+OR+halt+OR+suspend+OR+%22force+majeure%22+OR+ban+OR+resume)",
    ],
)

ALL_SPECS: List[CommoditySpec] = [NATGAS]  # COPPER, COBALT, LITHIUM disabled
SPEC_BY_NAME: Dict[str, CommoditySpec] = {s.name: s for s in ALL_SPECS}


# ── OSINT Twitter handles ─────────────────────────────────────────────────
# Shared across all commodities — these are multi-commodity wire accounts.
OSINT_WIRES = [
    "LME_news",         # LME — base metals (Cu, Al, Ni, Zn, Pb, Sn)
    "KitcoNewsNOW",     # Kitco — metals incl. battery metals
]


@dataclass
class SupplyShockReport:
    timestamp: datetime
    commodity: str              # CommoditySpec.name
    source: str
    source_type: str            # "authoritative" or "osint"
    text: str
    direction: str              # "bullish" or "bearish"
    tradeable: bool = True      # False = global tier → log-only, not traded
    fingerprint: str = ""

    def __post_init__(self):
        if not self.fingerprint:
            norm = re.sub(r"\s+", " ", self.text.lower().strip())[:80]
            key = f"{self.commodity}:{norm}"
            self.fingerprint = hashlib.md5(key.encode()).hexdigest()[:12]


class SupplyShockMonitor:
    """
    Polls news wires for supply disruptions on multiple commodities.
    Returns one report per confirmed event, tagged with commodity.
    """

    MINING_COM_RSS = "https://www.mining.com/feed/"
    GOOGLE_NEWS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

    CHECK_INTERVAL = 600      # 10 min between polls
    COOLDOWN = 7200           # 2h dedup per commodity (independent)
    CONFIRM_WINDOW = 1800
    OSINT_REQUIRED = 2

    def __init__(
        self,
        specs: List[CommoditySpec] = None,
        twitter_fetcher=None,
        enable_osint: bool = False,
    ):
        self.specs = specs or ALL_SPECS
        self.twitter_fetcher = twitter_fetcher
        self.enable_osint = enable_osint
        self.last_check: Optional[datetime] = None
        # Per-commodity state so cooldowns don't cross-block
        self.last_signal: Dict[str, datetime] = {}
        self.seen_fingerprints: Set[str] = set()
        self.recent_osint: List[SupplyShockReport] = []
        self._osint_account_ids: dict = {}

        if enable_osint and twitter_fetcher and getattr(twitter_fetcher, "bearer_token", None):
            try:
                self._osint_account_ids = twitter_fetcher._resolve_usernames(OSINT_WIRES)
                logger.info(
                    f"Resolved supply-shock OSINT accounts: {list(self._osint_account_ids.keys())}"
                )
            except Exception as e:
                logger.warning(f"Could not resolve supply-shock OSINT accounts: {e}")

    def _classify_text(self, text: str) -> Optional[Tuple[str, str, bool]]:
        """
        Returns (commodity_name, direction, tradeable) or None.

        Pipeline per commodity:
          1. Drop if any exclude_pattern hits (noise).
          2. Require at least one disruption or resolution keyword.
          3. Tier: tradeable_facility match → tradeable=True;
             log_only_facility match → tradeable=False;
             keyword-only match → spec.default_tradeable.
        Resolution takes precedence when both direction keywords appear.
        """
        t = text.lower()

        has_resolution = any(k in t for k in RESOLUTION_KEYWORDS)
        has_disruption = any(k in t for k in DISRUPTION_KEYWORDS)
        if not (has_resolution or has_disruption):
            return None
        direction = "bearish" if has_resolution else "bullish"

        for spec in self.specs:
            # Exclude noise specific to this commodity
            if any(p in t for p in spec.exclude_patterns):
                continue

            # Facility match wins (tiered)
            if any(f in t for f in spec.tradeable_facilities):
                return spec.name, direction, True
            if any(f in t for f in spec.log_only_facilities):
                return spec.name, direction, False

            # Keyword-only match → default tier
            if any(k in t for k in spec.keywords):
                return spec.name, direction, spec.default_tradeable
        return None

    def _parse_rss(self, xml: str, source_name: str) -> List[SupplyShockReport]:
        """Extract matching items from an RSS feed's <item> blocks."""
        reports = []
        for m in re.finditer(
            r"<item>.*?<title>(.*?)</title>.*?<pubDate>(.*?)</pubDate>.*?</item>",
            xml, re.DOTALL,
        ):
            title = re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))
            title = re.sub(r"<[^>]+>", "", title)
            title = (title.replace("&quot;", '"')
                          .replace("&amp;", "&")
                          .replace("&#39;", "'")
                          .replace("&#8217;", "'")
                          .strip())
            classified = self._classify_text(title)
            if classified:
                commodity, direction, tradeable = classified
                reports.append(SupplyShockReport(
                    timestamp=datetime.now(timezone.utc),
                    commodity=commodity,
                    source=source_name,
                    source_type="authoritative",
                    text=title,
                    direction=direction,
                    tradeable=tradeable,
                ))
        return reports

    def _fetch_mining_com(self) -> List[SupplyShockReport]:
        try:
            resp = requests.get(
                self.MINING_COM_RSS,
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (compatible; IranSentimentBot/1.0)"},
            )
            return self._parse_rss(resp.text, "mining.com")
        except Exception as e:
            logger.warning(f"Mining.com fetch failed: {e}")
            return []

    def _fetch_news_for_spec(self, spec: CommoditySpec) -> List[SupplyShockReport]:
        """Per-spec Google News RSS calls (each spec can have multiple queries)."""
        reports = []
        for i, query in enumerate(spec.news_queries):
            try:
                url = self.GOOGLE_NEWS_URL.format(query=query)
                resp = requests.get(url, timeout=15)
                reports.extend(self._parse_rss(resp.text, f"googlenews:{spec.name}:{i}"))
            except Exception as e:
                logger.warning(f"News RSS fetch failed for {spec.name} q{i}: {e}")
        return reports

    def _fetch_oilprice(self) -> List[SupplyShockReport]:
        """Oilprice.com main feed — broad energy coverage including nat gas."""
        try:
            resp = requests.get(
                "https://oilprice.com/rss/main",
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (compatible; IranSentimentBot/1.0)"},
            )
            return self._parse_rss(resp.text, "oilprice.com")
        except Exception as e:
            logger.warning(f"Oilprice fetch failed: {e}")
            return []

    def _fetch_gcaptain(self) -> List[SupplyShockReport]:
        """gCaptain — LNG carrier / shipping coverage."""
        try:
            resp = requests.get(
                "https://gcaptain.com/feed/",
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (compatible; IranSentimentBot/1.0)"},
            )
            return self._parse_rss(resp.text, "gcaptain.com")
        except Exception as e:
            logger.warning(f"gCaptain fetch failed: {e}")
            return []

    def _fetch_osint_twitter(self) -> List[SupplyShockReport]:
        if not self.enable_osint or not self.twitter_fetcher or not self._osint_account_ids:
            return []

        reports = []
        for username, user_id in self._osint_account_ids.items():
            try:
                tweets = self.twitter_fetcher.get_user_tweets(user_id, max_results=20)
                for tweet in tweets:
                    classified = self._classify_text(tweet.text)
                    if classified:
                        commodity, direction, tradeable = classified
                        reports.append(SupplyShockReport(
                            timestamp=tweet.timestamp,
                            commodity=commodity,
                            source=f"twitter:{username}",
                            source_type="osint",
                            text=tweet.text,
                            direction=direction,
                            tradeable=tradeable,
                        ))
            except Exception as e:
                logger.warning(f"Failed to fetch @{username}: {e}")
            time.sleep(0.5)
        return reports

    def check(self) -> Optional[SupplyShockReport]:
        """
        Poll all sources. Returns the first fresh authoritative signal across
        any commodity (respecting per-commodity cooldowns), or None.
        """
        now = datetime.now(timezone.utc)

        if self.last_check and (now - self.last_check).total_seconds() < self.CHECK_INTERVAL:
            return None
        self.last_check = now

        all_reports: List[SupplyShockReport] = []
        all_reports.extend(self._fetch_mining_com())
        all_reports.extend(self._fetch_oilprice())
        all_reports.extend(self._fetch_gcaptain())
        for spec in self.specs:
            all_reports.extend(self._fetch_news_for_spec(spec))
        all_reports.extend(self._fetch_osint_twitter())

        fresh = [r for r in all_reports if r.fingerprint not in self.seen_fingerprints]
        for r in fresh:
            self.seen_fingerprints.add(r.fingerprint)

        def in_cooldown(commodity: str) -> bool:
            last = self.last_signal.get(commodity)
            return bool(last and (now - last).total_seconds() < self.COOLDOWN)

        # Authoritative sources fire immediately, filtered by per-commodity cooldown
        auth = [r for r in fresh if r.source_type == "authoritative"
                and not in_cooldown(r.commodity)]
        if auth:
            r = auth[0]
            self.last_signal[r.commodity] = now
            return r

        # OSINT requires N independent sources within the window
        self.recent_osint.extend(r for r in fresh if r.source_type == "osint")
        cutoff = now - timedelta(seconds=self.CONFIRM_WINDOW)
        self.recent_osint = [r for r in self.recent_osint if r.timestamp > cutoff]

        # Group by commodity, require N distinct OSINT sources per group
        by_commodity: Dict[str, Set[str]] = {}
        for r in self.recent_osint:
            by_commodity.setdefault(r.commodity, set()).add(r.source)

        for commodity, sources in by_commodity.items():
            if len(sources) >= self.OSINT_REQUIRED and not in_cooldown(commodity):
                # Return most recent OSINT report for that commodity
                matching = [r for r in self.recent_osint if r.commodity == commodity]
                r = matching[-1]
                self.last_signal[commodity] = now
                return r

        return None


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)

    monitor = SupplyShockMonitor(enable_osint=False)
    print("Running one-shot multi-commodity supply-shock check...")

    mc = monitor._fetch_mining_com()
    print(f"\n  Mining.com total hits: {len(mc)}")
    by_com = {}
    for r in mc:
        by_com.setdefault(r.commodity, []).append(r)
    for name, rs in by_com.items():
        print(f"    {name}: {len(rs)}")
        for r in rs[:2]:
            print(f"      [{r.direction}] {r.text[:100]}")

    for spec in ALL_SPECS:
        gn = monitor._fetch_news_for_spec(spec)
        print(f"\n  Google News [{spec.name}]: {len(gn)} hits")
        for r in gn[:3]:
            print(f"    [{r.direction}] {r.text[:100]}")

    monitor.last_check = None
    result = monitor.check()
    print(f"\n  Signal: {result}")
