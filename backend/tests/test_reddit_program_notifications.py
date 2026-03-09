from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reddit_program_notifications import RedditProgramNotificationService


class _FakeGmailClient:
    def __init__(self):
        self.calls = []

    async def send_email(self, *, to_email, subject, body):
        self.calls.append({"to_email": to_email, "subject": subject, "body": body})
        return {"id": "msg_123", "threadId": "thread_123"}


def test_send_program_email_records_sent_log():
    program = {
        "id": "reddit_program_test",
        "spec": {
            "notification_config": {
                "email_enabled": True,
                "recipient_email": "nikitalienov@gmail.com",
            }
        },
        "notification_log": [],
    }
    client = _FakeGmailClient()
    service = RedditProgramNotificationService(gmail_client=client)

    import asyncio

    asyncio.run(
        service.send_program_email(
            program,
            key="created",
            kind="created",
            subject="program created",
            body="body",
        )
    )

    assert len(client.calls) == 1
    assert program["notification_log"][0]["state"] == "sent"
    assert program["notification_log"][0]["metadata"]["message_id"] == "msg_123"
