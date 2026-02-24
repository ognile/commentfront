import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import browser_manager


class FakePage:
    url = "https://m.facebook.com/"

    def is_closed(self):
        return False

    async def title(self):
        return "Facebook"


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_text(self, payload: str):
        self.messages.append(json.loads(payload))

    async def send_json(self, payload):
        self.messages.append(payload)


def _fresh_manager():
    browser_manager.PersistentBrowserManager._instance = None
    return browser_manager.PersistentBrowserManager()


def test_idle_timer_closes_session_after_last_subscriber_disconnect(monkeypatch):
    async def scenario():
        manager = _fresh_manager()
        manager._session_id = "Vanessa Hines"
        manager._page = FakePage()

        closed = {"count": 0}

        async def fake_close_session():
            closed["count"] += 1
            manager._session_id = None
            manager._page = None
            return {"success": True}

        monkeypatch.setattr(browser_manager, "IDLE_CLOSE_SECONDS", 1)
        monkeypatch.setattr(manager, "close_session", fake_close_session)

        ws = FakeWebSocket()
        manager.subscribe(ws)
        manager.unsubscribe(ws)
        await asyncio.sleep(1.2)

        assert closed["count"] == 1

    asyncio.run(scenario())


def test_subscribe_cancels_idle_close_timer(monkeypatch):
    async def scenario():
        manager = _fresh_manager()
        manager._session_id = "Vanessa Hines"
        manager._page = FakePage()

        closed = {"count": 0}

        async def fake_close_session():
            closed["count"] += 1
            return {"success": True}

        monkeypatch.setattr(browser_manager, "IDLE_CLOSE_SECONDS", 1)
        monkeypatch.setattr(manager, "close_session", fake_close_session)

        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        manager.subscribe(ws1)
        manager.unsubscribe(ws1)
        await asyncio.sleep(0.3)
        manager.subscribe(ws2)
        await asyncio.sleep(1.0)

        assert closed["count"] == 0
        manager.unsubscribe(ws2)
        manager._cancel_idle_close_timer()

    asyncio.run(scenario())


def test_ensure_session_ready_auto_heals_dead_stream():
    async def scenario():
        manager = _fresh_manager()
        manager._session_id = "Vanessa Hines"
        manager._page = FakePage()
        manager._stream_task_state = "failed"
        manager._streaming_task = None

        calls = []

        async def fake_auto_heal_session(*, session_id, reason):
            calls.append({"session_id": session_id, "reason": reason})
            return {"success": True, "session_id": session_id}

        manager.auto_heal_session = fake_auto_heal_session  # type: ignore[assignment]

        result = await manager.ensure_session_ready("Vanessa Hines")
        assert result["success"] is True
        assert calls
        assert calls[0]["session_id"] == "Vanessa Hines"
        assert "stream_task_inactive" in calls[0]["reason"]

    asyncio.run(scenario())


def test_send_bootstrap_frame_marks_subscriber_recent():
    async def scenario():
        manager = _fresh_manager()
        manager._latest_frame = b"fake-jpeg"
        ws = FakeWebSocket()
        manager.subscribe(ws)

        sent = await manager.send_bootstrap_frame(ws)
        assert sent is True
        assert ws.messages
        assert ws.messages[-1]["type"] == "frame"
        assert ws.messages[-1]["data"]["bootstrap"] is True
        assert manager.subscriber_has_recent_frame(ws, within_seconds=3.0) is True

        manager.unsubscribe(ws)
        manager._cancel_idle_close_timer()

    asyncio.run(scenario())
