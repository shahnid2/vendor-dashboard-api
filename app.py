# app.py
import os, logging, asyncio
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(), override=False)

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from av_client import get_overview, get_income_statement
from models import VendorComparisonRow

log = logging.getLogger("uvicorn.error")

app = FastAPI(title="Vendor Dashboard (Alpha Vantage)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

VENDOR_SYMBOLS = {
    "TEL": "TE Connectivity",
    "ST":  "Sensata Technologies",
    "DD":  "DuPont de Nemours",
    "CE":  "Celanese",
    "LYB": "LyondellBasell"
}

def _to_int(x) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None

def _latest_annual(inc: Dict[str, Any]):
    reports = (inc or {}).get("annualReports") or []
    return reports[0] if reports else None

def _sum_last4_quarterly_revenue(inc: Dict[str, Any]) -> Optional[int]:
    q = (inc or {}).get("quarterlyReports") or []
    if len(q) < 4:
        return None
    total = 0
    for i in range(4):
        total += _to_int(q[i].get("totalRevenue")) or 0
    return total if total > 0 else None

def _yoy_revenue(inc: Dict[str, Any]) -> Optional[float]:
    a = (inc or {}).get("annualReports") or []
    if len(a) < 2:
        return None
    cur = _to_int(a[0].get("totalRevenue"))
    prev = _to_int(a[1].get("totalRevenue"))
    if cur is None or not prev:
        return None
    return (cur - prev) / prev

def _revenue_flag(revenue: Optional[int], threshold: int = 3_000_000_000):
    if revenue is None:
        return None
    return "LOW" if revenue < threshold else "OK"

@app.get("/api/overview/{symbol}")
async def api_overview(symbol: str):
    try:
        return await get_overview(symbol.upper())
    except Exception as e:
        log.exception("overview failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/api/income/{symbol}")
async def api_income(symbol: str):
    try:
        return await get_income_statement(symbol.upper())
    except Exception as e:
        log.exception("income failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/api/compare", response_model=List[VendorComparisonRow])
async def api_compare(v: List[str] = Query(..., description="Repeat v per vendor, e.g. v=TEL&v=ST")):
    rows: List[VendorComparisonRow] = []
    for symbol in v:
        s = symbol.upper()
        try:
            ov, inc = await get_overview(s), await get_income_statement(s)
            latest = _latest_annual(inc)
            name = (ov.get("Name") if isinstance(ov, dict) else None) or VENDOR_SYMBOLS.get(s, s)
            sector = ov.get("Sector") if isinstance(ov, dict) else None
            industry = ov.get("Industry") if isinstance(ov, dict) else None
            market_cap = _to_int(ov.get("MarketCapitalization")) if isinstance(ov, dict) else None
            ebitda_ttm = _to_int(ov.get("EBITDA")) if isinstance(ov, dict) else None

            fy = latest.get("fiscalDateEnding") if latest else None
            revenue_annual = _to_int(latest.get("totalRevenue")) if latest else None
            net_income = _to_int(latest.get("netIncome")) if latest else None
            revenue_ttm = _sum_last4_quarterly_revenue(inc)
            yoy = _yoy_revenue(inc)

            rows.append(VendorComparisonRow(
                symbol=s, name=name, sector=sector, industry=industry,
                marketCap=market_cap, revenueTTM=revenue_ttm, ebitdaTTM=ebitda_ttm,
                yoyRevenue=yoy, fiscalYear=fy, revenue=revenue_annual,
                netIncome=net_income, revenueFlag=_revenue_flag(revenue_annual)
            ))
        except Exception as e:
            log.warning("compare row degraded for %s: %s", s, e)
            rows.append(VendorComparisonRow(
                symbol=s, name=VENDOR_SYMBOLS.get(s, s)
            ))
    return rows

# ---- PRIME CACHE ENDPOINT ----
@app.post("/api/prime")
async def api_prime():
    vendors = ["TEL", "ST", "DD", "CE", "LYB"]
    primed = []
    for s in vendors:
        try:
            _ = await get_overview(s)
            await asyncio.sleep(1.0)  # gentle spacing
            _ = await get_income_statement(s)
            primed.append({"symbol": s, "ok": True})
        except Exception as e:
            primed.append({"symbol": s, "ok": False, "error": str(e)})
            # if quota/limit, stop early to save calls
            if "quota" in str(e).lower() or "limit" in str(e).lower():
                break
        await asyncio.sleep(1.0)
    return {"primed": primed}

# ---------- STATIC ----------
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return FileResponse("static/index.html")

@app.get("/favicon.ico")
def favicon():
    path = "static/favicon.ico"
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="No favicon")
