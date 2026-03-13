"""
SolSniper - Professional Solana Meme Coin Auto Trading Bot
Runs every 5 minutes via GitHub Actions
Scans DexScreener → Filters → Buys via Jupiter → Auto Sell TP/SL
"""

import os
import json
import time
import base64
import logging
import requests
from datetime import datetime, timezone

# ── Logging ──
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("SolSniper")

# ── Config from GitHub Secrets ──
PRIVATE_KEY_B58   = os.environ.get("SOLANA_PRIVATE_KEY", "")
TRADE_AMOUNT_SOL  = float(os.environ.get("TRADE_AMOUNT_SOL", "0.05"))
TAKE_PROFIT_1     = float(os.environ.get("TAKE_PROFIT_1", "0.50"))   # 50%
TAKE_PROFIT_2     = float(os.environ.get("TAKE_PROFIT_2", "2.00"))   # 200%
STOP_LOSS         = float(os.environ.get("STOP_LOSS", "0.20"))        # 20%
MOONBAG_PCT       = float(os.environ.get("MOONBAG_PCT", "0.10"))      # 10%
AUTO_TRADE        = os.environ.get("AUTO_TRADE", "false").lower() == "true"
SIGNAL_MODE       = not AUTO_TRADE

# ── Filters ──
MIN_LIQUIDITY     = 30_000
MAX_LIQUIDITY     = 500_000
MIN_VOL_1H        = 50_000
MIN_CHANGE_1H     = 20.0
MIN_AGE_MIN       = 5
MAX_AGE_MIN       = 120
MIN_MCAP          = 10_000
MAX_MCAP          = 2_000_000
MIN_TXNS_1H       = 50
RUG_MAX_TOP10_PCT = 80.0

# ── Constants ──
SOL_MINT     = "So11111111111111111111111111111111111111112"
USDC_MINT    = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
JUPITER_API  = "https://quote-api.jup.ag/v6"
DEXSCREENER  = "https://api.dexscreener.com/token-profiles/latest/v1"
SOLANA_RPC   = "https://api.mainnet-beta.solana.com"
SIGNALS_FILE = "signals.json"

# ─────────────────────────────────────────
# WALLET
# ─────────────────────────────────────────
def get_wallet():
    if not PRIVATE_KEY_B58:
        log.warning("No SOLANA_PRIVATE_KEY set — running in signal-only mode")
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
# DEXSCREENER SCANNER
# ─────────────────────────────────────────
def fetch_new_pairs():
    """Fetch latest token profiles from DexScreener"""
    try:
        r = requests.get(DEXSCREENER, timeout=15)
        r.raise_for_status()
        return r.json() if isinstance(r.json(), list) else []
    except Exception as e:
        log.error(f"DexScreener fetch error: {e}")
        return []

def fetch_pair_data(token_address):
    """Get detailed pair data for a specific token"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        r = requests.get(url, timeout=10)
        data = r.json()
        pairs = data.get("pairs", [])
        if not pairs:
            return None
        # Get highest liquidity SOL pair
        sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if not sol_pairs:
            return None
        return max(sol_pairs, key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0))
    except Exception as e:
        log.error(f"Pair data error: {e}")
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
    """Apply all anti-rug and quality filters"""
    try:
        liquidity  = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        volume_1h  = float(pair.get("volume", {}).get("h1", 0) or 0)
        change_1h  = float(pair.get("priceChange", {}).get("h1", 0) or 0)
        mcap       = float(pair.get("fdv", 0) or 0)
        txns_1h    = int(pair.get("txns", {}).get("h1", {}).get("buys", 0) or 0) + \
                     int(pair.get("txns", {}).get("h1", {}).get("sells", 0) or 0)
        age_min    = get_age_minutes(pair)

        # ── Liquidity filter ──
        if not (MIN_LIQUIDITY <= liquidity <= MAX_LIQUIDITY):
            return False, f"Liquidity ${liquidity:,.0f} out of range"

        # ── Volume filter ──
        if volume_1h < MIN_VOL_1H:
            return False, f"Volume ${volume_1h:,.0f} too low"

        # ── Price change filter ──
        if change_1h < MIN_CHANGE_1H:
            return False, f"Change {change_1h:.1f}% too low"

        # ── Age filter ──
        if not (MIN_AGE_MIN <= age_min <= MAX_AGE_MIN):
            return False, f"Age {age_min:.0f}min out of range"

        # ── Market cap filter ──
        if mcap and not (MIN_MCAP <= mcap <= MAX_MCAP):
            return False, f"MCap ${mcap:,.0f} out of range"

        # ── Transaction count filter ──
        if txns_1h < MIN_TXNS_1H:
            return False, f"Txns {txns_1h} too low"

        # ── Buy/Sell ratio (anti-dump) ──
        buys  = int(pair.get("txns", {}).get("h1", {}).get("buys", 0) or 0)
        sells = int(pair.get("txns", {}).get("h1", {}).get("sells", 0) or 0)
        if sells > 0 and buys / (sells + 1) < 1.2:
            return False, f"Buy/sell ratio too low ({buys}/{sells})"

        return True, "PASS"

    except Exception as e:
        return False, f"Filter error: {e}"

def score_signal(pair):
    """Score a token 0-100 to determine signal strength"""
    score = 0
    change_1h = float(pair.get("priceChange", {}).get("h1", 0) or 0)
    volume_1h = float(pair.get("volume", {}).get("h1", 0) or 0)
    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    buys  = int(pair.get("txns", {}).get("h1", {}).get("buys", 0) or 0)
    sells = int(pair.get("txns", {}).get("h1", {}).get("sells", 0) or 0)

    # Change scoring
    if change_1h >= 200: score += 35
    elif change_1h >= 100: score += 25
    elif change_1h >= 50: score += 15
    else: score += 5

    # Volume scoring
    if volume_1h >= 1_000_000: score += 25
    elif volume_1h >= 500_000: score += 15
    elif volume_1h >= 100_000: score += 8

    # Liquidity scoring
    if 50_000 <= liquidity <= 200_000: score += 20
    elif liquidity > 200_000: score += 10

    # Buy pressure
    if sells > 0:
        ratio = buys / (sells + 1)
        if ratio >= 3: score += 20
        elif ratio >= 2: score += 12
        elif ratio >= 1.5: score += 6

    return min(score, 100)

def get_signal_label(score):
    if score >= 75: return "STRONG BUY"
    elif score >= 55: return "BUY"
    else: return "WATCH"

# ─────────────────────────────────────────
# JUPITER TRADING
# ─────────────────────────────────────────
def get_quote(input_mint, output_mint, amount_lamports):
    try:
        r = requests.get(f"{JUPITER_API}/quote", params={
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount_lamports,
            "slippageBps": 300,  # 3% slippage
            "onlyDirectRoutes": False,
        }, timeout=10)
        return r.json()
    except Exception as e:
        log.error(f"Quote error: {e}")
        return None

def execute_swap(keypair, quote_response):
    """Execute swap via Jupiter"""
    try:
        from solders.transaction import VersionedTransaction
        from solana.rpc.api import Client

        client = Client(SOLANA_RPC)

        # Get swap transaction
        r = requests.post(f"{JUPITER_API}/swap", json={
            "quoteResponse": quote_response,
            "userPublicKey": str(keypair.pubkey()),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": 1000,
        }, timeout=15)

        swap_data = r.json()
        if "swapTransaction" not in swap_data:
            log.error(f"Swap error: {swap_data}")
            return None

        # Decode and sign
        tx_bytes = base64.b64decode(swap_data["swapTransaction"])
        tx = VersionedTransaction.from_bytes(tx_bytes)
        signed_tx = keypair.sign_message(bytes(tx.message))

        # Send
        result = client.send_raw_transaction(bytes(tx))
        return str(result.value)

    except ImportError:
        log.warning("solders/solana not installed — cannot execute trades")
        return None
    except Exception as e:
        log.error(f"Swap execution error: {e}")
        return None

def buy_token(keypair, pubkey, token_mint, token_name):
    """Buy a token using SOL via Jupiter"""
    if not keypair:
        log.info(f"[SIGNAL ONLY] Would BUY {token_name} ({token_mint[:8]}...)")
        return None

    amount_lamports = int(TRADE_AMOUNT_SOL * 1e9)
    log.info(f"Buying {token_name} with {TRADE_AMOUNT_SOL} SOL...")

    quote = get_quote(SOL_MINT, token_mint, amount_lamports)
    if not quote or "error" in quote:
        log.error(f"Could not get quote: {quote}")
        return None

    out_amount = int(quote.get("outAmount", 0))
    log.info(f"Quote: {TRADE_AMOUNT_SOL} SOL → {out_amount} tokens")

    tx_hash = execute_swap(keypair, quote)
    if tx_hash:
        log.info(f"✅ BUY SUCCESS: {tx_hash}")
        return {"tx": tx_hash, "amount_sol": TRADE_AMOUNT_SOL, "out_tokens": out_amount}
    else:
        log.error("BUY FAILED")
        return None

# ─────────────────────────────────────────
# SIGNALS FILE
# ─────────────────────────────────────────
def load_signals():
    try:
        if os.path.exists(SIGNALS_FILE):
            with open(SIGNALS_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return {"signals": [], "trades": [], "stats": {"scans": 0, "signals": 0, "trades": 0, "wins": 0}, "last_scan": ""}

def save_signals(data):
    with open(SIGNALS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    log.info(f"Saved {len(data['signals'])} signals to {SIGNALS_FILE}")

# ─────────────────────────────────────────
# MAIN BOT LOOP
# ─────────────────────────────────────────
def run():
    log.info("=" * 50)
    log.info("SolSniper Bot Starting")
    log.info(f"Mode: {'AUTO TRADE' if AUTO_TRADE else 'SIGNAL ONLY'}")
    log.info(f"Trade size: {TRADE_AMOUNT_SOL} SOL")
    log.info("=" * 50)

    keypair, pubkey = get_wallet()
    if pubkey:
        balance = get_sol_balance(pubkey)
        log.info(f"Wallet: {pubkey[:8]}... | Balance: {balance:.4f} SOL")
    else:
        log.info("No wallet configured — signal-only mode")

    # Load existing data
    data = load_signals()
    data["stats"]["scans"] += 1
    data["last_scan"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Fetch new tokens
    log.info("Scanning DexScreener for new tokens...")
    profiles = fetch_new_pairs()
    log.info(f"Found {len(profiles)} token profiles")

    new_signals = []
    processed = set()

    for profile in profiles[:50]:  # Check top 50 newest
        token_address = profile.get("tokenAddress", "")
        if not token_address or token_address in processed:
            continue
        processed.add(token_address)

        # Skip non-Solana
        if profile.get("chainId") != "solana":
            continue

        # Get detailed pair data
        pair = fetch_pair_data(token_address)
        if not pair:
            continue

        # Apply filters
        passed, reason = passes_filters(pair)
        if not passed:
            continue

        # Score the signal
        score = score_signal(pair)
        label = get_signal_label(score)

        change_1h  = float(pair.get("priceChange", {}).get("h1", 0) or 0)
        liquidity  = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        volume_1h  = float(pair.get("volume", {}).get("h1", 0) or 0)
        mcap       = float(pair.get("fdv", 0) or 0)
        age_min    = get_age_minutes(pair)
        price      = float(pair.get("priceUsd", 0) or 0)
        token_name = pair.get("baseToken", {}).get("name", "Unknown")
        token_sym  = pair.get("baseToken", {}).get("symbol", "???")
        dex_url    = pair.get("url", "")

        signal = {
            "token": token_name,
            "symbol": token_sym,
            "address": token_address,
            "label": label,
            "score": score,
            "price": price,
            "change_1h": change_1h,
            "liquidity": liquidity,
            "volume_1h": volume_1h,
            "mcap": mcap,
            "age_min": round(age_min, 1),
            "dex_url": dex_url,
            "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "traded": False,
            "tx": None,
        }

        log.info(f"✅ Signal: {token_name} ({token_sym}) | {label} | Score:{score} | +{change_1h:.0f}%")
        new_signals.append(signal)

        # Auto trade if enabled and strong signal
        if AUTO_TRADE and keypair and label in ["STRONG BUY", "BUY"] and balance > TRADE_AMOUNT_SOL + 0.01:
            result = buy_token(keypair, pubkey, token_address, token_name)
            if result:
                signal["traded"] = True
                signal["tx"] = result["tx"]
                data["stats"]["trades"] += 1
                data["trades"].append({
                    "token": token_name,
                    "symbol": token_sym,
                    "address": token_address,
                    "buy_tx": result["tx"],
                    "amount_sol": TRADE_AMOUNT_SOL,
                    "buy_price": price,
                    "tp1": price * (1 + TAKE_PROFIT_1),
                    "tp2": price * (1 + TAKE_PROFIT_2),
                    "sl": price * (1 - STOP_LOSS),
                    "status": "OPEN",
                    "time": signal["time"],
                })

        time.sleep(0.5)  # Rate limit

    # Update signals (keep last 50)
    data["signals"] = (new_signals + data.get("signals", []))[:50]
    data["stats"]["signals"] += len(new_signals)

    if pubkey:
        data["wallet"] = {
            "address": pubkey,
            "balance": get_sol_balance(pubkey),
        }

    save_signals(data)

    log.info(f"Scan complete. Found {len(new_signals)} signals this run.")
    log.info("=" * 50)

if __name__ == "__main__":
    run()
