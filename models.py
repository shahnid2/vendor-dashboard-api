from pydantic import BaseModel
from typing import Optional

class VendorComparisonRow(BaseModel):
    symbol: str
    name: str
    industry: str
    fiscalYear: Optional[str] = None
    revenue: Optional[int] = None
    netIncome: Optional[int] = None
    revenueFlag: Optional[str] = None
