"""Shared types for the Content Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional

ContentType = Literal["video", "blog", "social", "newsletter"]
Length = Literal["short", "medium", "long"]
Status = Literal["draft", "scheduled", "published"]


@dataclass
class Brief:
    """Input describing what content to produce."""

    type: ContentType
    topic: str
    audience: Optional[str] = None
    tone: Optional[str] = None
    platform: Optional[str] = None
    length: Length = "medium"
    schedule_at: Optional[datetime] = None


@dataclass
class ContentDraft:
    """A produced piece of content, ready to format / schedule / publish."""

    id: str
    type: ContentType
    title: str
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)
    scheduled_for: Optional[datetime] = None
    status: Status = "draft"
