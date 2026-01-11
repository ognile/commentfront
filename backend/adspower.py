import requests
import time
import logging

class AdsPowerClient:
    def __init__(self, api_url="http://local.adspower.net:50325"):
        self.api_url = api_url
        self.logger = logging.getLogger("AdsPowerClient")

    def check_status(self):
        try:
            resp = requests.get(f"{self.api_url}/status", timeout=2)
            return resp.status_code == 200
        except requests.exceptions.ConnectionError:
            return False

    def get_profile_list(self, group_id=None, page=1, page_size=50):
        """
        Fetches the list of profiles currently in AdsPower.
        """
        if not self.check_status():
            self.logger.info("AdsPower Local API not found. Returning empty list.")
            return []

        try:
            # Query the Local API for the user list
            url = f"{self.api_url}/api/v1/user/list?page={page}&page_size={page_size}"
            if group_id:
                url += f"&group_id={group_id}"
                
            resp = requests.get(url).json()
            
            if resp["code"] != 0:
                self.logger.error(f"Error fetching profiles: {resp['msg']}")
                return []
                
            return resp["data"]["list"]
        except Exception as e:
            self.logger.error(f"Failed to get profile list: {e}")
            return []

    def start_profile(self, user_id):
        """
        Starts the browser profile and returns the WebSocket Endpoint.
        """
        if not self.check_status():
            self.logger.warning("AdsPower Local API not found. Returning MOCK endpoint.")
            return {"ws_endpoint": "ws://mock-endpoint", "mock": True}

        try:
            url = f"{self.api_url}/api/v1/browser/start?user_id={user_id}"
            resp = requests.get(url).json()
            
            if resp["code"] != 0:
                raise Exception(f"AdsPower Error: {resp['msg']}")
            
            return {
                "ws_endpoint": resp["data"]["ws"]["puppeteer"],
                "mock": False
            }
        except Exception as e:
            self.logger.error(f"Failed to launch profile {user_id}: {e}")
            raise e

    def stop_profile(self, user_id):
        if not self.check_status():
            self.logger.info(f"Mock stop profile {user_id}")
            return True

        try:
            url = f"{self.api_url}/api/v1/browser/stop?user_id={user_id}"
            requests.get(url)
            return True
        except Exception as e:
            self.logger.error(f"Failed to stop profile {user_id}: {e}")
            return False
