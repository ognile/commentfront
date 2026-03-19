import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reddit_email_challenge import classify_reddit_email_challenge, detect_reddit_email_challenge, resolve_reddit_email_challenge


class _FakeBodyLocator:
    def __init__(self, text: str):
        self._text = text

    async def inner_text(self):
        return self._text


class _FakeActionLocator:
    def __init__(self):
        self.values = []
        self.clicks = 0
        self.presses = []

    async def count(self):
        return 1

    async def is_visible(self):
        return True

    async def click(self, timeout=None):
        self.clicks += 1

    async def fill(self, value):
        self.values.append(value)

    async def type(self, value, delay=0):
        self.values.append(value)

    async def press(self, key):
        self.presses.append(key)

    @property
    def first(self):
        return self


class _FakePage:
    def __init__(self, *, url: str, body_text: str):
        self.url = url
        self.body_text = body_text
        self.code_locator = _FakeActionLocator()
        self.submit_locator = _FakeActionLocator()
        self.goto_calls = []

    def locator(self, selector: str):
        if selector == "body":
            return _FakeBodyLocator(self.body_text)
        if selector == 'input[name="code"]':
            return self.code_locator
        if selector == 'button[type="submit"]':
            return self.submit_locator
        raise AssertionError(f"unexpected selector: {selector}")

    async def wait_for_timeout(self, timeout_ms):
        return None

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append(url)
        self.url = url


def test_classify_reddit_email_challenge_detects_link_mode():
    result = classify_reddit_email_challenge(
        url="https://www.reddit.com/login/check-email",
        body_text="Check your email. We sent an email so you can verify your email address.",
    )

    assert result is not None
    assert result["kind"] == "reddit_email_verification"
    assert result["mode"] == "link"


def test_classify_reddit_email_challenge_detects_code_mode():
    result = classify_reddit_email_challenge(
        url="https://www.reddit.com/login/",
        body_text="Enter the code from your verification email. Your verification code is 654321.",
    )

    assert result is not None
    assert result["mode"] == "code"
    assert result["code_hint"] == "654321"


def test_detect_reddit_email_challenge_reads_page_body():
    page = _FakePage(
        url="https://www.reddit.com/login/check-email",
        body_text="Confirm your email. We sent an email to continue.",
    )

    result = asyncio.run(detect_reddit_email_challenge(page))

    assert result is not None
    assert result["mode"] == "link"


def test_resolve_reddit_email_challenge_submits_mailbox_code_for_code_challenge():
    page = _FakePage(
        url="https://www.reddit.com/login/",
        body_text="Enter the code from your verification email.",
    )

    async def fake_fetch(browser, *, email, email_password, proxy_url, fingerprint, poll_timeout_ms=120000):
        return {"success": True, "link": None, "code": "123456", "text_preview": "verification code"}

    with patch("reddit_email_challenge.fetch_reddit_verification_from_outlook", side_effect=fake_fetch):
        result = asyncio.run(
            resolve_reddit_email_challenge(
                page,
                object(),
                credential={"email": "mark@example.com", "email_password": "mailpass"},
                proxy_url="http://proxy.example:8080",
                fingerprint={"timezone": "America/New_York", "locale": "en-US"},
            )
        )

    assert result["handled"] is True
    assert result["mode"] == "code"
    assert page.code_locator.values[-1] == "123456"
    assert page.submit_locator.clicks == 1
