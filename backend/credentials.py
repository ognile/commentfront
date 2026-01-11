import os
import json
import logging
import pyotp
from datetime import datetime

class CredentialManager:
    def __init__(self, file_path=None):
        self.file_path = file_path or os.getenv("CREDENTIALS_PATH", "credentials.json")
        self.credentials = {}
        self.logger = logging.getLogger("CredentialManager")
        self.load_credentials()

    def load_credentials(self):
        """Load credentials from JSON file."""
        if not os.path.exists(self.file_path):
            self.logger.warning(f"Credential file not found at {self.file_path}")
            return

        try:
            with open(self.file_path, "r") as f:
                data = json.load(f)
                self.credentials = data.get("credentials", {})
            self.logger.info(f"Loaded {len(self.credentials)} credentials.")
        except Exception as e:
            self.logger.error(f"Failed to parse credentials: {e}")

    def save_credentials(self):
        """Save credentials to JSON file."""
        try:
            data = {
                "updated_at": datetime.utcnow().isoformat(),
                "credentials": self.credentials
            }
            with open(self.file_path, "w") as f:
                json.dump(data, f, indent=2)
            self.logger.info(f"Saved {len(self.credentials)} credentials.")
        except Exception as e:
            self.logger.error(f"Failed to save credentials: {e}")

    def add_credential(self, uid, password, secret=None, profile_name=None):
        """
        Add a new credential.
        
        Args:
            uid: Facebook UID or email
            password: Facebook password
            secret: 2FA secret (base32 format, optional)
            profile_name: Optional profile name to map to
        """
        if uid in self.credentials:
            self.logger.warning(f"Credential for {uid} already exists, updating...")
        
        self.credentials[uid] = {
            "uid": uid,
            "password": password,
            "secret": secret,
            "profile_name": profile_name,
            "created_at": datetime.utcnow().isoformat()
        }
        self.save_credentials()
        self.logger.info(f"Added credential for {uid}")
        return True

    def get_credential(self, uid):
        """Get a credential by UID."""
        return self.credentials.get(uid)

    def delete_credential(self, uid):
        """Delete a credential by UID."""
        if uid in self.credentials:
            del self.credentials[uid]
            self.save_credentials()
            self.logger.info(f"Deleted credential for {uid}")
            return True
        return False

    def update_profile_name(self, uid, profile_name):
        """Update the profile name for a credential."""
        if uid in self.credentials:
            self.credentials[uid]["profile_name"] = profile_name
            self.save_credentials()
            self.logger.info(f"Updated profile name for {uid} to: {profile_name}")
            return True
        return False

    def get_all_credentials(self):
        """Get all credentials (without passwords)."""
        return [
            {
                "uid": uid,
                "profile_name": cred.get("profile_name"),
                "has_secret": bool(cred.get("secret")),
                "created_at": cred.get("created_at")
            }
            for uid, cred in self.credentials.items()
        ]

    def generate_otp(self, uid):
        """
        Generate current OTP code for a UID.
        
        Args:
            uid: Facebook UID
            
        Returns:
            dict with 'code', 'remaining_seconds', 'valid'
        """
        cred = self.credentials.get(uid)
        if not cred:
            return {"code": None, "remaining_seconds": 0, "valid": False, "error": "UID not found"}
        
        secret = cred.get("secret")
        if not secret:
            return {"code": None, "remaining_seconds": 0, "valid": False, "error": "No 2FA secret configured"}
        
        try:
            totp = pyotp.TOTP(secret)
            code = totp.now()
            
            # Get time remaining in current 30-second window
            remaining = totp.interval - datetime.now().timestamp() % totp.interval
            
            return {
                "code": code,
                "remaining_seconds": int(remaining),
                "valid": True
            }
        except Exception as e:
            self.logger.error(f"Failed to generate OTP for {uid}: {e}")
            return {"code": None, "remaining_seconds": 0, "valid": False, "error": str(e)}

    def verify_otp(self, uid, code):
        """
        Verify an OTP code.
        
        Args:
            uid: Facebook UID
            code: OTP code to verify
            
        Returns:
            dict with 'valid'
        """
        cred = self.credentials.get(uid)
        if not cred:
            return {"valid": False, "error": "UID not found"}
        
        secret = cred.get("secret")
        if not secret:
            return {"valid": False, "error": "No 2FA secret configured"}
        
        try:
            totp = pyotp.TOTP(secret)
            valid = totp.verify(code, valid_window=1)  # Allow 1 step tolerance
            return {"valid": valid}
        except Exception as e:
            self.logger.error(f"Failed to verify OTP for {uid}: {e}")
            return {"valid": False, "error": str(e)}
