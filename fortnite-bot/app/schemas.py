"""Pydantic schemas for unified item representation across the pipeline."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class RawItem(BaseModel):
    """Unified normalized format for any source item."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    source: str  # e.g. "fortnite-api/shop", "x.com/HYPEX"
    source_level: int = Field(ge=1, le=4, description="1=official, 2=API, 3=leak, 4=trend")
    title: str
    url: str
    content: str = ""
    image_url: str = ""
    category: str = "general"
    published_at: Optional[datetime] = None
    is_official: bool = False
    is_leak: bool = False


class ScoredItem(RawItem):
    """RawItem with scoring information."""

    score: int = Field(ge=0, le=100)
    score_breakdown: dict = Field(default_factory=dict)
    publish_decision: str = "skip"  # immediate, conditional, digest, skip


class WrittenPost(BaseModel):
    """A fully written post ready for publishing."""

    raw_item: RawItem
    title: str  # post headline
    body: str  # full post text in Russian
    image_prompt: str
    image_url: str = ""  # local path or remote URL
    score: int
