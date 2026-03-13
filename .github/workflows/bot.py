"""
SolSniper - Professional Solana Meme Coin Auto Trading Bot
Runs every 5 minutes via GitHub Actions
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("SolSniper")

# ── Config ──
PRIVATE_KEY_B58  = os.environ.get("SOLANA_PRIVATE_KEY", "")
TRADE_AMOUNT_SOL = float(os.environ.get("TRADE_AMOUNT_SOL", "0.05"))
TAKE_PROFIT_1    = float(os.environ.get("TAKE_PROFIT_1", "0.50"))
TAKE_PROFIT_2    = float(os.environ.get("TAKE_PROFIT_2", "2.00"))
STOP_LOSS        = float(os.environ.get("STOP_LOSS", "0.20"))
AUTO_TRADE       = os.environ.get("AUTO_TRADE", "false").lower() == "true"

# ── Filters ──
MIN_LIQUIDITY  = 30_000
MAX_LIQUIDITY  = 500_000
MIN_VOL_1H     = 50_000
MIN_CHANGE_1H  = 20.0
MIN_AGE_MIN    = 5
MAX_AGE_MIN    = 120
MIN_MCAP       = 10_000
MAX_MCAP       = 2_000_000
MIN_TXNS_1H    = 50

SOL_MINT     = "So11111111111111111111111111111111111111112"
JUPITER_API  = "https://quote-api.jup.ag/v6"
SOLANA_RPC   = "https://api.mainnet-beta.solana.com"
SIGNALS_FILE = "signals.json"

# ─────────────────────────────────────────
# ALWAYS write signals.json first (prevents git error)
# ─────────────────────────────────────────
def load_signals():
    try:
        if os.path.exists(SIGNALS_FILE):
            with open(SIGNALS_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return {
        "signals": [],
        "trades": [],
        "stats": {"scans": 0, "signals": 0, "trades": 0},
        "wallet": {"address": "", "balance": 0},
        "last_scan": ""
    }

def save_signals(data):
    with open(SIGNALS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    log.info(f"✅ signals.json saved ({len(data['signals'])} signals)")

# ─────────────────────────────────────────
# WALLET
# ─────────────────────────────────────────
def get_wallet():
    if not PRIVATE_KEY_B58:
        log.warning("No SOLANKEY set — signal-only mode")
        return None, None
    try:
        from solders.keypair import Keypair
        kp = Keypair.from_base58_string(PRIVATE_KEY_B58)
        return kp, str(kp.pubkey())
    except Exception as e:
        log.error(f"Wallet error: {e}")
        return None, None

def get_sol_balance(pubkey):
    try:
        r = requests.post(SOLANA_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getBalance", "params": [pubkey]
        }, timeout=10)
        return r.json()["result"]["value"] / 1e9
    except:
        return 0.0

# ─────────────────────────────────────────
# DEXSCREENER
# ─────────────────────────────────────────
def fetch_new_tokens():
    try:
        r = requests.get(
            "https://api.dexscreener.com/token-profiles/latest/v1",
            timeout=15
        )
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        log.error(f"DexScreener error: {e}")
        return []

def fetch_pair_data(token_address):
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        r = requests.get(url, timeout=10)
        data = r.json()
        pairs = [p for p in data.get("pairs", []) if p.get("chainId") == "solana"]
        if not pairs:
            return None
        return max(pairs, key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0))
    except:
        return None

def get_age_minutes(pair):
    try:
        created = pair.get("pairCreatedAt", 0)
        if not created:
            return 999
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return (now_ms - created) / 60000
    except:
        return 999

def passes_filters(pair):
    try:
        liq       = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        vol_1h    = float(pair.get("volume", {}).get("h1", 0) or 0)
        change_1h = float(pair.get("priceChange", {}).get("h1", 0) or 0)
        mcap      = float(pair.get("fdv", 0) or 0)
        buys      = int(pair.get("txns", {}).get("h1", {}).get("buys", 0) or 0)
        sells     = int(pair.get("txns", {}).get("h1", {}).get("sells", 0) or 0)
        txns      = buys + sells
        age       = get_age_minutes(pair)

        if not (MIN_LIQUIDITY <= liq <= MAX_LIQUIDITY): return False
        if vol_1h < MIN_VOL_1H: return False
        if change_1h < MIN_CHANGE_1H: return False
        if not (MIN_AGE_MIN <= age <= MAX_AGE_MIN): return False
        if mcap and not (MIN_MCAP <= mcap <= MAX_MCAP): return False
        if txns < MIN_TXNS_1H: return False
        if sells > 0 and buys / (sells + 1) < 1.2: return False

        return True
    except:
        return False

def score_signal(pair):
    score = 0
    change = float(pair.get("priceChange", {}).get("h1", 0) or 0)
    vol    = float(pair.get("volume", {}).get("h1", 0) or 0)
    liq    = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    buys   = int(pair.get("txns", {}).get("h1", {}).get("buys", 0) or 0)
    sells  = int(pair.get("txns", {}).get("h1", {}).get("sells", 0) or 0)

    if change >= 200: score += 35
    elif change >= 100: score += 25
    elif change >= 50: score += 15
    else: score += 5

    if vol >= 1_000_000: score += 25
    elif vol >= 500_000: score += 15
    elif vol >= 100_000: score += 8

    if 50_000 <= liq <= 200_000: score += 20
    elif liq > 200_000: score += 10

    if sells > 0:
        ratio = buys / (sells + 1)
        if ratio >= 3: score += 20
        elif ratio >= 2: score += 12
        elif ratio >= 1.5: score += 6

    return min(score, 100)

# ─────────────────────────────────────────
# TRADING
# ─────────────────────────────────────────
def buy_token(keypair, token_mint, token_name, price):
    if not keypair:
        log.info(f"[SIGNAL] Would buy {token_name}")
        return None
    try:
        from solders.keypair import Keypair
        from solana.rpc.api import Client
        import base64

        amount_lamports = int(TRADE_AMOUNT_SOL * 1e9)

        # Get Jupiter quote
        r = requests.get(f"{JUPITER_API}/quote", params={
            "inputMint": SOL_MINT,
            "outputMint": token_mint,
            "amount": amount_lamports,
            "slippageBps": 300,
        }, timeout=10)
        quote = r.json()
        if "error" in quote:
            log.error(f"Quote error: {quote}")
            return None

        # Get swap transaction
        r2 = requests.post(f"{JUPITER_API}/swap", json={
            "quoteResponse": quote,
            "userPublicKey": str(keypair.pubkey()),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": 1000,
        }, timeout=15)
        swap = r2.json()
        if "swapTransaction" not in swap:
            log.error(f"Swap error: {swap}")
            return None

        # Sign and send
        from solders.transaction import VersionedTransaction
        client = Client(SOLANA_RPC)
        tx_bytes = base64.b64decode(swap["swapTransaction"])
        tx = VersionedTransaction.from_bytes(tx_bytes)
        result = client.send_raw_transaction(bytes(tx))
        tx_hash = str(result.value)
        log.info(f"✅ BUY SUCCESS: {tx_hash}")
        return tx_hash

    except Exception as e:
        log.error(f"Trade error: {e}")
        return None

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def run():
    log.info("=" * 50)
    log.info(f"SolSniper | Mode: {'AUTO TRADE' if AUTO_TRADE else 'SIGNAL ONLY'}")
    log.info("=" * 50)

    # Load existing data
    data = load_signals()
    data["stats"]["scans"] += 1
    data["last_scan"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Save immediately so file always exists
    save_signals(data)

    # Wallet
    keypair, pubkey = get_wallet()
    balance = 0.0
    if pubkey:
        balance = get_sol_balance(pubkey)
        log.info(f"Wallet: {pubkey[:8]}... | {balance:.4f} SOL")
        data["wallet"] = {"address": pubkey, "balance": balance}

    # Scan DexScreener
    log.info("Scanning DexScreener...")
    profiles = fetch_new_tokens()
    log.info(f"Got {len(profiles)} profiles")

    new_signals = []
    seen = set()

    for profile in profiles[:60]:
        token_address = profile.get("tokenAddress", "")
        if not token_address or token_address in seen:
            continue
        if profile.get("chainId") != "solana":
            continue
        seen.add(token_address)

        pair = fetch_pair_data(token_address)
        if not pair:
            continue

        if not passes_filters(pair):
            continue

        score  = score_signal(pair)
        label  = "STRONG BUY" if score >= 75 else "BUY" if score >= 55 else "WATCH"
        change = float(pair.get("priceChange", {}).get("h1", 0) or 0)
        liq    = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        vol    = float(pair.get("volume", {}).get("h1", 0) or 0)
        mcap   = float(pair.get("fdv", 0) or 0)
        age    = get_age_minutes(pair)
        price  = float(pair.get("priceUsd", 0) or 0)
        name   = pair.get("baseToken", {}).get("name", "Unknown")
        sym    = pair.get("baseToken", {}).get("symbol", "???")
        url    = pair.get("url", "")

        signal = {
            "token": name, "symbol": sym, "address": token_address,
            "label": label, "score": score, "price": price,
            "change_1h": change, "liquidity": liq, "volume_1h": vol,
            "mcap": mcap, "age_min": round(age, 1), "dex_url": url,
            "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "traded": False, "tx": None,
        }

        log.info(f"✅ {label} | {name} ({sym}) | +{change:.0f}% | Score:{score}")
        new_signals.append(signal)

        # Auto trade
        if AUTO_TRADE and keypair and label in ["STRONG BUY", "BUY"] and balance > TRADE_AMOUNT_SOL + 0.01:
            tx = buy_token(keypair, token_address, name, price)
            if tx:
                signal["traded"] = True
                signal["tx"] = tx
                data["stats"]["trades"] += 1
                data["trades"].insert(0, {
                    "token": name, "symbol": sym, "address": token_address,
                    "buy_tx": tx, "amount_sol": TRADE_AMOUNT_SOL,
                    "buy_price": price,
                    "tp1": price * (1 + TAKE_PROFIT_1),
                    "tp2": price * (1 + TAKE_PROFIT_2),
                    "sl": price * (1 - STOP_LOSS),
                    "status": "OPEN",
                    "time": signal["time"],
                })

        time.sleep(0.3)

    # Update data
    data["signals"] = (new_signals + data.get("signals", []))[:50]
    data["stats"]["signals"] += len(new_signals)

    # Save final
    save_signals(data)
    log.info(f"Done. {len(new_signals)} new signals this run.")

if __name__ == "__main__":
    run()
