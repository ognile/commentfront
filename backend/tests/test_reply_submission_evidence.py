from comment_bot import _has_strong_reply_submission_evidence


def _base_evidence():
    return {
        "submit_clicked": True,
        "image_attached": True,
        "text_after_attach_verified": True,
        "posting_indicator_seen": True,
        "local_comment_text_seen": False,
    }


def test_strong_evidence_accepts_success_with_posting_indicator():
    assert _has_strong_reply_submission_evidence(_base_evidence()) is True


def test_strong_evidence_accepts_success_with_local_text_without_posting_flag():
    evidence = _base_evidence()
    evidence["posting_indicator_seen"] = False
    evidence["local_comment_text_seen"] = True
    assert _has_strong_reply_submission_evidence(evidence) is True


def test_rejects_when_image_not_attached():
    evidence = _base_evidence()
    evidence["image_attached"] = False
    assert _has_strong_reply_submission_evidence(evidence) is False


def test_rejects_when_neither_posting_nor_local_text_seen():
    evidence = _base_evidence()
    evidence["posting_indicator_seen"] = False
    evidence["local_comment_text_seen"] = False
    assert _has_strong_reply_submission_evidence(evidence) is False
