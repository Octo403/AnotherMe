from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..storage.models import Source


class SearchFilter(BaseModel):
    purpose: Literal["general", "personal", "style"] = "general"
    subject: str | None = None
    scope: str | None = None
    as_of: int | None = None
    time_from: int | None = None
    time_to: int | None = None
    scene: str | None = None
    category: Literal['preference', 'event', 'plan', 'long_term', 'other_person'] | None = None

    @model_validator(mode="after")
    def ordered_time(self):
        if self.time_from is not None and self.time_to is not None and self.time_from > self.time_to:
            raise ValueError("time_from must not exceed time_to")
        return self


class Item(BaseModel):
    key: str
    kind: Literal["fact", "memory", "document", "style"]
    entity_id: int
    ordinal: int = 0
    text: str
    subject: str = ""
    scopes: list[str] = Field(default_factory=list)
    timestamp: int | None = None
    recorded_at: int | None = None
    valid_from: int | None = None
    valid_to: int | None = None
    basis: str = "direct"
    status: str = "confirmed"
    category: str = ""
    scene: str = ""
    response_scenes: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)


class Hit(BaseModel):
    item: Item
    score: float
    rerank_score: float | None = None


class SearchResult(BaseModel):
    status: Literal["ok", "no_personal_data", "no_match", "inference_only", "ambiguous_scope", "retrieval_error"]
    hits: list[Hit] = Field(default_factory=list)
    coarse: list[Hit] = Field(default_factory=list)
    ranked: list[Hit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)
    backend: str = ""
