import requests
import os
import json
import time
from datetime import datetime

BUY_AMOUNT_SOL = 0.05
TAKE_PROFIT_PCT = 50
STOP_LOSS_PCT = 15
MIN_LIQUIDITY = 50000
MIN_VOLUME_24H = 100000
MAX_AGE_HOURS = 24
MIN_PRICE_CHANGE = 20
SLIPPAGE = 0.15

def get_trending_tokens():
    try:
        trending_response = requests.get(
            "https://api.dexscreener.com/latest/dex/search?q=solana",
            timeout=10
        )
        if trending_response.status_code == 200:
            data = trending_response.json()
            pairs = data.get("pairs", [])
            return pairs
        return []
    except Exception as e:
        print(f"Error fetching tokens: {e}")
        return []

def analyze_token(pair):
    try:
        if not pair:
            return False
        chain = pair.get("chainId", "")
        if chain != "solana":
            return False
        liquidity = pair.get("liquidity", {}).get("usd", 0)
        if liquidity < MIN_LIQUIDITY:
            return False
        volume_24h = pair.get("volume", {}).get("h24", 0)
        if volume_24h < MIN_VOLUME_24H:
            return False
        price_change_24h = pair.get("priceChange", {}).get("h24", 0)
        if price_change_24h < MIN_PRICE_CHANGE:
            return False
        pair_created_at = pair.get("pairCreatedAt", 0)
        if pair_created_at:
            age_hours = (time.time() * 1000 - pair_created_at) / (1000 * 3600)
            if age_hours > MAX_AGE_HOURS:
                return False
        return True
    except Exception as e:
        print(f"Error analyzing token: {e}")
        return False

def log_signal(pair, action):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    token_name = pair.get("baseToken", {}).get("name", "Unknown")
    token_symbol = pair.get("baseToken", {}).get("symbol", "???")
    price = pair.get("priceUsd", "0")
    volume = pair.get("volume", {}).get("h24", 0)
    liquidity = pair.get("liquidity", {}).get("usd", 0)
    price_change = pair.get("priceChange", {}).get("h24", 0)
    pair_address = pair.get("pairAddress", "")
    print(f"\n==================================================")
    print(f"SIGNAL: {action}")
    print(f"Token: {token_name} ({token_symbol})")
    print(f"Price: ${price}")
    print(f"24h Volume: ${volume:,.0f}")
    print(f"Liquidity: ${liquidity:,.0f}")
    print(f"24h Change: {price_change}%")
    print(f"DexScreener: https://dexscreener.com/solana/{pair_address}")
    print(f"==================================================\n")

def run_bot():
    print(f"\nSOLANA DEX BOT STARTED")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Scanning DexScreener for Solana tokens...")
    pairs = get_trending_tokens()
    print(f"Found {len(pairs)} pairs to analyze...")
    signals_found = 0
    for pair in pairs:
        if analyze_token(pair):
            log_signal(pair, "BUY SIGNAL")
            signals_found += 1
            if signals_found >= 3:
                break
    if signals_found == 0:
        print("No tokens met the criteria this run.")
    else:
        print(f"Found {signals_found} buy signal(s) this run!")
    print(f"Bot scan complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    run_bot()
