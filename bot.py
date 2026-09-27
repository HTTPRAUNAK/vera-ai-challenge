"""
Vera AI Challenge — deterministic, context-grounded composer.

Required interface:
    compose(category, merchant, trigger, customer=None) -> dict

The implementation deliberately separates:
1) trigger strategy selection
2) context extraction
3) message construction
4) output validation

It does not invent facts. If a fact is unavailable in the supplied context,
the composer simply does not use it.
"""

from __future__ import annotations

import re
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Trigger strategy registry
# ---------------------------------------------------------------------------

TRIGGER_STRATEGIES = {
    # Knowledge / external
    "research_digest": "knowledge",
    "research_digest_release": "knowledge",
    "cde_opportunity": "knowledge",
    "regulation_change": "compliance",
    "category_trend_movement": "trend",
    "category_seasonal": "seasonal",
    "festival_upcoming": "seasonal",
    "weather_heatwave": "situational",
    "local_news_event": "situational",
    "ipl_match_today": "situational",
    "competitor_opened": "competitive",

    # Merchant performance / operations
    "perf_dip": "performance_problem",
    "seasonal_perf_dip": "performance_context",
    "perf_spike": "performance_win",
    "milestone_reached": "milestone",
    "review_theme_emerged": "review_problem",
    "gbp_unverified": "profile_problem",
    "supply_alert": "supply",
    "renewal_due": "renewal",
    "dormant_with_vera": "reactivation",
    "curious_ask_due": "curiosity",
    "active_planning_intent": "planning",
    "winback_eligible": "winback_merchant",

    # Customer
    "recall_due": "customer_recall",
    "customer_lapsed_soft": "customer_winback",
    "customer_lapsed_hard": "customer_winback",
    "appointment_tomorrow": "appointment",
    "unplanned_slot_open": "customer_slot",
    "chronic_refill_due": "customer_refill",
    "trial_followup": "customer_trial",
    "wedding_package_followup": "customer_wedding",
}


def _dig(obj: Any, *path: str, default=None):
    """Safe nested lookup."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _first_nonempty(*values):
    for v in values:
        if v not in (None, "", [], {}):
            return v
    return None


def _pct(x):
    if x is None:
        return None
    try:
        return f"{float(x) * 100:.0f}%"
    except Exception:
        return str(x)


def _money_title(offers):
    """Return the first active merchant offer exactly as supplied."""
    for offer in offers or []:
        if isinstance(offer, dict) and offer.get("status") == "active":
            title = offer.get("title")
            if title:
                return title
    return None


def _merchant_name(merchant):
    return (
        _dig(merchant, "identity", "owner_first_name")
        or _dig(merchant, "identity", "name")
        or "there"
    )


def _full_business_name(merchant):
    return _dig(merchant, "identity", "name") or _merchant_name(merchant)


def _languages(merchant, customer=None):
    if customer:
        pref = _dig(customer, "identity", "language_pref")
        if pref:
            return pref
    return _dig(merchant, "identity", "languages", default=[])


def _hi_en(merchant, customer=None):
    lang = _languages(merchant, customer)
    if isinstance(lang, str):
        return "hi" in lang.lower()
    return any(str(x).lower() in {"hi", "hinglish", "hi-en", "hi-en mix"} for x in lang)


def _voice_hint(category, customer=False):
    voice = _dig(category, "voice", default={}) or {}
    if isinstance(voice, dict):
        return {
            "tone": voice.get("tone"),
            "taboos": voice.get("taboos", []),
            "allowed": voice.get("vocab_allowed", []),
        }
    return {"tone": None, "taboos": [], "allowed": []}


def _category_slug(category, merchant):
    return (
        _dig(category, "slug")
        or _dig(merchant, "category_slug")
        or "business"
    )


def _peer_ctr(category):
    return _dig(category, "peer_stats", "avg_ctr")


def _active_offer(merchant):
    return _money_title(_dig(merchant, "offers", default=[]))


def _last_conversation(merchant):
    history = _dig(merchant, "conversation_history", default=[]) or []
    return history[-1] if history else None


def _send_as(trigger, customer):
    if customer is not None or trigger.get("scope") == "customer":
        return "merchant_on_behalf"
    return "vera"


def _cta_for(kind, scope):
    # Customer booking flows can use slot-choice CTAs.
    if scope == "customer":
        if kind in {"recall_due", "appointment_tomorrow", "unplanned_slot_open",
                    "trial_followup", "wedding_package_followup"}:
            return "open_ended"
        return "open_ended"

    # Merchant-side action triggers should have one primary ask.
    if kind in {"research_digest", "research_digest_release", "cde_opportunity",
                "curious_ask_due", "milestone_reached", "perf_spike",
                "category_seasonal"}:
        return "open_ended"

    if kind in {"perf_dip", "renewal_due", "gbp_unverified",
                "active_planning_intent", "competitor_opened",
                "review_theme_emerged", "supply_alert",
                "festival_upcoming", "ipl_match_today",
                "dormant_with_vera", "winback_eligible"}:
        return "open_ended"

    return "open_ended"


def _merchant_language_prefix(merchant, customer=None):
    if _hi_en(merchant, customer):
        return "Aapke"
    return "Your"


def _category_tone_suffix(category):
    slug = str(_category_slug(category, {})).lower()
    if slug == "dentists":
        return "Keep the wording clinical and peer-like; avoid medical guarantees."
    if slug == "pharmacies":
        return "Keep the wording practical and compliance-aware; avoid medical guarantees."
    return "Keep the wording category-appropriate and colleague-like."


# ---------------------------------------------------------------------------
# Context-specific message builders
# ---------------------------------------------------------------------------

def _research(category, merchant, trigger):
    payload = trigger.get("payload", {}) or {}
    item = payload.get("top_item")
    item_id = payload.get("top_item_id") or payload.get("digest_item_id")

    # Prefer the actual trigger payload; otherwise resolve the referenced digest item.
    if item is None and item_id:
        for d in _dig(category, "digest", default=[]) or []:
            if isinstance(d, dict) and d.get("id") == item_id:
                item = d
                break

    if isinstance(item, dict):
        title = item.get("title")
        source = item.get("source")
        n = item.get("trial_n")
        segment = item.get("patient_segment")
        parts = [f"{_merchant_name(merchant)}, a relevant update just landed"]
        if title:
            parts.append(f": {title}")
        if n:
            parts.append(f" ({n:,}-person trial)")
        if segment:
            parts.append(f" for {str(segment).replace('_', ' ')}")
        if source:
            parts.append(f". Source: {source}")
        return "".join(parts) + ". Want me to pull the item and draft something you can use?"

    digest = _dig(category, "digest", default=[]) or []
    if digest:
        d = digest[0]
        if isinstance(d, dict):
            title = d.get("title")
            source = d.get("source")
            if title:
                msg = f"{_merchant_name(merchant)}, this week's category update: {title}"
                if source:
                    msg += f" — {source}"
                return msg + ". Want me to pull it and turn it into something usable?"

    return f"{_merchant_name(merchant)}, a new category update is available. Want me to pull the relevant item for you?"


def _performance_problem(category, merchant, trigger):
    p = _dig(merchant, "performance", default={}) or {}
    tp = trigger.get("payload", {}) or {}
    metric = tp.get("metric", "performance")
    delta = tp.get("delta_pct")

    if delta is None:
        delta = _dig(p, "delta_7d", f"{metric}_pct")

    if delta is not None:
        pct = _pct(abs(delta))
        direction = "dropped" if float(delta) < 0 else "changed"
        msg = f"{_merchant_name(merchant)}, your {metric} {direction} {pct}"
        window = tp.get("window")
        if window:
            msg += f" over {window}"
        baseline = tp.get("vs_baseline")
        if baseline is not None:
            msg += f" (baseline: {baseline})"
        msg += ". Want me to check the most likely driver and suggest one fix?"
        return msg

    return f"{_merchant_name(merchant)}, I spotted a performance dip in your dashboard. Want me to check what changed?"


def _performance_win(category, merchant, trigger):
    tp = trigger.get("payload", {}) or {}
    metric = tp.get("metric", "performance")
    delta = tp.get("delta_pct")
    driver = tp.get("likely_driver")
    msg = f"{_merchant_name(merchant)}, your {metric} is up"
    if delta is not None:
        msg += f" {_pct(delta)}"
    msg += " in the latest window"
    if driver:
        msg += f", with {str(driver).replace('_', ' ')} as the likely driver"
    msg += ". Want me to turn what's working into the next post/campaign?"
    return msg


def _renewal(merchant, trigger):
    tp = trigger.get("payload", {}) or {}
    days = tp.get("days_remaining")
    plan = tp.get("plan")
    amount = tp.get("renewal_amount")

    msg = f"{_merchant_name(merchant)}, your Vera {plan or ''} plan"
    if days is not None:
        msg += f" has {days} days left"
    msg = msg.strip()

    if amount is not None:
        msg += f"; renewal is ₹{amount}"
    msg += ". Want me to help you review the renewal before it comes due?"
    return msg


def _competitor(merchant, trigger):
    tp = trigger.get("payload", {}) or {}
    name = tp.get("competitor_name")
    distance = tp.get("distance_km")
    offer = tp.get("their_offer")

    msg = f"{_merchant_name(merchant)}, a nearby competitor"
    if name:
        msg += f" ({name})"
    if distance is not None:
        msg += f" opened {distance} km away"
    if offer:
        msg += f" with {offer}"
    msg += ". Want me to compare that with your current offer and profile?"
    return msg


def _review_problem(merchant, trigger):
    tp = trigger.get("payload", {}) or {}
    theme = tp.get("theme")
    count = tp.get("occurrences_30d")
    trend = tp.get("trend")
    quote = tp.get("common_quote")

    msg = f"{_merchant_name(merchant)}, {count or 'several'} recent reviews mention {str(theme).replace('_', ' ')}"
    if trend:
        msg += f" and the trend is {trend}"
    if quote:
        msg += f' — one says "{quote}"'
    msg += ". Want me to suggest the first operational fix and a reply template?"
    return msg


def _milestone(merchant, trigger):
    tp = trigger.get("payload", {}) or {}
    metric = tp.get("metric", "milestone")
    now = tp.get("value_now")
    target = tp.get("milestone_value")

    msg = f"{_merchant_name(merchant)}, you're at {now}" if now is not None else f"{_merchant_name(merchant)}, you've hit a milestone"
    if target is not None:
        msg += f" toward {target}"
    msg += f" on {metric.replace('_', ' ')}. Want me to draft a simple post to use the milestone?"
    return msg


def _curiosity(merchant, trigger):
    tp = trigger.get("payload", {}) or {}
    ask = tp.get("ask_template")
    if ask == "what_service_in_demand_this_week":
        return f"{_merchant_name(merchant)}, quick one: what service are customers asking you for most this week? I can use that to suggest the next offer/post."
    return f"{_merchant_name(merchant)}, quick question based on your current business activity: what are customers asking for most right now?"


def _planning(merchant, trigger):
    tp = trigger.get("payload", {}) or {}
    topic = tp.get("intent_topic") or tp.get("program") or tp.get("program_topic")
    last = tp.get("merchant_last_message")

    msg = f"{_merchant_name(merchant)}, picking up your earlier plan"
    if topic:
        msg += f" for {str(topic).replace('_', ' ')}"
    if last:
        msg += f" — you said, “{last}”"
    msg += ". Want me to turn that into a concrete draft?"
    return msg


def _seasonal(merchant, trigger):
    tp = trigger.get("payload", {}) or {}
    event = tp.get("festival") or tp.get("season") or tp.get("event") or "the upcoming period"
    days = tp.get("days_until")
    msg = f"{_merchant_name(merchant)}, {event} is coming"
    if days is not None:
        msg += f" in {days} days"
    msg += ". Want me to suggest one category-appropriate offer or post using what you already have?"
    return msg


def _situational(merchant, trigger):
    tp = trigger.get("payload", {}) or {}
    event = tp.get("event") or tp.get("match") or tp.get("condition") or tp.get("headline")
    city = tp.get("city") or _dig(merchant, "identity", "city")
    msg = f"{_merchant_name(merchant)}, {event or 'a local event'} is happening"
    if city:
        msg += f" in {city}"
    msg += ". Want me to suggest one relevant action for today?"
    return msg


def _customer_recall(category, merchant, trigger, customer):
    tp = trigger.get("payload", {}) or {}
    name = _dig(customer, "identity", "name") or "there"
    last_visit = tp.get("last_service_date") or tp.get("last_visit")
    due = tp.get("due_date")
    service = tp.get("service_due") or "your recall"

    offer = _active_offer(merchant)
    slots = tp.get("available_slots") or []

    msg = f"Hi {name}, {_full_business_name(merchant)} here. "
    if last_visit:
        msg += f"Your last visit was {last_visit}; your {str(service).replace('_', ' ')} is now due."
    else:
        msg += f"Your {str(service).replace('_', ' ')} is now due."
    if due:
        msg += f" The recall date is {due}."
    if slots:
        labels = [s.get("label") for s in slots[:2] if isinstance(s, dict) and s.get("label")]
        if labels:
            msg += " Apke liye " + " ya ".join(labels) + " available hain."
    if offer:
        msg += f" {offer}."
    msg += " Which time works for you?"
    return msg


def _customer_winback(category, merchant, trigger, customer):
    tp = trigger.get("payload", {}) or {}
    name = _dig(customer, "identity", "name") or "there"
    days = tp.get("days_since_last_visit") or tp.get("days_lapsed")
    offer = _active_offer(merchant)

    msg = f"Hi {name}, {_full_business_name(merchant)} here. "
    if days is not None:
        msg += f"It’s been {days} days since your last visit. "
    else:
        msg += "We haven't seen you in a while. "
    if offer:
        msg += f"We currently have {offer}. "
    msg += "Would you like me to help you find a convenient time?"
    return msg


def _appointment(merchant, trigger, customer):
    tp = trigger.get("payload", {}) or {}
    name = _dig(customer, "identity", "name") or "there"
    slot = tp.get("appointment_time") or tp.get("slot") or tp.get("slot_label")
    service = tp.get("service") or tp.get("service_name")

    msg = f"Hi {name}, {_full_business_name(merchant)} here. Reminder"
    if service:
        msg += f" for your {service} appointment"
    else:
        msg += " for your appointment"
    if slot:
        msg += f" at {slot}"
    msg += ". Please reply if you need to reschedule."
    return msg


def _customer_refill(merchant, trigger, customer):
    tp = trigger.get("payload", {}) or {}
    name = _dig(customer, "identity", "name") or "there"
    item = tp.get("medicine") or tp.get("medication") or tp.get("item")
    due = tp.get("due_date")

    msg = f"Hi {name}, {_full_business_name(merchant)} here. "
    if item:
        msg += f"Your refill reminder for {item}"
    else:
        msg += "Your refill reminder"
    if due:
        msg += f" is due {due}"
    msg += ". Please confirm if you'd like us to help with the refill."
    return msg


def _customer_trial(merchant, trigger, customer):
    tp = trigger.get("payload", {}) or {}
    name = _dig(customer, "identity", "name") or "there"
    service = tp.get("service") or tp.get("trial_service") or "your trial"
    msg = f"Hi {name}, {_full_business_name(merchant)} here. Following up on {service}"
    if tp.get("next_step"):
        msg += f" — the next step is {tp['next_step']}"
    msg += ". Would you like us to help schedule it?"
    return msg


def _customer_wedding(merchant, trigger, customer):
    tp = trigger.get("payload", {}) or {}
    name = _dig(customer, "identity", "name") or "there"
    wedding = tp.get("wedding_date")
    next_step = tp.get("next_step_window_open")
    msg = f"Hi {name}, {_full_business_name(merchant)} here. "
    if wedding:
        msg += f"With your wedding on {wedding}"
    else:
        msg += "With your wedding coming up"
    if next_step:
        msg += f", your next step window for {str(next_step).replace('_', ' ')} is open"
    msg += ". Would you like us to plan the next step?"
    return msg


def _generic_merchant(merchant, trigger):
    kind = trigger.get("kind", "update").replace("_", " ")
    return f"{_merchant_name(merchant)}, I spotted a {kind} update relevant to your business. Want me to take the next step with you?"



def _regulation(category, merchant, trigger):
    payload = trigger.get("payload", {}) or {}
    item_id = payload.get("top_item_id")
    deadline = payload.get("deadline_iso")
    item = None
    for d in _dig(category, "digest", default=[]) or []:
        if isinstance(d, dict) and d.get("id") == item_id:
            item = d
            break
    msg = f"{_merchant_name(merchant)}, a regulation update relevant to your category just landed"
    if item and item.get("title"):
        msg += f": {item['title']}"
    if deadline:
        msg += f". Deadline: {deadline}"
    msg += ". Want me to pull the source and turn the requirement into a short checklist?"
    return msg


def _cde(category, merchant, trigger):
    payload = trigger.get("payload", {}) or {}
    item_id = payload.get("digest_item_id")
    credits = payload.get("credits")
    fee = payload.get("fee")
    item = None
    for d in _dig(category, "digest", default=[]) or []:
        if isinstance(d, dict) and d.get("id") == item_id:
            item = d
            break
    msg = f"{_merchant_name(merchant)}, there's a category learning opportunity"
    if item and item.get("title"):
        msg += f": {item['title']}"
    if credits is not None:
        msg += f" — {credits} credits"
    if fee:
        msg += f", {str(fee).replace('_', ' ')}"
    msg += ". Want me to pull the details?"
    return msg


def _profile_problem(merchant, trigger):
    payload = trigger.get("payload", {}) or {}
    uplift = payload.get("estimated_uplift_pct")
    path = payload.get("verification_path")
    msg = f"{_merchant_name(merchant)}, your Google Business Profile is still unverified"
    if uplift is not None:
        msg += f"; the supplied estimate is up to {_pct(uplift)} uplift"
    if path:
        msg += f" via {str(path).replace('_', ' ')}"
    msg += ". Want me to walk you through the verification step?"
    return msg


def _supply(merchant, trigger):
    payload = trigger.get("payload", {}) or {}
    molecule = payload.get("molecule")
    batches = payload.get("affected_batches") or []
    manufacturer = payload.get("manufacturer")
    msg = f"{_merchant_name(merchant)}, there's a supply alert for {molecule or 'an item'}"
    if manufacturer:
        msg += f" from {manufacturer}"
    if batches:
        msg += f" affecting {', '.join(map(str, batches))}"
    msg += ". Want me to help turn the alert into a stock-check action?"
    return msg


def _seasonal_perf(merchant, trigger):
    payload = trigger.get("payload", {}) or {}
    metric = payload.get("metric", "performance")
    delta = payload.get("delta_pct")
    note = payload.get("season_note")
    msg = f"{_merchant_name(merchant)}, your {metric} is down"
    if delta is not None:
        msg += f" {_pct(abs(delta))}"
    msg += ", but the supplied context marks this as an expected seasonal dip"
    if note:
        msg += f" ({str(note).replace('_', ' ')})"
    msg += ". Want me to suggest a low-risk seasonal action rather than overreacting to the dip?"
    return msg


def _merchant_winback(merchant, trigger):
    payload = trigger.get("payload", {}) or {}
    days = payload.get("days_since_expiry")
    added = payload.get("lapsed_customers_added_since_expiry")
    dip = payload.get("perf_dip_pct")
    msg = f"{_merchant_name(merchant)}, your win-back window is worth looking at"
    if days is not None:
        msg += f" — {days} days since expiry"
    if added is not None:
        msg += f" and {added} lapsed customers have been added since then"
    if dip is not None:
        msg += f"; performance is down {_pct(abs(dip))}"
    msg += ". Want me to draft one win-back action?"
    return msg

# ---------------------------------------------------------------------------
# Main composition
# ---------------------------------------------------------------------------

def compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
) -> dict:
    kind = str(trigger.get("kind", "")).lower()
    scope = trigger.get("scope", "customer" if customer else "merchant")

    # Guard against mismatched context.
    if scope == "customer" and customer is None:
        # We cannot personalize a customer-facing message without customer data.
        # Keep it truthful rather than inventing identity.
        body = _generic_merchant(merchant, trigger)
        send_as = "vera"
    else:
        strategy = TRIGGER_STRATEGIES.get(kind)

        if strategy == "knowledge" and kind != "cde_opportunity":
            body = _research(category, merchant, trigger)
        elif kind == "cde_opportunity":
            body = _cde(category, merchant, trigger)
        elif strategy == "performance_problem":
            body = _performance_problem(category, merchant, trigger)
        elif strategy == "performance_win":
            body = _performance_win(category, merchant, trigger)
        elif strategy == "renewal":
            body = _renewal(merchant, trigger)
        elif strategy == "compliance":
            body = _regulation(category, merchant, trigger)
        elif strategy == "knowledge" and kind == "cde_opportunity":
            body = _cde(category, merchant, trigger)
        elif strategy == "competitive":
            body = _competitor(merchant, trigger)
        elif strategy == "review_problem":
            body = _review_problem(merchant, trigger)
        elif strategy == "milestone":
            body = _milestone(merchant, trigger)
        elif strategy == "curiosity":
            body = _curiosity(merchant, trigger)
        elif strategy == "planning":
            body = _planning(merchant, trigger)
        elif strategy in {"seasonal"}:
            body = _seasonal(merchant, trigger)
        elif strategy == "situational":
            body = _situational(merchant, trigger)
        elif strategy == "customer_recall":
            body = _customer_recall(category, merchant, trigger, customer)
        elif strategy == "customer_winback":
            body = _customer_winback(category, merchant, trigger, customer)
        elif strategy == "appointment":
            body = _appointment(merchant, trigger, customer)
        elif strategy == "customer_refill":
            body = _customer_refill(merchant, trigger, customer)
        elif strategy == "customer_trial":
            body = _customer_trial(merchant, trigger, customer)
        elif strategy == "customer_wedding":
            body = _customer_wedding(merchant, trigger, customer)
        elif strategy == "profile_problem":
            body = _profile_problem(merchant, trigger)
        elif strategy == "supply":
            body = _supply(merchant, trigger)
        elif strategy == "performance_context":
            body = _seasonal_perf(merchant, trigger)
        elif strategy == "winback_merchant":
            body = _merchant_winback(merchant, trigger)
        else:
            body = _generic_merchant(merchant, trigger)

        send_as = _send_as(trigger, customer)

    # Basic safety/quality cleanup.
    body = re.sub(r"\s+", " ", str(body)).strip()
    body = body.replace("  ", " ")

    # Keep the output deterministic and grounded.
    return {
        "body": body,
        "cta": _cta_for(kind, scope),
        "send_as": send_as,
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": (
            f"Used the {kind or 'trigger'} as the primary reason for contact, "
            "then anchored the message on available merchant/customer facts "
            "without inventing missing data."
        ),
    }


if __name__ == "__main__":
    # Tiny smoke test using the challenge's supplied seed structure.
    demo_merchant = {
        "merchant_id": "m_demo",
        "category_slug": "dentists",
        "identity": {"owner_first_name": "Meera", "name": "Dr. Meera's Dental Clinic"},
        "performance": {"ctr": 0.021},
        "offers": [{"title": "Dental Cleaning @ ₹299", "status": "active"}],
    }
    demo_trigger = {
        "kind": "perf_dip",
        "scope": "merchant",
        "payload": {"metric": "calls", "delta_pct": -0.40, "window": "7d"},
        "suppression_key": "demo",
    }
    print(compose({"slug": "dentists"}, demo_merchant, demo_trigger))
