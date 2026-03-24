"""Quick test script for OKX credentials."""
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
load_dotenv()

from src.okx_trader import OKXTrader

print("Testing OKX credentials...")
trader = OKXTrader()

print(f"Simulation mode: {trader.simulation_mode}")
print(f"API Key set: {bool(trader.api_key)}")
print(f"API Secret set: {bool(trader.api_secret)}")
print(f"Passphrase set: {bool(trader.passphrase)}")

if not trader.simulation_mode:
    print("\n✅ LIVE MODE - credentials valid!")
    print(f"BTC price: ${trader.get_current_btc_price():,.2f}")
else:
    print("\n⚠️ Simulation mode - something wrong with credentials")

# Test a trade
print("\n--- Test Trade ---")
trade = trader.execute_trade("bellicose", "test-post", 100, trade_type="perpetual")
if trade:
    print(f"Trade executed: {trade.position.value} at ${trade.entry_price:,.2f}")