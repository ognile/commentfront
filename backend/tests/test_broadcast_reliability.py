import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def test_broadcast_update_uses_connection_snapshot_when_set_mutates():
    class MutatingWebSocket:
        def __init__(self):
            self.messages = []

        async def send_text(self, message: str):
            self.messages.append(message)
            main.active_connections.add(object())

    websocket = MutatingWebSocket()
    main.active_connections.clear()
    main.active_connections.add(websocket)

    try:
        asyncio.run(main.broadcast_update("test_event", {"ok": True}))
    finally:
        main.active_connections.clear()

    assert len(websocket.messages) == 1
