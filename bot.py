import os, json, time, requests, base64
from datetime import datetime, timezone

WALLET  = os.environ.get("WALLETADDRESS","Cc5hAzoAiuxksmU5MEX6dvjtWxVXSZp8WM4rDNyq9PzL")
KEY     = os.environ.get("SOLANKEY","")
TRADE   = float(os.environ.get("TRADEAMOUNT","0.05"))
AUTO    = os.environ.get("AUTOTRADE","false").lower()=="true"
RPC     = "https://mainnet.helius-rpc.com/?api-key=08d214d9-315d-40d5-90e1-54638e2c9508"
SOL     = "So11111111111111111111111111111111111111112"
FILE    = "signals.json"

def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)

def load():
    try:
        return json.load(open(FILE))
    except:
        return {"signals":[],"trades":[],"stats":{"scans":0,"signals":0,"trades":0},"wallet":{"address":WALLET,"balance":0},"last_scan":""}

def save(d):
    json.dump(d, open(FILE,"w"), indent=2)

def get_balance():
    try:
        r = requests.post(RPC, json={"jsonrpc":"2.0","id":1,"method":"getBalance","params":[WALLET]}, timeout=10)
        return round(r.json()["result"]["value"]/1e9, 6)
    except Exception as e:
        log(f"Balance error: {e}")
        return None

def get_pairs():
    pairs = []
    try:
        r = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=15)
        data = r.json()
        if isinstance(data, list):
            for t in data:
                if t.get("chainId") == "solana":
                    pairs.append(t.get("tokenAddress",""))
        log(f"Got {len(pairs)} tokens from DexScreener")
    except Exception as e:
        log(f"DexScreener error: {e}")
    return [p for p in pairs if p]

def get_pair_detail(addr):
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{addr}", timeout=10)
        ps = [p for p in r.json().get("pairs",[]) if p.get("chainId")=="solana"]
        if not ps: return None
        return max(ps, key=lambda x: float(x.get("liquidity",{}).get("usd",0) or 0))
    except:
        return None

def filter_pair(p):
    liq  = float(p.get("liquidity",{}).get("usd",0) or 0)
    vol  = float(p.get("volume",{}).get("h1",0) or 0)
    chg  = float(p.get("priceChange",{}).get("h1",0) or 0)
    buys = int(p.get("txns",{}).get("h1",{}).get("buys",0) or 0)
    sells= int(p.get("txns",{}).get("h1",{}).get("sells",0) or 0)
    sym  = p.get("baseToken",{}).get("symbol","?")
    log(f"  {sym}: liq=${liq:.0f} vol=${vol:.0f} chg={chg:.1f}% buys={buys} sells={sells}")
    if liq < 500:   return False, "liq<500"
    if vol < 50:    return False, "vol<50"
    if chg < 0.5:   return False, "chg<0.5%"
    if buys+sells < 1: return False, "no txns"
    return True, "OK"

def score(p):
    chg  = float(p.get("priceChange",{}).get("h1",0) or 0)
    vol  = float(p.get("volume",{}).get("h1",0) or 0)
    buys = int(p.get("txns",{}).get("h1",{}).get("buys",0) or 0)
    sells= int(p.get("txns",{}).get("h1",{}).get("sells",0) or 0)
    s = 0
    if chg >= 100: s+=40
    elif chg >= 50: s+=30
    elif chg >= 20: s+=20
    elif chg >= 5: s+=10
    else: s+=3
    if vol >= 100000: s+=25
    elif vol >= 10000: s+=15
    elif vol >= 1000: s+=8
    else: s+=2
    if sells==0 and buys>0: s+=20
    elif sells>0 and buys/(sells+1)>=2: s+=15
    elif sells>0 and buys/(sells+1)>=1.5: s+=8
    return min(s,100)

def buy_token(mint, name):
    if not KEY: return None
    try:
        from solders.keypair import Keypair
        from solders.transaction import VersionedTransaction
        from solana.rpc.api import Client
        kp = Keypair.from_base58_string(KEY)
        lam = int(TRADE*1e9)
        q = requests.get("https://quote-api.jup.ag/v6/quote", params={
            "inputMint":SOL,"outputMint":mint,"amount":lam,"slippageBps":500
        }, timeout=10).json()
        if "error" in q: return None
        sw = requests.post("https://quote-api.jup.ag/v6/swap", json={
            "quoteResponse":q,"userPublicKey":str(kp.pubkey()),
            "wrapAndUnwrapSol":True,"prioritizationFeeLamports":5000
        }, timeout=15).json()
        if "swapTransaction" not in sw: return None
        client = Client(RPC)
        tx = VersionedTransaction.from_bytes(base64.b64decode(sw["swapTransaction"]))
        result = client.send_raw_transaction(bytes(tx))
        return str(result.value)
    except Exception as e:
        log(f"Buy error: {e}")
        return None

def main():
    log("="*50)
    log(f"SolSniper | AUTO={AUTO} | TRADE={TRADE} SOL")
    log("="*50)

    d = load()
    d["stats"]["scans"] += 1
    d["last_scan"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    save(d)

    # Balance
    bal = get_balance()
    if bal is not None:
        d["wallet"] = {"address":WALLET, "balance":bal}
        log(f"Balance: {bal} SOL")
    else:
        bal = d.get("wallet",{}).get("balance",0)
        log(f"Balance (cached): {bal} SOL")

    # Scan
    log("Scanning DexScreener...")
    addrs = get_pairs()
    log(f"Checking {len(addrs)} tokens...")

    new_sigs = []
    seen = set()

    for addr in addrs[:20]:
        if addr in seen: continue
        seen.add(addr)
        p = get_pair_detail(addr)
        if not p: continue
        ok, reason = filter_pair(p)
        if not ok:
            continue
        sc = score(p)
        label = "STRONG BUY" if sc>=70 else "BUY" if sc>=50 else "WATCH"
        chg   = float(p.get("priceChange",{}).get("h1",0) or 0)
        liq   = float(p.get("liquidity",{}).get("usd",0) or 0)
        vol   = float(p.get("volume",{}).get("h1",0) or 0)
        mcap  = float(p.get("fdv",0) or 0)
        price = float(p.get("priceUsd",0) or 0)
        name  = p.get("baseToken",{}).get("name","Unknown")
        sym   = p.get("baseToken",{}).get("symbol","???")
        url   = p.get("url","")
        ts    = datetime.now(timezone.utc)

        sig = {"token":name,"symbol":sym,"address":addr,"label":label,"score":sc,
               "price":price,"change_1h":chg,"liquidity":liq,"volume_1h":vol,
               "mcap":mcap,"age_min":0,"dex_url":url,
               "time":ts.strftime("%H:%M:%S"),"date":ts.strftime("%Y-%m-%d"),
               "traded":False,"tx":None}

        log(f"SIGNAL [{label}] {name} ({sym}) +{chg:.0f}% score={sc}")
        new_sigs.append(sig)

        if AUTO and KEY and label in ["STRONG BUY","BUY"] and bal > TRADE+0.01:
            tx = buy_token(addr, name)
            if tx:
                sig["traded"] = True
                sig["tx"] = tx
                d["stats"]["trades"] += 1
                bal -= TRADE
                d["trades"].insert(0,{
                    "token":name,"symbol":sym,"address":addr,"buy_tx":tx,
                    "amount_sol":TRADE,"buy_price":price,
                    "tp1":price*1.5,"tp2":price*3,"sl":price*0.8,
                    "status":"OPEN","time":sig["time"]
                })
        time.sleep(0.2)

    d["signals"] = (new_sigs + d.get("signals",[]))[:50]
    d["stats"]["signals"] += len(new_sigs)
    d["wallet"]["balance"] = round(bal,6)
    save(d)
    log(f"Done. {len(new_sigs)} signals found.")

if __name__ == "__main__":
    main()
