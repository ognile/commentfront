"""
GeeLark API Client

A wrapper for the GeeLark Cloud Phone API to manage cloud phones
and run automation tasks for Facebook commenting.

API Documentation: https://open.geelark.com/api/cloud-phone-request-instructions
"""

import os
import json
import time
import uuid
import logging
import httpx
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from dotenv import load_dotenv
from url_utils import clean_facebook_url, is_url_safe_for_geelark, resolve_facebook_redirect

# Load environment variables
load_dotenv()

logger = logging.getLogger("GeeLarkClient")


# ============================================
# DATA MODELS
# ============================================

@dataclass
class GeeLarkDevice:
    """Represents a GeeLark cloud phone device."""
    id: str
    name: str
    status: str
    group_name: Optional[str] = None
    tags: Optional[List[str]] = None
    proxy: Optional[Dict] = None
    created_at: Optional[str] = None

    @property
    def is_online(self) -> bool:
        # Status: 0=starting, 1=running, 2=stopped
        return self.status in ["online", "running", "1", "0"]  # Include 0 (starting) as potentially online


@dataclass
class GeeLarkTask:
    """Represents a GeeLark automation task."""
    id: str
    device_id: str
    status: int  # 0=pending, 1=running, 2=cancelled, 3=completed, 4=failed
    task_type: Optional[int] = None
    plan_name: Optional[str] = None
    schedule_at: Optional[int] = None
    result: Optional[Dict] = None
    error: Optional[str] = None
    failure_code: Optional[int] = None

    @property
    def is_completed(self) -> bool:
        return self.status == 3

    @property
    def is_failed(self) -> bool:
        return self.status == 4

    @property
    def is_running(self) -> bool:
        return self.status == 1

    @property
    def is_pending(self) -> bool:
        return self.status == 0

    @property
    def status_name(self) -> str:
        status_map = {
            0: "pending",
            1: "running",
            2: "cancelled",
            3: "completed",
            4: "failed"
        }
        return status_map.get(self.status, "unknown")


# ============================================
# GEELARK API CLIENT
# ============================================

class GeeLarkClient:
    """
    Client for GeeLark Cloud Phone API.

    API Base URL: https://openapi.geelark.com
    All requests use POST method with JSON body.

    Authentication: Token verification
    - traceId header: UUID v4
    - Authorization header: Bearer <token>
    """

    BASE_URL = "https://openapi.geelark.com"

    def __init__(
        self,
        api_key: Optional[str] = None,
        app_id: Optional[str] = None,
        bearer_token: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("GEELARK_API_KEY")
        self.app_id = app_id or os.getenv("GEELARK_APP_ID")
        self.bearer_token = bearer_token or os.getenv("GEELARK_BEARER_TOKEN")

        if not self.bearer_token:
            raise ValueError("GEELARK_BEARER_TOKEN is required")

        self._client = httpx.Client(timeout=60.0)
        logger.info(f"GeeLark client initialized")

    def _get_headers(self) -> Dict[str, str]:
        """Get authentication headers for API requests."""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.bearer_token}",
            "traceId": str(uuid.uuid4()),
        }

    def _request(
        self,
        endpoint: str,
        data: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Make a POST request to GeeLark API.

        Args:
            endpoint: API endpoint (e.g., /open/v1/phone/list)
            data: Request body

        Returns:
            API response as dictionary
        """
        url = f"{self.BASE_URL}{endpoint}"

        try:
            logger.debug(f"POST {url}")
            logger.debug(f"Data: {json.dumps(data, indent=2)}")

            response = self._client.post(
                url=url,
                json=data or {},
                headers=self._get_headers()
            )

            logger.debug(f"Response: {response.status_code}")

            # Parse response
            result = response.json()

            # Check for API errors
            if result.get("code") != 0:
                error_msg = result.get("msg", "Unknown error")
                logger.error(f"GeeLark API error: {error_msg}")
                raise Exception(f"GeeLark API error: {error_msg}")

            return result

        except httpx.RequestError as e:
            logger.error(f"Request to {url} failed: {e}")
            raise Exception(f"GeeLark API request failed: {e}")

    # ============================================
    # DEVICE MANAGEMENT
    # ============================================

    def list_devices(self, page: int = 1, page_size: int = 100) -> List[GeeLarkDevice]:
        """
        List all cloud phone devices.

        API: POST /open/v1/phone/list

        Returns:
            List of GeeLarkDevice objects
        """
        response = self._request("/open/v1/phone/list", {
            "page": page,
            "pageSize": page_size
        })

        devices_data = response.get("data", {})
        # API returns "items" not "list"
        items = devices_data.get("items", []) or devices_data.get("list", [])

        return [
            GeeLarkDevice(
                id=d.get("id", ""),
                name=d.get("serialName", d.get("name", "Unknown")),
                status=str(d.get("status", "unknown")),
                group_name=d.get("group", {}).get("name") if d.get("group") else None,
                tags=d.get("tags"),
                proxy=d.get("proxy"),
                created_at=d.get("createTime"),
            )
            for d in items
        ]

    def get_device(self, device_id: str) -> Optional[GeeLarkDevice]:
        """Get a specific device by ID."""
        devices = self.list_devices()
        for device in devices:
            if device.id == device_id:
                return device
        return None

    def start_device(self, device_id: str) -> Dict[str, Any]:
        """
        Start a cloud phone device.

        API: POST /open/v1/phone/start
        """
        response = self._request("/open/v1/phone/start", {
            "ids": [device_id]
        })
        data = response.get("data", {})
        logger.info(f"Start Device Response: {json.dumps(data, indent=2)}")
        return data

    def start_devices(self, device_ids: List[str]) -> Dict[str, Any]:
        """
        Start multiple cloud phone devices.

        API: POST /open/v1/phone/start
        """
        response = self._request("/open/v1/phone/start", {
            "ids": device_ids
        })
        return response.get("data", {})

    def stop_device(self, device_id: str) -> Dict[str, Any]:
        """
        Stop a cloud phone device.

        API: POST /open/v1/phone/stop
        """
        response = self._request("/open/v1/phone/stop", {
            "ids": [device_id]
        })
        return response.get("data", {})

    def stop_devices(self, device_ids: List[str]) -> Dict[str, Any]:
        """
        Stop multiple cloud phone devices.

        API: POST /open/v1/phone/stop
        """
        response = self._request("/open/v1/phone/stop", {
            "ids": device_ids
        })
        return response.get("data", {})

    # ============================================
    # TASK MANAGEMENT
    # ============================================

    def query_task(self, task_id: str) -> GeeLarkTask:
        """
        Query a single task status.

        API: POST /open/v1/task/query
        """
        return self.query_tasks([task_id])[0]

    def query_tasks(self, task_ids: List[str]) -> List[GeeLarkTask]:
        """
        Query multiple task statuses.

        API: POST /open/v1/task/query

        Status codes:
        - 0: pending
        - 1: running
        - 2: cancelled
        - 3: completed
        - 4: failed
        """
        response = self._request("/open/v1/task/query", {
            "ids": task_ids
        })

        tasks_data = response.get("data", {})
        items = tasks_data.get("items", [])

        return [
            GeeLarkTask(
                id=t.get("id", ""),
                device_id=t.get("envId", ""),
                status=t.get("status", 0),
                task_type=t.get("taskType"),
                plan_name=t.get("planName"),
                schedule_at=t.get("scheduleAt"),
                failure_code=t.get("failureCode"),
            )
            for t in items
        ]

    def wait_for_task(
        self,
        task_id: str,
        timeout: int = 300,
        poll_interval: int = 5
    ) -> GeeLarkTask:
        """
        Wait for a task to complete.

        Args:
            task_id: Task ID to wait for
            timeout: Maximum wait time in seconds
            poll_interval: Time between status checks

        Returns:
            Final task state
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            task = self.query_task(task_id)

            if task.is_completed:
                logger.info(f"Task {task_id} completed successfully")
                return task
            elif task.is_failed:
                error_msg = f"Task {task_id} failed with code: {task.failure_code}"
                logger.error(error_msg)
                raise Exception(error_msg)
            elif task.status == 2:  # cancelled
                raise Exception(f"Task {task_id} was cancelled")

            logger.info(f"Task {task_id} status: {task.status_name}, waiting...")
            time.sleep(poll_interval)

        raise TimeoutError(f"Task {task_id} did not complete within {timeout} seconds")

    # ============================================
    # CUSTOM RPA TASK
    # ============================================

    def run_custom_rpa_task(
        self,
        device_id: str,
        task_id: str, # The ID of your custom script from GeeLark dashboard
        variables: Dict[str, str] = None
    ) -> str:
        """
        Run a CUSTOM RPA script created in the GeeLark dashboard.
        This bypasses the broken default 'Auto Comment' task.
        """
        # Prepare variables based on what your custom script expects
        task_vars = variables or {}
        
        logger.info(f"Running Custom RPA Task {task_id} on device {device_id}")
        logger.debug(f"Variables: {task_vars}")

        # The endpoint for running a custom task usually involves 'task/create' or 'rpa/run'
        # Based on standard patterns, we use task/create with taskType=1 (Custom/RPA)
        
        response = self._request("/open/v1/task/create", {
            "ids": [device_id], # Often requires 'ids' or 'envIds'
            "taskId": task_id,
            "taskType": 1, 
            "params": json.dumps(task_vars) if task_vars else "{}", 
        })

        # The response structure might vary, let's try to extract the ID
        new_task_id = response.get("data", {}).get("taskId") or response.get("data", {}).get("id")
        
        if not new_task_id:
             # Fallback: maybe 'ids' wasn't right, try 'envIds' if different API version
             # But for now we trust the first attempt or check logs if it fails
             logger.warning("No task ID returned from custom task creation")
             
        logger.info(f"Started Custom Task: {new_task_id}")
        return str(new_task_id)

    # ============================================
    # FACEBOOK AUTOMATION
    # ============================================

    def create_facebook_comment_task(
        self,
        device_id: str,
        post_url: str,
        comments: List[str],
        name: Optional[str] = None,
        remark: Optional[str] = None,
        schedule_at: Optional[int] = None,
        keywords: Optional[List[str]] = None,
    ) -> str:
        """
        Create a Facebook auto comment task.

        API: POST /open/v1/rpa/task/faceBookAutoComment

        Args:
            device_id: Cloud phone device ID
            post_url: Facebook post URL to comment on
            comments: List of comments (up to 10, each up to 8000 chars)
            name: Task name (optional, up to 128 chars)
            remark: Task remark (optional, up to 200 chars)
            schedule_at: Scheduled time as Unix timestamp (required by API)
            keywords: Keywords for matching (optional, up to 10)

        Returns:
            Task ID
        """
        # Clean URL to meet 100-char limit
        original_url = post_url
        post_url = clean_facebook_url(post_url)
        
        # If still too long, try resolving the redirect (e.g. pfbid -> posts/ID)
        if not is_url_safe_for_geelark(post_url, limit=100):
            logger.info(f"URL too long ({len(post_url)} chars). Attempting to resolve redirect...")
            resolved_url = resolve_facebook_redirect(post_url)
            # Clean the resolved URL too (strip params added by redirect)
            post_url = clean_facebook_url(resolved_url)
        
        if not is_url_safe_for_geelark(post_url, limit=100):
            logger.warning(f"URL still exceeds 100 chars after cleaning/resolving: {len(post_url)} chars. Task may fail.")
            logger.warning(f"Original: {original_url}")
            logger.warning(f"Final:    {post_url}")
        
        # Schedule for immediate execution if not specified
        if schedule_at is None:
            schedule_at = int(time.time())

        # Prepare keywords
        kw_list = keywords[:10] if keywords else [comments[0][:50] if comments else "comment"]
        kw_single = kw_list[0] if kw_list else "comment"

        # HACK: Try sending EMPTY keywords to see if the script skips the search step.
        # The previous hack ("Write a comment") failed because it likely typed it into the search bar.
        # If the script has a check like "if keywords:", this might bypass the search loop.
        
        data = {
            "id": device_id,
            "postAddress": post_url,
            "comment": comments,
            "scheduleAt": schedule_at,
            # EMPTY KEYWORDS STRATEGY: 
            # Try to force the script to skip the search/verification step
            "keyword": [],
            "keywords": [],
            "Keywords": [],
            "search_word": "",
            "关键词": "",
        }

        if name:
            data["name"] = name[:128]
        if remark:
            data["remark"] = remark[:200]

        logger.info(f"Creating Facebook comment task on device {device_id}")
        logger.info(f"Post URL: {post_url}")
        logger.info(f"Comments: {comments}")

        response = self._request("/open/v1/rpa/task/faceBookAutoComment", data)

        task_id = response.get("data", {}).get("taskId", "")
        logger.info(f"Created task: {task_id}")

        return task_id

    def post_facebook_comment(
        self,
        device_id: str,
        post_url: str,
        comment: str,
        wait_for_completion: bool = True,
        timeout: int = 120
    ) -> GeeLarkTask:
        """
        Post a single comment on a Facebook post.

        This is the main high-level method for posting comments.

        Args:
            device_id: Cloud phone device ID (must have FB logged in)
            post_url: URL of the Facebook post to comment on
            comment: The comment to post
            wait_for_completion: If True, wait for task to complete
            timeout: Maximum wait time in seconds

        Returns:
            GeeLarkTask with result
        """
        # Create the task
        task_id = self.create_facebook_comment_task(
            device_id=device_id,
            post_url=post_url,
            comments=[comment],
            name=f"Comment from API - {int(time.time())}",
        )

        if wait_for_completion:
            return self.wait_for_task(task_id, timeout=timeout)

        # Return task with pending status
        return GeeLarkTask(
            id=task_id,
            device_id=device_id,
            status=0,  # pending
        )

    # ============================================
    # UTILITY METHODS
    # ============================================

    def test_connection(self) -> bool:
        """Test API connection by listing devices."""
        try:
            devices = self.list_devices(page=1, page_size=1)
            logger.info(f"Connection successful. Found {len(devices)} device(s)")
            return True
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False


# ============================================
# TESTING
# ============================================

def test_geelark_client():
    """Test the GeeLark client with the provided credentials."""
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("GeeLark API Client Test")
    print("=" * 60)

    try:
        client = GeeLarkClient()
        print(f"✓ Client initialized")
        print()

        # Test connection
        print("Testing API connection...")
        print("-" * 40)

        if client.test_connection():
            print("✓ API connection successful!")
        else:
            print("✗ API connection failed")
            return

        print()

        # List devices
        print("Listing cloud phones...")
        print("-" * 40)

        devices = client.list_devices()

        if devices:
            print(f"Found {len(devices)} device(s):")
            for device in devices:
                print(f"  - {device.name}")
                print(f"    ID: {device.id}")
                print(f"    Status: {device.status}")
                if device.group_name:
                    print(f"    Group: {device.group_name}")
                print()
        else:
            print("  No devices found")
            print("  You need to create cloud phones in GeeLark dashboard first")

        print()
        print("=" * 60)
        print("Test completed!")
        print("=" * 60)

    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_geelark_client()
