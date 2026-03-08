import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reddit_bot


class _FakePage:
    def __init__(self):
        self.waits = []
        self.evaluate_result = False

    async def wait_for_timeout(self, timeout_ms):
        self.waits.append(timeout_ms)

    async def evaluate(self, script):
        return self.evaluate_result


def test_fill_comment_input_opens_join_conversation_trigger(monkeypatch):
    page = _FakePage()
    calls = []

    async def fake_fill(target_page, selectors, value):
        assert target_page is page
        calls.append(("fill", tuple(selectors), value))
        return len(calls) > 2

    async def fake_open(target_page):
        assert target_page is page
        calls.append(("open",))
        return True

    monkeypatch.setattr(reddit_bot, "_fill_first", fake_fill)
    monkeypatch.setattr(reddit_bot, "_open_comment_composer", fake_open)

    ok = asyncio.run(reddit_bot._fill_comment_input(page, "hello world"))

    assert ok is True
    assert calls == [
        ("fill", tuple(reddit_bot.COMMENT["composer_input"]), "hello world"),
        ("open",),
        ("fill", tuple(reddit_bot.COMMENT["composer_input"]), "hello world"),
    ]
    assert page.waits == [400]


def test_fill_comment_input_uses_reply_selectors_for_reply_flow(monkeypatch):
    page = _FakePage()
    selector_sets = []

    async def fake_fill(target_page, selectors, value):
        assert target_page is page
        selector_sets.append(tuple(selectors))
        return True

    async def fake_open(_target_page):
        raise AssertionError("reply flow should not open the trigger when input is already present")

    monkeypatch.setattr(reddit_bot, "_fill_first", fake_fill)
    monkeypatch.setattr(reddit_bot, "_open_comment_composer", fake_open)

    ok = asyncio.run(reddit_bot._fill_comment_input(page, "reply text", reply=True))

    assert ok is True
    assert selector_sets == [tuple(reddit_bot.COMMENT["reply_input"])]
    assert page.waits == []


def test_open_comment_composer_falls_back_to_js_probe(monkeypatch):
    page = _FakePage()
    page.evaluate_result = True

    async def fake_click_first(_page, _selectors, timeout_ms=4000):
        return False

    monkeypatch.setattr(reddit_bot, "_click_first", fake_click_first)

    ok = asyncio.run(reddit_bot._open_comment_composer(page))

    assert ok is True
    assert page.waits == [600]
