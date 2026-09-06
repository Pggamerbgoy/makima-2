import asyncio
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("makima.os.finance")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

class FinanceStore:
    """High-performance, thread-safe async in-memory cache for financial records."""
    
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._expenses: List[Dict[str, Any]] = []

    async def add_expense(self, expense: Dict[str, Any]) -> None:
        async with self._lock:
            self._expenses.append(expense)

    async def get_expenses(self, month: Optional[int] = None, year: Optional[int] = None) -> List[Dict[str, Any]]:
        async with self._lock:
            if not month or not year:
                return list(self._expenses)
            return [
                e for e in self._expenses
                if e["timestamp"].month == month and e["timestamp"].year == year
            ]

_store = FinanceStore()

async def track_expense(amount: float, category: str, description: str, currency: str = "USD") -> Dict[str, Any]:
    """Records a new financial expense with strict validation and async safety."""
    try:
        if amount <= 0:
            return {"status": "error", "message": "Amount must be strictly positive."}

        record = {
            "id": str(uuid.uuid4()),
            "amount": float(amount),
            "category": category.strip().lower(),
            "description": description.strip(),
            "currency": currency.upper(),
            "timestamp": datetime.utcnow()
        }
        await _store.add_expense(record)
        logger.info(f"Tracked expense: {amount} {currency} for {category}")
        return {"status": "success", "data": {**record, "timestamp": record["timestamp"].isoformat()}}
    except Exception as e:
        logger.exception("Failed to track expense")
        return {"status": "error", "message": f"Tracking failed: {str(e)}"}

async def analyze_budget(month: int, year: int, target_budget: float) -> Dict[str, Any]:
    """Analyzes monthly spending against a target budget, providing utilization metrics."""
    try:
        if not (1 <= month <= 12) or year < 2000:
            return {"status": "error", "message": "Invalid month or year provided."}

        expenses = await _store.get_expenses(month, year)
        total_spent = sum(e["amount"] for e in expenses)
        remaining = target_budget - total_spent
        utilization = (total_spent / target_budget) * 100 if target_budget > 0 else 0.0

        category_breakdown: Dict[str, float] = {}
        for e in expenses:
            cat = e["category"]
            category_breakdown[cat] = category_breakdown.get(cat, 0.0) + e["amount"]

        analysis = {
            "month": month, "year": year, "target_budget": target_budget,
            "total_spent": round(total_spent, 2), "remaining": round(remaining, 2),
            "utilization_percent": round(utilization, 2),
            "status": "over_budget" if remaining < 0 else "under_budget",
            "category_breakdown": {k: round(v, 2) for k, v in category_breakdown.items()}
        }
        logger.info(f"Budget analysis for {month}/{year}: {utilization:.1f}% utilized")
        return {"status": "success", "data": analysis}
    except Exception as e:
        logger.exception("Failed to analyze budget")
        return {"status": "error", "message": f"Analysis failed: {str(e)}"}

async def parse_receipt_ocr(receipt_text: str) -> Dict[str, Any]:
    """Parses raw OCR receipt text using advanced regex to extract merchant, date, total, and tax."""
    try:
        if not receipt_text or not isinstance(receipt_text, str):
            return {"status": "error", "message": "Invalid receipt text provided."}

        text = receipt_text.strip()
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        merchant = lines[0][:60] if lines else "Unknown Merchant"
        
        total, tax, date_str = 0.0, 0.0, None

        total_patterns = [
            r"(?i)(?:total|amount|grand total|balance due)\s*[:\-]?\s*\$?(\d{1,3}(?:,\d{3})*\.\d{2})",
            r"(?i)\$?(\d{1,3}(?:,\d{3})*\.\d{2})\s*(?:total|amount)"
        ]
        for pattern in total_patterns:
            match = re.search(pattern, text)
            if match:
                total = float(match.group(1).replace(',', ''))
                break

        tax_match = re.search(r"(?i)(?:tax|vat|gst|hst)\s*[:\-]?\s*\$?(\d{1,3}(?:,\d{3})*\.\d{2})", text)
        if tax_match:
            tax = float(tax_match.group(1).replace(',', ''))

        for pattern in [r"(\d{2}/\d{2}/\d{4})", r"(\d{4}-\d{2}-\d{2})", r"(\d{2}-\d{2}-\d{4})"]:
            match = re.search(pattern, text)
            if match:
                date_str = match.group(1)
                break

        parsed = {"merchant": merchant, "total_amount": total, "tax_amount": tax, "date": date_str, "lines_parsed": len(lines)}
        logger.info(f"Parsed receipt: {merchant} | Total: {total} | Tax: {tax}")
        return {"status": "success", "data": parsed}
    except Exception as e:
        logger.exception("Failed to parse receipt")
        return {"status": "error", "message": f"OCR parsing failed: {str(e)}"}

async def get_portfolio_summary(assets: List[Dict[str, Union[str, float, int]]]) -> Dict[str, Any]:
    """Calculates portfolio valuation, allocation weights, and concentration risk (HHI)."""
    try:
        if not assets or not isinstance(assets, list):
            return {"status": "error", "message": "Assets list is empty or invalid."}

        portfolio = []
        total_value = 0.0

        for asset in assets:
            ticker = str(asset.get("ticker", "UNKNOWN")).upper()
            shares = float(asset.get("shares", 0))
            price = float(asset.get("price", 0))
            value = shares * price
            total_value += value
            portfolio.append({"ticker": ticker, "shares": shares, "price": price, "value": value})

        for p in portfolio:
            p["weight_percent"] = round((p["value"] / total_value) * 100, 2) if total_value > 0 else 0.0
            p["value"] = round(p["value"], 2)

        portfolio.sort(key=lambda x: x["value"], reverse=True)
        
        # Herfindahl-Hirschman Index (HHI) for concentration risk (0 to 10,000 scale)
        hhi = sum((p["weight_percent"]) ** 2 for p in portfolio)

        summary = {
            "total_portfolio_value": round(total_value, 2),
            "total_assets": len(portfolio),
            "top_holding": portfolio[0]["ticker"] if portfolio else None,
            "concentration_hhi": round(hhi, 2),
            "allocations": portfolio
        }
        logger.info(f"Portfolio summary generated: Total Value ${total_value:,.2f} | HHI: {hhi:.2f}")
        return {"status": "success", "data": summary}
    except Exception as e:
        logger.exception("Failed to generate portfolio summary")
        return {"status": "error", "message": f"Portfolio calculation failed: {str(e)}"}

def register_finance_tools(registry: Any) -> None:
    """Registers the elite finance toolset into the Makima OS tool registry."""
    try:
        tools = [
            {
                "name": "track_expense",
                "func": track_expense,
                "description": "Call this tool EXCLUSIVELY to record a financial expense when the user provides an amount and category. Args: amount (float), category (str), description (str), currency (str).",
                "schema": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "number", "description": "Expense monetary amount (must be positive)"},
                        "category": {"type": "string", "description": "Expense category (e.g. food, travel, software, utilities)"},
                        "description": {"type": "string", "description": "Item description or memo"},
                        "currency": {"type": "string", "description": "Three-letter currency code (e.g. USD, EUR)", "default": "USD"},
                    },
                    "required": ["amount", "category", "description"],
                },
                "category": "finance",
            },
            {
                "name": "analyze_budget",
                "func": analyze_budget,
                "description": "Call this tool EXCLUSIVELY when asked to analyze monthly spending against a target budget. Args: month (int), year (int), target_budget (float).",
                "schema": {
                    "type": "object",
                    "properties": {
                        "month": {"type": "integer", "description": "Month number (1-12)"},
                        "year": {"type": "integer", "description": "Four-digit year (e.g. 2026)"},
                        "target_budget": {"type": "number", "description": "Total target spending budget amount"},
                    },
                    "required": ["month", "year", "target_budget"],
                },
                "category": "finance",
            },
            {
                "name": "parse_receipt_ocr",
                "func": parse_receipt_ocr,
                "description": "Call this tool EXCLUSIVELY to parse raw OCR text from a receipt and extract merchant, date, total, and tax. Args: receipt_text (str).",
                "schema": {
                    "type": "object",
                    "properties": {
                        "receipt_text": {"type": "string", "description": "Raw extracted OCR text from a physical or digital receipt"},
                    },
                    "required": ["receipt_text"],
                },
                "category": "finance",
            },
            {
                "name": "get_portfolio_summary",
                "func": get_portfolio_summary,
                "description": "Call this tool EXCLUSIVELY to calculate portfolio valuation, asset allocation weights, and Herfindahl-Hirschman Index (HHI) risk. Args: assets (list of dicts).",
                "schema": {
                    "type": "object",
                    "properties": {
                        "assets": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "ticker": {"type": "string", "description": "Asset ticker or symbol (e.g. AAPL, BTC)"},
                                    "shares": {"type": "number", "description": "Number of units/shares held"},
                                    "price": {"type": "number", "description": "Current price per unit"},
                                },
                                "required": ["ticker", "shares", "price"],
                            },
                            "description": "List of asset positions with ticker, shares, and current price",
                        },
                    },
                    "required": ["assets"],
                },
                "category": "finance",
            },
        ]

        for tool in tools:
            if hasattr(registry, "register"):
                registry.register(
                    name=tool["name"],
                    func=tool["func"],
                    description=tool["description"],
                    schema=tool["schema"],
                    category=tool["category"],
                )
            elif hasattr(registry, "add_tool"):
                registry.add_tool(
                    name=tool["name"],
                    func=tool["func"],
                    description=tool["description"],
                    schema=tool["schema"],
                    category=tool["category"],
                )
            else:
                registry[tool["name"]] = tool["func"]

        logger.info(f"Successfully registered {len(tools)} elite finance tools to Makima OS registry.")
    except Exception as e:
        logger.critical(f"Critical failure during finance tools registration: {str(e)}")
