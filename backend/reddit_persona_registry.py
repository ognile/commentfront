from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict


REGISTRY_PATH = Path(__file__).resolve().with_name("reddit_persona_registry.json")


def _load_registry() -> Dict[str, Any]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload.get("profiles"), dict) or not payload["profiles"]:
        raise RuntimeError("reddit persona registry is missing profiles")
    return payload


def load_reddit_persona_registry() -> Dict[str, Any]:
    payload = _load_registry()
    payload["registry_version"] = get_reddit_persona_registry_version()
    return payload


def get_reddit_persona_registry_version() -> str:
    return hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()


def get_reddit_persona(profile_name: str) -> Dict[str, Any]:
    registry = _load_registry()
    profiles = dict(registry.get("profiles") or {})
    normalized_name = str(profile_name or "").strip()
    persona = dict(profiles.get(normalized_name) or {})
    if not persona and normalized_name:
        prefixed = [value for key, value in profiles.items() if key.startswith(f"{normalized_name}_")]
        if len(prefixed) == 1:
            persona = dict(prefixed[0])
    if not persona and normalized_name:
        requested_parts = [part for part in normalized_name.split("_") if part]
        for key, value in profiles.items():
            key_parts = [part for part in key.split("_") if part]
            if requested_parts and requested_parts[0] == "reddit" and key_parts[: len(requested_parts)] == requested_parts:
                persona = dict(value)
                break
    if not persona:
        raise KeyError(f"reddit persona not found: {profile_name}")
    persona["registry_version"] = get_reddit_persona_registry_version()
    persona["registry_name"] = registry.get("registry_name")
    persona["approved_scenario"] = registry.get("approved_scenario")
    return persona


def get_reddit_persona_snapshot(profile_name: str) -> Dict[str, Any]:
    persona = get_reddit_persona(profile_name)
    return {
        "registry_name": persona.get("registry_name"),
        "registry_version": persona.get("registry_version"),
        "approved_scenario": persona.get("approved_scenario"),
        "profile_name": persona.get("profile_name"),
        "persona_id": persona.get("persona_id"),
        "default_role": persona.get("default_role"),
        "case_style": persona.get("case_style"),
        "core_signature": persona.get("core_signature"),
        "social_pattern": persona.get("social_pattern"),
        "sentence_shape": persona.get("sentence_shape"),
        "vocabulary_texture": persona.get("vocabulary_texture"),
        "length_band": dict(persona.get("length_band") or {}),
        "opening_pattern_constraints": dict(persona.get("opening_pattern_constraints") or {}),
        "anti_signatures": list(persona.get("anti_signatures") or []),
        "allowed_intensity": persona.get("allowed_intensity"),
    }
