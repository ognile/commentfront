"""
Gemini Observations Store - Persistent storage for AI debugging observations.
Stores last 75 observations to JSON file on /data volume.
"""
import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

logger = logging.getLogger("GeminiObservationsStore")

MAX_OBSERVATIONS = 75  # User requested 75


class GeminiObservationsStore:
    """Persistent store for Gemini AI observations."""

    def __init__(self, file_path: str = None):
        self.file_path = file_path or os.getenv(
            "GEMINI_OBSERVATIONS_PATH",
            os.path.join(os.path.dirname(__file__), "gemini_observations.json")
        )
        self.observations: List[Dict] = []
        self._load()

    def _load(self):
        """Load observations from disk."""
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, "r") as f:
                    data = json.load(f)
                    self.observations = data.get("observations", [])
                logger.info(f"Loaded {len(self.observations)} Gemini observations from {self.file_path}")
            else:
                self.observations = []
        except Exception as e:
            logger.error(f"Failed to load observations: {e}")
            self.observations = []

    def _save(self):
        """Save observations to disk with backup."""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)

            # Backup before write
            if os.path.exists(self.file_path):
                backup_path = self.file_path + ".backup"
                with open(self.file_path, "r") as f:
                    backup_data = f.read()
                with open(backup_path, "w") as f:
                    f.write(backup_data)

            data = {
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "count": len(self.observations),
                "observations": self.observations
            }
            with open(self.file_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save observations: {e}")

    def add_observation(
        self,
        screenshot_name: str,
        operation_type: str,
        prompt_type: str,
        full_response: str,
        parsed_result: Dict[str, Any],
        profile_name: Optional[str] = None,
        campaign_id: Optional[str] = None
    ):
        """
        Add a new observation and persist.

        Args:
            screenshot_name: Name of the screenshot file
            operation_type: Stage (verify_state, find_element, check_restriction, verify_comment)
            prompt_type: What prompt was used
            full_response: Full AI response text
            parsed_result: Parsed result dict
            profile_name: Which profile was being used
            campaign_id: Which campaign this belongs to
        """
        observation = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "screenshot_name": screenshot_name,
            "operation_type": operation_type,
            "prompt_type": prompt_type,
            "full_response": full_response,
            "parsed_result": parsed_result,
            "profile_name": profile_name,
            "campaign_id": campaign_id
        }

        self.observations.append(observation)

        # Keep only last MAX_OBSERVATIONS
        if len(self.observations) > MAX_OBSERVATIONS:
            self.observations = self.observations[-MAX_OBSERVATIONS:]

        self._save()

        # Also log for Railway logs
        logger.info(f"[GEMINI] {operation_type}/{prompt_type} | {profile_name} | {screenshot_name}: {full_response[:200]}...")

    def get_recent(self, limit: int = 75) -> List[Dict]:
        """Get recent observations (most recent first)."""
        return list(reversed(self.observations[-limit:]))

    def clear(self) -> int:
        """Clear all observations. Returns count cleared."""
        count = len(self.observations)
        self.observations = []
        self._save()
        return count


# Singleton instance
_store: Optional[GeminiObservationsStore] = None


def get_observations_store() -> GeminiObservationsStore:
    """Get or create the observations store singleton."""
    global _store
    if _store is None:
        _store = GeminiObservationsStore()
    return _store
