import os, time, json, sqlite3, requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.environ["ALPHA_VANTAGE_KEY"]
TTL = int(os.environ.get("CACHE_TTL_SECONDS", "86400"))

DB = "cache.db"
conn = sqlite3.connect(DB, check_same_thread=False)
conn.execute("""CREATE TABLE IF NOT EXISTS cache(
  cache_key TEXT PRIMARY KEY,
  payload   TEXT NOT NULL,
  ts        INTEGER NOT NULL
)""")
conn.commit()

app = FastAPI()
app.add_middleware(
  CORSMiddleware,
  allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

BASE = "https://www.alphavantage.co/query"

def cache_get(cache_key):
    cur = conn.execute("SELECT payload, ts FROM cache WHERE cache_key=?", (cache_key,))
    row = cur.fetchone()
    if not row: return None
    payload, ts = row
    if time.time() - ts > TTL:
        return None
    return json.loads(payload)

def cache_set(cache_key, data):
    conn.execute("REPLACE INTO cache(cache_key,payload,ts) VALUES (?,?,?)",
                 (cache_key, json.dumps(data), int(time.time())))
    conn.commit()

def av_get(function, symbol):
    cache_key = f"{function}:{symbol}"
    cached = cache_get(cache_key)
    if cached: return cached
    params = {"function": function, "symbol": symbol, "apikey": API_KEY}
    r = requests.get(BASE, params=params, timeout=20)
    if r.status_code != 200:
        raise HTTPException(502, "Alpha Vantage error")
    data = r.json()
    # Basic error/rate limit check
    if "Note" in data or "Information" in data:
        raise HTTPException(429, data.get("Note") or data.get("Information"))
    cache_set(cache_key, data)
    return data

def safe_float(x):
    try: return float(x)
    except: return None

def derive_metrics(symbol):
    # Use at least two fundamental endpoints
    ov = av_get("OVERVIEW", symbol)          # company overview (RevenueTTM, EBITDA, margins, etc.)
    inc = av_get("INCOME_STATEMENT", symbol) # annualReports / quarterlyReports with totalRevenue, grossProfit, etc.

    revenue_ttm = safe_float(ov.get("RevenueTTM"))
    gross_profit_ttm = safe_float(ov.get("GrossProfitTTM"))
    ebitda_ttm = safe_float(ov.get("EBITDA"))

    # YoY revenue from last two annual reports (fallback to quarterly if needed)
    yoy = None
    ar = (inc.get("annualReports") or [])[:2]
    if len(ar) >= 2:
        r0 = safe_float(ar[0].get("totalRevenue"))
        r1 = safe_float(ar[1].get("totalRevenue"))
        if r0 and r1 and r1 != 0:
            yoy = (r0 - r1) / r1

    # Simple flags
    flags = []
    if revenue_ttm is not None and revenue_ttm < 5_000_000_000:
        flags.append("LOW_REVENUE")
    if yoy is not None and yoy < 0:
        flags.append("NEG_YOY_REVENUE")

    return {
        "symbol": symbol,
        "name": ov.get("Name"),
        "sector": ov.get("Sector"),
        "marketCap": safe_float(ov.get("MarketCapitalization")),
        "revenueTTM": revenue_ttm,
        "grossProfitTTM": gross_profit_ttm,
        "ebitdaTTM": ebitda_ttm,
        "yoyRevenue": yoy,
        "flags": flags
    }

@app.get("/api/vendors")
def vendors(symbols: str):
    # e.g. /api/vendors?symbols=TEL,ST,DD,CE,LYB
    out = []
    for s in [x.strip().upper() for x in symbols.split(",") if x.strip()]:
        out.append(derive_metrics(s))
    return {"vendors": out}
