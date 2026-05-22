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
from typing import Any, Dict, List, Optional
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
        """Load proxies from JSON file with automatic recovery from backup."""
        from safe_io import safe_read_json
        data = safe_read_json(self.file_path)
        if data is None:
            self.logger.info(f"Proxy file not found at {self.file_path}, starting fresh")
            self.proxies = {}
            return

        self.proxies = data.get("proxies", {})
        self.logger.info(f"Loaded {len(self.proxies)} proxies.")

    def save_proxies(self):
        """Save proxies to JSON file atomically."""
        from safe_io import atomic_write_json
        data = {
            "updated_at": datetime.utcnow().isoformat(),
            "proxies": self.proxies
        }
        if not atomic_write_json(self.file_path, data):
            self.logger.error(f"Failed to save proxies atomically")
        else:
            self.logger.info(f"Saved {len(self.proxies)} proxies.")

    def _parse_proxy_url(self, url: str) -> Dict[str, Any]:
        parsed = urlparse(url)
        return {
            "host": parsed.hostname,
            "port": parsed.port,
            "username": unquote(parsed.username) if parsed.username else None,
        }

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

        parsed = self._parse_proxy_url(url)
        should_activate = not self.get_default_proxy()

        proxy = {
            "id": proxy_id,
            "name": name,
            "url": url,
            "host": parsed["host"],
            "port": parsed["port"],
            "username": parsed["username"],
            "type": proxy_type,
            "country": country,
            "health_status": "untested",
            "last_tested": None,
            "success_rate": None,
            "avg_response_ms": None,
            "test_count": 0,
            "created_at": datetime.utcnow().isoformat(),
            "is_default": should_activate
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
            parsed = self._parse_proxy_url(updates["url"])
            self.proxies[proxy_id]["host"] = parsed["host"]
            self.proxies[proxy_id]["port"] = parsed["port"]
            self.proxies[proxy_id]["username"] = parsed["username"]

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
        except Exception as e:
            self.logger.debug(f"Error masking URL: {e}")
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

    def set_default(self, proxy_id: str) -> bool:
        """
        Set a proxy as the default. Clears default from other proxies.

        Args:
            proxy_id: Proxy ID to set as default

        Returns:
            True if successful, False if proxy not found
        """
        if proxy_id not in self.proxies:
            return False

        # Clear is_default from all proxies
        for pid in self.proxies:
            self.proxies[pid]["is_default"] = False

        # Set is_default=True on target proxy
        self.proxies[proxy_id]["is_default"] = True
        self.save_proxies()
        self.logger.info(f"Set default proxy: {self.proxies[proxy_id].get('name')} ({proxy_id})")

        return True

    def clear_default(self) -> bool:
        """Clear the default proxy setting."""
        for pid in self.proxies:
            self.proxies[pid]["is_default"] = False
        self.save_proxies()
        self.logger.info("Cleared default proxy")
        return True

    def get_default_proxy(self) -> Optional[Dict]:
        """
        Get the proxy marked as default.

        Returns:
            The default proxy object, or None if no default is set
        """
        for proxy in self.proxies.values():
            if proxy.get("is_default"):
                return proxy
        return None

    def get_default_proxy_url(self) -> Optional[str]:
        """
        Get the URL of the default proxy.

        Returns:
            Proxy URL or None if no default is set
        """
        default = self.get_default_proxy()
        return default.get("url") if default else None

    def get_active_proxy(self) -> Optional[Dict[str, Any]]:
        """
        Resolve the single active runtime proxy.

        The UI-managed proxy store is authoritative once it contains any proxy.
        PROXY_URL is only a bootstrap source while the store is empty.
        """
        default = self.get_default_proxy()
        if default and default.get("url"):
            proxy = default.copy()
            proxy["source"] = "proxy_store"
            proxy["active"] = True
            proxy["url_masked"] = self._mask_url(proxy.get("url", ""))
            return proxy

        if self.proxies:
            return None

        env_url = os.getenv("PROXY_URL", "")
        if not env_url:
            return None
        parsed = self._parse_proxy_url(env_url)
        return {
            "id": "bootstrap_env",
            "name": "Bootstrap Proxy (env)",
            "url": env_url,
            "url_masked": self._mask_url(env_url),
            "host": parsed["host"],
            "port": parsed["port"],
            "username": parsed["username"],
            "type": "mobile",
            "country": "US",
            "health_status": "bootstrap",
            "last_tested": None,
            "success_rate": None,
            "avg_response_ms": None,
            "test_count": 0,
            "created_at": None,
            "is_default": True,
            "is_system": True,
            "source": "bootstrap_env",
            "active": True,
        }

    def get_active_proxy_url(self) -> Optional[str]:
        active = self.get_active_proxy()
        return active.get("url") if active else None


# Module-level singleton for active proxy resolution.
_proxy_manager_instance: Optional[ProxyManager] = None


def get_active_proxy() -> Optional[str]:
    """
    Return the URL for the single active runtime proxy.

    Browser launch code must use this function or receive its result from a
    caller. Session files are never proxy authorities.
    """
    global _proxy_manager_instance
    try:
        if _proxy_manager_instance is None:
            _proxy_manager_instance = ProxyManager()
        else:
            _proxy_manager_instance.load_proxies()
        return _proxy_manager_instance.get_active_proxy_url()
    except Exception as exc:
        logging.getLogger("ProxyManager").error(f"Failed to resolve active proxy: {exc}")
        return None


def get_active_proxy_info() -> Optional[Dict[str, Any]]:
    global _proxy_manager_instance
    try:
        if _proxy_manager_instance is None:
            _proxy_manager_instance = ProxyManager()
        else:
            _proxy_manager_instance.load_proxies()
        return _proxy_manager_instance.get_active_proxy()
    except Exception as exc:
        logging.getLogger("ProxyManager").error(f"Failed to resolve active proxy info: {exc}")
        return None
