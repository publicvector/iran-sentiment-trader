# Iran Sentiment Trader

Trading agent that monitors White House and Presidential posts related to the Iran war, classifies sentiment using an LLM, and trades bitcoin options on Coinbase accordingly.

## Strategy

- **Bellicose rhetoric** (threats, military language, escalatory tone) → Short Bitcoin
- **Conciliatory rhetoric** (de-escalation, diplomacy, peace language) → Long Bitcoin
- **Trading window**: Brief (positions held briefly)
- **Asset**: Bitcoin options on Coinbase

## Architecture

```
├── src/
│   ├── agent.py          # Main trading loop
│   ├── sentiment.py      # LLM sentiment classification
│   ├── trader.py        # Coinbase options execution
│   └── fetcher.py       # Social media / news fetching
├── config/
│   └── config.py        # Configuration settings
├── tests/
└── requirements.txt
```

## Setup

```bash
pip install -r requirements.txt
```

## Environment Variables

- `OPENAI_API_KEY` - For LLM sentiment analysis
- `COINBASE_API_KEY` - Coinbase API credentials
- `COINBASE_API_SECRET` - Coinbase API secret
- `TWITTER_API_KEY` - Twitter/X API (if using)