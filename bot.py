"""
SolSniper Pro - Narrative + Technical Signal Bot
Combines price action with narrative scoring for higher quality signals
"""

import os, json, time, requests, base64
from datetime import datetime, timezone

# ── Config ──
WALLET   = os.environ.get("WALLETADDRESS","Cc5hAzoAiuxksmU5MEX6dvjtWxVXSZp8WM4rDNyq9PzL")
KEY      = os.environ.get("SOLANKEY","") or os.environ.get("PRIVKEY","")
TRADE    = float(os.environ.get("TRADEAMOUNT","0.05"))
AUTO     = os.environ.get("AUTOTRADE","false").lower()=="true"
RPC      = "https://mainnet.helius-rpc.com/?api-key=08d214d9-315d-40d5-90e1-54638e2c9508"
SOL      = "So11111111111111111111111111111111111111112"
FILE     = "signals.json"
TGTOKEN  = os.environ.get("TELEGRAMTOKEN","")
TGCHAT   = os.environ.get("TELEGRAMCHAT","")

def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)

if KEY:
    log(f"Key: loaded ({len(KEY)} chars)")
else:
    log("WARNING: No private key!")

# ═══════════════════════════════════════
# NARRATIVE ENGINE
# Top narratives in Solana 2026
# ═══════════════════════════════════════

NARRATIVES = {
    "AI": {
        "keywords": ["ai","agent","gpt","neural","bot","model","llm","agi","deep","learn","robo","auto","smart","brain"],
        "score_bonus": 25,
        "label": "🤖 AI Agent"
    },
    "POLITIFI": {
        "keywords": ["trump","maga","based","america","usa","election","vote","white","house","senate","president","policy","freedom","liberty","eagle","patriot","republican","democrat","political","biden","kamala","elon","doge"],
        "score_bonus": 22,
        "label": "🏛️ PolitiFi"
    },
    "ANIMAL": {
        "keywords": ["dog","cat","wolf","bear","bull","ape","frog","pepe","doge","shib","bonk","wif","bird","duck","fish","whale","shark","lion","tiger","monkey","penguin","panda","fox","rabbit","hamster","horse","cow","pig"],
        "score_bonus": 20,
        "label": "🐾 Animal"
    },
    "PUMP_GRAD": {
        "keywords": ["pump","moon","rocket","launch","gem","100x","1000x","fair","lfg","gm","gn","based","wen","soon","alpha","degen","ape","chad","sigma","king","god","lord"],
        "score_bonus": 18,
        "label": "🚀 Pump.fun"
    },
    "COMMUNITY": {
        "keywords": ["community","people","dao","vote","holder","army","gang","club","society","union","network","family","friends","crew","squad"],
        "score_bonus": 15,
        "label": "👥 Community"
    },
    "CULTURE": {
        "keywords": ["meme","fun","lol","wtf","giga","chad","based","rare","nft","art","culture","vibe","vibes","aesthetic","drip","swag"],
        "score_bonus": 12,
        "label": "😂 Meme Culture"
    },
    "DEFI": {
        "keywords": ["defi","swap","yield","farm","stake","liquidity","pool","protocol","finance","bank","money","cash","coin","token","usd","btc","eth","sol"],
        "score_bonus": 10,
        "label": "💰 DeFi"
    },
}

def get_narrative(name, symbol):
    """Identify which narrative a token belongs to"""
    text = (name + " " + symbol).lower()
    best_narrative = None
    best_bonus = 0
    matched_labels = []

    for narr_name, narr_data in NARRATIVES.items():
        for kw in narr_data["keywords"]:
            if kw in text:
                if narr_data["score_bonus"] > best_bonus:
                    best_bonus = narr_data["score_bonus"]
                    best_narrative = narr_data["label"]
                if narr_data["label"] not in matched_labels:
                    matched_labels.append(narr_data["label"])
                break

    return best_narrative, best_bonus, matched_labels

# ═══════════════════════════════════════
# SIGNALS FILE
# ═══════════════════════════════════════
def load():
    try:
        return json.load(open(FILE))
    except:
        return {"signals":[],"trades":[],"stats":{"scans":0,"signals":0,"trades":0},
                "wallet":{"address":WALLET,"balance":0},"last_scan":""}

def save(d):
    json.dump(d, open(FILE,"w"), indent=2)

# ═══════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════
def telegram(msg):
    if not TGTOKEN or not TGCHAT: return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TGTOKEN}/sendMessage",
            json={"chat_id":TGCHAT,"text":msg,"parse_mode":"HTML"},
            timeout=10
        )
        if r.status_code == 200:
            log("Telegram sent!")
        else:
            log(f"Telegram error: {r.text}")
    except Exception as e:
        log(f"Telegram error: {e}")

# ═══════════════════════════════════════
# BALANCE
# ═══════════════════════════════════════
def get_balance():
    rpcs = [RPC, "https://api.mainnet-beta.solana.com", "https://rpc.ankr.com/solana"]
    for rpc in rpcs:
        try:
            r = requests.post(rpc,
                json={"jsonrpc":"2.0","id":1,"method":"getBalance","params":[WALLET]},
                headers={"Content-Type":"application/json"}, timeout=10)
            resp = r.json()
            if "result" in resp and "value" in resp["result"]:
                return round(resp["result"]["value"]/1e9, 6)
        except: continue
    return None

# ═══════════════════════════════════════
# DEXSCREENER
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
            log(f"DexScreener error: {e}")
    log(f"Got {len(tokens)} tokens from DexScreener")
    return tokens[:30]  # Check top 30

def get_pair(addr):
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{addr}", timeout=10)
        pairs = [p for p in r.json().get("pairs",[]) if p.get("chainId")=="solana"]
        if not pairs: return None
        return max(pairs, key=lambda x: float(x.get("liquidity",{}).get("usd",0) or 0))
    except: return None

def filter_pair(p):
    liq  = float(p.get("liquidity",{}).get("usd",0) or 0)
    vol  = float(p.get("volume",{}).get("h1",0) or 0)
    chg  = float(p.get("priceChange",{}).get("h1",0) or 0)
    buys = int(p.get("txns",{}).get("h1",{}).get("buys",0) or 0)
    sells= int(p.get("txns",{}).get("h1",{}).get("sells",0) or 0)
    sym  = p.get("baseToken",{}).get("symbol","?")

    log(f"  {sym}: liq=${liq:.0f} vol=${vol:.0f} chg={chg:.1f}% buys={buys} sells={sells}")

    if liq < 500: return False
    if vol < 50: return False
    if chg < 0.5: return False
    if buys+sells < 1: return False
    return True

def technical_score(p):
    """Score based on price action"""
    s = 0
    chg  = float(p.get("priceChange",{}).get("h1",0) or 0)
    chg6 = float(p.get("priceChange",{}).get("h6",0) or 0)
    vol  = float(p.get("volume",{}).get("h1",0) or 0)
    liq  = float(p.get("liquidity",{}).get("usd",0) or 0)
    buys = int(p.get("txns",{}).get("h1",{}).get("buys",0) or 0)
    sells= int(p.get("txns",{}).get("h1",{}).get("sells",0) or 0)

    # Price momentum
    if chg >= 500: s += 40
    elif chg >= 200: s += 35
    elif chg >= 100: s += 28
    elif chg >= 50: s += 20
    elif chg >= 20: s += 12
    elif chg >= 5: s += 6
    else: s += 2

    # 6h trend (sustained momentum)
    if chg6 > 0 and chg > 0: s += 8  # both 1h and 6h green
    elif chg6 > 50: s += 5

    # Volume
    if vol >= 1_000_000: s += 20
    elif vol >= 500_000: s += 15
    elif vol >= 100_000: s += 10
    elif vol >= 10_000: s += 5
    else: s += 2

    # Buy pressure
    if sells == 0 and buys > 0: s += 15  # all buys!
    elif sells > 0:
        ratio = buys/(sells+1)
        if ratio >= 4: s += 15
        elif ratio >= 3: s += 12
        elif ratio >= 2: s += 8
        elif ratio >= 1.5: s += 5

    # Liquidity sweet spot
    if 5_000 <= liq <= 100_000: s += 10  # small cap = more upside
    elif 100_000 <= liq <= 500_000: s += 6

    return s

# ═══════════════════════════════════════
# TRADING
# ═══════════════════════════════════════
def get_keypair():
    if not KEY: return None
    try:
        from solders.keypair import Keypair
        return Keypair.from_base58_string(KEY)
    except Exception as e:
        log(f"Keypair error: {e}")
        return None

def buy_token(mint, name):
    kp = get_keypair()
    if not kp: return None
    try:
        from solders.transaction import VersionedTransaction
        from solana.rpc.api import Client

        lam = int(TRADE*1e9)
        jup_urls = ["https://quote-api.jup.ag/v6", "https://jup.ag/api/v6"]

        for jup in jup_urls:
            try:
                q = requests.get(f"{jup}/quote", params={
                    "inputMint":SOL,"outputMint":mint,"amount":lam,"slippageBps":500
                }, timeout=15).json()
                if "error" in q: continue

                sw = requests.post(f"{jup}/swap", json={
                    "quoteResponse":q,"userPublicKey":str(kp.pubkey()),
                    "wrapAndUnwrapSol":True,"prioritizationFeeLamports":5000
                }, timeout=15).json()
                if "swapTransaction" not in sw: continue

                client = Client(RPC)
                tx = VersionedTransaction.from_bytes(base64.b64decode(sw["swapTransaction"]))
                result = client.send_raw_transaction(bytes(tx))
                tx_hash = str(result.value)
                log(f"BUY SUCCESS: {tx_hash}")
                return tx_hash
            except Exception as e:
                log(f"Jupiter error ({jup}): {e}")
                continue
    except Exception as e:
        log(f"Buy error: {e}")
    return None

# ═══════════════════════════════════════
# MAIN
# ═══════════════════════════════════════
def main():
    log("="*55)
    log(f"SolSniper Pro | AUTO={AUTO} | TRADE={TRADE} SOL")
    log("="*55)

    d = load()
    d["stats"]["scans"] += 1
    d["last_scan"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    save(d)

    # Balance
    fetched = get_balance()
    cached = float(d.get("wallet",{}).get("balance",0) or 0)
    bal = fetched if (fetched is not None and fetched >= 0) else cached
    d["wallet"] = {"address":WALLET, "balance":round(bal,6)}
    log(f"Balance: {bal} SOL")

    # Scan
    log("Scanning DexScreener...")
    addrs = get_tokens()
    log(f"Checking {len(addrs)} tokens...")

    new_sigs = []
    seen = set()

    for addr in addrs:
        if addr in seen: continue
        seen.add(addr)

        p = get_pair(addr)
        if not p: continue
        if not filter_pair(p): continue

        # Get token info
        name  = p.get("baseToken",{}).get("name","Unknown")
        sym   = p.get("baseToken",{}).get("symbol","???")
        chg   = float(p.get("priceChange",{}).get("h1",0) or 0)
        liq   = float(p.get("liquidity",{}).get("usd",0) or 0)
        vol   = float(p.get("volume",{}).get("h1",0) or 0)
        mcap  = float(p.get("fdv",0) or 0)
        price = float(p.get("priceUsd",0) or 0)
        url   = p.get("url","")

        # Score = Technical + Narrative
        tech_sc = technical_score(p)
        narrative, narr_bonus, all_narratives = get_narrative(name, sym)
        total_score = min(tech_sc + narr_bonus, 100)

        # Label
        if total_score >= 70:
            label = "STRONG BUY"
        elif total_score >= 50:
            label = "BUY"
        else:
            label = "WATCH"

        ts = datetime.now(timezone.utc)
        sig = {
            "token":name, "symbol":sym, "address":addr,
            "label":label, "score":total_score,
            "tech_score":tech_sc, "narr_bonus":narr_bonus,
            "narrative":narrative, "all_narratives":all_narratives,
            "price":price, "change_1h":chg,
            "liquidity":liq, "volume_1h":vol, "mcap":mcap,
            "dex_url":url,
            "time":ts.strftime("%H:%M:%S"),
            "date":ts.strftime("%Y-%m-%d"),
            "traded":False, "tx":None,
        }

        log(f"SIGNAL [{label}] {name} ({sym}) | Tech:{tech_sc} + Narr:{narr_bonus} = {total_score} | {narrative or 'No narrative'}")
        new_sigs.append(sig)

        # Telegram alert for STRONG BUY
        if label == "STRONG BUY":
            narr_str = " | ".join(all_narratives) if all_narratives else "📊 Technical"
            telegram(f"""⚡ <b>STRONG BUY SIGNAL!</b>

🪙 <b>{name} ({sym})</b>
📈 1h Change: <b>+{chg:.0f}%</b>
💧 Liquidity: ${liq:,.0f}
📊 Volume 1h: ${vol:,.0f}
🎯 Score: {total_score}/100 (Tech:{tech_sc} + Narr:{narr_bonus})
🏷️ Narrative: {narr_str}
🔗 <a href="{url}">View on DEX →</a>""")

        # Auto trade STRONG BUY only
        if AUTO and KEY and label == "STRONG BUY" and bal > TRADE + 0.01:
            tx = buy_token(addr, name)
            if tx:
                sig["traded"] = True
                sig["tx"] = tx
                d["stats"]["trades"] += 1
                bal -= TRADE
                d["trades"].insert(0, {
                    "token":name, "symbol":sym, "address":addr,
                    "buy_tx":tx, "amount_sol":TRADE, "buy_price":price,
                    "tp":price*100, "sl":price*0.8,
                    "status":"OPEN", "time":sig["time"],
                    "narrative": narrative or "Technical"
                })
                telegram(f"""✅ <b>TRADE EXECUTED!</b>
🪙 {name} ({sym})
💰 Spent: {TRADE} SOL
🎯 Target: 100x
🏷️ {narrative or 'Technical Signal'}
🔗 https://solscan.io/tx/{tx}""")

        time.sleep(0.2)

    d["signals"] = (new_sigs + d.get("signals",[]))[:50]
    d["stats"]["signals"] += len(new_sigs)
    d["wallet"]["balance"] = round(bal,6)
    save(d)

    strong = [s for s in new_sigs if s["label"]=="STRONG BUY"]
    log(f"Done. {len(new_sigs)} signals ({len(strong)} STRONG BUY)")

if __name__ == "__main__":
    main()
