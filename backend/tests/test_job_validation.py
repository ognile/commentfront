import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timedelta

from main import _build_queue_jobs, _validate_queue_jobs, media_index

COMMENT_URL = (
    "https://www.facebook.com/permalink.php?"
    "story_fbid=pfbid02M8r99ZESd75oL6deKBHb8n6hRPMu1u4G6S7B8ykxjyv1tDm8FHrtpQPYapQk8jnWl"
    "&id=61574636237654&comment_id=4418405568392620"
)


def _register_media(tmp_path, image_id: str = "img_test") -> str:
    image_path = tmp_path / "fixture.webp"
    image_path.write_bytes(b"RIFF....WEBP")
    media_index[image_id] = {
        "image_id": image_id,
        "path": str(image_path),
        "filename": "fixture.webp",
        "size": image_path.stat().st_size,
        "content_type": "image/webp",
        "uploaded_at": datetime.utcnow().isoformat(),
        "expires_at": (datetime.utcnow() + timedelta(hours=1)).isoformat(),
    }
    return image_id


def setup_function():
    media_index.clear()


def test_reply_job_validation_positive(tmp_path):
    image_id = _register_media(tmp_path)

    jobs = _build_queue_jobs(
        comments=None,
        jobs=[
            {
                "type": "reply_comment",
                "text": "THIS SHOULD BE LOWERCASED",
                "target_comment_url": COMMENT_URL,
                "image_id": image_id,
            }
        ],
    )

    assert jobs[0]["text"] == "this should be lowercased"

    validation = _validate_queue_jobs(
        url=COMMENT_URL,
        jobs=jobs,
        include_duplicate_guard=False,
    )

    assert validation["valid"] is True
    assert validation["target_comment_id"] == "4418405568392620"


def test_reply_job_validation_requires_comment_id(tmp_path):
    image_id = _register_media(tmp_path)

    jobs = _build_queue_jobs(
        comments=None,
        jobs=[
            {
                "type": "reply_comment",
                "text": "x",
                "target_comment_url": "https://www.facebook.com/permalink.php?id=61574636237654",
                "image_id": image_id,
            }
        ],
    )

    validation = _validate_queue_jobs(
        url="https://www.facebook.com/permalink.php?id=61574636237654",
        jobs=jobs,
        include_duplicate_guard=False,
    )

    assert validation["valid"] is False
    assert any("comment_id" in err for err in validation["errors"])
