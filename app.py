from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, Dict, Any
from pathlib import Path
import json
from bot import compose

app = FastAPI(title="Vera AI Challenge Bot", version="1.0.2")

ROOT = Path(__file__).parent
DATA_DIRS = [ROOT / "data", ROOT]

CATEGORIES = {}
_seen = set()
for data_dir in DATA_DIRS:
    if not data_dir.exists():
        continue
    for p in data_dir.glob("*.json"):
        if p in _seen:
            continue
        _seen.add(p)
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("slug"):
                CATEGORIES[d["slug"]] = d
        except Exception:
            pass


class ComposeRequest(BaseModel):
    category: Dict[str, Any]
    merchant: Dict[str, Any]
    trigger: Dict[str, Any]
    customer: Optional[Dict[str, Any]] = None


@app.get("/healthz")
def healthz():
    return {"status": "ok", "categories_loaded": sorted(CATEGORIES)}


@app.get("/metadata")
def metadata():
    return {"name": "vera-composer", "version": "1.0.2", "categories": sorted(CATEGORIES)}


def _customer_language(customer: Optional[Dict[str, Any]]) -> str:
    if not customer:
        return ""
    identity = customer.get("identity") or {}
    return str(identity.get("language_pref") or "").strip().lower()


def _respect_customer_language(result: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Remove accidental Hinglish fragments when the customer explicitly prefers English."""
    if _customer_language(customer) not in {"en", "english", "en-in"}:
        return result

    body = str(result.get("body", ""))
    replacements = {
        "Apke liye ": "We have ",
        " available hain.": " available.",
        " ya ": " or ",
    }
    for old, new in replacements.items():
        body = body.replace(old, new)
    result["body"] = body
    return result


@app.post("/compose")
def compose_api(req: ComposeRequest):
    result = compose(req.category, req.merchant, req.trigger, req.customer)
    return _respect_customer_language(result, req.customer)
