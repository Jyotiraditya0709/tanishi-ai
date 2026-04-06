"""
Tanishi Finance Agent — She watches your money.

Features:
- Track expenses via chat ("spent 500 on dinner")
- Parse Indian bank SMS/email alerts automatically
- Categorize spending (food, transport, shopping, etc.)
- Budget management with alerts
- Weekly/monthly spending reports
- UPI/Google Pay/PhonePay aware

All data stored locally in SQLite. Your money, your privacy.
"""

import os
import re
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

from tanishi.tools.registry import ToolDefinition


# ============================================================
# Data Models
# ============================================================

@dataclass
class Expense:
    id: str = ""
    amount: float = 0.0
    category: str = "other"
    description: str = ""
    payment_method: str = ""  # cash, upi, card, bank
    date: str = ""
    source: str = "manual"  # manual, sms, email

CATEGORIES = {
    "food": ["food", "restaurant", "zomato", "swiggy", "dinner", "lunch", "breakfast", "chai", "coffee", "biryani", "pizza", "burger", "snack", "eat", "meal", "canteen", "mess"],
    "transport": ["uber", "ola", "auto", "rickshaw", "petrol", "diesel", "fuel", "bus", "metro", "train", "flight", "cab", "taxi", "parking"],
    "shopping": ["amazon", "flipkart", "myntra", "clothes", "shoes", "shopping", "mall", "bought", "purchase", "order"],
    "entertainment": ["movie", "netflix", "spotify", "hotstar", "prime", "game", "gaming", "concert", "show"],
    "bills": ["electricity", "water", "gas", "wifi", "internet", "phone", "recharge", "rent", "emi", "insurance", "bill"],
    "health": ["medicine", "doctor", "hospital", "pharmacy", "gym", "medical", "health"],
    "education": ["book", "course", "udemy", "college", "fees", "tuition", "library", "stationery"],
    "transfer": ["transfer", "sent to", "paid to", "upi", "gpay", "phonepe", "paytm"],
    "subscription": ["subscription", "monthly", "annual", "premium", "plan"],
    "other": [],
}


def auto_categorize(description: str) -> str:
    """Auto-categorize an expense based on description."""
    desc_lower = description.lower()
    for category, keywords in CATEGORIES.items():
        for kw in keywords:
            if kw in desc_lower:
                return category
    return "other"


# ============================================================
# Indian Bank SMS Parser
# ============================================================

def parse_bank_sms(text: str) -> Optional[dict]:
    """
    Parse Indian bank transaction SMS/alerts.
    Handles formats from SBI, HDFC, ICICI, Axis, Kotak, etc.
    """
    text = text.strip()
    result = {"amount": 0, "type": "", "description": "", "bank": ""}

    # Common debit patterns
    debit_patterns = [
        r"(?:debited|deducted|spent|paid|sent)\s*(?:rs\.?|inr|₹)\s*([\d,]+\.?\d*)",
        r"(?:rs\.?|inr|₹)\s*([\d,]+\.?\d*)\s*(?:debited|deducted|spent|has been sent)",
        r"(?:dr|debit)\s*(?:rs\.?|inr|₹)\s*([\d,]+\.?\d*)",
        r"([\d,]+\.?\d*)\s*(?:debited|withdrawn|spent)",
        r"purchase of (?:rs\.?|inr|₹)\s*([\d,]+\.?\d*)",
    ]

    # Credit patterns
    credit_patterns = [
        r"(?:credited|received|refund)\s*(?:rs\.?|inr|₹)\s*([\d,]+\.?\d*)",
        r"(?:rs\.?|inr|₹)\s*([\d,]+\.?\d*)\s*(?:credited|received|deposited)",
        r"(?:cr|credit)\s*(?:rs\.?|inr|₹)\s*([\d,]+\.?\d*)",
    ]

    # UPI patterns
    upi_patterns = [
        r"(?:upi|gpay|phonepe|paytm).*?(?:rs\.?|inr|₹)\s*([\d,]+\.?\d*)",
        r"(?:rs\.?|inr|₹)\s*([\d,]+\.?\d*).*?(?:upi|gpay|phonepe|paytm)",
    ]

    text_lower = text.lower()

    # Try debit patterns
    for pattern in debit_patterns:
        match = re.search(pattern, text_lower)
        if match:
            result["amount"] = float(match.group(1).replace(",", ""))
            result["type"] = "debit"
            break

    # Try credit patterns if no debit found
    if not result["amount"]:
        for pattern in credit_patterns:
            match = re.search(pattern, text_lower)
            if match:
                result["amount"] = float(match.group(1).replace(",", ""))
                result["type"] = "credit"
                break

    # Try UPI patterns
    if not result["amount"]:
        for pattern in upi_patterns:
            match = re.search(pattern, text_lower)
            if match:
                result["amount"] = float(match.group(1).replace(",", ""))
                result["type"] = "debit"
                break

    if not result["amount"]:
        return None

    # Extract merchant/description
    merchant_patterns = [
        r"(?:at|to|for|@)\s+([A-Za-z0-9\s]+?)(?:\s+on|\s+ref|\s+upi|\.|$)",
        r"(?:VPA|vpa)\s+([a-z0-9@.\-]+)",
        r"to\s+([A-Za-z\s]+?)(?:\s+ref|\s+on|\.|$)",
    ]
    for pattern in merchant_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result["description"] = match.group(1).strip()[:50]
            break

    if not result["description"]:
        result["description"] = text[:60]

    # Detect bank
    banks = {
        "sbi": "SBI", "hdfc": "HDFC", "icici": "ICICI",
        "axis": "Axis", "kotak": "Kotak", "bob": "BOB",
        "pnb": "PNB", "yes bank": "Yes Bank", "idbi": "IDBI",
    }
    for key, name in banks.items():
        if key in text_lower:
            result["bank"] = name
            break

    # Detect payment method
    if any(w in text_lower for w in ["upi", "gpay", "phonepe", "paytm"]):
        result["payment_method"] = "upi"
    elif any(w in text_lower for w in ["card", "credit card", "debit card", "pos"]):
        result["payment_method"] = "card"
    elif any(w in text_lower for w in ["neft", "imps", "rtgs", "transfer"]):
        result["payment_method"] = "bank"
    else:
        result["payment_method"] = "other"

    return result


# ============================================================
# Finance Database
# ============================================================

class FinanceDB:
    """SQLite-based finance tracker."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id TEXT PRIMARY KEY,
                amount REAL NOT NULL,
                category TEXT DEFAULT 'other',
                description TEXT DEFAULT '',
                payment_method TEXT DEFAULT 'cash',
                date TEXT NOT NULL,
                source TEXT DEFAULT 'manual',
                created_at TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS budgets (
                category TEXT PRIMARY KEY,
                monthly_limit REAL NOT NULL,
                updated_at TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS income (
                id TEXT PRIMARY KEY,
                amount REAL NOT NULL,
                source TEXT DEFAULT '',
                date TEXT NOT NULL,
                created_at TEXT
            )
        """)

        conn.commit()
        conn.close()

    def add_expense(self, expense: Expense) -> Expense:
        expense.id = expense.id or f"exp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        expense.date = expense.date or datetime.now().strftime("%Y-%m-%d")
        expense.category = expense.category or auto_categorize(expense.description)

        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO expenses (id, amount, category, description, payment_method, date, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (expense.id, expense.amount, expense.category, expense.description,
              expense.payment_method, expense.date, expense.source, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return expense

    def set_budget(self, category: str, monthly_limit: float):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO budgets (category, monthly_limit, updated_at)
            VALUES (?, ?, ?)
        """, (category, monthly_limit, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_budgets(self) -> dict:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT category, monthly_limit FROM budgets")
        result = {row[0]: row[1] for row in c.fetchall()}
        conn.close()
        return result

    def get_spending(self, days: int = 30, category: str = "") -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        if category:
            c.execute("SELECT * FROM expenses WHERE date >= ? AND category = ? ORDER BY date DESC", (since, category))
        else:
            c.execute("SELECT * FROM expenses WHERE date >= ? ORDER BY date DESC", (since,))

        results = []
        for row in c.fetchall():
            results.append({
                "id": row[0], "amount": row[1], "category": row[2],
                "description": row[3], "payment_method": row[4],
                "date": row[5], "source": row[6],
            })
        conn.close()
        return results

    def get_summary(self, days: int = 30) -> dict:
        expenses = self.get_spending(days)
        total = sum(e["amount"] for e in expenses)

        by_category = {}
        for e in expenses:
            cat = e["category"]
            by_category[cat] = by_category.get(cat, 0) + e["amount"]

        # Sort by amount
        by_category = dict(sorted(by_category.items(), key=lambda x: x[1], reverse=True))

        # Budget check
        budgets = self.get_budgets()
        budget_status = {}
        for cat, limit in budgets.items():
            spent = by_category.get(cat, 0)
            budget_status[cat] = {
                "limit": limit,
                "spent": spent,
                "remaining": limit - spent,
                "percent": (spent / limit * 100) if limit > 0 else 0,
                "over": spent > limit,
            }

        # Daily average
        daily_avg = total / days if days > 0 else 0

        return {
            "total": total,
            "count": len(expenses),
            "days": days,
            "daily_average": round(daily_avg, 2),
            "by_category": by_category,
            "budget_status": budget_status,
            "top_expenses": sorted(expenses, key=lambda x: x["amount"], reverse=True)[:5],
        }


# ============================================================
# Tool Handlers
# ============================================================

_finance_db = None

def _get_db() -> FinanceDB:
    global _finance_db
    if _finance_db is None:
        from tanishi.core import get_config
        config = get_config()
        db_dir = config.tanishi_home if config.tanishi_home else Path.home() / ".tanishi"
        _finance_db = FinanceDB(db_dir / "finance.db")
    return _finance_db


async def log_expense(amount: float, description: str = "", category: str = "", payment_method: str = "cash") -> str:
    """Log a new expense."""
    db = _get_db()

    if not category:
        category = auto_categorize(description)

    expense = Expense(
        amount=amount,
        category=category,
        description=description,
        payment_method=payment_method,
    )
    db.add_expense(expense)

    # Check budget
    budgets = db.get_budgets()
    warning = ""
    if category in budgets:
        summary = db.get_summary(30)
        status = summary["budget_status"].get(category)
        if status and status["over"]:
            warning = f"\n⚠️ Budget EXCEEDED for {category}! Spent ₹{status['spent']:,.0f} of ₹{status['limit']:,.0f} limit."
        elif status and status["percent"] > 80:
            warning = f"\n⚠️ {category} budget at {status['percent']:.0f}%! ₹{status['remaining']:,.0f} remaining."

    return f"Logged: ₹{amount:,.0f} for {description or category} [{category}] via {payment_method}{warning}"


async def parse_transaction(text: str) -> str:
    """Parse a bank SMS or transaction alert and log it."""
    db = _get_db()
    parsed = parse_bank_sms(text)

    if not parsed:
        return "Couldn't parse that transaction. Try: 'spent 500 on dinner' or paste a bank SMS."

    if parsed["type"] == "credit":
        return f"Detected CREDIT of ₹{parsed['amount']:,.0f} from {parsed.get('description', 'unknown')}. I only track expenses — credits are good news, enjoy!"

    expense = Expense(
        amount=parsed["amount"],
        category=auto_categorize(parsed.get("description", "")),
        description=parsed.get("description", ""),
        payment_method=parsed.get("payment_method", "other"),
        source="sms",
    )
    db.add_expense(expense)

    return (
        f"Parsed and logged: ₹{parsed['amount']:,.0f} "
        f"{'from ' + parsed['bank'] + ' ' if parsed.get('bank') else ''}"
        f"for {parsed.get('description', 'unknown')} "
        f"[{expense.category}] via {parsed.get('payment_method', 'unknown')}"
    )


async def spending_report(days: int = 30) -> str:
    """Generate a spending report."""
    db = _get_db()
    summary = db.get_summary(days)

    if summary["count"] == 0:
        return f"No expenses recorded in the last {days} days. Either you're incredibly frugal or you haven't told me about your spending yet."

    lines = [f"💰 Spending Report — Last {days} days\n"]
    lines.append(f"Total spent: ₹{summary['total']:,.0f}")
    lines.append(f"Transactions: {summary['count']}")
    lines.append(f"Daily average: ₹{summary['daily_average']:,.0f}\n")

    lines.append("By category:")
    for cat, amount in summary["by_category"].items():
        pct = (amount / summary["total"] * 100) if summary["total"] > 0 else 0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        lines.append(f"  {cat:15s} ₹{amount:>8,.0f}  {bar}  {pct:.0f}%")

    # Budget alerts
    if summary["budget_status"]:
        lines.append("\nBudget status:")
        for cat, status in summary["budget_status"].items():
            icon = "🔴" if status["over"] else "🟡" if status["percent"] > 80 else "🟢"
            lines.append(f"  {icon} {cat}: ₹{status['spent']:,.0f} / ₹{status['limit']:,.0f} ({status['percent']:.0f}%)")

    # Top expenses
    if summary["top_expenses"]:
        lines.append("\nBiggest expenses:")
        for e in summary["top_expenses"][:5]:
            lines.append(f"  ₹{e['amount']:,.0f} — {e['description']} ({e['date']})")

    return "\n".join(lines)


async def set_budget(category: str, amount: float) -> str:
    """Set a monthly budget for a category."""
    db = _get_db()
    if category not in CATEGORIES:
        return f"Unknown category: {category}. Available: {', '.join(CATEGORIES.keys())}"
    db.set_budget(category, amount)
    return f"Budget set: ₹{amount:,.0f}/month for {category}"


async def spending_by_category(category: str, days: int = 30) -> str:
    """Get detailed spending for a specific category."""
    db = _get_db()
    expenses = db.get_spending(days, category)

    if not expenses:
        return f"No {category} expenses in the last {days} days."

    total = sum(e["amount"] for e in expenses)
    lines = [f"💰 {category.title()} Spending — Last {days} days\n"]
    lines.append(f"Total: ₹{total:,.0f} across {len(expenses)} transactions\n")

    for e in expenses:
        lines.append(f"  ₹{e['amount']:,.0f} — {e['description']} ({e['date']}) [{e['payment_method']}]")

    return "\n".join(lines)


# ============================================================
# Tool Definitions
# ============================================================

def get_finance_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="log_expense",
            description="Log an expense. Use when user says things like 'spent 500 on dinner', 'paid 2000 for groceries', 'bought shoes for 3000'. Automatically categorizes and tracks against budget.",
            input_schema={
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "Amount in INR (₹)"},
                    "description": {"type": "string", "description": "What was the expense for", "default": ""},
                    "category": {"type": "string", "description": "Category (auto-detected if empty). Options: food, transport, shopping, entertainment, bills, health, education, transfer, subscription, other", "default": ""},
                    "payment_method": {"type": "string", "description": "How they paid: cash, upi, card, bank", "default": "cash"},
                },
                "required": ["amount"],
            },
            handler=log_expense,
            category="finance",
            risk_level="low",
        ),
        ToolDefinition(
            name="parse_transaction",
            description="Parse a bank SMS, UPI notification, or transaction alert text and automatically log it as an expense. Use when user pastes a bank message or forwards a transaction notification.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The bank SMS or transaction alert text to parse"},
                },
                "required": ["text"],
            },
            handler=parse_transaction,
            category="finance",
            risk_level="low",
        ),
        ToolDefinition(
            name="spending_report",
            description="Generate a spending report showing total expenses, breakdown by category, budget status, and top expenses. Use when user asks about spending, expenses, money, budget, or 'where did my money go'.",
            input_schema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Number of days to report on", "default": 30},
                },
                "required": [],
            },
            handler=spending_report,
            category="finance",
            risk_level="low",
        ),
        ToolDefinition(
            name="set_budget",
            description="Set a monthly budget limit for a spending category. Tanishi will alert when approaching or exceeding the limit.",
            input_schema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Category: food, transport, shopping, entertainment, bills, health, education, subscription, other"},
                    "amount": {"type": "number", "description": "Monthly budget limit in INR (₹)"},
                },
                "required": ["category", "amount"],
            },
            handler=set_budget,
            category="finance",
            risk_level="low",
        ),
        ToolDefinition(
            name="spending_by_category",
            description="Get detailed spending breakdown for a specific category. Use when user asks 'how much did I spend on food' or 'show my transport expenses'.",
            input_schema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Category to filter"},
                    "days": {"type": "integer", "description": "Number of days", "default": 30},
                },
                "required": ["category"],
            },
            handler=spending_by_category,
            category="finance",
            risk_level="low",
        ),
    ]
