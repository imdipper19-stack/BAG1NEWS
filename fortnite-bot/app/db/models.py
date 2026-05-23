"""
SQLAlchemy 2.0 ORM models for the Fortnite AI Telegram Bot.

Tables:
    - sources         : news/data sources configuration
    - raw_items       : raw collected items from sources
    - posts           : AI-generated posts ready for publishing
    - published_posts : log of posts published to Telegram
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


class Source(Base):
    """A news or data source (e.g. Fortnite-API, HYPEX RSS, Reddit)."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source_level: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    # Relationships
    raw_items: Mapped[list["RawItem"]] = relationship("RawItem", back_populates="source")

    def __repr__(self) -> str:
        return f"<Source id={self.id} name={self.name!r} type={self.type!r}>"


class RawItem(Base):
    """A raw collected item from a source before AI processing."""

    __tablename__ = "raw_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sources.id"), nullable=True
    )
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(Text, unique=True, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_official: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_leak: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    published_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, server_default=func.now()
    )

    # Relationships
    source: Mapped[Optional["Source"]] = relationship("Source", back_populates="raw_items")
    posts: Mapped[list["Post"]] = relationship("Post", back_populates="raw_item")

    def __repr__(self) -> str:
        return f"<RawItem id={self.id} title={self.title!r} url={self.url!r}>"


class Post(Base):
    """An AI-generated post derived from a raw item."""

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_item_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("raw_items.id"), nullable=True
    )
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(
        Text, default="draft", server_default="draft"
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, server_default=func.now()
    )

    # Relationships
    raw_item: Mapped[Optional["RawItem"]] = relationship("RawItem", back_populates="posts")
    published_posts: Mapped[list["PublishedPost"]] = relationship(
        "PublishedPost", back_populates="post"
    )

    def __repr__(self) -> str:
        return f"<Post id={self.id} status={self.status!r} score={self.score}>"


class PublishedPost(Base):
    """A record of a post that has been published to Telegram."""

    __tablename__ = "published_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("posts.id"), nullable=True
    )
    telegram_message_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    channel_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, server_default=func.now()
    )

    # Relationships
    post: Mapped[Optional["Post"]] = relationship("Post", back_populates="published_posts")

    def __repr__(self) -> str:
        return (
            f"<PublishedPost id={self.id} post_id={self.post_id} "
            f"channel_id={self.channel_id!r}>"
        )
