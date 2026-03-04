"""
Persistent store for campaign AI product presets.

Products are shared across users. Last-used product is tracked per user.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from safe_io import atomic_write_json, safe_read_json


logger = logging.getLogger("CampaignAIProductStore")


def _utc_iso() -> str:
    return datetime.utcnow().isoformat()


_DEFAULT_GUMMY_PROMPT = """
product context:
nuora vaginal gummy is primarily for vaginal health support.
main problem mechanism: vaginal microbiome imbalance and pH disruption can allow odor/discomfort/discharge patterns to persist.
solution mechanism: supports beneficial vaginal flora balance and healthier vaginal environment; gut-vaginal link can be mentioned as secondary, not primary.

writing intent for this product:
when comments recommend this product, explain why with mechanism language (because/root cause/why it helps), not hype.
keep comments as isolated top-level responses to the op only.
allow mixed ecosystem roles (supportive, testimonial, alternatives, mild contrarian), but keep relevance to op symptoms/problem.
avoid diagnosis/cure promises and avoid over-absolute claims.
""".strip()


_DEFAULT_CAPSULE_PROMPT = """
product context:
nuora gut capsule is primarily for gut health support.
main problem mechanism: gut microbiome imbalance can drive bloating, irregular digestion, stool inconsistency, and discomfort patterns.
solution mechanism: supports microbiome balance and digestive stability through gut-focused support.

writing intent for this product:
when comments recommend this product, explain why with mechanism language (because/root cause/why it helps), not hype.
keep comments as isolated top-level responses to the op only.
allow mixed ecosystem roles (supportive, testimonial, alternatives, mild contrarian), but keep relevance to op problem.
avoid diagnosis/cure promises and avoid over-absolute claims.
""".strip()


def _seed_product_payloads() -> List[Dict[str, str]]:
    return [
        {
            "name": "nuora vaginal gummy",
            "prompt": _DEFAULT_GUMMY_PROMPT,
        },
        {
            "name": "nuora gut capsule",
            "prompt": _DEFAULT_CAPSULE_PROMPT,
        },
    ]


class CampaignAIProductStore:
    """Store and manage AI product presets and per-user last selections."""

    def __init__(self, file_path: Optional[str] = None):
        self.file_path = file_path or self._default_path()
        self._lock = threading.RLock()
        self.state = self._load()
        self._seed_defaults_if_needed()

    def _default_path(self) -> str:
        configured = os.getenv("CAMPAIGN_AI_PRODUCTS_PATH")
        if configured:
            return configured

        data_dir = os.getenv("DATA_DIR", "/data")
        preferred = os.path.join(data_dir, "campaign_ai_products.json")
        try:
            os.makedirs(os.path.dirname(preferred), exist_ok=True)
            return preferred
        except Exception:
            return os.path.join(os.path.dirname(__file__), "campaign_ai_products.json")

    def _empty_state(self) -> Dict:
        return {
            "updated_at": _utc_iso(),
            "products": {},
            "user_defaults": {},
        }

    def _load(self) -> Dict:
        data = safe_read_json(self.file_path)
        if not isinstance(data, dict):
            return self._empty_state()

        baseline = self._empty_state()
        baseline.update(data)
        baseline.setdefault("products", {})
        baseline.setdefault("user_defaults", {})
        return baseline

    def save(self) -> bool:
        with self._lock:
            self.state["updated_at"] = _utc_iso()
            return atomic_write_json(self.file_path, self.state)

    def _seed_defaults_if_needed(self) -> None:
        with self._lock:
            products = self.state.setdefault("products", {})
            has_active = any(bool(item.get("active", True)) for item in products.values())
            if has_active:
                return

            now = _utc_iso()
            for payload in _seed_product_payloads():
                seed_key = f"campaign_ai_product:{payload['name'].strip().lower()}"
                product_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, seed_key))
                products[product_id] = {
                    "id": product_id,
                    "name": payload["name"],
                    "prompt": payload["prompt"],
                    "active": True,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": "system_seed",
                    "updated_by": "system_seed",
                }

            self.save()

    def list_products(self, *, include_inactive: bool = False) -> List[Dict]:
        with self._lock:
            items = list((self.state.get("products") or {}).values())

        if not include_inactive:
            items = [item for item in items if bool(item.get("active", True))]

        items.sort(key=lambda x: str(x.get("name") or "").lower())
        return items

    def get_product(self, product_id: str, *, include_inactive: bool = False) -> Optional[Dict]:
        key = str(product_id or "").strip()
        if not key:
            return None
        with self._lock:
            item = (self.state.get("products") or {}).get(key)
            if not item:
                return None
            if not include_inactive and not bool(item.get("active", True)):
                return None
            return dict(item)

    def create_product(self, *, name: str, prompt: str, username: str) -> Dict:
        normalized_name = str(name or "").strip()
        normalized_prompt = str(prompt or "").strip()
        actor = str(username or "").strip() or "unknown"
        if not normalized_name:
            raise ValueError("name is required")
        if not normalized_prompt:
            raise ValueError("prompt is required")

        with self._lock:
            now = _utc_iso()
            product_id = str(uuid.uuid4())
            item = {
                "id": product_id,
                "name": normalized_name,
                "prompt": normalized_prompt,
                "active": True,
                "created_at": now,
                "updated_at": now,
                "created_by": actor,
                "updated_by": actor,
            }
            self.state.setdefault("products", {})[product_id] = item
            self.save()
            return dict(item)

    def update_product(
        self,
        product_id: str,
        *,
        name: Optional[str],
        prompt: Optional[str],
        active: Optional[bool],
        username: str,
    ) -> Optional[Dict]:
        key = str(product_id or "").strip()
        actor = str(username or "").strip() or "unknown"
        if not key:
            return None

        with self._lock:
            existing = (self.state.get("products") or {}).get(key)
            if not existing:
                return None

            if name is not None:
                normalized_name = str(name).strip()
                if not normalized_name:
                    raise ValueError("name cannot be empty")
                existing["name"] = normalized_name
            if prompt is not None:
                normalized_prompt = str(prompt).strip()
                if not normalized_prompt:
                    raise ValueError("prompt cannot be empty")
                existing["prompt"] = normalized_prompt
            if active is not None:
                existing["active"] = bool(active)

            existing["updated_at"] = _utc_iso()
            existing["updated_by"] = actor
            self.save()
            return dict(existing)

    def deactivate_product(self, product_id: str, *, username: str) -> bool:
        updated = self.update_product(
            product_id,
            name=None,
            prompt=None,
            active=False,
            username=username,
        )
        return updated is not None

    def get_last_product_id(self, username: str) -> Optional[str]:
        key = str(username or "").strip().lower()
        if not key:
            return None
        with self._lock:
            item = (self.state.get("user_defaults") or {}).get(key) or {}
            product_id = str(item.get("last_product_id") or "").strip()
            return product_id or None

    def set_last_product_id(self, username: str, product_id: str) -> None:
        user_key = str(username or "").strip().lower()
        product_key = str(product_id or "").strip()
        if not user_key or not product_key:
            return
        with self._lock:
            self.state.setdefault("user_defaults", {})[user_key] = {
                "last_product_id": product_key,
                "updated_at": _utc_iso(),
            }
            self.save()


_store_singleton: Optional[CampaignAIProductStore] = None


def get_campaign_ai_product_store() -> CampaignAIProductStore:
    global _store_singleton
    if _store_singleton is None:
        _store_singleton = CampaignAIProductStore()
    return _store_singleton

