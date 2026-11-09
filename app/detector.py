"""Turn a messy transaction export into subscriptions Sift can reason about.

The detector is intentionally deterministic and explainable. It groups similar
merchant descriptions, looks for a stable rhythm between charges, and reports a
confidence score. No transaction data leaves the machine.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from collections import Counter
from datetime import date, datetime, timedelta
from statistics import median, pstdev
from typing import Any, Dict, Iterable, List, Optional, Tuple


ALIASES: List[Tuple[str, str]] = [
    ("AMAZON PRIME", "Amazon Prime"),
    ("PRIME VIDEO", "Prime Video"),
    ("APPLE COM BILL", "iCloud+"),
    ("GOOGLE ONE", "Google One"),
    ("GOOGLE STORAGE", "Google One"),
    ("LINKEDIN PREMIUM", "LinkedIn Premium"),
    ("LINKEDIN", "LinkedIn Premium"),
    ("DISNEY HOTSTAR", "Disney+ Hotstar"),
    ("HOTSTAR", "Disney+ Hotstar"),
    ("YOUTUBE PREMIUM", "YouTube Premium"),
    ("MICROSOFT 365", "Microsoft 365"),
    ("XBOX GAME PASS", "Xbox Game Pass"),
    ("PLAYSTATION PLUS", "PlayStation Plus"),
    ("GITHUB COPILOT", "GitHub Copilot"),
    ("CHATGPT PLUS", "ChatGPT Plus"),
    ("OPENAI", "ChatGPT Plus"),
    ("CANVA PRO", "Canva Pro"),
    ("SWIGGY ONE", "Swiggy One"),
    ("ZOMATO GOLD", "Zomato Gold"),
    ("ZEPTO PASS", "Zepto Pass"),
    ("NETFLIX", "Netflix"),
    ("SPOTIFY", "Spotify"),
    ("ADOBE", "Adobe Creative Cloud"),
    ("NOTION", "Notion"),
    ("CULT", "Cult.fit"),
    ("ICLOUD", "iCloud+"),
    ("AUDIBLE", "Audible"),
    ("FIGMA", "Figma"),
    ("DROPBOX", "Dropbox"),
    ("BLINKIT", "Blinkit"),
]

CATEGORY_RULES: List[Tuple[str, str]] = [
    ("NETFLIX", "Entertainment"),
    ("SPOTIFY", "Entertainment"),
    ("HOTSTAR", "Entertainment"),
    ("DISNEY", "Entertainment"),
    ("PRIME VIDEO", "Entertainment"),
    ("YOUTUBE", "Entertainment"),
    ("AUDIBLE", "Entertainment"),
    ("PLAYSTATION", "Entertainment"),
    ("XBOX", "Entertainment"),
    ("NOTION", "Productivity"),
    ("ADOBE", "Productivity"),
    ("LINKEDIN", "Productivity"),
    ("CANVA", "Productivity"),
    ("FIGMA", "Productivity"),
    ("GITHUB", "Productivity"),
    ("MICROSOFT", "Productivity"),
    ("CHATGPT", "AI tools"),
    ("OPENAI", "AI tools"),
    ("GOOGLE ONE", "Storage"),
    ("ICLOUD", "Storage"),
    ("APPLE COM", "Storage"),
    ("DROPBOX", "Storage"),
    ("CULT", "Health"),
    ("AMAZON PRIME", "Shopping"),
    ("SWIGGY", "Shopping"),
    ("ZOMATO", "Shopping"),
    ("ZEPTO", "Shopping"),
    ("BLINKIT", "Shopping"),
]

COLORS = {
    "Entertainment": "#FF5A4E",
    "Productivity": "#5B7CFA",
    "Storage": "#00A88F",
    "Health": "#E86A33",
    "Shopping": "#C58A00",
    "AI tools": "#8B5CF6",
    "Other": "#6B7280",
}

CADENCES: Dict[str, Dict[str, Any]] = {
    "weekly": {"days": 7, "tolerance": 2, "periods": 52, "label": "Weekly"},
    "biweekly": {"days": 14, "tolerance": 3, "periods": 26, "label": "Every 2 weeks"},
    "monthly": {"days": 30, "tolerance": 5, "periods": 12, "label": "Monthly"},
    "quarterly": {"days": 91, "tolerance": 12, "periods": 4, "label": "Quarterly"},
    "yearly": {"days": 365, "tolerance": 45, "periods": 1, "label": "Yearly"},
}

NOISE_TOKENS = {
    "ACH",
    "ANNUAL",
    "AUTOPAY",
    "AUTO",
    "BILL",
    "CARD",
    "CREDIT",
    "DEBIT",
    "INDIA",
    "INVOICE",
    "MONTHLY",
    "LTD",
    "LIMITED",
    "MANDATE",
    "PAYMENT",
    "PAYMENTS",
    "POS",
    "PRIVATE",
    "PVT",
    "RECURRING",
    "SUBSCR",
    "SUBSCRIPTION",
    "TXN",
    "UPI",
    "WWW",
    "COM",
    "NET",
    "ORG",
}

DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d-%b-%Y",
)


def _header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _find_column(fieldnames: Iterable[str], aliases: Iterable[str]) -> Optional[str]:
    lookup = {_header_key(name): name for name in fieldnames}
    for alias in aliases:
        if _header_key(alias) in lookup:
            return lookup[_header_key(alias)]
    for name in fieldnames:
        key = _header_key(name)
        if any(_header_key(alias) in key for alias in aliases):
            return name
    return None


def parse_date(value: str) -> Optional[date]:
    value = (value or "").strip()
    if not value:
        return None
    value = re.sub(r"\s+\d{1,2}:\d{2}.*$", "", value)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[^0-9.,-]", "", text)
    if text.count(",") and text.count("."):
        text = text.replace(",", "")
    elif text.count(",") and not text.count("."):
        # 1,299 -> 1299; 12,99 -> 12.99
        if re.search(r",\d{2}$", text):
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
    try:
        amount = float(text)
    except ValueError:
        return None
    if negative:
        amount = -amount
    return amount


def normalize_merchant(raw: str) -> str:
    text = (raw or "").upper()
    text = text.replace("&", " AND ")
    text = re.sub(r"[#*]\s*[A-Z0-9-]+", " ", text)
    text = re.sub(r"\b[A-Z]{0,4}\d{3,}[A-Z0-9]*\b", " ", text)
    text = re.sub(r"\b\d{2,}\b", " ", text)
    text = re.sub(r"[^A-Z0-9+ ]", " ", text)
    tokens = [token for token in text.split() if token not in NOISE_TOKENS]
    tokens = [token for token in tokens if not (any(ch.isdigit() for ch in token) and len(token) > 4)]
    text = " ".join(tokens)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def display_name(normalized: str, fallback: str) -> str:
    for alias, name in ALIASES:
        if alias in normalized:
            return name
    cleaned = re.sub(r"\b(PREMIUM|PLUS|PRO|MEMBERSHIP)\b", "", fallback, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_")
    return cleaned.title() if cleaned else fallback.title()


def guess_category(normalized: str, provided: str = "") -> str:
    for key, category in CATEGORY_RULES:
        if key in normalized:
            return category
    provided = (provided or "").strip().title()
    return provided if provided else "Other"


def merchant_color(category: str) -> str:
    return COLORS.get(category, COLORS["Other"])


def merchant_initials(name: str) -> str:
    words = [word for word in re.split(r"\s+", name) if word]
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def transaction_id(merchant: str) -> str:
    digest = hashlib.sha1(merchant.encode("utf-8")).hexdigest()[:8]
    slug = re.sub(r"[^a-z0-9]+", "-", merchant.lower()).strip("-")
    return f"{slug}-{digest}"


def parse_csv(text: str, source: str = "transactions.csv") -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Parse common bank / UPI CSV exports without assuming one bank format."""
    if not text or not text.strip():
        raise ValueError("That file is empty. Try the sample CSV to see the expected format.")

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("I couldn't find a header row in that CSV.")

    date_col = _find_column(reader.fieldnames, ("transaction date", "txn date", "date", "posted date", "value date"))
    merchant_col = _find_column(reader.fieldnames, ("merchant", "description", "narration", "details", "payee", "name"))
    amount_col = _find_column(reader.fieldnames, ("amount", "transaction amount", "value", "debit"))
    category_col = _find_column(reader.fieldnames, ("category", "type of spend"))
    currency_col = _find_column(reader.fieldnames, ("currency", "ccy"))
    type_col = _find_column(reader.fieldnames, ("transaction type", "type", "dr cr", "debit credit"))

    if not date_col or not merchant_col or not amount_col:
        raise ValueError(
            "I need at least date, description/merchant and amount columns. "
            "Download the sample CSV for the expected format."
        )

    transactions: List[Dict[str, Any]] = []
    skipped = 0
    for row in reader:
        parsed_date = parse_date(str(row.get(date_col, "")))
        merchant = str(row.get(merchant_col, "")).strip()
        amount = parse_amount(row.get(amount_col))
        row_type = str(row.get(type_col, "")).strip().upper() if type_col else ""
        if row_type and any(word in row_type for word in ("CREDIT", "REFUND")):
            skipped += 1
            continue
        if not parsed_date or not merchant or amount is None:
            skipped += 1
            continue
        amount = abs(amount)
        if amount <= 0:
            skipped += 1
            continue
        normalized = normalize_merchant(merchant)
        if len(normalized.replace(" ", "")) < 3:
            skipped += 1
            continue
        transactions.append(
            {
                "date": parsed_date.isoformat(),
                "merchant": merchant,
                "normalized": normalized,
                "amount": round(amount, 2),
                "currency": str(row.get(currency_col, "INR")).strip().upper() if currency_col else "INR",
                "category": str(row.get(category_col, "")).strip() if category_col else "",
                "source": source,
            }
        )

    if not transactions:
        raise ValueError("I couldn't read any debit transactions from that CSV.")

    transactions.sort(key=lambda item: item["date"])
    meta = {
        "source": source,
        "transactions": len(transactions),
        "skipped": skipped,
        "currency": Counter(item["currency"] for item in transactions).most_common(1)[0][0],
        "first_date": transactions[0]["date"],
        "last_date": transactions[-1]["date"],
    }
    return transactions, meta


def _cadence_for(gaps: List[int]) -> Optional[Tuple[str, float]]:
    best: Optional[Tuple[str, float]] = None
    best_score = 0.0
    for name, spec in CADENCES.items():
        expected = spec["days"]
        tolerance = spec["tolerance"]
        if not gaps:
            continue
        matches = sum(1 for gap in gaps if abs(gap - expected) <= tolerance)
        fit = matches / len(gaps)
        med = median(gaps)
        # A cadence needs most gaps to fit, and the median to sit near expected.
        if len(gaps) == 1:
            ok = abs(gaps[0] - expected) <= tolerance
        else:
            ok = fit >= 0.66 and abs(med - expected) <= tolerance * 1.45
        if not ok:
            continue
        distance_penalty = abs(med - expected) / max(expected, 1)
        score = fit - distance_penalty
        if score > best_score:
            best = (name, fit)
            best_score = score
    return best


def _annual_cost(amount: float, cadence: str) -> float:
    return round(amount * CADENCES[cadence]["periods"], 2)


def _next_charge(last: date, interval_days: int, today: date) -> date:
    next_date = last + timedelta(days=interval_days)
    guard = 0
    while next_date < today and guard < 120:
        next_date += timedelta(days=interval_days)
        guard += 1
    return next_date


def _confidence(charge_count: int, fit: float, amount_cv: float) -> float:
    score = 0.48
    if charge_count >= 3:
        score += 0.18
    if charge_count >= 6:
        score += 0.08
    if charge_count >= 10:
        score += 0.04
    score += 0.14 * fit
    score += 0.08 * (1 - min(amount_cv, 0.30) / 0.30)
    return round(max(0.55, min(score, 0.99)), 2)


def confidence_label(value: float) -> str:
    if value >= 0.85:
        return "High"
    if value >= 0.70:
        return "Medium"
    return "Low"


def _merchant_category(rows: List[Dict[str, Any]], normalized: str) -> str:
    provided = Counter(row["category"] for row in rows if row.get("category")).most_common(1)
    return guess_category(normalized, provided[0][0] if provided else "")


def detect_subscriptions(
    transactions: List[Dict[str, Any]],
    today: Optional[date] = None,
    currency: str = "INR",
) -> List[Dict[str, Any]]:
    today = today or date.today()
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for transaction in transactions:
        grouped.setdefault(transaction["normalized"], []).append(transaction)

    subscriptions: List[Dict[str, Any]] = []
    for normalized, rows in grouped.items():
        rows = sorted(rows, key=lambda row: row["date"])
        if len(rows) < 2:
            continue
        dates = [datetime.strptime(row["date"], "%Y-%m-%d").date() for row in rows]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        if any(gap <= 0 for gap in gaps):
            continue
        cadence_match = _cadence_for(gaps)
        if not cadence_match:
            continue
        cadence, fit = cadence_match
        # Two coincidental coffee orders 30 days apart are not a subscription.
        # Monthly-ish rhythms need three charges; annual charges need two.
        if cadence != "yearly" and len(rows) < 3:
            continue
        amounts = [float(row["amount"]) for row in rows]
        mean_amount = sum(amounts) / len(amounts)
        amount_cv = (pstdev(amounts) / mean_amount) if len(amounts) > 1 and mean_amount else 0.0
        if amount_cv > 0.38:
            continue

        last_amount = amounts[-1]
        annual = _annual_cost(last_amount, cadence)
        interval = round(median(gaps))
        # If the rhythm stopped more than two cycles ago, treat it as lapsed
        # rather than projecting a renewal that probably won't happen.
        if (today - dates[-1]).days > interval * 2.2:
            continue
        next_date = _next_charge(dates[-1], interval, today)
        price_increase = None
        if len(amounts) >= 3:
            prior = median(amounts[:-1])
            if prior > 0 and last_amount > prior * 1.04:
                delta = round(last_amount - prior, 2)
                price_increase = {
                    "from": round(prior, 2),
                    "to": round(last_amount, 2),
                    "percent": round((last_amount / prior - 1) * 100),
                    "annual_impact": round(delta * CADENCES[cadence]["periods"], 2),
                }

        name = display_name(normalized, rows[-1]["merchant"])
        category = _merchant_category(rows, normalized)
        confidence = _confidence(len(rows), fit, amount_cv)
        subscriptions.append(
            {
                "id": transaction_id(normalized),
                "merchant": name,
                "normalized": normalized,
                "category": category,
                "color": merchant_color(category),
                "initials": merchant_initials(name),
                "currency": rows[-1].get("currency", currency),
                "amount": round(last_amount, 2),
                "cadence": cadence,
                "cadence_label": CADENCES[cadence]["label"],
                "interval_days": interval,
                "periods_per_year": CADENCES[cadence]["periods"],
                "monthly_cost": round(annual / 12, 2),
                "annual_cost": annual,
                "charge_count": len(rows),
                "charges": [
                    {"date": row["date"], "amount": round(float(row["amount"]), 2)}
                    for row in rows[-18:]
                ],
                "first_charge": dates[0].isoformat(),
                "last_charge": dates[-1].isoformat(),
                "next_renewal": next_date.isoformat(),
                "days_until_renewal": (next_date - today).days,
                "confidence": confidence,
                "confidence_label": confidence_label(confidence),
                "price_increase": price_increase,
                "decision": "unreviewed",
                "usage": None,
                "note": "",
            }
        )

    subscriptions.sort(key=lambda item: (-item["annual_cost"], item["merchant"]))
    return subscriptions


def _overlaps(subs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for sub in subs:
        grouped.setdefault(sub["category"], []).append(sub)
    overlaps = []
    for category, items in grouped.items():
        if len(items) < 2 or category == "Other":
            continue
        monthly = round(sum(item["monthly_cost"] for item in items), 2)
        overlaps.append(
            {
                "category": category,
                "count": len(items),
                "monthly_cost": monthly,
                "annual_cost": round(monthly * 12, 2),
                "merchants": [item["merchant"] for item in items],
            }
        )
    overlaps.sort(key=lambda item: -item["annual_cost"])
    return overlaps


def build_analysis(
    transactions: List[Dict[str, Any]],
    meta: Dict[str, Any],
    today: Optional[date] = None,
) -> Dict[str, Any]:
    today = today or date.today()
    subs = detect_subscriptions(transactions, today=today, currency=meta.get("currency", "INR"))
    monthly_total = round(sum(sub["monthly_cost"] for sub in subs), 2)
    annual_total = round(sum(sub["annual_cost"] for sub in subs), 2)
    upcoming = [sub for sub in subs if 0 <= sub["days_until_renewal"] <= 30]
    upcoming.sort(key=lambda item: item["days_until_renewal"])
    price_hikes = [sub for sub in subs if sub.get("price_increase")]
    next_30_total = round(sum(sub["amount"] for sub in upcoming), 2)

    metrics = {
        "monthly_total": monthly_total,
        "annual_total": annual_total,
        "active_count": len(subs),
        "upcoming_count": len(upcoming),
        "upcoming_30_total": next_30_total,
        "price_hike_count": len(price_hikes),
        "price_hike_annual_impact": round(
            sum(sub["price_increase"]["annual_impact"] for sub in price_hikes), 2
        ),
        "average_confidence": round(
            sum(sub["confidence"] for sub in subs) / len(subs), 2
        ) if subs else 0,
        "next_30": [
            {
                "id": sub["id"],
                "merchant": sub["merchant"],
                "amount": sub["amount"],
                "currency": sub["currency"],
                "date": sub["next_renewal"],
                "days": sub["days_until_renewal"],
                "color": sub["color"],
                "initials": sub["initials"],
            }
            for sub in upcoming
        ],
    }

    analysis = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": meta.get("source", "transactions.csv"),
        "is_demo": meta.get("source") == "sample_transactions.csv",
        "transactions_count": meta.get("transactions", len(transactions)),
        "skipped_count": meta.get("skipped", 0),
        "currency": meta.get("currency", "INR"),
        "date_range": [meta.get("first_date"), meta.get("last_date")],
        "subscriptions": subs,
        "overlaps": _overlaps(subs),
        "metrics": metrics,
        "insights": None,
    }
    return analysis


def sample_summary_for_ai(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Only derived, aggregated fields. Raw transactions never go to an LLM."""
    return {
        "currency": analysis["currency"],
        "monthly_total": analysis["metrics"]["monthly_total"],
        "annual_total": analysis["metrics"]["annual_total"],
        "subscription_count": len(analysis["subscriptions"]),
        "upcoming_30_days": analysis["metrics"]["next_30"],
        "overlaps": analysis["overlaps"],
        "subscriptions": [
            {
                "merchant": sub["merchant"],
                "category": sub["category"],
                "amount": sub["amount"],
                "cadence": sub["cadence"],
                "monthly_cost": sub["monthly_cost"],
                "annual_cost": sub["annual_cost"],
                "next_renewal": sub["next_renewal"],
                "price_increase": sub["price_increase"],
                "confidence": sub["confidence"],
                "decision": sub["decision"],
                "usage": sub["usage"],
            }
            for sub in analysis["subscriptions"]
        ],
    }


def round_currency(value: float) -> float:
    return round(float(value or 0), 2)


def human_days(value: int) -> str:
    if value == 0:
        return "today"
    if value == 1:
        return "tomorrow"
    if value < 0:
        return f"{-value} days ago"
    return f"in {value} days"


def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default
