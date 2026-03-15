"""
SolSniper - Complete Auto Trading Bot
Scans DexScreener, finds meme coins, auto buys via Jupiter
"""

import os, json, time, logging, requests, base64
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("SolSniper")

# ── Config from GitHub Secrets ──
PRIVATE_KEY      = os.environ.get("SOLANKEY", "")
WALLET_ADDRESS   = os.environ.get("WALLETADDRESS", "Cc5hAzoAiuxksmU5MEX6dvjtWxVXSZp8WM4rDNyq9PzL")
TRADE_SOL        = float(os.environ.get("TRADEAMOUNT", "0.05"))
AUTO_TRADE       = os.environ.get("AUTOTRADE", "false").lower() == "true"
TP1              = 0.50
TP2              = 2.00
SL               = 0.20
SOL_MINT         = "So11111111111111111111111111111111111111112"
SIGNALS_FILE     = "signals.json"

def now_utc():
    return datetime.now(timezone.utc)

# ═══════════════════════════════════════
# SIGNALS FILE
# ═══════════════════════════════════════
def load():
    try:
        if os.path.exists(SIGNALS_FILE):
            return json.load(open(SIGNALS_FILE))
    except: pass
    return {"signals":[],"trades":[],"stats":{"scans":0,"signals":0,"trades":0},
            "wallet":{"address":WALLET_ADDRESS,"balance":0},"last_scan":""}

def save(data):
    json.dump(data, open(SIGNALS_FILE,"w"), indent=2)
    log.info(f"Saved: {len(data['signals'])} signals, wallet={data['wallet']['balance']} SOL")

# ═══════════════════════════════════════
# WALLET BALANCE - using Helius free RPC
# ═══════════════════════════════════════
def get_balance(address):
    endpoints = [
        "https://api.mainnet-beta.solana.com",
        "https://rpc.ankr.com/solana",
        "https://solana-mainnet.g.alchemy.com/v2/demo",
    ]
    for ep in endpoints:
        try:
            r = requests.post(ep,
                json={"jsonrpc":"2.0","id":1,"method":"getBalance","params":[address]},
                timeout=10, headers={"Content-Type":"application/json"})
            val = r.json()["result"]["value"]
            sol = round(val / 1e9, 6)
            log.info(f"Balance: {sol} SOL from {ep}")
            return sol
        except Exception as e:
            log.warning(f"RPC failed {ep}: {e}")
    return 0.0

# ═══════════════════════════════════════
# DEXSCREENER SCANNER
# ═══════════════════════════════════════
def get_tokens():
    tokens = []
    seen = set()

    urls = [
        "https://api.dexscreener.com/token-profiles/latest/v1",
        "https://api.dexscreener.com/token-boosts/latest/v1",
        "https://api.dexscreener.com/token-boosts/top/v1",
    ]

    for url in urls:
        try:
            r = requests.get(url, timeout=15)
            data = r.json()
            if isinstance(data, list):
                for t in data:
                    if t.get("chainId") == "solana":
                        addr = t.get("tokenAddress","")
                        if addr and addr not in seen:
                            seen.add(addr)
                            tokens.append(addr)
        except Exception as e:
            log.warning(f"Fetch failed {url}: {e}")

    # Also search trending
    try:
        r = requests.get("https://api.dexscreener.com/latest/dex/search?q=solana", timeout=15)
        for p in r.json().get("pairs", []):
            if p.get("chainId") == "solana":
                addr = p.get("baseToken",{}).get("address","")
                if addr and addr not in seen:
                    seen.add(addr)
                    tokens.append(addr)
    except: pass

    log.info(f"Found {len(tokens)} unique Solana tokens to check")
    return tokens

def get_pair(token):
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token}", timeout=10)
        pairs = [p for p in r.json().get("pairs",[]) if p.get("chainId")=="solana"]
        if not pairs: return None
        return max(pairs, key=lambda x: float(x.get("liquidity",{}).get("usd",0) or 0))
    except: return None

def age_min(pair):
    try:
        ts = pair.get("pairCreatedAt", 0)
        if not ts: return 9999
        return (int(now_utc().timestamp()*1000) - ts) / 60000
    except: return 9999

def check(pair):
    try:
        liq  = float(pair.get("liquidity",{}).get("usd",0) or 0)
        vol  = float(pair.get("volume",{}).get("h1",0) or 0)
        chg  = float(pair.get("priceChange",{}).get("h1",0) or 0)
        mcap = float(pair.get("fdv",0) or 0)
        buys = int(pair.get("txns",{}).get("h1",{}).get("buys",0) or 0)
        sells= int(pair.get("txns",{}).get("h1",{}).get("sells",0) or 0)
        txns = buys + sells
        age  = age_min(pair)

        if liq < 3000: return False         # min $3K liquidity
        if vol < 1000: return False         # min $1K volume
        if chg < 3.0: return False          # min 3% change
        if age < 1 or age > 720: return False
        if txns < 3: return False
        if sells > 0 and buys/(sells+1) < 0.8: return False
        return True
    except: return False

def score(pair):
    s = 0
    chg  = float(pair.get("priceChange",{}).get("h1",0) or 0)
    vol  = float(pair.get("volume",{}).get("h1",0) or 0)
    buys = int(pair.get("txns",{}).get("h1",{}).get("buys",0) or 0)
    sells= int(pair.get("txns",{}).get("h1",{}).get("sells",0) or 0)

    if chg >= 200: s += 40
    elif chg >= 100: s += 30
    elif chg >= 50: s += 20
    elif chg >= 20: s += 10
    else: s += 3

    if vol >= 500_000: s += 25
    elif vol >= 100_000: s += 15
    elif vol >= 10_000: s += 8
    else: s += 2

    if sells > 0:
        r = buys/(sells+1)
        if r >= 3: s += 20
        elif r >= 2: s += 12
        elif r >= 1.5: s += 6
    else:
        s += 15  # all buys no sells is bullish

    return min(s, 100)

# ═══════════════════════════════════════
# TRADING via Jupiter
# ═══════════════════════════════════════
def get_keypair():
    if not PRIVATE_KEY: return None
    try:
        from solders.keypair import Keypair
        return Keypair.from_base58_string(PRIVATE_KEY)
    except Exception as e:
        log.error(f"Keypair error: {e}")
        return None

def buy(keypair, mint, name):
    try:
        from solders.transaction import VersionedTransaction
        from solana.rpc.api import Client

        lamports = int(TRADE_SOL * 1e9)
        q = requests.get("https://quote-api.jup.ag/v6/quote", params={
            "inputMint": SOL_MINT, "outputMint": mint,
            "amount": lamports, "slippageBps": 500
        }, timeout=10).json()

        if "error" in q:
            log.error(f"Quote error: {q}")
            return None

        sw = requests.post("https://quote-api.jup.ag/v6/swap", json={
            "quoteResponse": q,
            "userPublicKey": str(keypair.pubkey()),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": 5000,
        }, timeout=15).json()

        if "swapTransaction" not in sw:
            log.error(f"Swap error: {sw}")
            return None

        client = Client("https://api.mainnet-beta.solana.com")
        tx = VersionedTransaction.from_bytes(base64.b64decode(sw["swapTransaction"]))
        result = client.send_raw_transaction(bytes(tx))
        tx_hash = str(result.value)
        log.info(f"BUY {name}: {tx_hash}")
        return tx_hash
    except Exception as e:
        log.error(f"Buy error: {e}")
        return None

# ═══════════════════════════════════════
# MAIN
# ═══════════════════════════════════════
def run():
    log.info("=" * 55)
    log.info(f"SolSniper Bot | Mode: {'AUTO TRADE' if AUTO_TRADE else 'SIGNAL ONLY'}")
    log.info("=" * 55)

    data = load()
    data["stats"]["scans"] += 1
    data["last_scan"] = now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")
    save(data)  # Save immediately so file always exists

    # Get balance - keep old balance if fetch fails
    # Get balance - always keep old if RPC returns 0
    old_bal = data.get("wallet", {}).get("balance", 0.0)
    fetched = get_balance(WALLET_ADDRESS)
    bal = fetched if fetched > 0 else old_bal
    log.info(f"Balance: fetched={fetched}, using={bal} SOL")
    data["wallet"] = {"address": WALLET_ADDRESS, "balance": round(bal,6)}

    # Get keypair for trading
    kp = get_keypair() if AUTO_TRADE else None

    # Scan
    tokens = get_tokens()
    new_sigs = []
    seen = set()

    for addr in tokens:
        if addr in seen: continue
        seen.add(addr)

        pair = get_pair(addr)
        if not pair: continue
        if not check(pair): continue

        sc    = score(pair)
        label = "STRONG BUY" if sc >= 70 else "BUY" if sc >= 50 else "WATCH"
        chg   = float(pair.get("priceChange",{}).get("h1",0) or 0)
        liq   = float(pair.get("liquidity",{}).get("usd",0) or 0)
        vol   = float(pair.get("volume",{}).get("h1",0) or 0)
        mcap  = float(pair.get("fdv",0) or 0)
        price = float(pair.get("priceUsd",0) or 0)
        name  = pair.get("baseToken",{}).get("name","Unknown")
        sym   = pair.get("baseToken",{}).get("symbol","???")
        url   = pair.get("url","")

        sig = {
            "token":name, "symbol":sym, "address":addr,
            "label":label, "score":sc, "price":price,
            "change_1h":chg, "liquidity":liq, "volume_1h":vol,
            "mcap":mcap, "age_min":round(age_min(pair),1),
            "dex_url":url,
            "time":now_utc().strftime("%H:%M:%S"),
            "date":now_utc().strftime("%Y-%m-%d"),
            "traded":False, "tx":None,
        }

        log.info(f"SIGNAL [{label}] {name} ({sym}) +{chg:.0f}% score={sc}")
        new_sigs.append(sig)

        # Auto trade
        if AUTO_TRADE and kp and label in ["STRONG BUY","BUY"] and bal > TRADE_SOL + 0.01:
            tx = buy(kp, addr, name)
            if tx:
                sig["traded"] = True
                sig["tx"] = tx
                data["stats"]["trades"] += 1
                bal -= TRADE_SOL
                data["trades"].insert(0, {
                    "token":name, "symbol":sym, "address":addr,
                    "buy_tx":tx, "amount_sol":TRADE_SOL, "buy_price":price,
                    "tp1":price*(1+TP1), "tp2":price*(1+TP2),
                    "sl":price*(1-SL), "status":"OPEN",
                    "time":sig["time"],
                })

        time.sleep(0.2)

    data["signals"] = (new_sigs + data.get("signals",[]))[:50]
    data["stats"]["signals"] += len(new_sigs)
    data["wallet"]["balance"] = round(bal,6)
    save(data)
    log.info(f"Complete. {len(new_sigs)} signals found this run.")

if __name__ == "__main__":
    run()
