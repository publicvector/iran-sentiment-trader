"""
Hormuz Strait traffic monitor.

Scrapes vessel transit data from hormuztracker.com hourly.
An INCREASE in traffic = de-escalation signal → bullish BTC.

This is a supplementary signal to the sentiment classifier:
  - Traffic increase detected → open bullish BTC position
  - Bellicose tweet/post arrives → close the bullish position immediately
"""

import re
import time
import logging
import requests
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class HormuzReading:
    timestamp: datetime
    vessels_detected: Optional[int]  # ships transiting today
    normal_daily: int = 60           # pre-crisis baseline (~60/day)
    status: str = "unknown"          # "closed", "restricted", "open"
    raw_text: str = ""


class HormuzMonitor:
    """
    Monitors Strait of Hormuz shipping traffic.
    An increase in transits is a de-escalation signal (bullish BTC).
    """

    SCRAPE_URL = "https://www.hormuztracker.com/"
    FALLBACK_URL = "https://hormuzstraitmonitor.com/"
    CHECK_INTERVAL = 3600  # 1 hour (data updates hourly)

    # Thresholds for signal generation
    TRAFFIC_INCREASE_THRESHOLD = 5   # vessels above previous reading to trigger bullish
    TRAFFIC_NORMAL_THRESHOLD = 30    # vessels/day = "significant reopening"

    def __init__(self):
        self.readings: list[HormuzReading] = []
        self.last_check: Optional[datetime] = None

    def _scrape_traffic(self) -> Optional[HormuzReading]:
        """Scrape current vessel count from monitoring sites."""
        reading = HormuzReading(timestamp=datetime.now(), vessels_detected=None)

        # Try hormuztracker.com first (has more granular data)
        try:
            resp = requests.get(self.SCRAPE_URL, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (compatible; IranSentimentBot/1.0)"
            })
            text = resp.text

            # Look for vessel count patterns in the HTML
            # hormuztracker shows "~7-95% vs 138 avg" or similar
            patterns = [
                r'(\d+)\s*vessels?\s*detected',
                r'(\d+)\s*ships?\s*transit',
                r'~(\d+)',
                r'(\d+)\s*vessel',
            ]
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    count = int(match.group(1))
                    if 0 <= count <= 200:  # sanity check
                        reading.vessels_detected = count
                        break

            # Check status
            if "closed" in text.lower():
                reading.status = "closed"
            elif "restricted" in text.lower():
                reading.status = "restricted"
            elif "open" in text.lower() and "closed" not in text.lower():
                reading.status = "open"

            reading.raw_text = text[:500]

        except Exception as e:
            logger.warning(f"Failed to scrape hormuztracker.com: {e}")

        # Fallback: hormuzstraitmonitor.com
        if reading.vessels_detected is None:
            try:
                resp = requests.get(self.FALLBACK_URL, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; IranSentimentBot/1.0)"
                })
                text = resp.text

                if "near zero" in text.lower():
                    reading.vessels_detected = 0
                    reading.status = "closed"
                elif match := re.search(r'Ships transiting:\s*(\d+)', text, re.IGNORECASE):
                    reading.vessels_detected = int(match.group(1))

            except Exception as e:
                logger.warning(f"Failed to scrape fallback: {e}")

        return reading if reading.vessels_detected is not None else None

    def check(self) -> Optional[str]:
        """
        Check for traffic changes. Returns a signal string or None.

        Bearish signal fires ONLY when traffic drops from a previously elevated
        level — i.e., ships were transiting at meaningful volume and that trend
        ceased. It does NOT fire just because the strait is already low.

        Returns:
            "bullish"      - traffic increased (de-escalation) → long BTC, short oil
            "very_bullish" - traffic near or above normal (reopening)
            "bearish"      - traffic dropped FROM elevated level → short BTC, long oil
            None           - no change, first reading, or unable to determine
        """
        now = datetime.now()

        # Don't check more than once per interval
        if self.last_check and (now - self.last_check).total_seconds() < self.CHECK_INTERVAL:
            return None

        self.last_check = now

        reading = self._scrape_traffic()
        if not reading:
            logger.warning("Could not get Hormuz traffic reading")
            return None

        pct_of_normal = (reading.vessels_detected / reading.normal_daily * 100) if reading.vessels_detected else 0
        print(
            f"   [HORMUZ] {reading.vessels_detected} vessels detected "
            f"({pct_of_normal:.0f}% of normal ~{reading.normal_daily}/day, status: {reading.status})"
        )

        signal = None

        if not self.readings:
            # First reading: just set baseline, never trade on it
            print(f"   [HORMUZ] Baseline set — no signal until trend develops")
        else:
            prev = self.readings[-1]
            if prev.vessels_detected is not None and reading.vessels_detected is not None:
                delta = reading.vessels_detected - prev.vessels_detected

                # Bearish: traffic dropped AND the previous reading was elevated enough
                # to represent meaningful flow ceasing (not just noise around zero).
                if delta <= -self.TRAFFIC_INCREASE_THRESHOLD:
                    if prev.vessels_detected >= self.TRAFFIC_INCREASE_THRESHOLD:
                        signal = "bearish"
                        print(
                            f"   [HORMUZ] Traffic DROP from elevated level: "
                            f"{prev.vessels_detected} → {reading.vessels_detected} ({delta:+d}) → BEARISH"
                        )
                    else:
                        print(
                            f"   [HORMUZ] Traffic drop ({prev.vessels_detected} → {reading.vessels_detected}) "
                            f"but previous level was already low — no signal"
                        )

                # Bullish: traffic increased
                elif delta >= self.TRAFFIC_INCREASE_THRESHOLD:
                    signal = "bullish"
                    print(
                        f"   [HORMUZ] Traffic INCREASE: {prev.vessels_detected} → "
                        f"{reading.vessels_detected} ({delta:+d}) → BULLISH"
                    )

                # Very bullish: traffic back near normal levels
                if reading.vessels_detected >= self.TRAFFIC_NORMAL_THRESHOLD:
                    signal = "very_bullish"
                    print(
                        f"   [HORMUZ] Traffic approaching normal: "
                        f"{reading.vessels_detected} vessels → VERY BULLISH"
                    )

        self.readings.append(reading)

        # Keep only last 48 readings (2 days at hourly)
        if len(self.readings) > 48:
            self.readings = self.readings[-48:]

        return signal

    def get_latest(self) -> Optional[HormuzReading]:
        """Get the most recent reading."""
        return self.readings[-1] if self.readings else None

    def get_trend(self) -> Optional[str]:
        """Get the trend over the last few readings."""
        if len(self.readings) < 3:
            return None

        recent = [r.vessels_detected for r in self.readings[-3:] if r.vessels_detected is not None]
        if len(recent) < 2:
            return None

        if all(recent[i] <= recent[i + 1] for i in range(len(recent) - 1)):
            return "increasing"
        elif all(recent[i] >= recent[i + 1] for i in range(len(recent) - 1)):
            return "decreasing"
        return "flat"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    monitor = HormuzMonitor()

    print("Checking Hormuz Strait traffic...")
    # Force first reading
    reading = monitor._scrape_traffic()
    if reading:
        print(f"  Vessels detected: {reading.vessels_detected}")
        print(f"  Status: {reading.status}")
        print(f"  Normal daily: ~{reading.normal_daily}")
        pct = (reading.vessels_detected / reading.normal_daily * 100) if reading.vessels_detected else 0
        print(f"  Current vs normal: {pct:.0f}%")
    else:
        print("  Could not get reading")
