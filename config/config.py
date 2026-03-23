"""
Configuration for Iran Sentiment Trader.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# LLM Settings
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")

# Trading Settings
POSITION_SIZE_USD = float(os.getenv("POSITION_SIZE_USD", "100"))
MAX_POSITION_SIZE_USD = float(os.getenv("MAX_POSITION_SIZE_USD", "1000"))
POSITION_HOLD_SECONDS = int(os.getenv("POSITION_HOLD_SECONDS", "300"))
MAX_TRADES_PER_HOUR = int(os.getenv("MAX_TRADES_PER_HOUR", "3"))
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

# Coinbase Settings
COINBASE_API_KEY = os.getenv("COINBASE_API_KEY")
COINBASE_API_SECRET = os.getenv("COINBASE_API_SECRET")

# Twitter/X Settings
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")