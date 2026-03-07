import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reddit_login_bot import (
    _goto_in_authenticated_context,
    _wait_for_authenticated_surface,
    _wait_for_otp_resolution,
)


class _FakeLocator:
    def __init__(self, *, count_values: list[int] | None = None, visible: bool = True):
        self._count_values = list(count_values or [0])
        self._visible = visible
        self._index = 0

    async def count(self):
        value = self._count_values[min(self._index, len(self._count_values) - 1)]
        self._index += 1
        return value

    @property
    def first(self):
        return self

    async def is_visible(self):
        return self._visible


class _FakePage:
    def __init__(
        self,
        *,
        url: str,
        login_inputs: int = 0,
        goto_error: Exception | None = None,
        otp_counts_by_selector: dict[str, list[int]] | None = None,
    ):
        self.url = url
        self._login_inputs = login_inputs
        self._goto_error = goto_error
        self._otp_counts_by_selector = otp_counts_by_selector or {}
        self._locators = {}
        self.closed = False
        self.goto_calls = []

    def locator(self, selector: str):
        if selector == 'input[name="username"], input[name="password"]':
            if selector not in self._locators:
                self._locators[selector] = _FakeLocator(count_values=[self._login_inputs])
            return self._locators[selector]
        if selector in self._otp_counts_by_selector:
            if selector not in self._locators:
                self._locators[selector] = _FakeLocator(count_values=self._otp_counts_by_selector[selector])
            return self._locators[selector]
        raise AssertionError(f"unexpected selector: {selector}")

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append(url)
        if self._goto_error:
            raise self._goto_error
        self.url = url

    async def wait_for_timeout(self, timeout_ms):
        return None

    async def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self, *, cookies, fresh_page: _FakePage | None = None):
        self._cookies = cookies
        self._fresh_page = fresh_page

    async def cookies(self):
        return list(self._cookies)

    async def new_page(self):
        if not self._fresh_page:
            raise AssertionError("fresh page not configured")
        return self._fresh_page


def test_wait_for_authenticated_surface_accepts_auth_cookies_without_url_change():
    page = _FakePage(url="https://www.reddit.com/login/", login_inputs=0)
    context = _FakeContext(cookies=[{"name": "reddit_session"}])

    assert asyncio.run(
        _wait_for_authenticated_surface(page, context, profile_name="reddit_neera", timeout_ms=0)
    ) is True


def test_goto_in_authenticated_context_retries_in_fresh_page_after_empty_response():
    broken_page = _FakePage(
        url="https://www.reddit.com/login/",
        goto_error=RuntimeError("Page.goto: net::ERR_EMPTY_RESPONSE at https://www.reddit.com/user/Neera_Allvere/"),
    )
    fresh_page = _FakePage(url="about:blank")
    context = _FakeContext(cookies=[{"name": "reddit_session"}], fresh_page=fresh_page)

    resolved_page = asyncio.run(
        _goto_in_authenticated_context(
            context,
            broken_page,
            "https://www.reddit.com/user/Neera_Allvere/",
            profile_name="reddit_neera",
        )
    )

    assert resolved_page is fresh_page
    assert fresh_page.url == "https://www.reddit.com/user/Neera_Allvere/"
    assert broken_page.closed is True


def test_wait_for_otp_resolution_returns_when_otp_input_disappears():
    page = _FakePage(
        url="https://www.reddit.com/login/",
        otp_counts_by_selector={'input[name="otp"]': [1, 1, 0]},
    )
    context = _FakeContext(cookies=[])

    assert asyncio.run(
        _wait_for_otp_resolution(page, context, profile_name="reddit_neera", timeout_ms=3000)
    ) is True
