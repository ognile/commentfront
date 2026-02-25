"""
Premium automation request/response models.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Literal

from pydantic import BaseModel, Field, validator


CasingMode = Literal["natural_mixed", "mostly_lowercase", "strict_lowercase"]
JoinPendingPolicy = Literal["try_next_group", "wait", "fail_run"]
ShareTarget = Literal["own_feed", "group", "story"]


class RandomWindow(BaseModel):
    start_hour: int = Field(..., ge=0, le=23)
    end_hour: int = Field(..., ge=1, le=24)

    @validator("end_hour")
    def validate_range(cls, v: int, values: Dict[str, int]) -> int:
        start = values.get("start_hour")
        if start is not None and v <= start:
            raise ValueError("end_hour must be greater than start_hour")
        return v


class FeedPlan(BaseModel):
    total_posts: int = Field(4, ge=1)
    character_posts: int = Field(3, ge=0)
    ambient_posts: int = Field(1, ge=0)

    @validator("ambient_posts")
    def validate_totals(cls, v: int, values: Dict[str, int]) -> int:
        total = values.get("total_posts", 0)
        character = values.get("character_posts", 0)
        if character + v != total:
            raise ValueError("character_posts + ambient_posts must equal total_posts")
        return v


class GroupDiscovery(BaseModel):
    topic_seed: str = "menopause groups"
    allow_join_new: bool = True
    join_pending_policy: JoinPendingPolicy = "try_next_group"


class EngagementRecipe(BaseModel):
    likes_per_cycle: int = Field(2, ge=0)
    shares_per_cycle: int = Field(1, ge=0)
    replies_per_cycle: int = Field(1, ge=0)
    share_target: ShareTarget = "own_feed"


class ScheduleSpec(BaseModel):
    start_at: Optional[str] = None
    duration_days: int = Field(7, ge=1)
    random_windows: List[RandomWindow] = Field(default_factory=lambda: [RandomWindow(start_hour=8, end_hour=22)])
    timezone: str = "America/New_York"


class VerificationContract(BaseModel):
    required_feed_posts: Optional[int] = None
    required_group_posts: Optional[int] = None
    required_likes: Optional[int] = None
    required_shares: Optional[int] = None
    required_comment_replies: Optional[int] = None
    required_character_posts: Optional[int] = None
    required_ambient_posts: Optional[int] = None
    require_evidence: bool = True
    require_target_reference: bool = True
    require_action_metadata: bool = True
    require_before_after_screenshots: bool = True
    require_profile_identity: bool = True


class PremiumRunSpec(BaseModel):
    profile_name: str
    feed_plan: FeedPlan = Field(default_factory=FeedPlan)
    group_discovery: GroupDiscovery = Field(default_factory=GroupDiscovery)
    engagement_recipe: EngagementRecipe = Field(default_factory=EngagementRecipe)
    schedule: ScheduleSpec = Field(default_factory=ScheduleSpec)
    verification_contract: VerificationContract = Field(default_factory=VerificationContract)
    metadata: Dict[str, str] = Field(default_factory=dict)


class CharacterProfile(BaseModel):
    persona_description: str
    reference_image_mode: Literal["session_profile_picture", "manual_reference"] = "session_profile_picture"
    manual_reference_image_base64: Optional[str] = None
    character_prompt_hints: List[str] = Field(default_factory=list)
    ambient_prompt_hints: List[str] = Field(default_factory=list)


class ContentPolicy(BaseModel):
    rules_snapshot_version: Optional[str] = None
    casing_mode: CasingMode = "natural_mixed"


class ExecutionPolicy(BaseModel):
    enabled: bool = True
    max_retries: int = Field(1, ge=0, le=5)
    stop_on_first_failure: bool = True
    allow_text_only_if_image_fails: bool = False
    dedupe_precheck_enabled: bool = True
    dedupe_recent_feed_posts: int = Field(5, ge=1, le=20)
    dedupe_threshold: float = Field(0.90, ge=0.5, le=1.0)
    block_on_duplicate: bool = True
    single_submit_guard: bool = True
    tunnel_recovery_cycles: int = Field(2, ge=0, le=10)
    tunnel_recovery_delay_seconds: int = Field(90, ge=15, le=1800)


class PremiumProfileConfig(BaseModel):
    character_profile: CharacterProfile
    content_policy: ContentPolicy = Field(default_factory=ContentPolicy)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)


class RulesSyncRequest(BaseModel):
    negative_patterns_text: Optional[str] = None
    vocabulary_guidance_text: Optional[str] = None
    source_paths: Dict[str, str] = Field(default_factory=dict)
    source_sha: Optional[str] = None


class PremiumRunCreateRequest(BaseModel):
    run_spec: PremiumRunSpec
