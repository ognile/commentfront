import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adaptive_agent import find_element_by_description


def test_find_element_skips_empty_aria_partial_match():
    elements = [
        {
            "tag": "DIV",
            "ariaLabel": "",
            "text": "󳄫",
            "bounds": {"x": 0, "y": 100, "w": 30, "h": 30},
        },
        {
            "tag": "DIV",
            "ariaLabel": "Write something...",
            "text": "Write something...",
            "bounds": {"x": 0, "y": 120, "w": 200, "h": 30},
        },
    ]

    matched = asyncio.run(find_element_by_description("Write something...", elements))
    assert matched is not None
    assert matched.get("ariaLabel") == "Write something..."
