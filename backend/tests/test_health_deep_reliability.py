import asyncio
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gemini_vision
import main
import profile_manager


@pytest.fixture(autouse=True)
def isolate_queue_state(tmp_path):
    old_queue_path = main.queue_manager.file_path
    old_campaigns = copy.deepcopy(main.queue_manager.campaigns)
    old_history = copy.deepcopy(main.queue_manager.history)
    old_processor_state = copy.deepcopy(main.queue_manager.processor_state)

    main.queue_manager.file_path = str(tmp_path / "campaign_queue.json")
    main.queue_manager.campaigns = {}
    main.queue_manager.history = []
    main.queue_manager.processor_state = {
        "is_running": False,
        "current_campaign_id": None,
        "last_processed_at": None,
    }
    main.queue_manager.save()

    yield

    main.queue_manager.file_path = old_queue_path
    main.queue_manager.campaigns = old_campaigns
    main.queue_manager.history = old_history
    main.queue_manager.processor_state = old_processor_state


def _patch_common_health_dependencies(monkeypatch):
    class FakeCircuitBreaker:
        state = "closed"

        def get_status(self):
            return {
                "state": "closed",
                "failure_count": 0,
                "failure_threshold": 3,
                "recovery_timeout_seconds": 60.0,
                "seconds_until_recovery": 0,
            }

    class FakeProfileManager:
        def __init__(self):
            self.state = {
                "profiles": {
                    "alpha": {"status": "active"},
                    "beta": {"status": "restricted"},
                }
            }

    monkeypatch.setattr(gemini_vision, "get_circuit_breaker", lambda: FakeCircuitBreaker())
    monkeypatch.setattr(profile_manager, "ProfileManager", FakeProfileManager)
    monkeypatch.setattr(main, "list_saved_sessions", lambda: [{"has_valid_cookies": True}])


def test_health_deep_does_not_trigger_queue_recovery(monkeypatch):
    _patch_common_health_dependencies(monkeypatch)

    campaign_id = "campaign_processing_1"
    main.queue_manager.campaigns = {
        campaign_id: {
            "id": campaign_id,
            "status": "processing",
            "created_at": "2026-03-05T00:00:00Z",
        }
    }
    main.queue_manager.processor_state = {
        "is_running": True,
        "current_campaign_id": campaign_id,
        "last_processed_at": None,
    }

    load_call_count = {"count": 0}

    def _fail_if_called(_self):
        load_call_count["count"] += 1
        raise AssertionError("CampaignQueueManager.load must not be called from /health/deep")

    class FakeProxyManager:
        def list_proxies(self):
            return []

        def get_default_proxy(self):
            return None

    monkeypatch.setattr(main.CampaignQueueManager, "load", _fail_if_called)
    monkeypatch.setattr(main, "ProxyManager", FakeProxyManager)
    monkeypatch.setattr(main, "get_system_proxy", lambda: None)

    result = asyncio.run(main.health_deep())

    assert load_call_count["count"] == 0
    assert main.queue_manager.campaigns[campaign_id]["status"] == "processing"
    assert result["checks"]["queue"]["pending"] == 0
    assert result["checks"]["queue"]["processor_running"] is True
    assert result["checks"]["queue"]["current_campaign_id"] == campaign_id


def test_health_deep_reports_runtime_proxy_health_fields(monkeypatch):
    _patch_common_health_dependencies(monkeypatch)

    class FakeProxyManager:
        def list_proxies(self):
            return [
                {"id": "p1", "health_status": "healthy"},
                {"id": "p2", "health_status": "unhealthy"},
            ]

        def get_default_proxy(self):
            return {"id": "p1", "url": "http://default-proxy:9000"}

    async def _fake_runtime_proxy_health():
        return {
            "healthy": True,
            "ip": "107.77.225.131",
            "response_ms": 551,
            "error": None,
        }

    monkeypatch.setattr(main, "ProxyManager", FakeProxyManager)
    monkeypatch.setattr(main, "get_system_proxy", lambda: "http://default-proxy:9000")
    monkeypatch.setattr(main, "check_proxy_health", _fake_runtime_proxy_health)

    result = asyncio.run(main.health_deep())
    proxy = result["checks"]["proxy"]

    assert proxy["total"] == 2
    assert proxy["recent_failures"] == 1
    assert proxy["runtime"] == {
        "configured": True,
        "healthy": True,
        "ip": "107.77.225.131",
        "response_ms": 551,
        "error": None,
        "source": "default",
    }
