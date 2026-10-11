from typing import Literal

from pydantic import BaseModel, Field

Scene = Literal["agree", "refuse", "comfort", "explain", "task", "uncertain", "chat"]


class StyleHit(BaseModel):
    id: int
    scope: str
    relationship: str = "unknown"
    scenes: list[Scene]
    score: float
    context: list[dict]
    target: str
    source_ids: list[int]
    guidance: str


class ProfileClaim(BaseModel):
    key: str
    text: str
    scope: str
    basis: Literal["direct", "observed", "inferred"]
    status: Literal["confirmed", "candidate"] = "confirmed"
    enabled: bool = True
    source_ids: list[int] = Field(min_length=1)
    trait_id: int | None = None


class StyleProfile(BaseModel):
    name: str = "AnotherMe"
    relationship: str = "unknown"
    claims: list[ProfileClaim] = Field(default_factory=list)
