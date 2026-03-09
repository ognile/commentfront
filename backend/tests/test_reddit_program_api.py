import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from reddit_mission_store import RedditMissionStore


def test_create_reddit_mission_persists_target_comment_url(tmp_path, monkeypatch):
    temp_store = RedditMissionStore(file_path=str(tmp_path / "reddit_missions.json"))
    monkeypatch.setattr(main, "reddit_mission_store", temp_store)

    request = main.RedditMissionCreateRequest(
        profile_name="reddit_amy",
        action="reply_comment",
        target_comment_url="https://www.reddit.com/r/womenshealth/comments/abc123/endometrial_biopsy/comment/xyz789/",
        exact_text="this is the reply",
        cadence=main.RedditMissionCadence(type="once"),
    )

    asyncio.run(main.create_reddit_mission(request=request, current_user={"username": "tester"}))
    stored = temp_store.list_missions()[0]

    assert stored["target_comment_url"].endswith("/comment/xyz789/")
