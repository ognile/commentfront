import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def setup_function():
    main.media_index.clear()


def test_get_media_or_none_rehydrates_persisted_index(tmp_path, monkeypatch):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    image_path = media_dir / "proof.png"
    image_path.write_bytes(b"png")
    index_path = media_dir / "media_index.json"
    index_path.write_text(
        json.dumps(
            {
                "img123": {
                    "image_id": "img123",
                    "path": str(image_path),
                    "filename": "proof.png",
                    "size": image_path.stat().st_size,
                    "content_type": "image/png",
                    "uploaded_at": datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(hours=1)).isoformat(),
                    "uploaded_by": "tester",
                }
            }
        )
    )

    monkeypatch.setattr(main, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(main, "MEDIA_INDEX_PATH", index_path)

    assert main.media_index == {}

    item = main._get_media_or_none("img123")

    assert item is not None
    assert item["path"] == str(image_path)
    assert "img123" in main.media_index


def test_cleanup_expired_media_removes_disk_and_index_entry(tmp_path, monkeypatch):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    image_path = media_dir / "expired.png"
    image_path.write_bytes(b"png")
    index_path = media_dir / "media_index.json"

    monkeypatch.setattr(main, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(main, "MEDIA_INDEX_PATH", index_path)

    main.media_index["img_expired"] = {
        "image_id": "img_expired",
        "path": str(image_path),
        "filename": "expired.png",
        "size": image_path.stat().st_size,
        "content_type": "image/png",
        "uploaded_at": datetime.utcnow().isoformat(),
        "expires_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat(),
        "uploaded_by": "tester",
    }
    main._persist_media_index()

    main._cleanup_expired_media()

    assert "img_expired" not in main.media_index
    assert image_path.exists() is False
    assert json.loads(index_path.read_text()) == {}
