"""Starter funnels for the five core industries. Copy is generic on purpose: no fake reviews,
statistics, licences or guarantees. Bracketed text marks what the client must supply."""

from __future__ import annotations

from typing import Any

_BASE_FIELDS = [
    {"name": "full_name", "label": "Your name", "type": "text", "required": True},
    {"name": "phone", "label": "Phone", "type": "tel", "required": True},
    {"name": "email", "label": "Email", "type": "email", "required": False},
]

_FOLLOW_UP = [
    {"delay_minutes": 0, "action": "notify_owner", "description": "Alert the business owner (SMS/email) with the lead details"},
    {"delay_minutes": 5, "action": "send_acknowledgement", "description": "Send an approved acknowledgement email to the lead"},
    {"delay_minutes": 1440, "action": "follow_up_task", "description": "Create a follow-up task if the lead has not been contacted"},
]


def _t(key: str, name: str, industry: str, headline: str, sub: str, benefits: list[str], cta: str,
       service_label: str, services: list[str], conversion: str) -> dict[str, Any]:
    return {
        "key": key, "name": name, "industry": industry, "target_conversion": conversion,
        "call_to_action": cta,
        "lead_fields": [*_BASE_FIELDS,
                        {"name": "service", "label": service_label, "type": "select", "required": True, "options": services},
                        {"name": "message", "label": "Anything we should know?", "type": "textarea", "required": False}],
        "form_config": {
            "submit_label": cta, "success_message": "Thanks! We'll contact you shortly.",
            "consent_text": "By submitting, you agree that [Business name] may contact you about your request. "
                            "We never sell your information.",
            "copy": {"headline": headline, "subheadline": sub, "benefits": benefits,
                     "proof": "[Add a real customer review or licence number here, or remove this line]",
                     "business_name": "[Business name]", "phone_display": "[Business phone]"},
        },
        "follow_up_workflow": {"steps": _FOLLOW_UP},
    }


TEMPLATES: dict[str, dict[str, Any]] = {t["key"]: t for t in [
    _t("hvac", "HVAC service request", "HVAC",
       "Heating or cooling trouble? Get it fixed fast.",
       "Local technicians for repairs, tune-ups and new systems in [service area].",
       ["Upfront pricing before work starts", "Repairs, maintenance and replacements", "Flexible appointment times"],
       "Request service", "What do you need?", ["Repair", "Maintenance / tune-up", "New system quote", "Other"],
       "Service request submitted"),
    _t("plumbing", "Plumbing service request", "Plumbing",
       "Leaks, clogs or no hot water? We can help.",
       "Licensed plumbers serving [service area].",
       ["Clear quotes before we start", "Drains, water heaters, leaks and fixtures", "Clean, respectful technicians"],
       "Book a plumber", "What's the problem?", ["Leak", "Clogged drain", "Water heater", "Other"],
       "Booking request submitted"),
    _t("roofing", "Roof inspection request", "Roofing",
       "Protect your home with a roof inspection.",
       "Inspections, repairs and replacements in [service area].",
       ["Written inspection findings", "Help with storm-damage documentation", "Repair or replacement options"],
       "Request an inspection", "What do you need?", ["Inspection", "Repair", "Replacement quote", "Other"],
       "Inspection request submitted"),
    _t("accounting", "Accounting consultation", "Accounting",
       "Spend less time on the books.",
       "Bookkeeping, tax preparation and advisory for small businesses in [service area].",
       ["Monthly bookkeeping and reports", "Business and personal tax preparation", "A dedicated point of contact"],
       "Book a consultation", "Which service?", ["Bookkeeping", "Tax preparation", "Payroll", "Advisory"],
       "Consultation booked"),
    _t("real_estate", "Home valuation request", "Real estate",
       "What is your home worth today?",
       "Get a local market analysis from [Agent name], serving [service area].",
       ["Based on recent local sales", "No-obligation conversation", "Guidance on timing and preparation"],
       "Get my valuation", "I'm looking to…", ["Sell", "Buy", "Sell and buy", "Just curious"],
       "Valuation requested"),
]}


def summary() -> list[dict[str, str]]:
    return [{"key": t["key"], "name": t["name"], "industry": t["industry"]} for t in TEMPLATES.values()]
