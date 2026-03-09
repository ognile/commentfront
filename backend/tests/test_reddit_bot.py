import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reddit_bot


class _FakePage:
    def __init__(self):
        self.waits = []
        self.evaluate_result = False
        self.keyboard = _FakeKeyboard()

    async def wait_for_timeout(self, timeout_ms):
        self.waits.append(timeout_ms)

    async def evaluate(self, script, *args):
        return self.evaluate_result


class _FakeKeyboard:
    def __init__(self):
        self.typed = []

    async def type(self, text, delay=0):
        self.typed.append((text, delay))


def test_fill_comment_input_opens_join_conversation_trigger(monkeypatch):
    page = _FakePage()
    calls = []

    async def fake_fill(target_page, selectors, value):
        assert target_page is page
        calls.append(("fill", tuple(selectors), value))
        return len(calls) > 2

    async def fake_open(target_page, expected_title=None):
        assert target_page is page
        assert expected_title is None
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

    async def fake_thread_context(_page, expected_title):
        assert expected_title == "Endometrial biopsy"
        return True

    monkeypatch.setattr(reddit_bot, "_click_first", fake_click_first)
    monkeypatch.setattr(reddit_bot, "_thread_context_present", fake_thread_context)

    ok = asyncio.run(reddit_bot._open_comment_composer(page, "Endometrial biopsy"))

    assert ok is True
    assert page.waits == [600]


def test_fill_comment_input_falls_back_to_keyboard_after_composer_opens(monkeypatch):
    page = _FakePage()
    page.evaluate_result = True
    calls = []

    async def fake_fill(target_page, selectors, value):
        assert target_page is page
        calls.append(("fill", tuple(selectors), value))
        return False

    async def fake_open(target_page, expected_title=None):
        assert target_page is page
        assert expected_title is None
        calls.append(("open",))
        return True

    monkeypatch.setattr(reddit_bot, "_fill_first", fake_fill)
    monkeypatch.setattr(reddit_bot, "_open_comment_composer", fake_open)

    ok = asyncio.run(reddit_bot._fill_comment_input(page, "typed by keyboard"))

    assert ok is True
    assert calls == [
        ("fill", tuple(reddit_bot.COMMENT["composer_input"]), "typed by keyboard"),
        ("open",),
        ("fill", tuple(reddit_bot.COMMENT["composer_input"]), "typed by keyboard"),
    ]
    assert page.keyboard.typed == [("typed by keyboard", 25)]
    assert page.waits == [400, 500]


def test_open_comment_composer_uses_visible_text_region_before_layout(monkeypatch):
    page = _FakePage()
    calls = []

    async def fake_click_first(_page, _selectors, timeout_ms=4000):
        calls.append("selector")
        return False

    async def fake_text_region(_page, expected_title):
        calls.append(("text_region", expected_title))
        return True

    async def fake_layout(_page, expected_title):
        calls.append(("layout", expected_title))
        return True

    async def fake_thread_context(_page, expected_title):
        calls.append(("thread", expected_title))
        return True

    monkeypatch.setattr(reddit_bot, "_click_first", fake_click_first)
    monkeypatch.setattr(reddit_bot, "_click_composer_text_region", fake_text_region)
    monkeypatch.setattr(reddit_bot, "_click_composer_region_from_layout", fake_layout)
    monkeypatch.setattr(reddit_bot, "_thread_context_present", fake_thread_context)

    ok = asyncio.run(reddit_bot._open_comment_composer(page, "Endometrial biopsy"))

    assert ok is True
    assert calls == [
        "selector",
        ("text_region", "Endometrial biopsy"),
        ("thread", "Endometrial biopsy"),
    ]


def test_click_composer_text_region_uses_evaluate_candidate():
    page = _FakePage()
    page.evaluate_result = {
        "ok": True,
        "x": 190,
        "y": 402,
        "source": "text_node",
        "label": "join the conversation",
    }

    ok = asyncio.run(reddit_bot._click_composer_text_region(page, "Endometrial biopsy"))

    assert ok is True
    assert page.waits == [600]


def test_click_composer_region_from_layout_requires_thread_context(monkeypatch):
    page = _FakePage()

    async def fake_thread_context(_page, expected_title):
        assert expected_title == "Endometrial biopsy"
        return False

    monkeypatch.setattr(reddit_bot, "_thread_context_present", fake_thread_context)

    ok = asyncio.run(reddit_bot._click_composer_region_from_layout(page, "Endometrial biopsy"))

    assert ok is False
