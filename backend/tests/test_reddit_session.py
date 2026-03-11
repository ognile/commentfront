import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reddit_session import RedditSession, _default_reddit_sessions_dir, verify_reddit_session_logged_in


class _FakeLocator:
    def __init__(self, *, text: str = "", count: int = 0):
        self._text = text
        self._count = count

    async def inner_text(self):
        return self._text

    async def count(self):
        return self._count


class _FakeContext:
    def __init__(self, storage_state):
        self._storage_state = storage_state

    async def storage_state(self):
        return self._storage_state


class _FakePage:
    def __init__(self, destinations):
        self.destinations = destinations
        self.viewport_size = {"width": 393, "height": 873}
        self.url = "https://www.reddit.com/"
        self._current = {"body": "", "login_inputs": 0}

    async def evaluate(self, script):
        if script == "navigator.userAgent":
            return "fake-agent"
        raise AssertionError(f"unexpected evaluate call: {script}")

    async def goto(self, url, wait_until=None, timeout=None):
        self.url = self.destinations[url]["url"]
        self._current = self.destinations[url]

    async def wait_for_timeout(self, timeout_ms):
        return None

    def locator(self, selector):
        if selector == "body":
            return _FakeLocator(text=self._current.get("body", ""))
        if selector == 'input[name="username"], input[name="password"]':
            return _FakeLocator(count=self._current.get("login_inputs", 0))
        raise AssertionError(f"unexpected selector: {selector}")


def test_extract_from_context_persists_device_and_identity_fields():
    session = RedditSession("reddit_neera")
    context = _FakeContext(
        {
            "cookies": [
                {"name": "reddit_loid", "value": "abc", "domain": ".reddit.com"},
                {"name": "c_user", "value": "123", "domain": ".facebook.com"},
            ],
            "origins": [{"origin": "https://www.reddit.com", "localStorage": []}],
        }
    )
    page = _FakePage({})

    data = asyncio.run(
        session.extract_from_context(
            context,
            page,
            username="Neera_Allvere",
            email="NathenNewtonased23@mail.com",
            profile_url="https://www.reddit.com/user/Neera_Allvere/",
            proxy="http://proxy.example:1234",
            linked_credential_id="reddit::Neera_Allvere",
            display_name="Neera_Allvere",
            device={"timezone": "America/Chicago", "locale": "en-US"},
            bootstrap_source_session_id="adele_compton",
            cookie_blocklist_domains=["facebook.com"],
        )
    )

    assert data["device"] == {"timezone": "America/Chicago", "locale": "en-US"}
    assert data["user_agent"] == "fake-agent"
    assert data["viewport"] == {"width": 393, "height": 873}
    assert data["linked_credential_id"] == "reddit::Neera_Allvere"
    assert data["bootstrap_source_session_id"] == "adele_compton"
    assert [cookie["name"] for cookie in data["cookies"]] == ["reddit_loid"]
    assert [cookie["name"] for cookie in data["storage_state"]["cookies"]] == ["reddit_loid"]


def test_get_device_fingerprint_prefers_persisted_device():
    session = RedditSession("reddit_neera")
    session.data = {
        "username": "Neera_Allvere",
        "device": {"timezone": "America/Los_Angeles", "locale": "en-US"},
    }

    assert session.get_device_fingerprint() == {
        "timezone": "America/Los_Angeles",
        "locale": "en-US",
    }


def test_verify_reddit_session_logged_in_uses_authenticated_destination():
    session = RedditSession("reddit_neera")
    session.data = {"cookies": []}
    page = _FakePage(
        {
            "https://www.reddit.com/submit": {
                "url": "https://www.reddit.com/submit",
                "body": "create a post in your community",
                "login_inputs": 0,
            }
        }
    )

    assert asyncio.run(verify_reddit_session_logged_in(page, session)) is True


def test_verify_reddit_session_logged_in_fails_after_login_redirect():
    session = RedditSession("reddit_neera")
    session.data = {"cookies": []}
    page = _FakePage(
        {
            "https://www.reddit.com/submit": {
                "url": "https://www.reddit.com/login/",
                "body": "log in to continue",
                "login_inputs": 2,
            },
            "https://www.reddit.com/settings/account": {
                "url": "https://www.reddit.com/login/",
                "body": "log in to continue",
                "login_inputs": 2,
            },
        }
    )

    assert asyncio.run(verify_reddit_session_logged_in(page, session)) is False


def test_default_reddit_sessions_dir_prefers_env(monkeypatch):
    monkeypatch.setenv("REDDIT_SESSIONS_DIR", "/tmp/reddit-sessions")

    assert _default_reddit_sessions_dir() == Path("/tmp/reddit-sessions")


def test_subreddit_identity_state_round_trip(tmp_path):
    session = RedditSession("reddit_neera")
    session.session_file = tmp_path / "reddit_neera.json"
    session.data = {"profile_name": "reddit_neera", "cookies": []}

    assert session.update_subreddit_identity_state(
        "AskWomenOver40",
        {"user_flair": "Divorced", "available_options": ["Divorced", "Married"]},
    )

    reloaded = RedditSession("reddit_neera")
    reloaded.session_file = session.session_file
    reloaded.load()

    assert reloaded.get_subreddit_identity_state("askwomenover40")["user_flair"] == "Divorced"
