import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config


def test_gemini_registry_defaults_text_and_vision_to_35_flash():
    assert config.get_gemini_model("text") == "gemini-3.5-flash"
    assert config.get_gemini_model("vision") == "gemini-3.5-flash"
    assert config.GEMINI_MODEL == config.get_gemini_model("vision")


def test_gemini_registry_keeps_image_capability_separate():
    assert config.get_gemini_model("image") == "gemini-3-pro-image-preview"
    assert config.get_gemini_model("image") != config.get_gemini_model("text")


def test_gemini_model_ids_do_not_drift_outside_registry():
    backend_dir = Path(__file__).resolve().parents[1]
    allowed = {
        backend_dir / "config.py",
        Path(__file__).resolve(),
    }
    pattern = re.compile(r"gemini-\d+(?:\.\d+)?-[a-z0-9-]+")
    offenders = []

    for path in backend_dir.rglob("*.py"):
        if path in allowed:
            continue
        text = path.read_text(errors="ignore")
        if pattern.search(text):
            offenders.append(str(path.relative_to(backend_dir)))

    assert offenders == []
