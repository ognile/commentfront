"""
Proxy Manager - CRUD operations for proxy management
Follows the same pattern as credentials.py
"""

import os
import json
import logging
import asyncio
import aiohttp
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse, unquote
import uuid


class ProxyManager:
    def __init__(self, file_path: str = None):
        self.file_path = file_path or os.getenv(
            "PROXIES_PATH",
            os.path.join(os.path.dirname(__file__), "proxies.json")
        )
        self.proxies: Dict[str, dict] = {}
        self.logger = logging.getLogger("ProxyManager")
        self.load_proxies()

    def load_proxies(self):
        """Load proxies from JSON file."""
        if not os.path.exists(self.file_path):
            self.logger.info(f"Proxy file not found at {self.file_path}, starting fresh")
            self.proxies = {}
            return

        try:
            with open(self.file_path, "r") as f:
                data = json.load(f)
                self.proxies = data.get("proxies", {})
            self.logger.info(f"Loaded {len(self.proxies)} proxies.")
        except Exception as e:
            self.logger.error(f"Failed to parse proxies: {e}")
            self.proxies = {}

    def save_proxies(self):
        """Save proxies to JSON file."""
        try:
            data = {
                "updated_at": datetime.utcnow().isoformat(),
                "proxies": self.proxies
            }
            with open(self.file_path, "w") as f:
                json.dump(data, f, indent=2)
            self.logger.info(f"Saved {len(self.proxies)} proxies.")
        except Exception as e:
            self.logger.error(f"Failed to save proxies: {e}")

    def add_proxy(
        self,
        name: str,
        url: str,
        proxy_type: str = "mobile",
        country: str = "US"
    ) -> Dict:
        """
        Add a new proxy.

        Args:
            name: Human-readable name for the proxy
            url: Proxy URL (http://user:pass@host:port)
            proxy_type: Type of proxy (mobile, residential, datacenter)
            country: Country code

        Returns:
            The created proxy object with its ID
        """
        # Generate unique ID
        proxy_id = f"proxy_{uuid.uuid4().hex[:8]}"

        # Parse URL to extract components
        parsed = urlparse(url)

        proxy = {
            "id": proxy_id,
            "name": name,
            "url": url,
            "host": parsed.hostname,
            "port": parsed.port,
            "username": unquote(parsed.username) if parsed.username else None,
            "type": proxy_type,
            "country": country,
            "health_status": "untested",
            "last_tested": None,
            "success_rate": None,
            "avg_response_ms": None,
            "test_count": 0,
            "assigned_sessions": [],
            "created_at": datetime.utcnow().isoformat()
        }

        self.proxies[proxy_id] = proxy
        self.save_proxies()
        self.logger.info(f"Added proxy: {name} ({proxy_id})")

        return proxy

    def get_proxy(self, proxy_id: str) -> Optional[Dict]:
        """Get a proxy by ID."""
        return self.proxies.get(proxy_id)

    def get_proxy_url(self, proxy_id: str) -> Optional[str]:
        """Get the URL for a proxy by ID."""
        proxy = self.proxies.get(proxy_id)
        return proxy.get("url") if proxy else None

    def update_proxy(self, proxy_id: str, updates: Dict) -> Optional[Dict]:
        """
        Update a proxy.

        Args:
            proxy_id: Proxy ID to update
            updates: Dictionary of fields to update

        Returns:
            Updated proxy object or None if not found
        """
        if proxy_id not in self.proxies:
            return None

        # Fields that can be updated
        allowed_fields = {"name", "url", "type", "country"}

        for field in allowed_fields:
            if field in updates:
                self.proxies[proxy_id][field] = updates[field]

        # Re-parse URL if it was updated
        if "url" in updates:
            parsed = urlparse(updates["url"])
            self.proxies[proxy_id]["host"] = parsed.hostname
            self.proxies[proxy_id]["port"] = parsed.port
            self.proxies[proxy_id]["username"] = unquote(parsed.username) if parsed.username else None

        self.proxies[proxy_id]["updated_at"] = datetime.utcnow().isoformat()
        self.save_proxies()
        self.logger.info(f"Updated proxy: {proxy_id}")

        return self.proxies[proxy_id]

    def delete_proxy(self, proxy_id: str) -> bool:
        """Delete a proxy by ID."""
        if proxy_id in self.proxies:
            proxy_name = self.proxies[proxy_id].get("name", proxy_id)
            del self.proxies[proxy_id]
            self.save_proxies()
            self.logger.info(f"Deleted proxy: {proxy_name} ({proxy_id})")
            return True
        return False

    def list_proxies(self) -> List[Dict]:
        """
        Get all proxies (with passwords masked).

        Returns:
            List of proxy objects with masked URLs
        """
        result = []
        for proxy_id, proxy in self.proxies.items():
            # Create a copy with masked URL
            proxy_copy = proxy.copy()
            proxy_copy["url_masked"] = self._mask_url(proxy.get("url", ""))
            result.append(proxy_copy)
        return result

    def _mask_url(self, url: str) -> str:
        """Mask password in proxy URL for display."""
        try:
            parsed = urlparse(url)
            if parsed.password:
                # Replace password with ****
                masked = url.replace(f":{parsed.password}@", ":****@")
                return masked
            return url
        except:
            return url

    async def test_proxy(self, proxy_id: str) -> Dict:
        """
        Test a proxy's connectivity.

        Args:
            proxy_id: Proxy ID to test

        Returns:
            Test result with success, response_time_ms, and any error
        """
        proxy = self.proxies.get(proxy_id)
        if not proxy:
            return {"success": False, "error": "Proxy not found"}

        proxy_url = proxy.get("url")
        if not proxy_url:
            return {"success": False, "error": "Proxy URL not configured"}

        # Test by making a request through the proxy
        test_url = "https://api.ipify.org?format=json"  # Simple IP check service

        start_time = datetime.now()

        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    test_url,
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    response_time = (datetime.now() - start_time).total_seconds() * 1000

                    if response.status == 200:
                        data = await response.json()
                        ip = data.get("ip", "unknown")

                        # Update proxy stats
                        self._update_proxy_stats(proxy_id, True, response_time)

                        return {
                            "success": True,
                            "response_time_ms": int(response_time),
                            "ip": ip,
                            "proxy_id": proxy_id
                        }
                    else:
                        self._update_proxy_stats(proxy_id, False, response_time)
                        return {
                            "success": False,
                            "error": f"HTTP {response.status}",
                            "response_time_ms": int(response_time)
                        }

        except asyncio.TimeoutError:
            self._update_proxy_stats(proxy_id, False, 30000)
            return {"success": False, "error": "Timeout (30s)"}
        except aiohttp.ClientProxyConnectionError as e:
            self._update_proxy_stats(proxy_id, False, None)
            return {"success": False, "error": f"Proxy connection failed: {str(e)}"}
        except Exception as e:
            self._update_proxy_stats(proxy_id, False, None)
            return {"success": False, "error": str(e)}

    def _update_proxy_stats(self, proxy_id: str, success: bool, response_time_ms: Optional[float]):
        """Update proxy health statistics after a test."""
        if proxy_id not in self.proxies:
            return

        proxy = self.proxies[proxy_id]
        proxy["last_tested"] = datetime.utcnow().isoformat()
        proxy["test_count"] = proxy.get("test_count", 0) + 1

        # Calculate rolling success rate
        old_rate = proxy.get("success_rate") or 0
        old_count = proxy["test_count"] - 1
        if old_count > 0:
            new_rate = ((old_rate * old_count) + (1 if success else 0)) / proxy["test_count"]
        else:
            new_rate = 1 if success else 0
        proxy["success_rate"] = round(new_rate, 2)

        # Update average response time
        if response_time_ms is not None:
            old_avg = proxy.get("avg_response_ms") or response_time_ms
            if old_count > 0:
                new_avg = ((old_avg * old_count) + response_time_ms) / proxy["test_count"]
            else:
                new_avg = response_time_ms
            proxy["avg_response_ms"] = int(new_avg)

        # Update health status
        if proxy["success_rate"] >= 0.95:
            proxy["health_status"] = "healthy"
        elif proxy["success_rate"] >= 0.80:
            proxy["health_status"] = "degraded"
        else:
            proxy["health_status"] = "unhealthy"

        self.save_proxies()

    def assign_to_session(self, proxy_id: str, session_name: str) -> bool:
        """
        Assign a proxy to a session.

        Args:
            proxy_id: Proxy ID
            session_name: Session profile name

        Returns:
            True if successful
        """
        if proxy_id not in self.proxies:
            return False

        assigned = self.proxies[proxy_id].get("assigned_sessions", [])
        if session_name not in assigned:
            assigned.append(session_name)
            self.proxies[proxy_id]["assigned_sessions"] = assigned
            self.save_proxies()

        return True

    def unassign_from_session(self, proxy_id: str, session_name: str) -> bool:
        """Remove proxy assignment from a session."""
        if proxy_id not in self.proxies:
            return False

        assigned = self.proxies[proxy_id].get("assigned_sessions", [])
        if session_name in assigned:
            assigned.remove(session_name)
            self.proxies[proxy_id]["assigned_sessions"] = assigned
            self.save_proxies()

        return True

    def get_proxy_for_session(self, session_name: str) -> Optional[str]:
        """
        Get the proxy URL assigned to a session.

        Args:
            session_name: Session profile name

        Returns:
            Proxy URL or None if no proxy assigned
        """
        for proxy_id, proxy in self.proxies.items():
            if session_name in proxy.get("assigned_sessions", []):
                return proxy.get("url")
        return None
