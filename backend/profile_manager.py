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
        """Load state from disk."""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, "r") as f:
                    self.state = json.load(f)
                logger.info(f"Loaded profile state from {self.state_file} with {len(self.state.get('profiles', {}))} profiles")
        except Exception as e:
            logger.error(f"Failed to load profile state: {e}")
            self.state = {"profiles": {}}

    def _save_state(self):
        """Save state to disk."""
        try:
            # Backup before write
            if os.path.exists(self.state_file):
                backup_path = self.state_file + ".backup"
                with open(self.state_file, "r") as f:
                    backup_data = f.read()
                with open(backup_path, "w") as f:
                    f.write(backup_data)

            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
            logger.debug(f"Saved profile state to {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to save profile state: {e}")

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
        return self.state["profiles"].get(profile_name)

    def get_all_profiles(self) -> Dict[str, Dict]:
        """Get all profile states."""
        return self.state["profiles"]

    def get_profiles_by_priority(
        self,
        filter_tags: Optional[List[str]] = None,
        count: int = 10,
        sessions: Optional[List[Dict]] = None
    ) -> List[str]:
        """
        Get profiles sorted by priority (LRU - least recently used first).

        Args:
            filter_tags: Optional list of tags to filter by
            count: Number of profiles to return
            sessions: List of session dicts (with profile_name, valid, tags)

        Returns:
            List of profile names in priority order
        """
        # Check restrictions and auto-expire them
        self._check_restriction_expiry()

        # Get valid profile names
        valid_profiles = []

        if sessions:
            for session in sessions:
                if not session.get("valid", False):
                    continue

                profile_name = session.get("profile_name") or session.get("file", "").replace(".json", "")

                # Apply tag filter if specified
                if filter_tags:
                    session_tags = session.get("tags", [])
                    if not any(tag in session_tags for tag in filter_tags):
                        continue

                # Skip restricted profiles
                profile_state = self.state["profiles"].get(profile_name, {})
                if profile_state.get("status") == "restricted":
                    logger.info(f"Skipping restricted profile: {profile_name}")
                    continue

                valid_profiles.append(profile_name)

        # Sort by last_used_at (oldest first, None = never used = highest priority)
        def sort_key(name):
            state = self.state["profiles"].get(name, {})
            last_used = state.get("last_used_at")
            if last_used is None:
                return ""  # Empty string sorts before any date
            return last_used

        sorted_profiles = sorted(valid_profiles, key=sort_key)

        logger.info(f"Profile priority order: {sorted_profiles[:count]}")
        return sorted_profiles[:count]

    def mark_profile_used(
        self,
        profile_name: str,
        campaign_id: Optional[str] = None,
        comment: Optional[str] = None,
        success: bool = True
    ):
        """
        Mark a profile as used (updates last_used_at, moves to back of queue).

        Args:
            profile_name: The profile name
            campaign_id: Optional campaign ID for tracking
            comment: Optional comment text for history
            success: Whether the comment was successful
        """
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

        now = datetime.utcnow()
        profile = self.state["profiles"][profile_name]

        # Update last used timestamp
        profile["last_used_at"] = now.isoformat() + "Z"
        profile["usage_count"] = profile.get("usage_count", 0) + 1

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

        # Add to usage history (keep last 20)
        if "usage_history" not in profile:
            profile["usage_history"] = []

        profile["usage_history"].append({
            "timestamp": now.isoformat() + "Z",
            "campaign_id": campaign_id,
            "comment": (comment[:100] + "...") if comment and len(comment) > 100 else comment,
            "success": success
        })
        profile["usage_history"] = profile["usage_history"][-20:]

        logger.info(f"Marked profile {profile_name} as used (count: {profile['usage_count']})")
        self._save_state()

    def mark_profile_restricted(
        self,
        profile_name: str,
        hours: int = 24,
        reason: str = "unknown"
    ):
        """
        Mark a profile as restricted for a duration.

        Args:
            profile_name: The profile name
            hours: How many hours to restrict (default 24)
            reason: Reason for restriction (moderation_notice, comment_ban, manual)
        """
        if profile_name not in self.state["profiles"]:
            self.state["profiles"][profile_name] = {
                "last_used_at": None,
                "usage_count": 0,
                "status": "active",
                "daily_stats": {},
                "usage_history": []
            }

        now = datetime.utcnow()
        expires_at = now + timedelta(hours=hours)

        profile = self.state["profiles"][profile_name]
        profile["status"] = "restricted"
        profile["restriction_expires_at"] = expires_at.isoformat() + "Z"
        profile["restriction_reason"] = reason

        # Track restriction in history
        if "restriction_history" not in profile:
            profile["restriction_history"] = []
        profile["restriction_history"].append({
            "timestamp": now.isoformat() + "Z",
            "reason": reason,
            "duration_hours": hours
        })
        profile["restriction_history"] = profile["restriction_history"][-10:]

        logger.warning(f"Restricted profile {profile_name} for {hours}h (reason: {reason})")
        self._save_state()

    def unblock_profile(self, profile_name: str):
        """Manually unblock a restricted profile."""
        if profile_name not in self.state["profiles"]:
            return

        profile = self.state["profiles"][profile_name]
        profile["status"] = "active"
        profile["restriction_expires_at"] = None
        profile["restriction_reason"] = None

        logger.info(f"Unblocked profile: {profile_name}")
        self._save_state()

    def extend_restriction(self, profile_name: str, additional_hours: int):
        """Extend an existing restriction."""
        if profile_name not in self.state["profiles"]:
            return

        profile = self.state["profiles"][profile_name]

        if profile.get("status") != "restricted":
            return

        current_expires = profile.get("restriction_expires_at")
        if current_expires:
            expires_dt = datetime.fromisoformat(current_expires.replace("Z", "+00:00"))
            new_expires = expires_dt + timedelta(hours=additional_hours)
        else:
            new_expires = datetime.utcnow() + timedelta(hours=additional_hours)

        profile["restriction_expires_at"] = new_expires.isoformat().replace("+00:00", "") + "Z"
        logger.info(f"Extended restriction for {profile_name} by {additional_hours}h")
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
            "restriction_expires_at": profile.get("restriction_expires_at"),
            "restriction_reason": profile.get("restriction_reason"),
            "total_comments": total_comments,
            "success_rate": (total_success / total_comments * 100) if total_comments > 0 else 0,
            "daily_stats": daily_stats,
            "usage_history": profile.get("usage_history", [])[-10:],  # Last 10
            "restriction_history": profile.get("restriction_history", [])
        }


# Singleton instance
_profile_manager: Optional[ProfileManager] = None


def get_profile_manager() -> ProfileManager:
    """Get or create the profile manager singleton."""
    global _profile_manager
    if _profile_manager is None:
        _profile_manager = ProfileManager()
    return _profile_manager
