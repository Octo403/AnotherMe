from typing import Literal

from pydantic import BaseModel, Field, model_validator

Category = Literal["event", "preference", "long_term", "plan", "other_person", "inference", "unknown"]


class Evidence(BaseModel):
    message_id: int
    batch_id: str
    source_id: int
    timestamp: int
    direction: str
    speaker_id: str
    excerpt: str


class Memory(BaseModel):
    entry_key: str
    episode_id: int | None = None
    category: Category
    subject: str = "unknown"
    attribute: str = ""
    value: str = ""
    occurred_at: int | None = None
    recorded_at: int
    valid_from: int | None = None
    valid_to: int | None = None
    scope: str = "unknown"
    relationship: str = "unknown"
    split: Literal["train", "dev", "test", "unassigned"] = "unassigned"
    basis: Literal["direct", "inferred", "unknown"] = "unknown"
    status: Literal["candidate", "confirmed", "unknown", "rejected", "superseded"] = "candidate"
    summary: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    updates_preference: bool = False

    @model_validator(mode="after")
    def no_inferred_certainty(self):
        if self.status == "confirmed" and (self.basis != "direct" or self.category in ("inference", "unknown")):
            raise ValueError("Inference and unknown memories cannot be confirmed facts")
        if self.status == "confirmed" and not self.evidence:
            raise ValueError("Confirmed memory requires evidence")
        return self
