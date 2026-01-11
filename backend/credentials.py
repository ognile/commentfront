import os
import logging

class CredentialManager:
    def __init__(self, file_path="accounts info.txt"):
        self.file_path = file_path
        self.credentials = {}
        self.logger = logging.getLogger("CredentialManager")
        self.load_credentials()

    def load_credentials(self):
        if not os.path.exists(self.file_path):
            self.logger.warning(f"Credential file not found at {self.file_path}")
            return

        try:
            with open(self.file_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    parts = line.split(":")
                    if len(parts) >= 3:
                        # Format: UID:PASS:2FA
                        uid = parts[0].strip()
                        password = parts[1].strip()
                        secret = parts[2].strip()
                        
                        self.credentials[uid] = {
                            "uid": uid,
                            "password": password,
                            "secret": secret
                        }
            self.logger.info(f"Loaded {len(self.credentials)} credentials.")
        except Exception as e:
            self.logger.error(f"Failed to parse credentials: {e}")

    def get_credential(self, search_term):
        """
        Tries to find credentials by matching the search_term (Profile Name or ID)
        with the stored UIDs.
        """
        # Direct match
        if search_term in self.credentials:
            return self.credentials[search_term]
            
        # Partial match (if AdsPower name contains the UID)
        for uid, creds in self.credentials.items():
            if uid in search_term:
                return creds
                
        return None
