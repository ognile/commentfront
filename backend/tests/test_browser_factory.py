import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_factory import _build_android_chromium_identity, apply_page_identity_overrides


class _FakeCDPSession:
    def __init__(self):
        self.calls = []

    async def send(self, method, params):
        self.calls.append((method, params))


class _FakeContext:
    def __init__(self):
        self.session = _FakeCDPSession()

    async def new_cdp_session(self, page):
        return self.session


class _FakePage:
    def __init__(self):
        self.scripts = []

    async def add_init_script(self, script, arg=None):
        self.scripts.append((script, arg))


def test_build_android_chromium_identity_matches_mobile_ua():
    identity = _build_android_chromium_identity(
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Mobile Safari/537.36"
    )

    assert identity is not None
    assert identity["platform"] == "Android"
    assert identity["navigator_platform"] == "Linux armv8l"
    assert identity["model"] == "Pixel 7"
    assert identity["full_version"] == "133.0.0.0"
    assert identity["brands"][1]["brand"] == "Chromium"


def test_apply_page_identity_overrides_sets_cdp_and_init_script_for_android():
    context = _FakeContext()
    page = _FakePage()

    asyncio.run(
        apply_page_identity_overrides(
            context,
            page,
            user_agent=(
                "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/133.0.0.0 Mobile Safari/537.36"
            ),
            locale="en-US",
        )
    )

    assert context.session.calls[0][0] == "Emulation.setUserAgentOverride"
    override = context.session.calls[0][1]
    assert override["platform"] == "Linux armv8l"
    assert override["userAgentMetadata"]["platform"] == "Android"
    assert page.scripts


def test_apply_page_identity_overrides_skips_non_android_uas():
    context = _FakeContext()
    page = _FakePage()

    asyncio.run(
        apply_page_identity_overrides(
            context,
            page,
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            locale="en-US",
        )
    )

    assert context.session.calls == []
    assert page.scripts == []
