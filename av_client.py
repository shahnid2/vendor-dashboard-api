# av_client.py
import os, json, httpx, logging
from dotenv import load_dotenv, find_dotenv
from cache import cache_get, cache_set

load_dotenv(find_dotenv(), override=False)
log = logging.getLogger("av_client")

BASE = "https://www.alphavantage.co/query"
API_KEY = os.getenv("ALPHAVANTAGE_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "Missing ALPHAVANTAGE_API_KEY. Put it in .env at project root, e.g.\n"
        "ALPHAVANTAGE_API_KEY=E3B3Q6Q60PZ9TJEB"
    )

def _ttl():
    try: return int(os.getenv("CACHE_TTL_SECONDS", "86400"))
    except: return 86400

def _maybe_raise_alpha_error(data: dict, params: dict):
    if isinstance(data, dict):
        msg = data.get("Error Message") or data.get("Information") or data.get("Note")
        if msg:
            f = params.get("function"); s = params.get("symbol")
            raise RuntimeError(f"Alpha Vantage {f}({s}) error: {msg}")

async def _fetch(params: dict):
    key = "av:" + json.dumps(params, sort_keys=True)
    cached = cache_get(key, _ttl())
    if cached:
        return cached
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(BASE, params=params)
        data = r.json()
    _maybe_raise_alpha_error(data, params)
    cache_set(key, data)
    return data

async def get_overview(symbol: str):
    return await _fetch({"function": "OVERVIEW", "symbol": symbol, "apikey": API_KEY})

async def get_income_statement(symbol: str):
    return await _fetch({"function": "INCOME_STATEMENT", "symbol": symbol, "apikey": API_KEY})
