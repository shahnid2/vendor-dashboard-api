from pydantic import BaseModel
from typing import Optional

class VendorComparisonRow(BaseModel):
    symbol: str
    name: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    marketCap: Optional[int] = None          # from OVERVIEW.MarketCapitalization
    revenueTTM: Optional[int] = None         # sum last 4 quarterly totalRevenue
    ebitdaTTM: Optional[int] = None          # from OVERVIEW.EBITDA
    yoyRevenue: Optional[float] = None       # (last annual - prev annual)/prev annual
    fiscalYear: Optional[str] = None         # latest annual fiscalDateEnding
    revenue: Optional[int] = None            # latest annual totalRevenue
    netIncome: Optional[int] = None          # latest annual netIncome
    revenueFlag: Optional[str] = None        # "LOW" | "OK" | None
