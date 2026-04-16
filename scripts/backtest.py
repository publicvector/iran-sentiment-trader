"""
Backtest: Iran sentiment → BTC price direction over the past month.

Uses real Trump Truth Social posts about Iran (sourced from news articles),
GPT-4o-mini sentiment classification, and actual BTC daily prices from CoinGecko.

For each post, we check: did BTC move in the direction we predicted
within 24h of the post?
"""

import os
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
load_dotenv()

import requests
from src.sentiment import IranSentimentClassifier, Sentiment

# ── Real Trump Truth Social posts about Iran (past month) ──────────────────
# Sourced from PBS, Al Jazeera, NBC, Mediaite, CNBC, trumpstruth.org, etc.

POSTS = [
    {
        "date": "2026-02-28",
        "text": (
            "A short time ago, the United States military began major combat "
            "operations in Iran. Our objective is to defend the American people "
            "by eliminating imminent threats from the Iranian regime."
        ),
        "source": "PBS / Truth Social video",
    },
    {
        "date": "2026-02-28",
        "text": (
            "We're going to destroy their missiles and raze their missile "
            "industry to the ground. We're going to annihilate their navy."
        ),
        "source": "PBS / Truth Social video",
    },
    {
        "date": "2026-03-06",
        "text": (
            "There will be no deal to end the war against Iran without an "
            "UNCONDITIONAL SURRENDER!"
        ),
        "source": "CNBC",
    },
    {
        "date": "2026-03-07",
        "text": (
            "Iran, which is being beaten to hell, has apologised and surrendered "
            "to its Middle East neighbours, and promised that it will not shoot "
            "at them anymore. This promise was only made because of the relentless "
            "US and Israeli attacks. Today, Iran will be hit very hard."
        ),
        "source": "ANI / X post",
    },
    {
        "date": "2026-03-10",
        "text": (
            "If Iran has put out any mines in the Hormuz Strait, and we have no "
            "reports of them doing so, we want them removed, IMMEDIATELY! If for "
            "any reason mines were placed, and they are not removed forthwith, "
            "the Military consequences to Iran will be at a level never seen before."
        ),
        "source": "NPR",
    },
    {
        "date": "2026-03-20",
        "text": (
            "The Hormuz Strait will have to be guarded and policed, as necessary, "
            "by other Nations who use it. The United States does not! We are very "
            "close to achieving our objectives."
        ),
        "source": "RealClearPolitics",
    },
    {
        "date": "2026-03-20",
        "text": (
            "We are getting very close to meeting our objectives as we consider "
            "winding down our great Military effort in the region."
        ),
        "source": "@POTUS Twitter",
    },
    {
        "date": "2026-03-23",
        "text": (
            "I am pleased to report that the United States of America, and the "
            "country of Iran, have had, over the last two days, very good and "
            "productive conversations regarding a complete and total resolution "
            "of our hostilities in the Middle East."
        ),
        "source": "Al Jazeera",
    },
    {
        "date": "2026-03-23",
        "text": (
            "I have instructed the Department of War to postpone any and all "
            "military strikes against Iranian power plants and energy "
            "infrastructure for a five day period, subject to the success of "
            "the ongoing meetings and discussions."
        ),
        "source": "Al Jazeera",
    },
    {
        "date": "2026-03-26",
        "text": (
            "As per Iranian Government request, please let this statement serve "
            "to represent that I am pausing the period of Energy Plant destruction "
            "by 10 Days to Monday, April 6, 2026, at 8 PM, Eastern Time. Talks "
            "are ongoing and, despite erroneous statements to the contrary by the "
            "Fake News Media, and others, they are going very well."
        ),
        "source": "Al Jazeera",
    },
    {
        "date": "2026-03-29",
        "text": "Iran is feckless and weak! They are begging to make a deal with the United States.",
        "source": "trumpstruth.org / GB News",
    },
    {
        "date": "2026-03-30",
        "text": (
            "If Iran does not agree to a deal with the United States shortly, "
            "we will have no choice but to blow up their energy sites, STARTING "
            "WITH THE BIGGEST ONE FIRST!"
        ),
        "source": "MSNBC liveblog",
    },
    {
        "date": "2026-03-31",
        "text": (
            "You'll have to start learning how to fight for yourself, the U.S.A. "
            "won't be there to help you anymore, just like you weren't there for "
            "us. Iran has been, essentially, decimated. The hard part is done. "
            "Go get your own oil!"
        ),
        "source": "CNBC / Al Jazeera",
    },
    {
        "date": "2026-03-31",
        "text": (
            "France has been VERY UNHELPFUL with respect to the Butcher of Iran, "
            "who has been successfully eliminated! The U.S.A. will REMEMBER!!"
        ),
        "source": "CNBC",
    },
    {
        "date": "2026-04-01",
        "text": (
            "Iran's New Regime President, much less Radicalized and far more "
            "intelligent than his predecessors, has just asked the United States "
            "of America for a CEASEFIRE! We will consider when Hormuz Strait is "
            "open, free, and clear. Until then, we are blasting Iran into oblivion "
            "or, as they say, back to the Stone Ages!!!"
        ),
        "source": "Mediaite",
    },
    {
        "date": "2026-04-01",
        "text": (
            "We are in serious discussions with A NEW, AND MORE REASONABLE, "
            "REGIME to end our Military Operations in Iran."
        ),
        "source": "CNN liveblog",
    },
    {
        "date": "2026-04-03",
        "text": (
            "With a little more time, we can easily OPEN THE HORMUZ STRAIT, "
            "TAKE THE OIL, & MAKE A FORTUNE. IT WOULD BE A 'GUSHER' FOR THE WORLD???"
        ),
        "source": "trumpstruth.org",
    },
    {
        "date": "2026-04-03",
        "text": "KEEP THE OIL, ANYONE?",
        "source": "trumpstruth.org",
    },
    {
        "date": "2026-04-04",
        "text": (
            "Remember when I gave Iran ten days to MAKE A DEAL or OPEN UP THE "
            "HORMUZ STRAIT. Time is running out - 48 hours before all Hell will "
            "reign down on them..."
        ),
        "source": "trumpstruth.org",
    },
]


def get_btc_daily_prices() -> dict:
    """Fetch daily BTC closing prices for the past 40 days from CoinGecko."""
    resp = requests.get(
        "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
        params={"vs_currency": "usd", "days": "40", "interval": "daily"},
        timeout=30,
    )
    data = resp.json()
    prices = {}
    for ts, price in data.get("prices", []):
        dt = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        prices[dt] = price
    return prices


def run_backtest():
    print("=" * 90)
    print("  BACKTEST: Trump Iran Posts → BTC Sentiment Trading (Past Month)")
    print("  Strategy: BELLICOSE → short BTC | CONCILIATORY → long BTC | NEUTRAL → skip")
    print("=" * 90)

    print("\nFetching BTC daily prices...")
    btc_prices = get_btc_daily_prices()
    dates_available = sorted(btc_prices.keys())
    print(f"Got {len(btc_prices)} daily prices ({dates_available[0]} to {dates_available[-1]})")

    classifier = IranSentimentClassifier()

    results = []
    total_pnl_pct = 0.0
    wins = 0
    losses = 0
    flat = 0
    skipped_no_price = 0

    print(f"\nClassifying {len(POSTS)} posts...\n")
    print(
        f"{'#':<3} {'Date':<12} {'Sentiment':<14} {'Signal':<7} "
        f"{'BTC@Post':>10} {'BTC+24h':>10} {'Move%':>7} {'P&L%':>8} {'Result':>6}"
    )
    print("-" * 90)

    for i, post in enumerate(POSTS, 1):
        date_str = post["date"]
        text = post["text"]

        sentiment = classifier.classify(text)

        price_today = btc_prices.get(date_str)
        next_date = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        price_next = btc_prices.get(next_date)

        if not price_today or not price_next:
            skipped_no_price += 1
            continue

        actual_move_pct = ((price_next - price_today) / price_today) * 100

        if sentiment == Sentiment.NEUTRAL:
            flat += 1
            pnl_pct = 0.0
            signal = "SKIP"
            result = "-"
        elif sentiment == Sentiment.BELLICOSE:
            signal = "SHORT"
            pnl_pct = -actual_move_pct  # short profits when price drops
            if pnl_pct > 0:
                wins += 1
                result = "WIN"
            else:
                losses += 1
                result = "LOSS"
            total_pnl_pct += pnl_pct
        else:  # CONCILIATORY
            signal = "LONG"
            pnl_pct = actual_move_pct  # long profits when price rises
            if pnl_pct > 0:
                wins += 1
                result = "WIN"
            else:
                losses += 1
                result = "LOSS"
            total_pnl_pct += pnl_pct

        results.append(
            {
                "date": date_str,
                "text": text[:60],
                "sentiment": sentiment.value,
                "signal": signal,
                "price_today": price_today,
                "price_next": price_next,
                "pnl_pct": pnl_pct,
                "result": result,
            }
        )

        print(
            f"{i:<3} {date_str:<12} {sentiment.value:<14} {signal:<7} "
            f"${price_today:>9,.0f} ${price_next:>9,.0f} {actual_move_pct:>+6.2f}% "
            f"{pnl_pct:>+7.2f}% {'  ' + result:>6}"
        )

    # ── Summary ────────────────────────────────────────────────────────────
    total_trades = wins + losses
    print("\n" + "=" * 90)
    print("  RESULTS")
    print("=" * 90)
    print(f"  Posts analyzed:        {len(results)}")
    print(f"  Trades taken:          {total_trades}")
    print(f"  Skipped (neutral):     {flat}")
    if skipped_no_price:
        print(f"  Skipped (no price):    {skipped_no_price}")
    print(f"  Wins:                  {wins}")
    print(f"  Losses:                {losses}")
    if total_trades > 0:
        print(f"  Win rate:              {wins}/{total_trades} = {wins / total_trades * 100:.1f}%")
        avg = total_pnl_pct / total_trades
        print(f"  Avg P&L per trade:     {avg:+.2f}%")
    print(f"  Cumulative P&L:        {total_pnl_pct:+.2f}%")

    # Hypothetical account
    account = 1000
    final = account
    for r in results:
        if r["signal"] != "SKIP":
            final *= 1 + r["pnl_pct"] / 100
    print(f"\n  $1,000 compounded:     ${final:,.2f} ({((final - account) / account) * 100:+.2f}%)")

    # BTC buy & hold
    if dates_available:
        start_p = btc_prices[dates_available[0]]
        end_p = btc_prices[dates_available[-1]]
        bh = ((end_p - start_p) / start_p) * 100
        print(f"  BTC buy & hold:        {bh:+.2f}% (${start_p:,.0f} → ${end_p:,.0f})")

    # ── Per-sentiment breakdown ────────────────────────────────────────────
    print("\n  BREAKDOWN BY SENTIMENT:")
    for s in ["bellicose", "conciliatory"]:
        trades_s = [r for r in results if r["sentiment"] == s and r["signal"] != "SKIP"]
        if trades_s:
            w = sum(1 for t in trades_s if t["pnl_pct"] > 0)
            total = sum(t["pnl_pct"] for t in trades_s)
            print(f"    {s.upper():<14} {len(trades_s)} trades, {w} wins, P&L: {total:+.2f}%")

    print("\n" + "=" * 90)


if __name__ == "__main__":
    run_backtest()
