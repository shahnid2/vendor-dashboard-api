import os, time, json, sqlite3, requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from dotenv import load_dotenv

# Load .env for local dev
load_dotenv()

API_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")
TTL = int(os.environ.get("CACHE_TTL_SECONDS", "86400"))
DB_PATH = os.environ.get("DB_PATH", "cache.db")

# --- SQLite cache setup ---
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute("""CREATE TABLE IF NOT EXISTS cache(
  cache_key TEXT PRIMARY KEY,
  payload   TEXT NOT NULL,
  ts        INTEGER NOT NULL
)""")
conn.commit()

# --- FastAPI app setup ---
app = FastAPI()

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE = "https://www.alphavantage.co/query"


# --- Helpers ---
def cache_get(cache_key):
    cur = conn.execute("SELECT payload, ts FROM cache WHERE cache_key=?", (cache_key,))
    row = cur.fetchone()
    if not row:
        return None
    payload, ts = row
    if time.time() - ts > TTL:
        return None
    return json.loads(payload)


def cache_set(cache_key, data):
    conn.execute(
        "REPLACE INTO cache(cache_key,payload,ts) VALUES (?,?,?)",
        (cache_key, json.dumps(data), int(time.time())),
    )
    conn.commit()


def av_get(function, symbol):
    cache_key = f"{function}:{symbol}"
    cached = cache_get(cache_key)
    if cached:
        return cached
    params = {"function": function, "symbol": symbol, "apikey": API_KEY}
    r = requests.get(BASE, params=params, timeout=20)
    if r.status_code != 200:
        raise HTTPException(502, "Alpha Vantage error")
    data = r.json()
    if "Note" in data or "Information" in data:
        raise HTTPException(429, data.get("Note") or data.get("Information"))
    cache_set(cache_key, data)
    return data


def safe_float(x):
    try:
        return float(x)
    except Exception:
        return None


def derive_metrics(symbol):
    ov = av_get("OVERVIEW", symbol)
    inc = av_get("INCOME_STATEMENT", symbol)

    revenue_ttm = safe_float(ov.get("RevenueTTM"))
    gross_profit_ttm = safe_float(ov.get("GrossProfitTTM"))
    ebitda_ttm = safe_float(ov.get("EBITDA"))

    # YoY revenue change from last two annual reports
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
        "flags": flags,
    }


# --- Routes ---
@app.get("/", include_in_schema=False)
def home():
    """Friendly landing page instead of 404"""
    return JSONResponse(
        {
            "ok": True,
            "service": "vendor-dashboard-api",
            "health": "/api/health",
            "docs": "/docs",
        }
    )


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/vendors")
def vendors(symbols: str):
    # Example: /api/vendors?symbols=TEL,ST,DD,CE,LYB
    out = []
    for s in [x.strip().upper() for x in symbols.split(",") if x.strip()]:
        out.append(derive_metrics(s))
    return {"vendors": out}


@app.get("/docs/", include_in_schema=False)
def docs_redirect():
    return RedirectResponse(url="/docs")


# Optional: make base URL show a simple HTML (instead of JSON)
@app.get("/welcome", include_in_schema=False)
def welcome_html():
    return HTMLResponse(
        """
        <!doctype html><meta charset="utf-8">
        <title>Vendor Dashboard API</title>
        <h1>Vendor Dashboard API</h1>
        <ul>
          <li><a href="/api/health">/api/health</a></li>
          <li><a href="/docs">/docs</a></li>
        </ul>
        """
    )
