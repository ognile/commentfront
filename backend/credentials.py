import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pyotp


SUPPORTED_PLATFORMS = {"facebook", "reddit"}


def _default_reddit_profile_name(username: str) -> str:
    return f"reddit_{str(username or '').strip().lower()}"


def _default_reddit_profile_url(username: str) -> str:
    return f"https://www.reddit.com/user/{str(username or '').strip()}/"


def _reddit_source_tag(source_label: Optional[str]) -> Optional[str]:
    raw_value = str(source_label or "").strip()
    if not raw_value:
        return None

    stem = Path(raw_value).stem or Path(raw_value).name or raw_value
    normalized = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    if not normalized:
        return None
    return f"source_{normalized}"


def _resolve_reddit_tags(
    *,
    tags: Optional[List[str]],
    fixture: bool,
    source_label: Optional[str],
) -> List[str]:
    resolved: List[str] = []
    base = list(tags or ["reddit"])

    for tag in base:
        value = str(tag or "").strip().lower()
        if value and value not in resolved:
            resolved.append(value)

    if "reddit" not in resolved:
        resolved.insert(0, "reddit")

    source_tag = _reddit_source_tag(source_label)
    if source_tag and source_tag not in resolved:
        resolved.append(source_tag)

    if fixture and "fixture" not in resolved:
        resolved.append("fixture")

    return resolved


class CredentialManager:
    def __init__(self, file_path=None):
        self.file_path = file_path or os.getenv(
            "CREDENTIALS_PATH",
            os.path.join(os.path.dirname(__file__), "credentials.json"),
        )
        self.credentials: Dict[str, dict] = {}
        self.logger = logging.getLogger("CredentialManager")
        self.load_credentials()

    def _normalize_platform(self, platform: Optional[str]) -> str:
        value = str(platform or "facebook").strip().lower()
        return value if value in SUPPORTED_PLATFORMS else "facebook"

    def _storage_key(self, uid: str, platform: str) -> str:
        normalized_uid = str(uid or "").strip()
        if platform == "facebook":
            return normalized_uid
        return f"{platform}::{normalized_uid}"

    def _find_storage_key(self, identifier: str, platform: Optional[str] = None) -> Optional[str]:
        needle = str(identifier or "").strip()
        if not needle:
            return None

        normalized_platform = self._normalize_platform(platform)
        if platform:
            if needle.startswith(f"{normalized_platform}::") and needle in self.credentials:
                return needle
            candidate = self._storage_key(needle, normalized_platform)
            if candidate in self.credentials:
                return candidate

        if needle in self.credentials:
            return needle

        for key, record in self.credentials.items():
            record_platform = self._normalize_platform(record.get("platform"))
            if platform and record_platform != normalized_platform:
                continue
            if needle in {
                str(record.get("uid") or "").strip(),
                str(record.get("username") or "").strip(),
                str(record.get("email") or "").strip(),
                key,
            }:
                return key
        return None

    def _normalize_record(self, storage_key: str, record: dict) -> dict:
        platform = self._normalize_platform(record.get("platform"))
        uid = str(record.get("uid") or "").strip()
        username = str(record.get("username") or uid).strip()
        secret = str(record.get("totp_secret") or record.get("secret") or "").strip() or None

        normalized = {
            "credential_id": storage_key,
            "platform": platform,
            "uid": uid or username,
            "username": username or uid,
            "password": record.get("password"),
            "secret": secret,
            "totp_secret": secret,
            "profile_name": record.get("profile_name"),
            "display_name": record.get("display_name") or record.get("profile_name"),
            "email": record.get("email"),
            "email_password": record.get("email_password"),
            "profile_url": record.get("profile_url"),
            "tags": list(record.get("tags") or []),
            "fixture": bool(record.get("fixture", False)),
            "linked_session_id": record.get("linked_session_id"),
            "metadata": dict(record.get("metadata") or {}),
            "created_at": record.get("created_at") or datetime.utcnow().isoformat(),
            "updated_at": record.get("updated_at"),
        }

        if platform == "reddit":
            normalized["display_name"] = normalized["display_name"] or normalized["username"]
        else:
            normalized["display_name"] = normalized["display_name"] or normalized["uid"]

        return normalized

    def load_credentials(self):
        """Load credentials from JSON file."""
        from safe_io import safe_read_json

        data = safe_read_json(self.file_path)
        if data is None:
            self.logger.warning(f"Credential file not found at {self.file_path}")
            self.credentials = {}
            return

        try:
            raw_credentials = data.get("credentials", {})
            normalized: Dict[str, dict] = {}
            for storage_key, record in raw_credentials.items():
                normalized[storage_key] = self._normalize_record(storage_key, record)
            self.credentials = normalized
            self.logger.info(f"Loaded {len(self.credentials)} credentials.")
        except Exception as e:
            self.logger.error(f"Failed to parse credentials: {e}")
            self.credentials = {}

    def save_credentials(self):
        """Save credentials to JSON file."""
        from safe_io import atomic_write_json

        payload = {
            "updated_at": datetime.utcnow().isoformat(),
            "credentials": self.credentials,
        }
        if not atomic_write_json(self.file_path, payload):
            self.logger.error(f"Failed to save credentials atomically to {self.file_path}")
            return
        self.logger.info(f"Saved {len(self.credentials)} credentials.")

    def add_credential(
        self,
        uid,
        password,
        secret=None,
        profile_name=None,
        *,
        platform: str = "facebook",
        email: Optional[str] = None,
        email_password: Optional[str] = None,
        username: Optional[str] = None,
        profile_url: Optional[str] = None,
        display_name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        fixture: bool = False,
        linked_session_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        """
        Add or update a credential.

        `uid` remains the canonical lookup field for backward compatibility.
        For Reddit, `uid` should be the username.
        """
        normalized_platform = self._normalize_platform(platform)
        normalized_uid = str(uid or username or email or "").strip()
        if not normalized_uid:
            raise ValueError("uid is required")

        storage_key = self._find_storage_key(normalized_uid, normalized_platform)
        if storage_key is None:
            storage_key = self._storage_key(normalized_uid, normalized_platform)

        existing = self.credentials.get(storage_key, {})
        if existing:
            self.logger.warning(f"Credential for {storage_key} already exists, updating...")

        record = self._normalize_record(
            storage_key,
            {
                **existing,
                "platform": normalized_platform,
                "uid": normalized_uid,
                "username": username or existing.get("username") or normalized_uid,
                "password": password,
                "secret": secret,
                "totp_secret": secret,
                "profile_name": profile_name or existing.get("profile_name"),
                "display_name": display_name or existing.get("display_name") or profile_name,
                "email": email if email is not None else existing.get("email"),
                "email_password": email_password if email_password is not None else existing.get("email_password"),
                "profile_url": profile_url if profile_url is not None else existing.get("profile_url"),
                "tags": tags if tags is not None else existing.get("tags", []),
                "fixture": bool(existing.get("fixture", False) or fixture),
                "linked_session_id": linked_session_id if linked_session_id is not None else existing.get("linked_session_id"),
                "metadata": metadata if metadata is not None else existing.get("metadata", {}),
                "created_at": existing.get("created_at") or datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            },
        )

        self.credentials[storage_key] = record
        self.save_credentials()
        self.logger.info(f"Added credential for {storage_key}")
        return storage_key

    def import_reddit_account_line(
        self,
        line: str,
        *,
        profile_name: Optional[str] = None,
        fixture: bool = False,
        tags: Optional[List[str]] = None,
        source_label: Optional[str] = None,
    ) -> str:
        """
        Import a Reddit account from either:
        username:password:email:email_password
        username:password:email:email_password:totp_secret:profile_url
        """
        raw = str(line or "").strip()
        if not raw:
            raise ValueError("Empty Reddit credential line")

        if raw.count(":") == 3:
            username, password, email, email_password = [part.strip() for part in raw.split(":", 3)]
            totp_secret = ""
            profile_url = ""
        else:
            parts = raw.split(":", 5)
            if len(parts) != 6:
                raise ValueError("Expected 4 or 6 Reddit credential fields")
            username, password, email, email_password, totp_secret, profile_url = [part.strip() for part in parts]

        if not username or not password or not email:
            raise ValueError("Missing required Reddit credential fields")

        inferred_profile_name = profile_name or _default_reddit_profile_name(username)
        resolved_profile_url = profile_url or _default_reddit_profile_url(username)
        return self.add_credential(
            uid=username,
            username=username,
            password=password,
            secret=totp_secret or None,
            profile_name=inferred_profile_name,
            platform="reddit",
            email=email,
            email_password=email_password or None,
            profile_url=resolved_profile_url,
            display_name=username,
            tags=_resolve_reddit_tags(tags=tags, fixture=fixture, source_label=source_label),
            fixture=fixture,
        )

    def get_credential(self, identifier, platform: Optional[str] = None):
        """Get a credential by uid/username/email/credential_id."""
        storage_key = self._find_storage_key(identifier, platform)
        if not storage_key:
            return None
        record = dict(self.credentials.get(storage_key) or {})
        if not record:
            return None
        record["credential_id"] = storage_key
        return record

    def delete_credential(self, identifier, platform: Optional[str] = None):
        """Delete a credential."""
        storage_key = self._find_storage_key(identifier, platform)
        if storage_key and storage_key in self.credentials:
            del self.credentials[storage_key]
            self.save_credentials()
            self.logger.info(f"Deleted credential for {storage_key}")
            return True
        return False

    def update_profile_name(self, identifier, profile_name, platform: Optional[str] = None):
        """Update the profile name for a credential."""
        storage_key = self._find_storage_key(identifier, platform)
        if storage_key and storage_key in self.credentials:
            self.credentials[storage_key]["profile_name"] = profile_name
            self.credentials[storage_key]["display_name"] = profile_name
            self.credentials[storage_key]["updated_at"] = datetime.utcnow().isoformat()
            self.save_credentials()
            self.logger.info(f"Updated profile name for {storage_key} to: {profile_name}")
            return True
        return False

    def set_linked_session_id(self, identifier, linked_session_id: Optional[str], platform: Optional[str] = None) -> bool:
        """Link a credential to a session/profile identifier."""
        storage_key = self._find_storage_key(identifier, platform)
        if not storage_key:
            return False
        self.credentials[storage_key]["linked_session_id"] = linked_session_id
        self.credentials[storage_key]["updated_at"] = datetime.utcnow().isoformat()
        self.save_credentials()
        return True

    def get_all_credentials(self, platform: Optional[str] = None):
        """Get all credentials without raw passwords."""
        normalized_platform = self._normalize_platform(platform) if platform else None
        output = []
        for storage_key, record in self.credentials.items():
            if normalized_platform and self._normalize_platform(record.get("platform")) != normalized_platform:
                continue
            output.append(
                {
                    "credential_id": storage_key,
                    "uid": record.get("uid"),
                    "platform": record.get("platform"),
                    "username": record.get("username"),
                    "email": record.get("email"),
                    "profile_name": record.get("profile_name"),
                    "display_name": record.get("display_name"),
                    "profile_url": record.get("profile_url"),
                    "tags": list(record.get("tags") or []),
                    "fixture": bool(record.get("fixture", False)),
                    "linked_session_id": record.get("linked_session_id"),
                    "has_secret": bool(record.get("totp_secret")),
                    "created_at": record.get("created_at"),
                    "updated_at": record.get("updated_at"),
                }
            )
        return output

    def _totp_secret_for_record(self, record: Optional[dict]) -> Optional[str]:
        if not record:
            return None
        return str(record.get("totp_secret") or record.get("secret") or "").strip() or None

    def generate_otp(self, identifier, platform: Optional[str] = None):
        """
        Generate current OTP code for a credential.
        """
        cred = self.get_credential(identifier, platform)
        if not cred:
            return {"code": None, "remaining_seconds": 0, "valid": False, "error": "Credential not found"}

        secret = self._totp_secret_for_record(cred)
        if not secret:
            return {"code": None, "remaining_seconds": 0, "valid": False, "error": "No 2FA secret configured"}

        try:
            normalized_secret = secret.replace(" ", "").replace("-", "").upper()
            totp = pyotp.TOTP(normalized_secret)
            code = totp.now()
            remaining = totp.interval - datetime.now().timestamp() % totp.interval

            return {
                "code": code,
                "remaining_seconds": int(remaining),
                "valid": True,
            }
        except Exception as e:
            self.logger.error(f"Failed to generate OTP for {identifier}: {e}")
            return {"code": None, "remaining_seconds": 0, "valid": False, "error": str(e)}

    def verify_otp(self, identifier, code, platform: Optional[str] = None):
        """Verify an OTP code."""
        cred = self.get_credential(identifier, platform)
        if not cred:
            return {"valid": False, "error": "Credential not found"}

        secret = self._totp_secret_for_record(cred)
        if not secret:
            return {"valid": False, "error": "No 2FA secret configured"}

        try:
            normalized_secret = secret.replace(" ", "").replace("-", "").upper()
            totp = pyotp.TOTP(normalized_secret)
            valid = totp.verify(code, valid_window=1)
            return {"valid": valid}
        except Exception as e:
            self.logger.error(f"Failed to verify OTP for {identifier}: {e}")
            return {"valid": False, "error": str(e)}
