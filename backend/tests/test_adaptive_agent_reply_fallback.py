import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adaptive_agent import AdaptiveAgent


class DummyLocator:
    def __init__(self, *, count=0, input_value_text="", bbox=None, on_submit=None):
        self._count = count
        self._input_value_text = input_value_text
        self._bbox = bbox or {"x": 10, "y": 10, "width": 200, "height": 36}
        self._on_submit = on_submit
        self.tap_calls = 0
        self.click_calls = 0

    @property
    def first(self):
        return self

    async def count(self):
        return self._count

    async def click(self, *args, **kwargs):
        self.click_calls += 1
        if self._on_submit:
            self._on_submit()

    async def tap(self, *args, **kwargs):
        self.tap_calls += 1
        if self._on_submit:
            self._on_submit()

    async def input_value(self):
        return self._input_value_text

    async def evaluate(self, _script):
        return self._input_value_text

    async def bounding_box(self):
        return self._bbox

    def set_value(self, value: str):
        self._input_value_text = value


class DummyKeyboard:
    def __init__(self, input_locator: DummyLocator):
        self.input_locator = input_locator
        self.typed = []

    async def type(self, text, delay=0):
        self.typed.append(text)
        self.input_locator.set_value((self.input_locator._input_value_text or "") + text)


class DummyTouchscreen:
    def __init__(self):
        self.taps = []

    async def tap(self, x, y):
        self.taps.append((x, y))


class DummyMouse:
    def __init__(self):
        self.clicks = []

    async def click(self, x, y):
        self.clicks.append((x, y))


class DummyPage:
    def __init__(self, *, submit_selector_available: bool):
        self.input_locator = DummyLocator(count=1, input_value_text="")
        self.submit_selector_available = submit_selector_available
        self.keyboard = DummyKeyboard(self.input_locator)
        self.touchscreen = DummyTouchscreen()
        self.mouse = DummyMouse()

    def _on_submit(self):
        # Simulate composer reset after successful send.
        self.input_locator.set_value("")

    def locator(self, selector: str):
        if selector.startswith("textarea[role=\"combobox\"]"):
            return self.input_locator

        if self.submit_selector_available and selector == '[aria-label="Post a comment"]':
            return DummyLocator(count=1, on_submit=self._on_submit)

        return DummyLocator(count=0)

    async def evaluate(self, _script, _needle):
        # No explicit text match in thread; fallback should still pass when input clears.
        return False


def _build_reply_task(reply_text: str) -> str:
    return (
        "Reply supportively to exactly 1 group comment(s).\n"
        "Use this supportive tone and wording:\n"
        f"{reply_text}\n"
        "Finish with DONE only after replies are sent."
    )


def test_reply_fallback_completes_when_submit_selector_click_clears_input():
    reply_text = "sending support here, you are not alone in this phase."
    agent = AdaptiveAgent(profile_name="Vanessa Hines", task=_build_reply_task(reply_text), max_steps=10)
    agent.page = DummyPage(submit_selector_available=True)

    visible_elements = [{"tag": "TEXTAREA", "ariaLabel": "Write a public reply", "role": "combobox"}]

    outcome = asyncio.run(agent._fallback_submit_reply(visible_elements, 4, "/tmp/adaptive_step_4.png"))

    assert outcome == "completion"
    assert any(step.get("action_taken") == "FALLBACK_REPLY_SUBMIT" for step in agent.results["steps"])


def test_reply_fallback_uses_coordinate_submit_when_selector_missing():
    reply_text = "sending support here, you are not alone in this phase."
    agent = AdaptiveAgent(profile_name="Vanessa Hines", task=_build_reply_task(reply_text), max_steps=10)
    agent.page = DummyPage(submit_selector_available=False)

    visible_elements = [{"tag": "TEXTAREA", "ariaLabel": "Write a public reply", "role": "combobox"}]

    outcome = asyncio.run(agent._fallback_submit_reply(visible_elements, 6, "/tmp/adaptive_step_6.png"))

    assert outcome == "completion"
    assert len(agent.page.touchscreen.taps) == 1
