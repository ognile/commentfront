"""Pydantic models for community simulation feature."""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel


class CommunityPersona(BaseModel):
    profile_name: str
    display_name: Optional[str] = None
    age: Optional[int] = None
    persona_prompt: str  # e.g. "Linda, 52, yoga teacher, recently divorced, grateful for her garden"
    image_style_hints: List[str] = []


class WarmupConfig(BaseModel):
    days: int = 5
    posts_min: int = 2
    posts_max: int = 5
    start_hour: int = 8  # earliest post hour (in timezone)
    end_hour: int = 22  # latest post hour (in timezone)
    timezone: str = "America/New_York"


class CommunityConfig(BaseModel):
    start_date: str  # ISO date e.g. "2026-03-25"
    start_hour: int = 8
    end_hour: int = 22
    timezone: str = "America/New_York"


class CommunityPlanCreate(BaseModel):
    name: str
    phase: str  # "warmup" or "community"
    config: dict = {}


class SheetImportRow(BaseModel):
    profile_name: str
    day: int  # day offset from plan start (1-based)
    action: str  # join_group, post_in_group, reply_to_post, like_post
    target_url: Optional[str] = None
    text: Optional[str] = None
    image_prompt: Optional[str] = None
    image_url: Optional[str] = None


class JoinGroupRequest(BaseModel):
    group_url: str
    profile_names: List[str]
    stagger_minutes: int = 30  # minutes between each join request
