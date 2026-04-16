"""
Run the Iran Sentiment Trader agent continuously.

Uses the full IranSentimentTrader with:
  - 4-category sentiment classifier (bellicose/conciliatory/mixed/neutral)
  - Kalshi directional BTC contracts
  - Hormuz Strait traffic monitoring
  - Contrary signal position management
  - 8h max hold with early exit on signal reversal
"""

import os
import sys
import signal

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from src.agent import IranSentimentTrader


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Iran Sentiment Trader")
    parser.add_argument("--poll", type=int, default=30, help="Poll interval in seconds")
    parser.add_argument("--hold", type=int, default=28800, help="Max position hold time in seconds (default 8h)")
    parser.add_argument("--exchange", default="kalshi", choices=["kalshi", "kraken", "coinbase", "okx", "dydx"])
    parser.add_argument("--type", default="directional", help="Trade type")
    args = parser.parse_args()

    agent = IranSentimentTrader(
        poll_interval=args.poll,
        hold_time=args.hold,
        trade_type=args.type,
        exchange=args.exchange,
    )

    # Handle shutdown gracefully
    def shutdown(sig, frame):
        print("\nShutting down — closing open positions...")
        for trade in agent.active_trades + agent.hormuz_trades:
            agent.trader.close_position(trade)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Run the main loop
    agent.run()


if __name__ == "__main__":
    main()
