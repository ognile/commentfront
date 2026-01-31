"""
Profile Manager Module
Manages profile rotation state and priority queue for fair profile usage.
Uses LRU (Least Recently Used) strategy to rotate profiles.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger("ProfileManager")


@dataclass
class ProfileState:
    """State for a single profile."""
    last_used_at: Optional[str] = None  # ISO timestamp
    usage_count: int = 0
    status: str = "active"  # active | restricted | cooldown
    restriction_expires_at: Optional[str] = None
    restriction_reason: Optional[str] = None
    daily_stats: Dict[str, Dict[str, int]] = None  # {date: {comments, success, failed}}
    usage_history: List[Dict[str, Any]] = None  # Last 20 usage records

    def __post_init__(self):
        if self.daily_stats is None:
            self.daily_stats = {}
        if self.usage_history is None:
            self.usage_history = []


class ProfileManager:
    """Manages profile rotation and usage tracking."""

    def __init__(self, state_file: str = None, sessions_dir: str = None):
        # Use env vars for Railway persistent volume, fallback to local paths
        self.state_file = state_file or os.getenv(
            "PROFILE_STATE_PATH",
            os.path.join(os.path.dirname(__file__), "profile_state.json")
        )
        self.sessions_dir = sessions_dir or os.getenv(
            "SESSIONS_DIR",
            os.path.join(os.path.dirname(__file__), "sessions")
        )
        self.state: Dict[str, Dict] = {"profiles": {}}
        self._load_state()
        self._sync_with_sessions()

    def _load_state(self):
        """Load state from disk with automatic recovery from backup."""
        from safe_io import safe_read_json
        data = safe_read_json(self.state_file, default={"profiles": {}})
        self.state = data
        logger.info(f"Loaded profile state from {self.state_file} with {len(self.state.get('profiles', {}))} profiles")

    def _save_state(self):
        """Save state to disk atomically."""
        from safe_io import atomic_write_json
        if not atomic_write_json(self.state_file, self.state):
            logger.error(f"Failed to save profile state atomically")

    def _normalize_name(self, name: str) -> str:
        """Normalize profile name to match session filename format."""
        return name.replace(" ", "_").replace("/", "_").lower()

    def _sync_with_sessions(self):
        """Sync state with actual session files on disk."""
        try:
            if not os.path.exists(self.sessions_dir):
                return

            # Get list of session files
            session_files = [f for f in os.listdir(self.sessions_dir) if f.endswith(".json")]

            # Add any new sessions to state
            for session_file in session_files:
                profile_name = session_file.replace(".json", "")
                if profile_name not in self.state["profiles"]:
                    self.state["profiles"][profile_name] = {
                        "last_used_at": None,
                        "usage_count": 0,
                        "status": "active",
                        "restriction_expires_at": None,
                        "restriction_reason": None,
                        "daily_stats": {},
                        "usage_history": []
                    }
                    logger.info(f"Added new profile to state: {profile_name}")

            # Remove profiles that no longer have session files
            profiles_to_remove = []
            for profile_name in self.state["profiles"]:
                session_path = os.path.join(self.sessions_dir, f"{profile_name}.json")
                if not os.path.exists(session_path):
                    profiles_to_remove.append(profile_name)

            for profile_name in profiles_to_remove:
                del self.state["profiles"][profile_name]
                logger.info(f"Removed missing profile from state: {profile_name}")

            if profiles_to_remove:
                self._save_state()

        except Exception as e:
            logger.error(f"Failed to sync with sessions: {e}")

    def get_profile_state(self, profile_name: str) -> Optional[Dict]:
        """Get state for a specific profile."""
        normalized = self._normalize_name(profile_name)
        return self.state["profiles"].get(normalized)

    def get_all_profiles(self) -> Dict[str, Dict]:
        """Get all profile states."""
        return self.state["profiles"]

    def get_eligible_profiles(
        self,
        filter_tags: Optional[List[str]] = None,
        count: int = 1,
        sessions: Optional[List[Dict]] = None,
        exclude_profiles: Optional[List[str]] = None
    ) -> List[str]:
        """
        UNIFIED profile selection - ALL features must use this.

        Selection criteria (applied in order):
        1. Must have valid cookies
        2. Must match ALL filter_tags (AND logic)
        3. Must NOT be restricted (or restriction expired)
        4. Sorted by last_used_at (least recently SUCCESSFULLY used first)

        Args:
            filter_tags: Tags that profiles must have (AND logic - must match ALL)
            count: Number of profiles to return
            sessions: Pre-loaded sessions list (optional, loads from fb_session if not provided)
            exclude_profiles: Profile names to skip (e.g., already assigned)

        Returns:
            List of profile names in LRU order (least recently successfully used first)
        """
        # Import here to avoid circular imports
        from fb_session import list_saved_sessions

        if sessions is None:
            sessions = list_saved_sessions()

        exclude_set = set(exclude_profiles or [])
        eligible = []

        # Track skip reasons for debugging
        skip_reasons = {
            "no_cookies": 0,
            "tag_mismatch": 0,
            "restricted": 0,
            "auto_burned": 0,
            "excluded": 0
        }

        for session in sessions:
            profile_name = session.get("profile_name")

            # Skip excluded profiles
            if profile_name in exclude_set:
                skip_reasons["excluded"] += 1
                continue

            # Must have valid cookies
            if not session.get("has_valid_cookies", False):
                skip_reasons["no_cookies"] += 1
                continue

            # Must match ALL tags (AND logic)
            if filter_tags:
                session_tags = session.get("tags", [])
                if not all(tag in session_tags for tag in filter_tags):
                    skip_reasons["tag_mismatch"] += 1
                    continue

            # Must not be restricted (check and auto-expire if needed)
            state = self.get_profile_state(profile_name) or {}
            if state.get("status") == "restricted":
                expires_at = state.get("restriction_expires_at")
                if expires_at:
                    try:
                        expires_dt = datetime.fromisoformat(expires_at.replace("Z", ""))
                        if datetime.utcnow() < expires_dt:
                            # Still restricted, skip
                            skip_reasons["restricted"] += 1
                            continue
                        else:
                            # Restriction expired, auto-unblock
                            self._clear_restriction(profile_name)
                    except Exception as e:
                        # Invalid date, skip to be safe
                        logger.warning(f"Invalid restriction date for {profile_name}: {e}")
                        skip_reasons["restricted"] += 1
                        continue
                else:
                    # Restricted with no expiry, skip
                    skip_reasons["restricted"] += 1
                    continue

            # Auto-restrict profiles with very low success rates
            total_attempts = state.get("usage_count", 0)
            if total_attempts >= 10:
                daily_stats = state.get("daily_stats", {})
                total_success = sum(d.get("success", 0) for d in daily_stats.values())
                success_rate = total_success / total_attempts
                if success_rate < 0.10:
                    self.mark_profile_restricted(
                        profile_name,
                        reason=f"auto-burned: {total_success}/{total_attempts} success rate ({success_rate:.0%})"
                    )
                    skip_reasons["auto_burned"] += 1
                    continue

            eligible.append({
                "profile_name": profile_name,
                "last_used_at": state.get("last_used_at")  # None = never used = highest priority
            })

        # Sort by LRU (None/oldest first - never used profiles get highest priority)
        eligible.sort(key=lambda x: x["last_used_at"] or "")

        result = [p["profile_name"] for p in eligible[:count]]
        logger.info(
            f"Profile selection: {len(result)}/{len(eligible)} eligible, "
            f"skipped: {skip_reasons}, tags={filter_tags}"
        )
        return result

    def _clear_restriction(self, profile_name: str):
        """Clear restriction on a profile (internal use for auto-expiry)."""
        if profile_name in self.state["profiles"]:
            self.state["profiles"][profile_name]["status"] = "active"
            self.state["profiles"][profile_name]["restriction_expires_at"] = None
            self.state["profiles"][profile_name]["restriction_reason"] = None
            logger.info(f"Auto-unblocked profile {profile_name} (restriction expired)")
            self._save_state()

    def mark_profile_used(
        self,
        profile_name: str,
        campaign_id: Optional[str] = None,
        comment: Optional[str] = None,
        success: bool = True,
        failure_type: Optional[str] = None  # "restriction", "infrastructure", "facebook_error", None
    ):
        """
        Mark a profile as used. Only updates LRU timestamp on SUCCESS.

        Args:
            profile_name: The profile name
            campaign_id: Optional campaign ID for tracking
            comment: Optional comment text for history
            success: Whether the comment was successful
            failure_type: Type of failure for analytics granularity:
                - "restriction": Profile got restricted/throttled
                - "infrastructure": Timeout, proxy, connection issues
                - "facebook_error": UI issues, element not found, etc.
                - None: Success or unknown failure
        """
        normalized = self._normalize_name(profile_name)
        if normalized not in self.state["profiles"]:
            self.state["profiles"][normalized] = {
                "last_used_at": None,
                "usage_count": 0,
                "status": "active",
                "restriction_expires_at": None,
                "restriction_reason": None,
                "daily_stats": {},
                "usage_history": [],
                "failure_breakdown": {}
            }

        now = datetime.utcnow()
        profile = self.state["profiles"][normalized]

        # Always increment usage count (for total attempts tracking)
        profile["usage_count"] = profile.get("usage_count", 0) + 1

        # ONLY update LRU timestamp on SUCCESS
        # This ensures failed attempts don't push profile to back of queue
        if success:
            profile["last_used_at"] = now.isoformat() + "Z"

        # Update daily stats
        today = now.strftime("%Y-%m-%d")
        if "daily_stats" not in profile:
            profile["daily_stats"] = {}
        if today not in profile["daily_stats"]:
            profile["daily_stats"][today] = {"comments": 0, "success": 0, "failed": 0}

        profile["daily_stats"][today]["comments"] += 1
        if success:
            profile["daily_stats"][today]["success"] += 1
        else:
            profile["daily_stats"][today]["failed"] += 1

        # Track failure types separately for analytics granularity
        if failure_type:
            if "failure_breakdown" not in profile:
                profile["failure_breakdown"] = {}
            profile["failure_breakdown"][failure_type] = profile["failure_breakdown"].get(failure_type, 0) + 1

        # Add to usage history (keep last 20)
        if "usage_history" not in profile:
            profile["usage_history"] = []

        history_entry = {
            "timestamp": now.isoformat() + "Z",
            "campaign_id": campaign_id,
            "comment": (comment[:100] + "...") if comment and len(comment) > 100 else comment,
            "success": success
        }
        if failure_type:
            history_entry["failure_type"] = failure_type

        profile["usage_history"].append(history_entry)
        profile["usage_history"] = profile["usage_history"][-20:]

        failure_desc = failure_type or "unknown"
        log_msg = f"Profile {normalized}: {'SUCCESS' if success else f'FAILED ({failure_desc})'}"
        if success:
            logger.info(log_msg)
        else:
            logger.warning(log_msg)

        self._save_state()

    def mark_profile_restricted(
        self,
        profile_name: str,
        hours: int = 0,
        reason: str = "unknown"
    ):
        """
        Mark a profile as restricted with progressive escalation.

        Escalation ladder (based on restriction_count):
        - 1st offense: 24 hours
        - 2nd offense: 72 hours (3 days)
        - 3rd offense: 168 hours (7 days)
        - 4th+ offense: 720 hours (30 days)

        Args:
            profile_name: The profile name
            hours: Override duration (0 = use escalation ladder)
            reason: Reason for restriction
        """
        normalized = self._normalize_name(profile_name)
        if normalized not in self.state["profiles"]:
            self.state["profiles"][normalized] = {
                "last_used_at": None,
                "usage_count": 0,
                "status": "active",
                "daily_stats": {},
                "usage_history": []
            }

        now = datetime.utcnow()
        profile = self.state["profiles"][normalized]

        # Increment restriction count for escalation
        restriction_count = profile.get("restriction_count", 0) + 1
        profile["restriction_count"] = restriction_count

        # Use escalation ladder unless caller explicitly overrides
        if hours == 0:
            if restriction_count >= 4:
                hours = 720   # 30 days
            elif restriction_count == 3:
                hours = 168   # 7 days
            elif restriction_count == 2:
                hours = 72    # 3 days
            else:
                hours = 24    # first offense

        expires_at = now + timedelta(hours=hours)

        profile["status"] = "restricted"
        profile["restriction_expires_at"] = expires_at.isoformat() + "Z"
        profile["restriction_reason"] = reason

        # Track restriction in history
        if "restriction_history" not in profile:
            profile["restriction_history"] = []
        profile["restriction_history"].append({
            "timestamp": now.isoformat() + "Z",
            "reason": reason,
            "duration_hours": hours,
            "restriction_count": restriction_count
        })
        profile["restriction_history"] = profile["restriction_history"][-10:]

        logger.warning(f"Restricted profile {normalized} for {hours}h (reason: {reason}, offense #{restriction_count})")
        self._save_state()

    def unblock_profile(self, profile_name: str):
        """Manually unblock a restricted profile. Resets restriction_count and appeal state."""
        normalized = self._normalize_name(profile_name)
        if normalized not in self.state["profiles"]:
            return

        profile = self.state["profiles"][normalized]
        profile["status"] = "active"
        profile["restriction_expires_at"] = None
        profile["restriction_reason"] = None
        profile["restriction_count"] = 0  # Reset escalation on manual unblock
        # Reset appeal state
        profile["appeal_status"] = "none"
        profile["appeal_attempts"] = 0
        profile["appeal_last_error"] = None

        logger.info(f"Unblocked profile: {normalized} (restriction_count + appeal state reset)")
        self._save_state()

    def extend_restriction(self, profile_name: str, additional_hours: int):
        """Extend an existing restriction."""
        normalized = self._normalize_name(profile_name)
        if normalized not in self.state["profiles"]:
            return

        profile = self.state["profiles"][normalized]

        if profile.get("status") != "restricted":
            return

        current_expires = profile.get("restriction_expires_at")
        if current_expires:
            expires_dt = datetime.fromisoformat(current_expires.replace("Z", "+00:00"))
            new_expires = expires_dt + timedelta(hours=additional_hours)
        else:
            new_expires = datetime.utcnow() + timedelta(hours=additional_hours)

        profile["restriction_expires_at"] = new_expires.isoformat().replace("+00:00", "") + "Z"
        logger.info(f"Extended restriction for {normalized} by {additional_hours}h")
        self._save_state()

    def _check_restriction_expiry(self):
        """Check and auto-expire restrictions that have passed."""
        now = datetime.utcnow()
        changed = False

        for profile_name, profile in self.state["profiles"].items():
            if profile.get("status") == "restricted":
                expires_at = profile.get("restriction_expires_at")
                if expires_at:
                    try:
                        expires_dt = datetime.fromisoformat(expires_at.replace("Z", ""))
                        if now > expires_dt:
                            profile["status"] = "active"
                            profile["restriction_expires_at"] = None
                            profile["restriction_reason"] = None
                            logger.info(f"Auto-unblocked profile {profile_name} (restriction expired)")
                            changed = True
                    except Exception as e:
                        logger.error(f"Error parsing expiry date for {profile_name}: {e}")

        if changed:
            self._save_state()

    def get_analytics_summary(self) -> Dict:
        """Get summary analytics for all profiles."""
        self._check_restriction_expiry()

        today = datetime.utcnow().strftime("%Y-%m-%d")
        week_start = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")

        today_comments = 0
        today_success = 0
        week_comments = 0
        week_success = 0
        restricted_count = 0
        active_count = 0

        for profile_name, profile in self.state["profiles"].items():
            if profile.get("status") == "restricted":
                restricted_count += 1
            else:
                active_count += 1

            daily_stats = profile.get("daily_stats", {})

            # Today's stats
            if today in daily_stats:
                today_comments += daily_stats[today].get("comments", 0)
                today_success += daily_stats[today].get("success", 0)

            # Week's stats
            for date, stats in daily_stats.items():
                if date >= week_start:
                    week_comments += stats.get("comments", 0)
                    week_success += stats.get("success", 0)

        return {
            "today": {
                "comments": today_comments,
                "success": today_success,
                "success_rate": (today_success / today_comments * 100) if today_comments > 0 else 0
            },
            "week": {
                "comments": week_comments,
                "success": week_success,
                "success_rate": (week_success / week_comments * 100) if week_comments > 0 else 0
            },
            "profiles": {
                "active": active_count,
                "restricted": restricted_count,
                "total": active_count + restricted_count
            }
        }

    def get_profile_analytics(self, profile_name: str) -> Optional[Dict]:
        """Get detailed analytics for a single profile."""
        profile = self.state["profiles"].get(profile_name)
        if not profile:
            return None

        # Calculate success rate
        daily_stats = profile.get("daily_stats", {})
        total_comments = sum(s.get("comments", 0) for s in daily_stats.values())
        total_success = sum(s.get("success", 0) for s in daily_stats.values())

        return {
            "profile_name": profile_name,
            "status": profile.get("status", "active"),
            "last_used_at": profile.get("last_used_at"),
            "usage_count": profile.get("usage_count", 0),
            "restriction_count": profile.get("restriction_count", 0),
            "restriction_expires_at": profile.get("restriction_expires_at"),
            "restriction_reason": profile.get("restriction_reason"),
            "total_comments": total_comments,
            "success_rate": (total_success / total_comments * 100) if total_comments > 0 else 0,
            "daily_stats": daily_stats,
            "usage_history": profile.get("usage_history", [])[-10:],  # Last 10
            "restriction_history": profile.get("restriction_history", []),
            # Appeal tracking
            "appeal_status": profile.get("appeal_status", "none"),
            "appeal_attempts": profile.get("appeal_attempts", 0),
            "appeal_last_attempt_at": profile.get("appeal_last_attempt_at"),
            "appeal_last_result": profile.get("appeal_last_result"),
            "appeal_last_error": profile.get("appeal_last_error"),
        }


    # === Appeal Management ===

    def get_appealable_profiles(self, max_attempts: int = 3) -> List[str]:
        """Get restricted profiles eligible for appeal."""
        results = []
        for name, state in self.state["profiles"].items():
            if state.get("status") != "restricted":
                continue
            appeal_status = state.get("appeal_status", "none")
            if appeal_status in ("in_review", "exhausted"):
                continue
            if state.get("appeal_attempts", 0) >= max_attempts:
                continue
            results.append(name)
        return results

    def update_appeal_state(
        self,
        profile_name: str,
        result: str,
        error: str = None,
        steps_used: int = 0,
        max_attempts: int = 3
    ):
        """Record an appeal attempt result for a profile."""
        normalized = self._normalize_name(profile_name)
        profile = self.state["profiles"].get(normalized)
        if not profile:
            return

        now = datetime.utcnow()
        attempts = profile.get("appeal_attempts", 0) + 1
        profile["appeal_attempts"] = attempts
        profile["appeal_last_attempt_at"] = now.isoformat() + "Z"
        profile["appeal_last_result"] = result
        profile["appeal_last_error"] = error

        if result == "task_completed":
            profile["appeal_status"] = "in_review"
        elif attempts >= max_attempts:
            profile["appeal_status"] = "exhausted"
        else:
            profile["appeal_status"] = "failed"

        if "appeal_history" not in profile:
            profile["appeal_history"] = []
        profile["appeal_history"].append({
            "timestamp": now.isoformat() + "Z",
            "result": result,
            "error": error,
            "steps_used": steps_used,
            "attempt": attempts
        })
        profile["appeal_history"] = profile["appeal_history"][-10:]

        logger.info(f"Appeal update {normalized}: status={profile['appeal_status']}, attempts={attempts}, result={result}")
        self._save_state()

    def classify_restriction(self, profile_name: str) -> str:
        """Classify restriction type: 'checkpoint', 'expired', or 'comment_restriction'."""
        normalized = self._normalize_name(profile_name)
        profile = self.state["profiles"].get(normalized, {})
        reason = (profile.get("restriction_reason") or "").lower()

        if "human" in reason or "confirm" in reason:
            return "checkpoint"
        if "ended on" in reason:
            return "expired"
        return "comment_restriction"


# Singleton instance
_profile_manager: Optional[ProfileManager] = None


def get_profile_manager() -> ProfileManager:
    """Get or create the profile manager singleton."""
    global _profile_manager
    if _profile_manager is None:
        _profile_manager = ProfileManager()
    return _profile_manager
