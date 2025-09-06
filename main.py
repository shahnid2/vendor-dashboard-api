"""
FastAPI backend for Vendor Dashboard
- Hides Alpha Vantage API key (server-side)
- Uses SQLite cache with TTL (persistent via DB_PATH env)
- Exposes /api/vendors and /api/health
- CORS is configurable via ALLOWED_ORIGINS env
"""

import os
import time
import json
import sqlite3
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# -----------------------
# Environment & constants
# -----------------------
load_dotenv()

API_KEY = os.getenv("ALPHA_VANTAGE_KEY")  # REQUIRED in production
TTL = int(os.getenv("CACHE_TTL_SECONDS", "86400"))  # default 24h cache

# Step A.3 — Pin SQLite path for production (persistent disk mount in hosting)
DB_PATH = os.getenv("DB_PATH", "cache.db")

# Alpha Vantage base
AV_BASE = "https://www.alphavantage.co/query"

# -----------------------
# SQLite cache bootstrap
# -----------------------
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS cache(
      cache_key TEXT PRIMARY KEY,
      payload   TEXT NOT NULL,
      ts        INTEGER NOT NULL
    )
    """
)
conn.commit()

def cache_get(cache_key: str) -> Optional[Dict[str, Any]]:
    cur = conn.execute("SELECT payload, ts FROM cache WHERE cache_key=?", (cache_key,))
    row = cur.fetchone()
    if not row:
        return None
    payload, ts = row
    if time.time() - ts > TTL:
        return None
    try:
        return json.loads(payload)
    except Exception:
        return None

def cache_set(cache_key: str, data: Dict[str, Any]) -> None:
    conn.execute(
        "REPLACE INTO cache(cache_key,payload,ts) VALUES (?,?,?)",
        (cache_key, json.dumps(data), int(time.time())),
    )
    conn.commit()

# -----------------------
# FastAPI app & CORS
# -----------------------
app = FastAPI(title="Vendor Dashboard API", version="1.0.0")

# Step A.5 — CORS config (restrict in production)
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Step A.4 — Health endpoint
@app.get("/api/health")
def health() -> Dict[str, bool]:
    return {"ok": True}

# -----------------------
# Alpha Vantage helpers
# -----------------------
def ensure_api_key() -> str:
    if not API_KEY:
        # Fail fast with 500 if API key is missing in the environment
        raise HTTPException(
            status_code=500,
            detail="Server misconfiguration: ALPHA_VANTAGE_KEY is not set.",
        )
    return API_KEY

def av_get(function: str, symbol: str) -> Dict[str, Any]:
    """
    Wrapper around Alpha Vantage that:
      - checks cache
      - calls API if needed
      - caches successful responses
      - surfaces rate-limit and error messages
    """
    cache_key = f"{function}:{symbol.upper()}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    key = ensure_api_key()
    params = {"function": function, "symbol": symbol.upper(), "apikey": key}
    try:
        r = requests.get(AV_BASE, params=params, timeout=20)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {e}")

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Alpha Vantage HTTP {r.status_code}")

    data = r.json()

    # Alpha Vantage returns "Note"/"Information" when rate-limited or mis-queried
    if isinstance(data, dict) and ("Note" in data or "Information" in data or "Error Message" in data):
        msg = data.get("Note") or data.get("Information") or data.get("Error Message") or "Alpha Vantage error"
        # Do not cache error payloads
        raise HTTPException(status_code=429 if "frequency" in msg.lower() or "limit" in msg.lower() else 502, detail=msg)

    # Cache only valid responses
    cache_set(cache_key, data)
    return data

def safe_float(x: Any) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

def derive_metrics(symbol: str) -> Dict[str, Any]:
    """
    Uses at least TWO fundamental endpoints:
      - OVERVIEW
      - INCOME_STATEMENT
    Computes basic vendor metrics + simple flags.
    """
    ov = av_get("OVERVIEW", symbol)
    inc = av_get("INCOME_STATEMENT", symbol)

    revenue_ttm = safe_float(ov.get("RevenueTTM"))
    gross_profit_ttm = safe_float(ov.get("GrossProfitTTM"))
    ebitda_ttm = safe_float(ov.get("EBITDA"))
    market_cap = safe_float(ov.get("MarketCapitalization"))

    # YoY revenue from last two annual reports (fallback logic if not enough data)
    yoy = None
    annual_reports: List[Dict[str, Any]] = (inc.get("annualReports") or [])[:2]
    if len(annual_reports) >= 2:
        r0 = safe_float(annual_reports[0].get("totalRevenue"))
        r1 = safe_float(annual_reports[1].get("totalRevenue"))
        if r0 is not None and r1 not in (None, 0):
            yoy = (r0 - r1) / r1

    # Simple flags (customize thresholds to taste)
    flags: List[str] = []
    if revenue_ttm is not None and revenue_ttm < 5_000_000_000:
        flags.append("LOW_REVENUE")
    if yoy is not None and yoy < 0:
        flags.append("NEG_YOY_REVENUE")

    return {
        "symbol": symbol.upper(),
        "name": ov.get("Name"),
        "sector": ov.get("Sector"),
        "marketCap": market_cap,
        "revenueTTM": revenue_ttm,
        "grossProfitTTM": gross_profit_ttm,
        "ebitdaTTM": ebitda_ttm,
        "yoyRevenue": yoy,  # decimal (e.g., 0.12 = 12%)
        "flags": flags,
    }

# -----------------------
# API: /api/vendors
# -----------------------
@app.get("/api/vendors")
def vendors(symbols: str = Query(..., description="Comma-separated tickers, e.g. TEL,ST,DD,CE,LYB")) -> Dict[str, Any]:
    """
    Example:
      GET /api/vendors?symbols=TEL,ST,DD,CE,LYB
    """
    # Validate
    tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="Provide at least one symbol (e.g., symbols=TEL,ST)")

    out: List[Dict[str, Any]] = []
    for sym in tickers:
        try:
            out.append(derive_metrics(sym))
        except HTTPException as e:
            # Surface per-symbol failure but keep others
            out.append({"symbol": sym, "error": e.detail})
        except Exception as e:
            out.append({"symbol": sym, "error": f"Unhandled error: {e}"})

    return {"vendors": out}

# -----------------------
# Local dev entry point
# -----------------------
if __name__ == "__main__":
    # Local testing: `python main.py`
    # For production on Render/other hosts, use:
    #   uvicorn main:app --host 0.0.0.0 --port $PORT
    try:
        import uvicorn  # type: ignore
        port = int(os.getenv("PORT", "8000"))
        uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
    except Exception as e:
        print("Uvicorn not installed or failed to start:", e)
        print("Install with: python3 -m pip install uvicorn[standard]")
