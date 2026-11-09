"""The AI layer: a grounded savings brief and cancellation email drafts.

Design choices:
* The model sees only derived subscription data, never raw transactions.
* The model cannot cancel anything. It can recommend, explain and draft.
* Every AI response is validated against the detector's data before display.
* If no provider/key is available, a transparent local coach keeps the app useful.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests


DEFAULT_BASE_URL = "https://algoholia.com/api/free-llm/v1"
DEFAULT_MODEL = "auto"
VALID_ACTIONS = {"cancel", "keep", "review"}


def _money(value: float, currency: str = "INR") -> str:
    symbol = "₹" if currency == "INR" else f"{currency} "
    return f"{symbol}{value:,.0f}"


def _mode() -> str:
    return os.getenv("AI_MODE", "auto").strip().lower()


def _base_url() -> str:
    return os.getenv("AI_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")


def _model() -> str:
    return os.getenv("AI_MODEL", DEFAULT_MODEL).strip()


def _api_key() -> str:
    return os.getenv("AI_API_KEY", "").strip()


def provider_label() -> str:
    if _mode() == "local":
        return "Local coach"
    base = _base_url()
    if "algoholia.com" in base or "pollinations.ai" in base:
        return "Sift AI · live demo"
    return "Sift AI · live"


def _chat(messages: List[Dict[str, str]], max_tokens: int = 900, temperature: float = 0.4) -> str:
    if _mode() == "local":
        raise RuntimeError("AI_MODE=local")
    key = _api_key()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload: Dict[str, Any] = {
        "model": _model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if "pollinations.ai" in _base_url():
        # The free endpoint otherwise spends a long time in hidden reasoning.
        payload["reasoning_effort"] = "low"
    response = requests.post(
        f"{_base_url()}/chat/completions",
        headers=headers,
        json=payload,
        timeout=35,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def extract_json(text: str) -> Dict[str, Any]:
    if not text:
        raise ValueError("Empty AI response")
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S | re.I)
    candidate = fenced.group(1).strip() if fenced else text.strip()
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", candidate):
        try:
            parsed, _ = decoder.raw_decode(candidate[match.start():])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    raise ValueError("AI response was not valid JSON")


def _find_subscription(analysis: Dict[str, Any], merchant: str) -> Optional[Dict[str, Any]]:
    needle = re.sub(r"[^a-z0-9]", "", (merchant or "").lower())
    if not needle:
        return None
    for sub in analysis["subscriptions"]:
        if re.sub(r"[^a-z0-9]", "", sub["merchant"].lower()) == needle:
            return sub
    for sub in analysis["subscriptions"]:
        haystack = re.sub(r"[^a-z0-9]", "", sub["merchant"].lower())
        if needle in haystack or haystack in needle:
            return sub
    return None


def _validated_ai(
    raw: Dict[str, Any],
    analysis: Dict[str, Any],
    fallback: Dict[str, Any],
) -> Dict[str, Any]:
    recommendations: List[Dict[str, Any]] = []
    seen = set()
    for item in raw.get("recommendations", [])[:6]:
        if not isinstance(item, dict):
            continue
        sub = _find_subscription(analysis, str(item.get("merchant", "")))
        if not sub or sub["id"] in seen:
            continue
        action = str(item.get("action", "review")).lower()
        if action not in VALID_ACTIONS:
            action = "review"
        if sub.get("decision") == "keep" or sub.get("usage") == "daily":
            action = "keep"
        if sub.get("decision") == "cancel" or sub.get("usage") == "never":
            action = "cancel"
        seen.add(sub["id"])
        recommendations.append(
            {
                "id": sub["id"],
                "merchant": sub["merchant"],
                "action": action,
                "reason": str(item.get("reason", "")).strip()[:280]
                or "Check whether this still earns a place in your stack.",
                "monthly_savings": round(sub["monthly_cost"], 2) if action == "cancel" else 0,
                "annual_savings": round(sub["annual_cost"], 2) if action == "cancel" else 0,
                "annual_review": round(sub["annual_cost"], 2) if action == "review" else 0,
                "confidence": str(item.get("confidence", "medium")).lower()[:10],
            }
        )
    if not recommendations:
        return fallback
    if len(recommendations) < 2:
        for item in fallback["recommendations"]:
            if item["id"] not in seen:
                recommendations.append(item)
                seen.add(item["id"])
            if len(recommendations) >= 3:
                break
    return _finish_insights(
        raw.get("headline") or fallback["headline"],
        raw.get("summary") or fallback["summary"],
        recommendations,
        list(raw.get("watchouts") or fallback["watchouts"])[:3],
        analysis,
        provider=provider_label(),
        model=_model(),
    )


def _finish_insights(
    headline: Any,
    summary: Any,
    recommendations: List[Dict[str, Any]],
    watchouts: List[Any],
    analysis: Dict[str, Any],
    provider: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    # Never trust the model's arithmetic. Recompute from detected subscriptions.
    potential = round(sum(item.get("annual_savings", 0) for item in recommendations), 2)
    review_potential = round(sum(item.get("annual_review", 0) for item in recommendations), 2)
    return {
        "headline": str(headline).strip()[:120] or "Your subscription stack, decoded.",
        "summary": str(summary).strip()[:420],
        "recommendations": recommendations,
        "watchouts": [str(item).strip()[:220] for item in watchouts if str(item).strip()],
        "potential_annual_savings": potential,
        "review_annual_savings": review_potential,
        "provider": provider,
        "model": model,
        "mode": "live" if "live" in provider.lower() else "local",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def local_insights(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """A useful, honest baseline when a provider is unavailable."""
    subs = analysis["subscriptions"]
    metrics = analysis["metrics"]
    currency = analysis["currency"]
    recs: List[Dict[str, Any]] = []
    seen = set()

    def add(sub: Dict[str, Any], action: str, reason: str) -> None:
        if sub["id"] in seen or len(recs) >= 5:
            return
        seen.add(sub["id"])
        recs.append(
            {
                "id": sub["id"],
                "merchant": sub["merchant"],
                "action": action,
                "reason": reason,
                "monthly_savings": round(sub["monthly_cost"], 2) if action == "cancel" else 0,
                "annual_savings": round(sub["annual_cost"], 2) if action == "cancel" else 0,
                "annual_review": round(sub["annual_cost"], 2) if action == "review" else 0,
                "confidence": "high" if sub["confidence"] >= 0.85 else "medium",
            }
        )

    for sub in subs:
        if sub.get("decision") == "cancel" or sub.get("usage") == "never":
            add(sub, "cancel", "You marked this as a cancellation. Sift will draft the request, but you approve the send.")
        elif sub.get("decision") == "keep" or sub.get("usage") == "daily":
            add(sub, "keep", "You marked this as part of your routine. No change recommended.")
        elif sub.get("usage") == "rarely":
            add(sub, "review", "You said you rarely use it. A quick usage check could turn this into an easy saving.")

    # Price rises are factual and worth surfacing before a renewal.
    for sub in sorted(subs, key=lambda item: -item["annual_cost"]):
        if sub.get("price_increase") and sub["id"] not in seen:
            hike = sub["price_increase"]
            add(
                sub,
                "review",
                f"The price moved from {_money(hike['from'], currency)} to {_money(hike['to'], currency)} "
                f"(+{hike['percent']}%). That adds about {_money(hike['annual_impact'], currency)} a year.",
            )

    # Storage overlap is usually the clearest duplicate category.
    storage = [sub for sub in subs if sub["category"] == "Storage"]
    if len(storage) > 1:
        cheapest = min(storage, key=lambda item: item["annual_cost"])
        add(
            cheapest,
            "review",
            f"You pay for {len(storage)} storage plans. Keep this only if you genuinely need "
            f"{storage[0]['merchant']} and {storage[1]['merchant']} side by side.",
        )

    entertainment = [sub for sub in subs if sub["category"] == "Entertainment"]
    if len(entertainment) > 2:
        smallest = min(entertainment, key=lambda item: item["annual_cost"])
        add(
            smallest,
            "review",
            f"You have {len(entertainment)} entertainment subscriptions. This is the smallest, "
            "so it is the easiest one to test-drive a month without.",
        )

    for sub in sorted(subs, key=lambda item: -item["annual_cost"]):
        if len(recs) >= 4:
            break
        if sub["id"] not in seen and sub["category"] != "Health" and sub["annual_cost"] >= 9000:
            add(
                sub,
                "review",
                f"This is one of your largest annual costs at {_money(sub['annual_cost'], currency)}. "
                "Confirm you used it in the last 30 days before it renews.",
            )

    # A balanced brief should not be all red flags. Show a likely keep too.
    if not any(item["action"] == "keep" for item in recs):
        healthy = [sub for sub in subs if sub["category"] == "Health"]
        if healthy:
            add(healthy[0], "keep", "Health spending is usually intentional. Keep it unless your routine has changed.")
    if not recs and subs:
        add(subs[0], "review", "Start with your largest subscription and check whether it earned its last renewal.")

    price_hikes = metrics["price_hike_count"]
    upcoming = metrics["upcoming_count"]
    watchouts = []
    if upcoming:
        watchouts.append(
            f"{upcoming} renewal{'s' if upcoming != 1 else ''} land in the next 30 days, "
            f"totalling {_money(metrics['upcoming_30_total'], currency)}."
        )
    if price_hikes:
        watchouts.append(
            f"{price_hikes} price increase{'s' if price_hikes != 1 else ''} detected, adding "
            f"{_money(metrics['price_hike_annual_impact'], currency)} a year at the current rhythm."
        )
    watchouts.append("Sift can see the charge, not whether you used it. Your usage check-in is the missing signal.")

    summary = (
        f"I found {len(subs)} recurring charges costing {_money(metrics['monthly_total'], currency)} a month "
        f"({_money(metrics['annual_total'], currency)} a year). The clearest places to look are price rises, "
        "overlapping categories and anything you have stopped using."
    )
    if metrics["annual_total"]:
        summary += " None of this is cancelled automatically."

    return _finish_insights(
        f"{_money(metrics['annual_total'], currency)} a year on autopilot.",
        summary,
        recs,
        watchouts,
        analysis,
        provider="Local coach",
        model="transparent heuristics",
    )


def live_insights(analysis: Dict[str, Any]) -> Dict[str, Any]:
    fallback = local_insights(analysis)
    if _mode() == "local":
        return fallback
    currency = analysis["currency"]
    data = {
        "currency": currency,
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
                "detector_confidence": sub["confidence"],
                "user_decision": sub["decision"],
                "user_usage": sub["usage"],
            }
            for sub in analysis["subscriptions"]
        ],
    }
    system = (
        "You are Sift, a sharp but calm subscription auditor. You receive derived, aggregated "
        "subscription data only. Never invent subscriptions, usage, prices or dates. A user's "
        "decision of keep/daily must never be recommended for cancellation. If usage is null, say "
        "a human usage check is needed rather than guessing. Prefer specific, useful observations "
        "over generic advice. Currency is INR; format money with ₹. Return ONLY valid JSON, no markdown."
    )
    user = (
        "Write a savings brief from this data:\n"
        + json.dumps(data, ensure_ascii=False)
        + """

Return exactly this shape:
{
  "headline": "12 words max, specific, no hype",
  "summary": "2 sentences max. Mention total annual spend and the strongest pattern.",
  "recommendations": [
    {
      "merchant": "exact merchant name from data",
      "action": "cancel | keep | review",
      "reason": "1 sentence, grounded in the data; mention the annual cost where useful",
      "confidence": "high | medium | low"
    }
  ],
  "watchouts": ["up to 3 short factual alerts"],
  "potential_annual_savings": 0
}

Rules:
- At most 5 recommendations. Include at least one keep when a subscription looks intentional.
- Use "review" when usage is unknown; use "cancel" only when user_decision is cancel or user_usage is never.
- Do not claim you know usage. Do not promise savings that are not confirmed cancellations.
- Mention overlapping categories and price increases only when present in the data.
- Do not cancel anything or imply that an email has been sent. Sift drafts; the human decides.
"""
    )
    try:
        content = _chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=950,
            temperature=0.35,
        )
        raw = extract_json(content)
        result = _validated_ai(raw, analysis, fallback)
        result["provider"] = provider_label()
        result["model"] = _model()
        result["mode"] = "live"
        return result
    except Exception as exc:  # network, quota, parsing, provider weirdness
        fallback["fallback_reason"] = str(exc)[:180]
        return fallback


def local_email(sub: Dict[str, Any], reason: str = "") -> Dict[str, str]:
    cadence = sub["cadence_label"].lower()
    reason_line = f" My reason for cancelling is: {reason.strip()}." if reason.strip() else ""
    body = (
        f"Hello {sub['merchant']} team,\n\n"
        f"I would like to cancel my {cadence} subscription of "
        f"{_money(sub['amount'], sub['currency'])}. My next renewal is currently "
        f"{sub['next_renewal']}.{reason_line}\n\n"
        "Please confirm in writing that the subscription is cancelled and that no further "
        "payments will be taken. I would also appreciate confirmation of the final billing date.\n\n"
        "Thank you,\n[Your name]\n[Account email]"
    )
    return {
        "subject": f"Cancellation request — {sub['merchant']}",
        "body": body,
        "provider": "Local template",
        "mode": "local",
    }


def draft_email(sub: Dict[str, Any], reason: str = "") -> Dict[str, str]:
    fallback = local_email(sub, reason)
    if _mode() == "local":
        return fallback
    system = (
        "You write short, polite, unambiguous subscription cancellation emails. "
        "Do not invent account numbers, names, dates or policy claims. Do not be aggressive. "
        "Return only valid JSON with keys subject and body."
    )
    user = (
        f"Subscription: {sub['merchant']}\n"
        f"Price: {_money(sub['amount'], sub['currency'])} {sub['cadence_label']}\n"
        f"Next renewal: {sub['next_renewal']}\n"
        f"User's cancellation reason (may be empty): {reason or 'not given'}\n\n"
        "Draft an email under 140 words. Ask for written confirmation of cancellation and "
        "that no further charges will be made. Use [Your name] and [Account email] placeholders. "
        'Return JSON like {"subject": "...", "body": "..."}'
    )
    try:
        content = _chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=420,
            temperature=0.3,
        )
        raw = extract_json(content)
        subject = str(raw.get("subject", "")).strip()
        body = str(raw.get("body", "")).strip()
        if not subject or not body:
            return fallback
        return {
            "subject": subject[:120],
            "body": body[:1800],
            "provider": provider_label(),
            "mode": "live",
        }
    except Exception as exc:
        fallback["fallback_reason"] = str(exc)[:180]
        return fallback
