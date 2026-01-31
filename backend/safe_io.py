"""
Safe I/O Module — Atomic JSON file operations with backup/restore.

Prevents data corruption from:
- Crashes during writes (temp file + atomic rename)
- Concurrent async access (per-file asyncio locks)
- Data loss (automatic backup before overwrite, restore on corruption)
"""

import asyncio
import json
import logging
import os
import shutil
from typing import Any, Dict, Optional

logger = logging.getLogger("SafeIO")

# Per-file asyncio locks to prevent concurrent writes
_file_locks: Dict[str, asyncio.Lock] = {}


def _get_lock(file_path: str) -> asyncio.Lock:
    """Get or create an asyncio lock for a specific file."""
    if file_path not in _file_locks:
        _file_locks[file_path] = asyncio.Lock()
    return _file_locks[file_path]


def atomic_write_json(file_path: str, data: Any, indent: int = 2) -> bool:
    """
    Write JSON data atomically using temp file + rename.

    Steps:
    1. Backup existing file to .bak
    2. Write to .tmp file
    3. Atomic rename .tmp → target (atomic on Unix)

    If write fails, attempts to restore from backup.

    Args:
        file_path: Path to the JSON file
        data: Data to serialize as JSON
        indent: JSON indentation (default 2)

    Returns:
        True if write succeeded, False otherwise
    """
    tmp_path = file_path + ".tmp"
    bak_path = file_path + ".bak"

    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)

        # 1. Backup existing file
        if os.path.exists(file_path):
            shutil.copy2(file_path, bak_path)

        # 2. Write to temp file
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=indent)

        # 3. Atomic rename
        os.replace(tmp_path, file_path)

        return True

    except Exception as e:
        logger.error(f"Atomic write failed for {file_path}: {e}")

        # Clean up temp file
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        # Restore from backup if target was lost
        if not os.path.exists(file_path) and os.path.exists(bak_path):
            try:
                shutil.copy2(bak_path, file_path)
                logger.info(f"Restored {file_path} from backup after failed write")
            except Exception as restore_err:
                logger.error(f"Failed to restore from backup: {restore_err}")

        return False


def safe_read_json(file_path: str, default: Any = None) -> Any:
    """
    Read JSON with automatic recovery from backup on corruption.

    If the main file is missing or corrupt, tries .bak file.

    Args:
        file_path: Path to the JSON file
        default: Default value if file doesn't exist and no backup

    Returns:
        Parsed JSON data, or default if unrecoverable
    """
    bak_path = file_path + ".bak"

    # Try main file first
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Corrupt JSON in {file_path}: {e}")
            # Fall through to backup

    # Try backup
    if os.path.exists(bak_path):
        try:
            with open(bak_path, "r") as f:
                data = json.load(f)
            logger.info(f"Restored {file_path} from backup")
            # Restore the main file from backup
            try:
                shutil.copy2(bak_path, file_path)
            except Exception:
                pass
            return data
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"Backup also corrupt for {file_path}: {e}")

    return default
