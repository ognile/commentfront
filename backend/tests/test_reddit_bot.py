import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reddit_bot


class _FakePage:
    def __init__(self):
        self.waits = []
        self.evaluate_result = False
        self.keyboard = _FakeKeyboard()
        self.mouse = _FakeMouse()
        self.body_text = ""
        self.url = "https://www.reddit.com/"

    async def wait_for_timeout(self, timeout_ms):
        self.waits.append(timeout_ms)

    async def evaluate(self, script, *args):
        return self.evaluate_result

    def locator(self, selector):
        if selector == "body":
            return _FakeBodyLocator(self.body_text)
        raise AssertionError(f"unexpected locator: {selector}")


class _FakeKeyboard:
    def __init__(self):
        self.typed = []
        self.pressed = []

    async def type(self, text, delay=0):
        self.typed.append((text, delay))

    async def press(self, key):
        self.pressed.append(key)


class _FakeMouse:
    def __init__(self):
        self.clicks = []
        self.wheels = []

    async def click(self, x, y):
        self.clicks.append((x, y))

    async def wheel(self, dx, dy):
        self.wheels.append((dx, dy))


class _FakeBodyLocator:
    def __init__(self, text):
        self._text = text

    async def inner_text(self):
        return self._text


class _FakeFillLocator:
    def __init__(self, *, fill_error=False):
        self.fill_error = fill_error
        self.fill_calls = []
        self.clicks = 0

    async def count(self):
        return 1

    async def is_visible(self):
        return True

    async def fill(self, value):
        self.fill_calls.append(value)
        if self.fill_error:
            raise RuntimeError("fill unsupported")

    async def click(self):
        self.clicks += 1

    @property
    def first(self):
        return self


class _FakeFillPage(_FakePage):
    def __init__(self, locator):
        super().__init__()
        self._editable_locator = locator

    def locator(self, selector):
        if selector == "body":
            return _FakeBodyLocator(self.body_text)
        return self._editable_locator


class _FakeRecorder:
    def __init__(self):
        self.attempt_id = "attempt-123"
        self.trace_id = "trace-123"
        self.finalized = []
        self.network_capture = type("Capture", (), {"events": []})()

    async def finalize(self, verdict, *, metadata=None):
        self.finalized.append({"verdict": verdict, "metadata": metadata})


class _FakeViewportLocator:
    def __init__(self, box, *, visible=True):
        self._box = box
        self._visible = visible
        self.scrolled = False

    async def count(self):
        return 1

    async def is_visible(self):
        return self._visible

    async def bounding_box(self):
        if self.scrolled and self._box:
            return {"x": 230, "y": 356, "width": 83, "height": 32}
        return self._box

    async def scroll_into_view_if_needed(self):
        self.scrolled = True

    @property
    def first(self):
        return self

    def nth(self, _idx):
        return self


class _FakeViewportPage:
    def __init__(self, locator):
        self._locator = locator
        self.viewport_size = {"width": 393, "height": 873}

    def locator(self, _selector):
        return self._locator


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


def test_fill_comment_input_recovers_thread_context_before_retrying_composer(monkeypatch):
    page = _FakePage()
    calls = []
    thread_context = {"on_thread": False}

    async def fake_fill(target_page, selectors, value):
        assert target_page is page
        calls.append(("fill", tuple(selectors), value))
        return len([entry for entry in calls if entry[0] == "fill"]) > 1

    async def fake_open(target_page, expected_title=None):
        assert target_page is page
        calls.append(("open", expected_title))
        return thread_context["on_thread"]

    async def fake_thread_context(_page, expected_title):
        calls.append(("thread_context", expected_title, thread_context["on_thread"]))
        return thread_context["on_thread"]

    async def fake_ensure_thread(_page, *, url, expected_title):
        calls.append(("ensure_thread", url, expected_title))
        thread_context["on_thread"] = True
        return True

    monkeypatch.setattr(reddit_bot, "_fill_first", fake_fill)
    monkeypatch.setattr(reddit_bot, "_open_comment_composer", fake_open)
    monkeypatch.setattr(reddit_bot, "_thread_context_present", fake_thread_context)
    monkeypatch.setattr(reddit_bot, "_ensure_thread_context", fake_ensure_thread)

    ok = asyncio.run(
        reddit_bot._fill_comment_input(
            page,
            "hello world",
            expected_title="Crazy urgent care experience",
            thread_url="https://www.reddit.com/r/Healthyhooha/comments/1rmydsd/crazy_urgent_care_experience/",
        )
    )

    assert ok is True
    assert calls == [
        ("fill", tuple(reddit_bot.COMMENT["composer_input"]), "hello world"),
        ("open", "Crazy urgent care experience"),
        ("thread_context", "Crazy urgent care experience", False),
        (
            "ensure_thread",
            "https://www.reddit.com/r/Healthyhooha/comments/1rmydsd/crazy_urgent_care_experience/",
            "Crazy urgent care experience",
        ),
        ("open", "Crazy urgent care experience"),
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


def test_scroll_until_comment_surface_visible_scrolls_until_search_input_appears(monkeypatch):
    page = _FakePage()
    state = {"count": 0}

    async def fake_active_editable(_page):
        return False

    async def fake_visible_selector_exists(_page, _selectors):
        return False

    async def fake_first_viewport_locator(_page, selectors):
        if tuple(selectors) == tuple(reddit_bot.COMMENT["search_comments_input"]):
            state["count"] += 1
            return object() if state["count"] >= 3 else None
        return None

    monkeypatch.setattr(reddit_bot, "_active_editable_present", fake_active_editable)
    monkeypatch.setattr(reddit_bot, "_visible_selector_exists", fake_visible_selector_exists)
    monkeypatch.setattr(reddit_bot, "_first_viewport_locator", fake_first_viewport_locator)

    ok = asyncio.run(reddit_bot._scroll_until_comment_surface_visible(page, max_scrolls=4))

    assert ok is True
    assert page.mouse.wheels == [(0, 520), (0, 520)]
    assert page.waits == [900, 900]


def test_comment_on_post_attempts_fill_before_scrolling(monkeypatch):
    page = _FakePage()
    calls = []

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_fill_comment_input(_page, text, **_kwargs):
        calls.append(("fill", text))
        return len(calls) > 1

    async def fake_scroll(_page, **_kwargs):
        calls.append(("scroll",))
        return True

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_ensure_subreddit_user_flair", lambda *_args, **_kwargs: asyncio.sleep(0, result={"status": "skipped"}))
    monkeypatch.setattr(reddit_bot, "_goto", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_load_post_context", lambda *_args, **_kwargs: asyncio.sleep(0, result={"title": "Example"}))
    monkeypatch.setattr(reddit_bot, "_ensure_thread_context", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_raise_if_community_comment_banned", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_fill_comment_input", fake_fill_comment_input)
    monkeypatch.setattr(reddit_bot, "_scroll_until_comment_surface_visible", fake_scroll)
    monkeypatch.setattr(reddit_bot, "_click_first", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "save_debug_screenshot", lambda *_args, **_kwargs: asyncio.sleep(0, result="shot.png"))
    monkeypatch.setattr(reddit_bot, "_verify_text_visible", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.comment_on_post(
            session,
            url="https://www.reddit.com/r/WomensHealth/comments/thread123/example_post/",
            text="supportive text",
            subreddit="WomensHealth",
        )
    )

    assert result["success"] is True
    assert calls == [("fill", "supportive text"), ("scroll",), ("fill", "supportive text")]


def test_comment_on_post_classifies_unable_to_create_comment_banner_as_target_unavailable(monkeypatch):
    page = _FakePage()
    page.body_text = "unable to create comment try that again"
    page.url = "https://www.reddit.com/r/WomensHealth/comments/thread123/example_post/"

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_ensure_subreddit_user_flair", lambda *_args, **_kwargs: asyncio.sleep(0, result={"status": "skipped"}))
    monkeypatch.setattr(reddit_bot, "_goto", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_load_post_context", lambda *_args, **_kwargs: asyncio.sleep(0, result={"title": "Example"}))
    monkeypatch.setattr(reddit_bot, "_ensure_thread_context", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_raise_if_community_comment_banned", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_fill_comment_input", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_click_first", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "save_debug_screenshot", lambda *_args, **_kwargs: asyncio.sleep(0, result="shot.png"))
    monkeypatch.setattr(reddit_bot, "_verify_text_visible", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.comment_on_post(
            session,
            url="https://www.reddit.com/r/WomensHealth/comments/thread123/example_post/",
            text="supportive text",
            subreddit="WomensHealth",
        )
    )

    assert result["success"] is False
    assert result["failure_class"] == "target_unavailable"
    assert result["error"] == "unable to create comment"
    assert result["target_url"] == "https://www.reddit.com/r/WomensHealth/comments/thread123/example_post/"


def test_fill_first_falls_back_to_click_and_keyboard_when_fill_is_unsupported(monkeypatch):
    locator = _FakeFillLocator(fill_error=True)
    page = _FakeFillPage(locator)
    keyboard_calls = []

    async def fake_keyboard(_page, text, reply=False):
        keyboard_calls.append((text, reply))
        return True

    monkeypatch.setattr(reddit_bot, "_keyboard_type_and_verify", fake_keyboard)

    ok = asyncio.run(reddit_bot._fill_first(page, ['[role="textbox"]'], "reply text"))

    assert ok is True
    assert locator.fill_calls == ["reply text"]
    assert locator.clicks == 1
    assert keyboard_calls == [("reply text", False)]
    assert page.waits == [300]


def test_reply_input_selectors_include_role_textbox_surface():
    assert '[role="textbox"]' in reddit_bot.COMMENT["reply_input"]
    assert '[role="textbox"]' in reddit_bot.COMMENT["composer_input"]


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


def test_ensure_thread_context_recovers_by_reloading_exact_url(monkeypatch):
    page = _FakePage()
    calls = []
    state = {"loaded": False}

    async def fake_thread_context(_page, expected_title):
        calls.append(("thread_context", expected_title, state["loaded"]))
        return state["loaded"]

    async def fake_goto(_page, url):
        calls.append(("goto", url))
        state["loaded"] = True

    monkeypatch.setattr(reddit_bot, "_thread_context_present", fake_thread_context)
    monkeypatch.setattr(reddit_bot, "_goto", fake_goto)

    ok = asyncio.run(
        reddit_bot._ensure_thread_context(
            page,
            url="https://www.reddit.com/r/AskWomenOver40/comments/thread123/example_post/",
            expected_title="Should I visit the urogynecologist?",
        )
    )

    assert ok is True
    assert calls == [
        ("thread_context", "Should I visit the urogynecologist?", False),
        ("goto", "https://www.reddit.com/r/AskWomenOver40/comments/thread123/example_post/"),
        ("thread_context", "Should I visit the urogynecologist?", True),
    ]


def test_fill_comment_input_falls_back_to_keyboard_after_composer_opens(monkeypatch):
    page = _FakePage()
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
    monkeypatch.setattr(reddit_bot, "_active_editable_present", lambda _page: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_keyboard_type_and_verify", lambda _page, text, reply=False: asyncio.sleep(0, result=True))

    ok = asyncio.run(reddit_bot._fill_comment_input(page, "typed by keyboard"))

    assert ok is True
    assert calls == [
        ("fill", tuple(reddit_bot.COMMENT["composer_input"]), "typed by keyboard"),
        ("open",),
        ("fill", tuple(reddit_bot.COMMENT["composer_input"]), "typed by keyboard"),
    ]
    assert page.waits == [400]


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


def test_detect_community_comment_ban_returns_reason():
    page = _FakePage()
    page.body_text = "you’re currently banned from this community and can’t comment on posts."

    reason = asyncio.run(reddit_bot._detect_community_comment_ban(page))

    assert reason == "reddit community ban: can't comment on posts"


def test_detect_community_comment_ban_returns_contributing_reason():
    page = _FakePage()
    page.body_text = "you've been banned from contributing to this community"

    reason = asyncio.run(reddit_bot._detect_community_comment_ban(page))

    assert reason == "reddit community ban: can't contribute to community"


def test_dismiss_reddit_open_app_sheet_clicks_close_button():
    page = _FakePage()
    page.evaluate_result = {"dismissed": True}

    dismissed = asyncio.run(reddit_bot._dismiss_reddit_open_app_sheet(page))

    assert dismissed is True
    assert page.waits == [700]


def test_dismiss_reddit_open_app_sheet_returns_false_without_confirmed_sheet():
    page = _FakePage()
    page.evaluate_result = {"dismissed": False}

    dismissed = asyncio.run(reddit_bot._dismiss_reddit_open_app_sheet(page))

    assert dismissed is False
    assert page.waits == []


def test_comment_on_post_returns_community_restricted(monkeypatch):
    page = _FakePage()
    page.body_text = "you’re currently banned from this community and can’t comment on posts."
    page.url = "https://www.reddit.com/r/WomensHealth/comments/example/post/"

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_goto(_page, _url):
        return None

    async def fake_dump(_page, _context):
        return None

    async def fake_title(_page):
        return "Endometrial biopsy"

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_goto", fake_goto)
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", fake_dump)
    monkeypatch.setattr(reddit_bot, "_current_thread_title", fake_title)
    monkeypatch.setattr(reddit_bot, "_capture_reddit_failure_state", lambda *_args, **_kwargs: asyncio.sleep(0))

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.comment_on_post(
            session,
            url="https://www.reddit.com/r/WomensHealth/comments/example/post/",
            text="supportive text",
        )
    )

    assert result["success"] is False
    assert result["failure_class"] == "community_restricted"
    assert result["throttled"] is True
    assert "community ban" in result["error"]


def test_comment_on_post_preserves_identity_evidence_on_community_restricted(monkeypatch):
    page = _FakePage()
    page.body_text = "you’re currently banned from this community and can’t comment on posts."
    page.url = "https://www.reddit.com/r/AskWomenOver40/comments/example/post/"

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(
        reddit_bot,
        "_ensure_subreddit_user_flair",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"status": "applied", "chosen_flair": "widowed"}),
    )
    monkeypatch.setattr(reddit_bot, "_goto", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_load_post_context", lambda *_args, **_kwargs: asyncio.sleep(0, result={"title": "Example"}))
    monkeypatch.setattr(reddit_bot, "_ensure_thread_context", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_scroll_until_comment_surface_visible", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_capture_reddit_failure_state", lambda *_args, **_kwargs: asyncio.sleep(0))

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.comment_on_post(
            session,
            url="https://www.reddit.com/r/AskWomenOver40/comments/example/post/",
            text="supportive text",
            subreddit="AskWomenOver40",
            auto_user_flair=True,
        )
    )

    assert result["success"] is False
    assert result["failure_class"] == "community_restricted"
    assert result["identity_evidence"] == {"status": "applied", "chosen_flair": "widowed"}


def test_ensure_subreddit_user_flair_prefers_thread_url_before_root(monkeypatch):
    page = _FakePage()
    visited = []
    artifacts = []

    class _Session:
        profile_name = "reddit_alpha"

        def __init__(self):
            self.state = {}

        def get_subreddit_identity_state(self, subreddit):
            return self.state.get(subreddit, {})

        def update_subreddit_identity_state(self, subreddit, payload):
            self.state[subreddit] = payload

    async def fake_goto(_page, url):
        visited.append(url)
        _page.url = url

    async def fake_open_dialog(_page):
        return _page.url.endswith("/comments/thread123/example_post/")

    async def fake_collect(_page):
        return {"options": ["divorced", "widowed"], "current_flair": None}

    async def fake_select(_page, option_text):
        return option_text == "widowed"

    async def fake_attach(*_args, **_kwargs):
        artifacts.append("attached")
        return None

    monkeypatch.setattr(reddit_bot, "_goto", fake_goto)
    monkeypatch.setattr(reddit_bot, "_open_user_flair_dialog", fake_open_dialog)
    monkeypatch.setattr(reddit_bot, "_collect_user_flair_options", fake_collect)
    monkeypatch.setattr(reddit_bot, "_select_user_flair_option", fake_select)
    monkeypatch.setattr(reddit_bot, "_ensure_user_flair_visibility_toggle", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_confirm_user_flair_dialog", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "attach_current_json_artifact", fake_attach)
    monkeypatch.setattr(
        reddit_bot.SUBREDDIT_IDENTITY_GENERATOR,
        "choose_user_flair",
        lambda **_kwargs: asyncio.sleep(
            0,
            result={
                "choice": "widowed",
                "reasoning": "best fit",
                "persona_snapshot": {"persona_id": "persona-1"},
            },
        ),
    )

    session = _Session()
    evidence = asyncio.run(
        reddit_bot._ensure_subreddit_user_flair(
            page,
            session,
            subreddit="AskWomenOver40",
            action="comment_post",
            auto_user_flair=True,
            preferred_url="https://www.reddit.com/r/AskWomenOver40/comments/thread123/example_post/",
        )
    )

    assert visited == ["https://www.reddit.com/r/AskWomenOver40/comments/thread123/example_post/"]
    assert evidence["status"] == "applied"
    assert evidence["chosen_flair"] == "widowed"
    assert session.state["AskWomenOver40"]["user_flair"] == "widowed"
    assert artifacts == ["attached"]


def test_comment_on_post_passes_target_url_as_preferred_flair_entry(monkeypatch):
    page = _FakePage()
    recorded = {}

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_ensure_flair(_page, _session, *, subreddit, action, desired_flair=None, auto_user_flair=False, preferred_url=None):
        recorded.update(
            {
                "subreddit": subreddit,
                "action": action,
                "desired_flair": desired_flair,
                "auto_user_flair": auto_user_flair,
                "preferred_url": preferred_url,
            }
        )
        return {"status": "skipped"}

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_ensure_subreddit_user_flair", fake_ensure_flair)
    monkeypatch.setattr(reddit_bot, "_goto", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_load_post_context", lambda *_args, **_kwargs: asyncio.sleep(0, result={"title": "Example"}))
    monkeypatch.setattr(reddit_bot, "_ensure_thread_context", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_scroll_until_comment_surface_visible", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_raise_if_community_comment_banned", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_fill_comment_input", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_click_first", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "save_debug_screenshot", lambda *_args, **_kwargs: asyncio.sleep(0, result="shot.png"))
    monkeypatch.setattr(reddit_bot, "_verify_text_visible", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.comment_on_post(
            session,
            url="https://www.reddit.com/r/AskWomenOver40/comments/thread123/example_post/",
            text="supportive text",
            subreddit="AskWomenOver40",
            auto_user_flair=True,
        )
    )

    assert result["success"] is True
    assert recorded["subreddit"] == "AskWomenOver40"
    assert recorded["action"] == "comment_post"
    assert recorded["auto_user_flair"] is True
    assert recorded["preferred_url"] == "https://www.reddit.com/r/AskWomenOver40/comments/thread123/example_post/"


def test_open_user_flair_dialog_rejects_false_positive_without_dialog_state(monkeypatch):
    calls = []

    monkeypatch.setattr(reddit_bot, "_open_subreddit_community_menu", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))

    async def fake_click_named(_page, *, action_name, needles, max_text_length=None, **_kwargs):
        calls.append((action_name, tuple(needles), max_text_length))
        return action_name == "subreddit_open_user_flair"

    monkeypatch.setattr(reddit_bot, "_click_named_control", fake_click_named)
    monkeypatch.setattr(reddit_bot, "_click_visible_text_region", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_verify_named_control_state", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))

    ok = asyncio.run(reddit_bot._open_user_flair_dialog(_FakePage()))

    assert ok is False
    assert calls == [
        ("subreddit_open_user_flair", ("change user flair", "add user flair", "user flair", "edit user flair"), 96),
    ]


def test_create_post_uses_semantic_title_fallback_when_selector_title_is_missing(monkeypatch):
    page = _FakePage()
    page.url = "https://www.reddit.com/r/WomensHealth/comments/example/new_post/"

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_goto(_page, url):
        assert url == "https://www.reddit.com/r/WomensHealth/submit?type=TEXT"

    async def fake_dump(_page, _context):
        return None

    async def fake_fill_first(_page, selectors, value):
        if tuple(selectors) == tuple(reddit_bot.POST["title_input"]):
            return False
        if tuple(selectors) == tuple(reddit_bot.POST["body_input"]):
            assert value == "body text"
            return True
        raise AssertionError(f"unexpected selectors: {selectors}")

    async def fake_semantic_fill(_page, *, kind, value):
        return kind == "title" and value == "hello title"

    async def fake_click_first(_page, selectors, timeout_ms=4000):
        assert tuple(selectors) == tuple(reddit_bot.POST["post_button"])
        return True

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_goto", fake_goto)
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", fake_dump)
    monkeypatch.setattr(reddit_bot, "_fill_first", fake_fill_first)
    monkeypatch.setattr(reddit_bot, "_fill_post_field_by_semantics", fake_semantic_fill)
    monkeypatch.setattr(reddit_bot, "_click_first", fake_click_first)
    monkeypatch.setattr(reddit_bot, "save_debug_screenshot", lambda *_args, **_kwargs: asyncio.sleep(0, result="shot.png"))

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.create_post(
            session,
            title="hello title",
            body="body text",
            subreddit="WomensHealth",
        )
    )

    assert result["success"] is True
    assert result["current_url"] == "https://www.reddit.com/r/WomensHealth/comments/example/new_post/"


def test_create_post_uses_semantic_body_fallback_when_body_selector_is_missing(monkeypatch):
    page = _FakePage()
    page.url = "https://www.reddit.com/r/WomensHealth/comments/example/new_post/"

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_goto(_page, _url):
        return None

    async def fake_dump(_page, _context):
        return None

    async def fake_fill_first(_page, selectors, value):
        if tuple(selectors) == tuple(reddit_bot.POST["title_input"]):
            return True
        if tuple(selectors) == tuple(reddit_bot.POST["body_input"]):
            assert value == "body text"
            return False
        raise AssertionError(f"unexpected selectors: {selectors}")

    async def fake_semantic_fill(_page, *, kind, value):
        return kind == "body" and value == "body text"

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_goto", fake_goto)
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", fake_dump)
    monkeypatch.setattr(reddit_bot, "_fill_first", fake_fill_first)
    monkeypatch.setattr(reddit_bot, "_fill_post_field_by_semantics", fake_semantic_fill)
    monkeypatch.setattr(reddit_bot, "_click_first", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "save_debug_screenshot", lambda *_args, **_kwargs: asyncio.sleep(0, result="shot.png"))

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.create_post(
            session,
            title="hello title",
            body="body text",
            subreddit="WomensHealth",
        )
    )

    assert result["success"] is True
    assert result["current_url"] == "https://www.reddit.com/r/WomensHealth/comments/example/new_post/"


def test_create_post_returns_community_restricted(monkeypatch):
    page = _FakePage()
    page.url = "https://www.reddit.com/r/WomensHealth/submit?type=TEXT"
    page.body_text = "you've been banned from contributing to this community"

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_fill_first(_page, selectors, value):
        if tuple(selectors) == tuple(reddit_bot.POST["title_input"]):
            return True
        if tuple(selectors) == tuple(reddit_bot.POST["body_input"]):
            return True
        raise AssertionError(f"unexpected selectors: {selectors}")

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_goto", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_fill_first", fake_fill_first)
    monkeypatch.setattr(reddit_bot, "_fill_post_field_by_semantics", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_capture_reddit_failure_state", lambda *_args, **_kwargs: asyncio.sleep(0))

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.create_post(
            session,
            title="hello title",
            body="body text",
            subreddit="WomensHealth",
        )
    )

    assert result["success"] is False
    assert result["failure_class"] == "community_restricted"
    assert result["throttled"] is True
    assert "community ban" in result["error"]


def test_create_post_preserves_identity_evidence_on_community_restricted(monkeypatch):
    page = _FakePage()
    page.url = "https://www.reddit.com/r/AskWomenOver40/submit?type=TEXT"
    page.body_text = "you've been banned from contributing to this community"

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_fill_first(_page, selectors, value):
        if tuple(selectors) == tuple(reddit_bot.POST["title_input"]):
            return True
        if tuple(selectors) == tuple(reddit_bot.POST["body_input"]):
            return True
        raise AssertionError(f"unexpected selectors: {selectors}")

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_goto", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(
        reddit_bot,
        "_ensure_subreddit_user_flair",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"status": "applied", "chosen_flair": "widowed"}),
    )
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_fill_first", fake_fill_first)
    monkeypatch.setattr(reddit_bot, "_fill_post_field_by_semantics", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_capture_reddit_failure_state", lambda *_args, **_kwargs: asyncio.sleep(0))

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.create_post(
            session,
            title="hello title",
            body="body text",
            subreddit="AskWomenOver40",
            auto_user_flair=True,
        )
    )

    assert result["success"] is False
    assert result["failure_class"] == "community_restricted"
    assert result["identity_evidence"] == {"status": "applied", "chosen_flair": "widowed"}


def test_create_post_accepts_subreddit_feed_permalink_when_post_appears_in_feed(monkeypatch):
    page = _FakePage()
    page.url = "https://www.reddit.com/r/Healthyhooha/"

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_goto", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_fill_first", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_fill_post_field_by_semantics", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_raise_if_community_comment_banned", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_click_first", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_post_requires_flair", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "save_debug_screenshot", lambda *_args, **_kwargs: asyncio.sleep(0, result="shot.png"))
    monkeypatch.setattr(
        reddit_bot,
        "_find_created_post_permalink_on_feed",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result="https://www.reddit.com/r/Healthyhooha/comments/example/new_post/",
        ),
    )

    session = type(
        "Session",
        (),
        {
            "profile_name": "reddit_mary_miaby",
            "get_username": lambda self: "Mary_Miaby",
        },
    )()
    result = asyncio.run(
        reddit_bot.create_post(
            session,
            title="hello title",
            body="body text",
            subreddit="Healthyhooha",
        )
    )

    assert result["success"] is True
    assert result["current_url"] == "https://www.reddit.com/r/Healthyhooha/"
    assert result["target_url"] == "https://www.reddit.com/r/Healthyhooha/comments/example/new_post/"


def test_create_post_retries_submit_after_required_flair(monkeypatch):
    page = _FakePage()
    page.url = "https://www.reddit.com/r/PCOS/submit?type=TEXT"
    post_clicks = []

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_click_first(_page, selectors, timeout_ms=4000):
        if tuple(selectors) == tuple(reddit_bot.POST["post_button"]):
            post_clicks.append("post")
            if len(post_clicks) >= 2:
                page.url = "https://www.reddit.com/r/PCOS/comments/example/new_post/"
            return True
        raise AssertionError(f"unexpected selectors: {selectors}")

    flair_checks = iter([True, False])

    async def fake_requires_flair(_page):
        return next(flair_checks)

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_goto", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_fill_first", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_fill_post_field_by_semantics", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_raise_if_community_comment_banned", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_click_first", fake_click_first)
    monkeypatch.setattr(reddit_bot, "_post_requires_flair", fake_requires_flair)
    monkeypatch.setattr(reddit_bot, "_ensure_post_flair", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "save_debug_screenshot", lambda *_args, **_kwargs: asyncio.sleep(0, result="shot.png"))

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.create_post(
            session,
            title="hello title",
            body="body text",
            subreddit="PCOS",
        )
    )

    assert result["success"] is True
    assert result["current_url"] == "https://www.reddit.com/r/PCOS/comments/example/new_post/"
    assert post_clicks == ["post", "post"]


def test_create_post_returns_error_when_required_flair_cannot_be_selected(monkeypatch):
    page = _FakePage()
    page.url = "https://www.reddit.com/r/PCOS/submit?type=TEXT"

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_click_first(_page, selectors, timeout_ms=4000):
        if tuple(selectors) == tuple(reddit_bot.POST["post_button"]):
            return True
        raise AssertionError(f"unexpected selectors: {selectors}")

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_goto", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_fill_first", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_fill_post_field_by_semantics", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_raise_if_community_comment_banned", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_click_first", fake_click_first)
    monkeypatch.setattr(reddit_bot, "_post_requires_flair", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_ensure_post_flair", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_capture_reddit_failure_state", lambda *_args, **_kwargs: asyncio.sleep(0))

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.create_post(
            session,
            title="hello title",
            body="body text",
            subreddit="PCOS",
        )
    )

    assert result["success"] is False
    assert result["error"] == "Reddit post flair selection failed"


def test_upvote_comment_prefers_target_comment_surface(monkeypatch):
    page = _FakePage()
    page.url = "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/comment/c1/"
    calls = []

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_goto(_page, url):
        calls.append(("goto", url))

    async def fake_ensure_thread(_page, *, url, expected_title):
        calls.append(("ensure_thread", url, expected_title))
        return True

    async def fake_dump(_page, _context):
        return None

    async def fake_comment_row(_page, *, target_comment_url=None, author, expected_title=None, body_snippet=None):
        calls.append(("row", target_comment_url, author, expected_title, body_snippet))
        return {
            "author": {"x": 120, "y": 120},
            "reply": {"left": 70, "y": 700},
            "vote": {"x": 28, "y": 700},
        }

    monkeypatch.setattr(
        reddit_bot,
        "_load_target_comment_context",
        lambda _url: asyncio.sleep(
            0,
            result={
                "thread_url": "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/",
                "author": "helper_user",
                "body_snippet": "helpful reply target",
                "title": "example post",
            },
        ),
    )
    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_goto", fake_goto)
    monkeypatch.setattr(reddit_bot, "_ensure_thread_context", fake_ensure_thread)
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", fake_dump)
    monkeypatch.setattr(reddit_bot, "_comment_action_row", fake_comment_row)
    monkeypatch.setattr(reddit_bot, "_vote_point_is_active", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_verify_named_control_state", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_click_named_control", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_click_comment_upvote_region", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_capture_row_signature", lambda *_args, **_kwargs: asyncio.sleep(0, result=["before"]))
    monkeypatch.setattr(reddit_bot, "save_debug_screenshot", lambda *_args, **_kwargs: asyncio.sleep(0, result="shot.png"))
    monkeypatch.setattr(reddit_bot, "_network_has_vote_mutation", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reddit_bot, "get_current_forensic_recorder", lambda: object())

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.upvote_comment(
            session,
            target_comment_url="https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/comment/c1/",
        )
    )

    assert result["success"] is True
    assert result["target_url"] == "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/"
    assert calls[0] == ("goto", "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/comment/c1/")
    assert not any(call[0] == "ensure_thread" for call in calls)


def test_upvote_comment_falls_back_to_canonical_comment_surface(monkeypatch):
    page = _FakePage()
    page.url = "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/comment/o4v87n6/"
    goto_calls = []
    rows = [
        None,
        {
            "author": {"x": 120, "y": 120},
            "focus": {"left": 92, "bottom": 618, "top": 560},
            "vote": {"x": 28, "y": 640},
        },
    ]

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_goto(_page, url):
        goto_calls.append(url)

    monkeypatch.setattr(
        reddit_bot,
        "_load_target_comment_context",
        lambda _url: asyncio.sleep(
            0,
            result={
                "thread_url": "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/",
                "author": "helper_user",
                "body_snippet": "helpful reply target",
                "title": "example post",
            },
        ),
    )
    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_goto", fake_goto)
    monkeypatch.setattr(reddit_bot, "_ensure_thread_context", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(
        reddit_bot,
        "_scroll_target_comment_into_view",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=rows.pop(0)),
    )
    monkeypatch.setattr(reddit_bot, "_vote_point_is_active", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_verify_named_control_state", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_click_named_control", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_click_comment_upvote_region", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_capture_row_signature", lambda *_args, **_kwargs: asyncio.sleep(0, result=["after"]))
    monkeypatch.setattr(reddit_bot, "save_debug_screenshot", lambda *_args, **_kwargs: asyncio.sleep(0, result="shot.png"))
    monkeypatch.setattr(reddit_bot, "_network_has_vote_mutation", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reddit_bot, "get_current_forensic_recorder", lambda: object())

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.upvote_comment(
            session,
            target_comment_url="https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/o4v87n6/",
        )
    )

    assert result["success"] is True
    assert goto_calls[:2] == [
        "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/o4v87n6/",
        "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/comment/o4v87n6/",
    ]


def test_reply_comment_prefers_target_comment_surface(monkeypatch):
    page = _FakePage()
    page.url = "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/comment/c1/"
    calls = []

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_goto(_page, url):
        calls.append(("goto", url))

    async def fake_dump(_page, _context):
        return None

    async def fake_comment_row(_page, *, target_comment_url=None, author, expected_title=None, body_snippet=None):
        calls.append(("row", target_comment_url, author, expected_title, body_snippet))
        return {
            "author": {"x": 120, "y": 120},
            "reply": {"left": 70, "y": 700, "x": 110},
        }

    monkeypatch.setattr(
        reddit_bot,
        "_load_target_comment_context",
        lambda _url: asyncio.sleep(
            0,
            result={
                "thread_url": "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/",
                "author": "helper_user",
                "body_snippet": "helpful reply target",
                "title": "example post",
            },
        ),
    )
    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_goto", fake_goto)
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", fake_dump)
    monkeypatch.setattr(reddit_bot, "_raise_if_community_comment_banned", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_comment_action_row", fake_comment_row)
    monkeypatch.setattr(reddit_bot, "_click_named_control", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_dismiss_reddit_open_app_sheet", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_ensure_reply_inline_box", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_fill_comment_input", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_click_reply_submit", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "save_debug_screenshot", lambda *_args, **_kwargs: asyncio.sleep(0, result="shot.png"))
    monkeypatch.setattr(reddit_bot, "_verify_text_visible", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.reply_to_comment(
            session,
            target_comment_url="https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/comment/c1/",
            text="reply text",
        )
    )

    assert result["success"] is True
    assert result["target_url"] == "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/"
    assert calls[0] == ("goto", "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/comment/c1/")


def test_reply_comment_preserves_identity_evidence_on_community_restriction(monkeypatch):
    page = _FakePage()
    page.body_text = "you’re currently banned from this community and can’t comment on posts."
    page.url = "https://www.reddit.com/r/AskWomenOver40/comments/thread123/example_post/comment/c1/"

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    monkeypatch.setattr(
        reddit_bot,
        "_load_target_comment_context",
        lambda _url: asyncio.sleep(
            0,
            result={
                "thread_url": "https://www.reddit.com/r/AskWomenOver40/comments/thread123/example_post/",
                "author": "helper_user",
                "body_snippet": "helpful reply target",
                "title": "example post",
            },
        ),
    )
    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(
        reddit_bot,
        "_ensure_subreddit_user_flair",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"status": "applied", "chosen_flair": "widowed"}),
    )
    monkeypatch.setattr(reddit_bot, "_goto", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_capture_reddit_failure_state", lambda *_args, **_kwargs: asyncio.sleep(0))

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.reply_to_comment(
            session,
            target_comment_url="https://www.reddit.com/r/AskWomenOver40/comments/thread123/example_post/comment/c1/",
            text="reply text",
            subreddit="AskWomenOver40",
            auto_user_flair=True,
        )
    )

    assert result["success"] is False
    assert result["failure_class"] == "community_restricted"
    assert result["identity_evidence"] == {"status": "applied", "chosen_flair": "widowed"}


def test_scroll_target_comment_into_view_scrolls_until_row_appears(monkeypatch):
    page = _FakePage()
    states = [
        None,
        None,
        {
            "author": {"x": 120, "y": 120},
            "reply": {"left": 70, "y": 700, "x": 110},
        },
    ]

    async def fake_comment_row(_page, *, target_comment_url=None, author, expected_title=None, body_snippet=None):
        assert author == "helper_user"
        assert target_comment_url.endswith("/comment/c1/")
        assert expected_title == "example post"
        assert body_snippet == "helpful reply target"
        return states.pop(0)

    monkeypatch.setattr(reddit_bot, "_comment_action_row", fake_comment_row)

    row = asyncio.run(
        reddit_bot._scroll_target_comment_into_view(
            page,
            target_comment_url="https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/comment/c1/",
            author="helper_user",
            expected_title="example post",
            body_snippet="helpful reply target",
            max_scrolls=2,
        )
    )

    assert row is not None
    assert page.mouse.wheels == [(0, 620)]
    assert page.waits == [900]


def test_build_reply_target_surfaces_adds_canonical_and_context_variants():
    surfaces = reddit_bot._build_reply_target_surfaces(
        "https://www.reddit.com/r/Healthyhooha/comments/1r27x9r/help_insane_vaginitis_yes_a_doctor_already_saw_me/o4v87n6/",
        "https://www.reddit.com/r/Healthyhooha/comments/1r27x9r/help_insane_vaginitis_yes_a_doctor_already_saw_me/",
    )

    assert surfaces == [
        "https://www.reddit.com/r/Healthyhooha/comments/1r27x9r/help_insane_vaginitis_yes_a_doctor_already_saw_me/o4v87n6/",
        "https://www.reddit.com/r/Healthyhooha/comments/1r27x9r/help_insane_vaginitis_yes_a_doctor_already_saw_me/comment/o4v87n6/",
        "https://www.reddit.com/r/Healthyhooha/comments/1r27x9r/help_insane_vaginitis_yes_a_doctor_already_saw_me/?comment=o4v87n6&context=3",
        "https://www.reddit.com/comments/1r27x9r/_/comment/o4v87n6/?context=3",
        "https://www.reddit.com/r/Healthyhooha/comments/1r27x9r/help_insane_vaginitis_yes_a_doctor_already_saw_me/",
    ]


def test_click_comment_upvote_region_tries_fallback_candidates(monkeypatch):
    page = _FakePage()
    active_points = []

    async def fake_vote_point_is_active(_page, *, x, y):
        active_points.append((x, y))
        return len(active_points) == 2

    monkeypatch.setattr(reddit_bot, "_vote_point_is_active", fake_vote_point_is_active)
    monkeypatch.setattr(reddit_bot, "_network_has_vote_mutation", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(reddit_bot, "get_current_forensic_recorder", lambda: _FakeRecorder())

    ok = asyncio.run(
        reddit_bot._click_comment_upvote_region(
            page,
            row={
                "vote": {"x": 75, "y": 324},
                "voteCandidates": [
                    {"x": 75, "y": 324},
                    {"x": 40, "y": 324},
                ],
                "reply": {"left": 117.0, "y": 324},
                "author": {"left": 48.0},
            },
        )
    )

    assert ok is True
    assert page.mouse.clicks == [(75, 324), (40, 324)]
    assert active_points == [(75.0, 324.0), (40.0, 324.0)]


def test_run_reddit_action_finalizes_timeout(monkeypatch):
    recorder = _FakeRecorder()

    async def fake_start_forensic_attempt(**_kwargs):
        return recorder

    async def fake_attach_artifact(*_args, **_kwargs):
        return None

    monkeypatch.setattr(reddit_bot, "start_forensic_attempt", fake_start_forensic_attempt)
    monkeypatch.setattr(reddit_bot, "attach_current_json_artifact", fake_attach_artifact)
    monkeypatch.setattr(reddit_bot, "set_current_forensic_recorder", lambda _recorder: "token")
    monkeypatch.setattr(reddit_bot, "reset_current_forensic_recorder", lambda _token: None)

    async def fake_wait_for(_awaitable, timeout):
        _awaitable.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(reddit_bot.asyncio, "wait_for", fake_wait_for)

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.run_reddit_action(
            session,
            action="browse_feed",
            forensic_context={"metadata": {"action_timeout_seconds": 30}},
        )
    )

    assert result["success"] is False
    assert "timeout" in result["error"].lower()
    assert result["attempt_id"] == "attempt-123"
    assert recorder.finalized
    assert recorder.finalized[0]["verdict"].final_verdict == "infra_failure"


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
    assert page.mouse.clicks == [(190, 402)]
    assert page.waits == [700]


def test_ensure_thread_context_retries_navigation_when_title_missing(monkeypatch):
    page = _FakePage()
    calls = []

    async def fake_thread_context(_page, expected_title):
        calls.append(("thread", expected_title))
        return len([entry for entry in calls if entry[0] == "thread"]) >= 2

    async def fake_goto(_page, url):
        calls.append(("goto", url))
        return None

    monkeypatch.setattr(reddit_bot, "_thread_context_present", fake_thread_context)
    monkeypatch.setattr(reddit_bot, "_goto", fake_goto)

    ok = asyncio.run(
        reddit_bot._ensure_thread_context(
            page,
            url="https://www.reddit.com/r/PCOS/comments/1roqvnw/glp1_pcos/",
            expected_title="GLP-1 & PCOS",
        )
    )

    assert ok is True
    assert calls == [
        ("thread", "GLP-1 & PCOS"),
        ("goto", "https://www.reddit.com/r/PCOS/comments/1roqvnw/glp1_pcos/"),
        ("thread", "GLP-1 & PCOS"),
    ]


def test_goto_does_not_trigger_open_app_dismiss(monkeypatch):
    page = _FakePage()
    calls = []

    async def fake_goto_with_retry(_page, url, profile_name=None):
        calls.append(("goto_with_retry", url, profile_name))
        return None

    async def fake_cookie_banner(_page):
        calls.append(("cookie",))
        return False

    async def fake_dismiss(_page):
        calls.append(("dismiss",))
        return True

    monkeypatch.setattr(reddit_bot, "_goto_with_retry", fake_goto_with_retry)
    monkeypatch.setattr(reddit_bot, "_dismiss_cookie_banner", fake_cookie_banner)
    monkeypatch.setattr(reddit_bot, "_dismiss_reddit_open_app_sheet", fake_dismiss)

    asyncio.run(reddit_bot._goto(page, "https://www.reddit.com/r/AskWomenOver40/comments/example/"))

    assert calls == [
        ("goto_with_retry", "https://www.reddit.com/r/AskWomenOver40/comments/example/", "reddit_action"),
        ("cookie",),
    ]
    assert page.waits == [2500, 500]


def test_click_composer_region_from_layout_requires_thread_context(monkeypatch):
    page = _FakePage()

    async def fake_thread_context(_page, expected_title):
        assert expected_title == "Endometrial biopsy"
        return False

    monkeypatch.setattr(reddit_bot, "_thread_context_present", fake_thread_context)

    ok = asyncio.run(reddit_bot._click_composer_region_from_layout(page, "Endometrial biopsy"))

    assert ok is False


def test_fill_comment_input_reply_flow_never_opens_global_composer(monkeypatch):
    page = _FakePage()
    calls = []

    async def fake_fill(target_page, selectors, value):
        assert target_page is page
        calls.append(("fill", tuple(selectors), value))
        return False

    async def fake_active(target_page):
        assert target_page is page
        calls.append(("active",))
        return False

    async def fake_open(_page, expected_title=None):
        raise AssertionError("reply flow must not open the global composer")

    monkeypatch.setattr(reddit_bot, "_fill_first", fake_fill)
    monkeypatch.setattr(reddit_bot, "_active_editable_present", fake_active)
    monkeypatch.setattr(reddit_bot, "_open_comment_composer", fake_open)

    ok = asyncio.run(
        reddit_bot._fill_comment_input(
            page,
            "reply text",
            reply=True,
            expected_title="Endometrial biopsy",
            allow_global_trigger=False,
        )
    )

    assert ok is False
    assert calls == [
        ("fill", tuple(reddit_bot.COMMENT["reply_input"]), "reply text"),
        ("active",),
    ]


def test_click_visible_text_region_clicks_candidate(monkeypatch):
    page = _FakePage()

    async def fake_find(_page, **kwargs):
        assert kwargs["needle"] == "join"
        return {"x": 311, "y": 142, "source": "text_node", "label": "join"}

    monkeypatch.setattr(reddit_bot, "_find_visible_text_region", fake_find)

    ok = asyncio.run(
        reddit_bot._click_visible_text_region(
            page,
            needle="join",
            action_name="join_subreddit",
            min_top=60,
            max_top=260,
        )
    )

    assert ok is True
    assert page.mouse.clicks == [(311, 142)]
    assert page.waits == [700]


def test_click_post_upvote_region_uses_share_box_geometry():
    page = _FakePage()

    ok = asyncio.run(
        reddit_bot._click_post_upvote_region(
            page,
            share_box={"x": 256.0, "y": 420.0, "height": 32.0},
        )
    )

    assert ok is True
    assert page.mouse.clicks == [(32, 436)]
    assert page.waits == [900]


def test_click_comment_upvote_region_uses_vote_point():
    page = _FakePage()

    ok = asyncio.run(
        reddit_bot._click_comment_upvote_region(
            page,
            row={"vote": {"x": 35, "y": 356}, "reply": {"x": 152, "y": 356}},
        )
    )

    assert ok is True
    assert page.mouse.clicks == [(35, 356)]
    assert page.waits == [900]


def test_click_reply_row_button_uses_reply_center():
    page = _FakePage()

    ok = asyncio.run(
        reddit_bot._click_reply_row_button(
            page,
            row={"reply": {"x": 152, "y": 702}},
        )
    )

    assert ok is True
    assert page.mouse.clicks == [(152, 702)]
    assert page.waits == [900]


def test_network_has_vote_mutation_detects_post_vote():
    class _Recorder:
        class _Capture:
            events = [
                {
                    "kind": "request",
                    "method": "POST",
                    "url": "https://www.reddit.com/svc/shreddit/graphql",
                    "post_data_excerpt": '{"operation":"UpdatePostVoteState","variables":{"input":{"postId":"t3_abc","voteState":"UP"}}}',
                }
            ]

        network_capture = _Capture()

    assert reddit_bot._network_has_vote_mutation(_Recorder(), target_kind="post") is True


def test_network_has_vote_mutation_detects_comment_vote():
    class _Recorder:
        class _Capture:
            events = [
                {
                    "kind": "request",
                    "method": "POST",
                    "url": "https://www.reddit.com/svc/shreddit/graphql",
                    "post_data_excerpt": '{"variables":{"input":{"commentId":"t1_xyz","voteState":"UP"}}}',
                }
            ]

        network_capture = _Capture()

    assert reddit_bot._network_has_vote_mutation(_Recorder(), target_kind="comment") is True


def test_network_has_vote_mutation_detects_vote_reset():
    class _Recorder:
        class _Capture:
            events = [
                {
                    "kind": "request",
                    "method": "POST",
                    "url": "https://www.reddit.com/svc/shreddit/graphql",
                    "post_data_excerpt": '{"operation":"UpdatePostVoteState","variables":{"input":{"postId":"t3_abc","voteState":"NONE"}}}',
                }
            ]

        network_capture = _Capture()

    assert reddit_bot._network_has_vote_mutation(_Recorder(), target_kind="post", vote_state="NONE") is True


def test_upvote_post_recovers_after_toggle_off_existing_vote(monkeypatch):
    page = _FakePage()
    page.url = "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/"
    share_locator = _FakeViewportLocator({"x": 256.0, "y": 420.0, "width": 83.0, "height": 32.0})
    click_log = []
    screenshots = iter(["shot-before.png", "shot-after.png"])

    class _Recorder:
        class _Capture:
            events = []

        network_capture = _Capture()

    recorder = _Recorder()

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_click_post_upvote_region(_page, *, share_box):
        click_log.append(dict(share_box))
        vote_state = "NONE" if len(click_log) == 1 else "UP"
        recorder.network_capture.events.append(
            {
                "kind": "request",
                "method": "POST",
                "url": "https://www.reddit.com/svc/shreddit/graphql",
                "post_data_excerpt": (
                    '{"operation":"UpdatePostVoteState","variables":{"input":{"postId":"t3_abc","voteState":"%s"}}}'
                    % vote_state
                ),
            }
        )
        return True

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_goto", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(
        reddit_bot,
        "_load_post_context",
        lambda _url: asyncio.sleep(0, result={"title": "Odd smell"}),
    )
    monkeypatch.setattr(reddit_bot, "_ensure_thread_context", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "_scroll_until_post_actions_visible", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "_first_viewport_locator", lambda *_args, **_kwargs: asyncio.sleep(0, result=share_locator))
    monkeypatch.setattr(reddit_bot, "_capture_row_signature", lambda *_args, **_kwargs: asyncio.sleep(0, result=["same"]))
    monkeypatch.setattr(reddit_bot, "_vote_region_is_active", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_verify_named_control_state", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_click_post_upvote_region", fake_click_post_upvote_region)
    monkeypatch.setattr(reddit_bot, "_click_named_control", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "save_debug_screenshot", lambda *_args, **_kwargs: asyncio.sleep(0, result=next(screenshots)))
    monkeypatch.setattr(reddit_bot, "get_current_forensic_recorder", lambda: recorder)

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.upvote_post(
            session,
            url="https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/",
        )
    )

    assert result["success"] is True
    assert result["screenshot"] == "shot-after.png"
    assert len(click_log) == 2
    assert reddit_bot._network_has_vote_mutation(recorder, target_kind="post", vote_state="NONE") is True
    assert reddit_bot._network_has_vote_mutation(recorder, target_kind="post", vote_state="UP") is True


def test_upvote_comment_recovers_after_toggle_off_existing_vote(monkeypatch):
    page = _FakePage()
    page.url = "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/comment/c1/"
    screenshots = iter(["shot-before.png", "shot-after.png"])
    click_log = []

    class _Recorder:
        class _Capture:
            events = []

        network_capture = _Capture()

    recorder = _Recorder()

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_click_comment_upvote_region(_page, *, row):
        click_log.append(dict(row))
        vote_state = "NONE" if len(click_log) == 1 else "UP"
        recorder.network_capture.events.append(
            {
                "kind": "request",
                "method": "POST",
                "url": "https://www.reddit.com/svc/shreddit/graphql",
                "post_data_excerpt": (
                    '{"operation":"UpdateCommentVoteState","variables":{"input":{"commentId":"t1_xyz","voteState":"%s"}}}'
                    % vote_state
                ),
            }
        )
        return True

    monkeypatch.setattr(
        reddit_bot,
        "_load_target_comment_context",
        lambda _url: asyncio.sleep(
            0,
            result={
                "thread_url": "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/",
                "author": "helper_user",
                "body_snippet": "helpful reply target",
                "title": "example post",
            },
        ),
    )
    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_goto", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(
        reddit_bot,
        "_comment_action_row",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result={
                "reply": {"left": 70, "y": 700, "x": 110},
                "vote": {"x": 28, "y": 700},
            },
        ),
    )
    monkeypatch.setattr(reddit_bot, "_vote_point_is_active", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_verify_named_control_state", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_click_named_control", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_click_comment_upvote_region", fake_click_comment_upvote_region)
    monkeypatch.setattr(reddit_bot, "_capture_row_signature", lambda *_args, **_kwargs: asyncio.sleep(0, result=["same"]))
    monkeypatch.setattr(reddit_bot, "save_debug_screenshot", lambda *_args, **_kwargs: asyncio.sleep(0, result=next(screenshots)))
    monkeypatch.setattr(reddit_bot, "get_current_forensic_recorder", lambda: recorder)

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.upvote_comment(
            session,
            target_comment_url="https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/comment/c1/",
        )
    )

    assert result["success"] is True
    assert result["target_url"] == "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/"
    assert result["target_comment_url"] == "https://www.reddit.com/r/Healthyhooha/comments/thread123/example_post/comment/c1/"
    assert len(click_log) == 2


def test_fill_comment_input_reply_uses_inline_box_fallback(monkeypatch):
    page = _FakePage()
    calls = []

    async def fake_fill(target_page, selectors, value):
        assert target_page is page
        calls.append(("fill", tuple(selectors), value))
        return False

    async def fake_active(target_page):
        assert target_page is page
        calls.append(("active",))
        return len([entry for entry in calls if entry == ("active",)]) > 1

    async def fake_inline_present(target_page, *, author=None, row=None):
        assert target_page is page
        calls.append(("inline_present",))
        assert author is None
        assert row is None
        return True

    async def fake_focus(target_page):
        assert target_page is page
        calls.append(("focus_inline",))
        return True

    monkeypatch.setattr(reddit_bot, "_fill_first", fake_fill)
    monkeypatch.setattr(reddit_bot, "_active_editable_present", fake_active)
    monkeypatch.setattr(reddit_bot, "_reply_inline_box_present", fake_inline_present)
    monkeypatch.setattr(reddit_bot, "_focus_reply_inline_box", fake_focus)
    monkeypatch.setattr(reddit_bot, "_keyboard_type_and_verify", lambda _page, text, reply=False: asyncio.sleep(0, result=True))

    ok = asyncio.run(
        reddit_bot._fill_comment_input(
            page,
            "reply text",
            reply=True,
            expected_title="Endometrial biopsy",
            allow_global_trigger=False,
        )
    )

    assert ok is True
    assert calls == [
        ("fill", tuple(reddit_bot.COMMENT["reply_input"]), "reply text"),
        ("active",),
        ("inline_present",),
        ("focus_inline",),
        ("active",),
    ]


def test_fill_comment_input_reply_uses_placeholder_click_before_focus(monkeypatch):
    page = _FakePage()
    calls = []

    async def fake_fill(target_page, selectors, value):
        assert target_page is page
        calls.append(("fill", tuple(selectors), value))
        return False

    async def fake_active(target_page):
        assert target_page is page
        calls.append(("active",))
        return False

    async def fake_inline_present(target_page, *, author=None, row=None):
        assert target_page is page
        calls.append(("inline_present",))
        assert author == "aenflex"
        assert row is None
        return True

    async def fake_placeholder(target_page, *, author):
        assert target_page is page
        calls.append(("placeholder", author))
        return True

    async def fake_keyboard(target_page, text, reply=False):
        assert target_page is page
        calls.append(("keyboard", text, reply))
        return True

    async def fake_focus(_page):
        raise AssertionError("focus fallback should not run when placeholder click works")

    monkeypatch.setattr(reddit_bot, "_fill_first", fake_fill)
    monkeypatch.setattr(reddit_bot, "_active_editable_present", fake_active)
    monkeypatch.setattr(reddit_bot, "_reply_inline_box_present", fake_inline_present)
    monkeypatch.setattr(reddit_bot, "_click_reply_inline_placeholder", fake_placeholder)
    monkeypatch.setattr(reddit_bot, "_keyboard_type_and_verify", fake_keyboard)
    monkeypatch.setattr(reddit_bot, "_focus_reply_inline_box", fake_focus)

    ok = asyncio.run(
        reddit_bot._fill_comment_input(
            page,
            "reply text",
            reply=True,
            expected_title="Endometrial biopsy",
            target_author="aenflex",
            allow_global_trigger=False,
        )
    )

    assert ok is True
    assert calls == [
        ("fill", tuple(reddit_bot.COMMENT["reply_input"]), "reply text"),
        ("active",),
        ("inline_present",),
        ("placeholder", "aenflex"),
        ("keyboard", "reply text", True),
    ]


def test_ensure_reply_inline_box_uses_dom_retry_when_first_click_does_not_open(monkeypatch):
    page = _FakePage()
    calls = []

    async def fake_inline_present(target_page, *, author=None, row=None):
        assert target_page is page
        calls.append("present")
        assert author == "aenflex"
        assert row == {"reply": {"x": 152, "y": 702}}
        return len(calls) >= 5

    async def fake_click_row(target_page, *, row):
        assert target_page is page
        calls.append(("row", row["reply"]["x"], row["reply"]["y"]))
        return True

    async def fake_dom_click(target_page, *, x, y, action_name):
        assert target_page is page
        calls.append(("dom", x, y, action_name))
        return True

    async def fake_named(_page, **kwargs):
        raise AssertionError("named control retry should not run once dom retry works")

    monkeypatch.setattr(reddit_bot, "_reply_inline_box_present", fake_inline_present)
    monkeypatch.setattr(reddit_bot, "_click_reply_row_button", fake_click_row)
    monkeypatch.setattr(reddit_bot, "_dom_click_at_point", fake_dom_click)
    monkeypatch.setattr(reddit_bot, "_click_named_control", fake_named)

    ok = asyncio.run(
        reddit_bot._ensure_reply_inline_box(
            page,
            row={"reply": {"x": 152, "y": 702}},
            author="aenflex",
            expected_title="Endometrial biopsy",
        )
    )

    assert ok is True
    assert calls == [
        "present",
        ("row", 152, 702),
        "present",
        ("dom", 152.0, 702.0, "reply_comment_dom_retry"),
        "present",
    ]


def test_reply_to_comment_retries_after_dismissing_open_app_sheet(monkeypatch):
    page = _FakePage()
    page.url = "https://www.reddit.com/r/PCOS/comments/1roqvnw/glp1_pcos/"
    ensure_calls = []

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_comment_context(_target_comment_url):
        return {
            "thread_url": "https://www.reddit.com/r/PCOS/comments/1roqvnw/glp1_pcos/",
            "author": "Pigeon-From-Hell",
            "title": "GLP-1 & PCOS",
        }

    async def fake_goto(_page, _url):
        return None

    async def fake_dump(_page, _label):
        return None

    async def fake_current_title(_page):
        return "GLP-1 & PCOS"

    async def fake_raise_if_banned(_page, capture_context=None):
        return None

    async def fake_comment_row(_page, target_comment_url=None, author=None, expected_title=None, body_snippet=None):
        return {"reply": {"x": 152, "y": 702}}

    async def fake_click_named(_page, **kwargs):
        return True

    async def fake_dismiss_open_sheet(_page):
        return True

    async def fake_ensure_reply_box(_page, *, row, author, expected_title):
        ensure_calls.append((author, expected_title))
        return len(ensure_calls) >= 2

    async def fake_fill_comment(*_args, **_kwargs):
        return True

    async def fake_click_submit(*_args, **_kwargs):
        return True

    async def fake_save(_page, _label):
        return "/tmp/reply.png"

    async def fake_verify(_page, _text):
        return True

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_load_target_comment_context", fake_comment_context)
    monkeypatch.setattr(reddit_bot, "_goto", fake_goto)
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", fake_dump)
    monkeypatch.setattr(reddit_bot, "_current_thread_title", fake_current_title)
    monkeypatch.setattr(reddit_bot, "_raise_if_community_comment_banned", fake_raise_if_banned)
    monkeypatch.setattr(reddit_bot, "_comment_action_row", fake_comment_row)
    monkeypatch.setattr(reddit_bot, "_click_named_control", fake_click_named)
    monkeypatch.setattr(reddit_bot, "_dismiss_reddit_open_app_sheet", fake_dismiss_open_sheet)
    monkeypatch.setattr(reddit_bot, "_ensure_reply_inline_box", fake_ensure_reply_box)
    monkeypatch.setattr(reddit_bot, "_fill_comment_input", fake_fill_comment)
    monkeypatch.setattr(reddit_bot, "_click_reply_submit", fake_click_submit)
    monkeypatch.setattr(reddit_bot, "save_debug_screenshot", fake_save)
    monkeypatch.setattr(reddit_bot, "_verify_text_visible", fake_verify)

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.reply_to_comment(
            session,
            target_comment_url="https://www.reddit.com/r/PCOS/comments/1roqvnw/glp1_pcos/o9gaqm9/",
            text="helpful reply",
        )
    )

    assert result["success"] is True
    assert ensure_calls == [
        ("Pigeon-From-Hell", "GLP-1 & PCOS"),
        ("Pigeon-From-Hell", "GLP-1 & PCOS"),
    ]


def test_reply_to_comment_reports_surface_errors_without_losing_first_failure(monkeypatch):
    page = _FakePage()
    goto_calls = []

    @asynccontextmanager
    async def fake_session_page(_session, _proxy_url):
        yield (None, None, page)

    async def fake_comment_context(_target_comment_url):
        return {
            "thread_url": "https://www.reddit.com/r/Healthyhooha/comments/1r27x9r/help_insane_vaginitis_yes_a_doctor_already_saw_me/",
            "author": "horse_oats",
            "body_snippet": "please go see a doctor",
            "title": "HELP!!! Insane Vaginitis. Yes, a doctor already saw me. Still struggling.",
        }

    async def fake_goto(_page, url):
        goto_calls.append(url)
        page.url = url
        return None

    async def fake_dump(_page, _label):
        return None

    async def fake_raise_if_banned(_page, capture_context=None):
        return None

    rows = [
        {"reply": {"x": 152, "y": 702}, "author": {"x": 120, "y": 620}},
        None,
        None,
        None,
        None,
    ]

    async def fake_scroll_row(_page, *, target_comment_url=None, author=None, expected_title=None, body_snippet=None, max_scrolls=18):
        return rows.pop(0)

    async def fake_click_row(_page, *, row):
        return True

    async def fake_dismiss(_page):
        return False

    async def fake_ensure_reply_box(_page, *, row, author, expected_title):
        return True

    async def fake_fill_comment(*_args, **_kwargs):
        return False

    monkeypatch.setattr(reddit_bot, "_session_page", fake_session_page)
    monkeypatch.setattr(reddit_bot, "_load_target_comment_context", fake_comment_context)
    monkeypatch.setattr(reddit_bot, "_goto", fake_goto)
    monkeypatch.setattr(reddit_bot, "dump_interactive_elements", fake_dump)
    monkeypatch.setattr(reddit_bot, "_raise_if_community_comment_banned", fake_raise_if_banned)
    monkeypatch.setattr(reddit_bot, "_scroll_target_comment_into_view", fake_scroll_row)
    monkeypatch.setattr(reddit_bot, "_click_reply_row_button", fake_click_row)
    monkeypatch.setattr(reddit_bot, "_click_named_control", lambda *_args, **_kwargs: asyncio.sleep(0, result=False))
    monkeypatch.setattr(reddit_bot, "_dismiss_reddit_open_app_sheet", fake_dismiss)
    monkeypatch.setattr(reddit_bot, "_ensure_reply_inline_box", fake_ensure_reply_box)
    monkeypatch.setattr(reddit_bot, "_fill_comment_input", fake_fill_comment)
    monkeypatch.setattr(reddit_bot, "_capture_reddit_failure_state", lambda *_args, **_kwargs: asyncio.sleep(0))

    session = type("Session", (), {"profile_name": "reddit_alpha"})()
    result = asyncio.run(
        reddit_bot.reply_to_comment(
            session,
            target_comment_url="https://www.reddit.com/r/Healthyhooha/comments/1r27x9r/help_insane_vaginitis_yes_a_doctor_already_saw_me/o4v87n6/",
            text="helpful reply",
        )
    )

    assert result["success"] is False
    assert "reply input not found" in result["error"]
    assert "target comment context not found" in result["error"]
    assert goto_calls[:3] == [
        "https://www.reddit.com/r/Healthyhooha/comments/1r27x9r/help_insane_vaginitis_yes_a_doctor_already_saw_me/o4v87n6/",
        "https://www.reddit.com/r/Healthyhooha/comments/1r27x9r/help_insane_vaginitis_yes_a_doctor_already_saw_me/comment/o4v87n6/",
        "https://www.reddit.com/r/Healthyhooha/comments/1r27x9r/help_insane_vaginitis_yes_a_doctor_already_saw_me/?comment=o4v87n6&context=3",
    ]


def test_first_viewport_locator_scrolls_offscreen_candidate_into_view():
    locator = _FakeViewportLocator({"x": 256, "y": 1828, "width": 83, "height": 32})
    page = _FakeViewportPage(locator)

    found = asyncio.run(reddit_bot._first_viewport_locator(page, reddit_bot.COMMENT["share_button"]))

    assert found is locator
    assert locator.scrolled is True


def test_click_reply_submit_prefers_anchor_aware_click(monkeypatch):
    page = _FakePage()
    calls = []

    async def fake_inline_submit(target_page):
        assert target_page is page
        calls.append(("inline_submit",))
        return False

    async def fake_named(target_page, **kwargs):
        assert target_page is page
        calls.append(("named", kwargs["needles"], kwargs["anchor_text"]))
        return True

    async def fake_click_first(_page, _selectors, timeout_ms=4000):
        raise AssertionError("selector fallback should not run when anchored submit click works")

    monkeypatch.setattr(reddit_bot, "_click_reply_inline_submit_button", fake_inline_submit)
    monkeypatch.setattr(reddit_bot, "_click_named_control", fake_named)
    monkeypatch.setattr(reddit_bot, "_click_first", fake_click_first)

    ok = asyncio.run(reddit_bot._click_reply_submit(page, "helpful reply text"))

    assert ok is True
    assert calls == [
        ("inline_submit",),
        ("named", ["comment"], "helpful reply text"),
    ]


def test_vote_region_is_active_detects_true():
    class _Page:
        async def evaluate(self, script, arg):
            assert arg["left"] == 18.0
            assert arg["right"] == 120.0
            assert arg["y"] == 372.0
            return True

    ok = asyncio.run(reddit_bot._vote_region_is_active(_Page(), left=18, right=120, y=372))
    assert ok is True
