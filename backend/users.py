"""
User Management for CommentBot
Simple JSON-based storage for small team authentication.
"""
import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from auth import get_password_hash, verify_password

logger = logging.getLogger("UserManager")

USERS_FILE = Path(os.getenv("USERS_PATH", str(Path(__file__).parent / "users.json")))


class UserManager:
    """
    Manages user accounts with JSON file storage.
    Similar pattern to CredentialManager for consistency.
    """

    def __init__(self):
        self.users: dict = {}
        self.load_users()
        self._ensure_admin_exists()

    def load_users(self):
        """Load users from JSON file."""
        if not USERS_FILE.exists():
            logger.info("Users file not found, starting with empty users")
            self.users = {}
            return

        try:
            with open(USERS_FILE, "r") as f:
                data = json.load(f)
                self.users = data.get("users", {})
            logger.info(f"Loaded {len(self.users)} users")
        except Exception as e:
            logger.error(f"Failed to load users: {e}")
            self.users = {}

    def save_users(self):
        """Save users to JSON file."""
        try:
            data = {
                "updated_at": datetime.utcnow().isoformat(),
                "users": self.users
            }
            with open(USERS_FILE, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved {len(self.users)} users")
        except Exception as e:
            logger.error(f"Failed to save users: {e}")

    def _ensure_admin_exists(self):
        """Create initial admin user if no users exist."""
        if self.users:
            return

        admin_username = os.getenv("INITIAL_ADMIN_USERNAME", "admin")
        admin_password = os.getenv("INITIAL_ADMIN_PASSWORD")

        if not admin_password:
            logger.warning("No INITIAL_ADMIN_PASSWORD set - using default 'changeme123'")
            admin_password = "changeme123"

        self.create_user(admin_username, admin_password, role="admin")
        logger.info(f"Created initial admin user: {admin_username}")

    def create_user(self, username: str, password: str, role: str = "user") -> Optional[dict]:
        """
        Create a new user account.

        Args:
            username: Unique username
            password: Plain text password (will be hashed)
            role: 'admin' or 'user' (default: 'user')

        Returns:
            User dict without password if created, None if username exists
        """
        # Case-insensitive username check
        if username.lower() in [u.lower() for u in self.users.keys()]:
            logger.warning(f"Username {username} already exists")
            return None

        self.users[username] = {
            "username": username,
            "hashed_password": get_password_hash(password),
            "role": role,
            "created_at": datetime.utcnow().isoformat(),
            "last_login": None,
            "is_active": True
        }
        self.save_users()
        logger.info(f"Created user: {username} with role: {role}")
        return self.get_user(username)

    def get_user(self, username: str) -> Optional[dict]:
        """
        Get user by username (without password hash).

        Args:
            username: Username to lookup

        Returns:
            User dict without hashed_password, or None if not found
        """
        user = self.users.get(username)
        if user:
            return {k: v for k, v in user.items() if k != "hashed_password"}
        return None

    def get_user_with_password(self, username: str) -> Optional[dict]:
        """
        Get user by username including password hash.
        Only use internally for authentication.

        Args:
            username: Username to lookup

        Returns:
            Full user dict including hashed_password
        """
        return self.users.get(username)

    def authenticate(self, username: str, password: str) -> Optional[dict]:
        """
        Authenticate a user with username and password.

        Args:
            username: Username
            password: Plain text password

        Returns:
            User dict (without password) if authenticated, None otherwise
        """
        user = self.get_user_with_password(username)
        if not user:
            logger.warning(f"Authentication failed: user {username} not found")
            return None

        if not user.get("is_active"):
            logger.warning(f"Authentication failed: user {username} is inactive")
            return None

        if not verify_password(password, user["hashed_password"]):
            logger.warning(f"Authentication failed: invalid password for {username}")
            return None

        # Update last login
        self.users[username]["last_login"] = datetime.utcnow().isoformat()
        self.save_users()

        logger.info(f"User {username} authenticated successfully")
        return self.get_user(username)

    def change_password(self, username: str, new_password: str) -> bool:
        """
        Change a user's password.

        Args:
            username: Username
            new_password: New plain text password

        Returns:
            True if changed, False if user not found
        """
        if username not in self.users:
            return False

        self.users[username]["hashed_password"] = get_password_hash(new_password)
        self.save_users()
        logger.info(f"Password changed for user: {username}")
        return True

    def update_role(self, username: str, role: str) -> bool:
        """
        Update a user's role.

        Args:
            username: Username
            role: New role ('admin' or 'user')

        Returns:
            True if updated, False if user not found
        """
        if username not in self.users:
            return False

        if role not in ("admin", "user"):
            logger.error(f"Invalid role: {role}")
            return False

        self.users[username]["role"] = role
        self.save_users()
        logger.info(f"Role updated for user {username}: {role}")
        return True

    def delete_user(self, username: str) -> bool:
        """
        Delete a user account.

        Args:
            username: Username to delete

        Returns:
            True if deleted, False if not found
        """
        if username not in self.users:
            return False

        del self.users[username]
        self.save_users()
        logger.info(f"Deleted user: {username}")
        return True

    def list_users(self) -> List[dict]:
        """
        Get all users (without passwords).

        Returns:
            List of user dicts
        """
        return [self.get_user(username) for username in self.users.keys()]

    def count_admins(self) -> int:
        """
        Count the number of admin users.

        Returns:
            Number of users with admin role
        """
        return sum(1 for user in self.users.values() if user.get("role") == "admin")

    def is_last_admin(self, username: str) -> bool:
        """
        Check if a user is the last admin.

        Args:
            username: Username to check

        Returns:
            True if this is the only admin user
        """
        user = self.users.get(username)
        if not user or user.get("role") != "admin":
            return False
        return self.count_admins() == 1


# Singleton instance - import this in other modules
user_manager = UserManager()
