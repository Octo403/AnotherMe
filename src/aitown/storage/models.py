from typing import Literal

from pydantic import BaseModel


class Source(BaseModel):
    source_kind: Literal["archive_message", "user_statement", "user_correction"] = "archive_message"
    record_id: int | None = None
    message_id: int | None = None
    batch_id: str | None = None
    source_id: int | None = None
    conversation_id: str
    alias: str
    timestamp: int
    direction: str
    speaker_id: str
    status: str
    content: str
    episode_id: int | None = None


class Fact(BaseModel):
    kind: Literal["fact"] = "fact"
    id: int
    subject: str
    attribute: str
    value: str
    valid_from: int | None = None
    valid_to: int | None = None
    status: str


class Trait(BaseModel):
    kind: Literal["trait"] = "trait"
    id: int
    trait: str
    scene: str
    basis: str
    status: str


class Style(BaseModel):
    kind: Literal["style"] = "style"
    id: int
    scene: str
    split: str
    context: str
    target: str
    episode_id: int


class HistoryTurn(BaseModel):
    kind: Literal["history"] = "history"
    id: int
    session_id: str
    question: str
    answer: str
    created: float
