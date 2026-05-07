"""In-memory scheduler. Replace with a real queue/cron when wiring publish APIs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List

from .types import ContentDraft


@dataclass
class ScheduledItem:
    draft: ContentDraft
    scheduled_for: datetime


@dataclass
class Scheduler:
    items: List[ScheduledItem] = field(default_factory=list)

    def schedule(self, draft: ContentDraft, when: datetime) -> ContentDraft:
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        draft.scheduled_for = when
        draft.status = "scheduled"
        self.items.append(ScheduledItem(draft=draft, scheduled_for=when))
        return draft

    def due(self, now: datetime | None = None) -> List[ContentDraft]:
        now = now or datetime.now(timezone.utc)
        return [item.draft for item in self.items if item.scheduled_for <= now]
