# app.py
import os, logging
from typing import List, Dict, Any
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

# ---------- API ----------
VENDOR_SYMBOLS = {
    "TEL": "TE Connectivity",
    "ST":  "Sensata Technologies",
    "DD":  "DuPont de Nemours",
    "CE":  "Celanese",
    "LYB": "LyondellBasell"
}

def _parse_latest_income(income_json: Dict[str, Any]):
    reports = (income_json or {}).get("annualReports") or []
    if not reports:
        return None
    latest = reports[0]
    def to_int(x):
        try: return int(x)
        except: return None
    return {
        "fiscalYear": latest.get("fiscalDateEnding"),
        "revenue": to_int(latest.get("totalRevenue")),
        "netIncome": to_int(latest.get("netIncome")),
    }

def _revenue_flag(revenue: int, threshold: int = 3_000_000_000):
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
            inc_latest = _parse_latest_income(inc)
            name = ov.get("Name") or VENDOR_SYMBOLS.get(s, s)
            industry = ov.get("Industry") or ov.get("Sector") or ""
            fy = inc_latest["fiscalYear"] if inc_latest else None
            revenue = inc_latest["revenue"] if inc_latest else None
            net_income = inc_latest["netIncome"] if inc_latest else None
            rows.append(VendorComparisonRow(
                symbol=s, name=name, industry=industry,
                fiscalYear=fy, revenue=revenue, netIncome=net_income,
                revenueFlag=_revenue_flag(revenue)
            ))
        except Exception as e:
            log.warning("compare row degraded for %s: %s", s, e)
            rows.append(VendorComparisonRow(
                symbol=s, name=VENDOR_SYMBOLS.get(s, s), industry="", revenueFlag=None
            ))
    return rows

# ---------- STATIC ----------
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return FileResponse("static/index.html")

@app.get("/api/debug/env")
def debug_env():
    return {"ALPHAVANTAGE_API_KEY_present": bool(os.getenv("ALPHAVANTAGE_API_KEY"))}
