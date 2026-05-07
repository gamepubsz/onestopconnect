"""Smoke tests for the Content Agent."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from content_agent import Brief, ContentAgent
from content_agent.formatters import truncate_for_platform


@pytest.mark.parametrize("ctype", ["video", "blog", "social", "newsletter"])
def test_each_content_type_produces_a_draft(ctype: str) -> None:
    draft = ContentAgent().run(Brief(type=ctype, topic="shipping faster"))  # type: ignore[arg-type]
    assert draft.type == ctype
    assert draft.title
    assert draft.body
    assert draft.status == "draft"


def test_unknown_type_raises() -> None:
    with pytest.raises(ValueError):
        ContentAgent().run(Brief(type="podcast", topic="x"))  # type: ignore[arg-type]


def test_empty_topic_raises() -> None:
    with pytest.raises(ValueError):
        ContentAgent().run(Brief(type="blog", topic="   "))


def test_scheduling_marks_status() -> None:
    when = datetime.now(timezone.utc) + timedelta(hours=1)
    agent = ContentAgent()
    draft = agent.run(Brief(type="social", topic="ai agents", schedule_at=when))
    assert draft.status == "scheduled"
    assert draft.scheduled_for == when
    assert agent.scheduler.items[0].draft.id == draft.id


def test_truncate_for_platform_respects_x_limit() -> None:
    text = "a" * 400
    out = truncate_for_platform(text, "x")
    assert len(out) <= 280
    assert out.endswith("…")
