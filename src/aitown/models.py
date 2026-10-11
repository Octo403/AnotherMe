from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field, model_validator


class AgentProfile(BaseModel):
    id: str = Field(default="anotherme", min_length=1)
    name: str = Field(default="AnotherMe", min_length=1)
    language: str = Field(default="中文", min_length=1)
    persona: str = "根据有来源的资料回答，不编造本人经历；表达风格仅参考已启用的本人训练样本。"


class ResultBinding(BaseModel):
    model_config = {'extra': 'forbid'}
    argument: str = Field(min_length=1, max_length=100)
    call_index: int = Field(ge=0, le=2, description='此前调用的零起始序号；第一个调用不能有任何 bindings')
    field: str = Field(min_length=1, max_length=100)


class SkillCall(BaseModel):
    model_config = {'extra': 'forbid'}
    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    bindings: list[ResultBinding] = Field(default_factory=list, max_length=4,
        description='没有依赖时必须为空。只对来自此前结果的参数填写；该参数不要同时出现在 arguments。')


class ReferenceResolution(BaseModel):
    kind: Literal['person', 'event', 'task']
    expression: str
    selected: str | None = None
    candidates: list[str] = Field(default_factory=list)
    turn_ids: list[int] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    model_config = {'extra': 'forbid'}
    original_question: str = ''
    intent: Literal["chat", "knowledge", "skill"]
    query: str = Field(min_length=1)
    calls: list[SkillCall] = Field(default_factory=list, max_length=3)
    candidate_calls: list[SkillCall] = Field(default_factory=list, max_length=3)
    plan: list[Literal['rag', 'skill']] = Field(default_factory=list, max_length=2)
    confidence: Literal['high', 'medium', 'low'] = 'medium'
    confidence_reason: str = ''
    needs_clarification: bool = False
    clarification_question: str = ''
    retrieval_query: str = ''
    purpose: Literal['general', 'personal'] = 'general'
    subject: str | None = None
    as_of: int | None = None
    time_from: int | None = None
    time_to: int | None = None

    @model_validator(mode='after')
    def ordered_plan(self):
        if len(set(self.plan)) != len(self.plan):
            raise ValueError('A plan cannot repeat a node; extra retrieval uses a separate counter')
        if self.time_from is not None and self.time_to is not None and self.time_from > self.time_to:
            raise ValueError('Invalid time interval')
        return self


class ToolPlan(BaseModel):
    calls: list[SkillCall] = Field(default_factory=list, max_length=3)


class ErrorInfo(BaseModel):
    category: Literal['model', 'structured', 'database', 'retrieval', 'skill', 'checkpoint', 'reference']
    phase: str
    code: str
    message: str
    status_code: int | None = None


class ModelCall(BaseModel):
    phase: str
    attempt: int = 1
    seconds: float = 0
    status: Literal['ok', 'failed', 'repair'] = 'ok'
    status_code: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_request: bool = True


class ExecutionStep(BaseModel):
    node: str
    attempt: int = 1
    status: Literal['ok', 'failed', 'clarification', 'retry', 'skipped'] = 'ok'
    reason: str = ''


class Claim(BaseModel):
    text: str
    subject: Literal['general', 'self', 'task', 'other'] = 'general'
    subject_id: str | None = None
    source_refs: list[str] = Field(default_factory=list, max_length=8)
    tool_refs: list[int] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def identified_person(self):
        if self.subject == 'other' and not self.subject_id:
            raise ValueError('An other-person claim requires the exact source subject_id')
        return self


class CheckIssue(BaseModel):
    code: str
    message: str


class AnswerPayload(BaseModel):
    answer: str = Field(min_length=1, max_length=6000)
    source_refs: list[str] = Field(default_factory=list, max_length=12)
    claims: list[Claim] = Field(default_factory=list, max_length=12)
    missing_parts: list[str] = Field(default_factory=list, max_length=8,
        description='尚未完成的用户任务部分；已在回答说明的压缩正文缺失或省略，不是未完成任务')


class ReviewPayload(AnswerPayload):
    corrections: list[str] = Field(default_factory=list)
    needs_retrieval: bool = False
    retrieval_query: str = ''
    evidence_sufficient: bool = True


class ReviewRecord(BaseModel):
    round: int
    draft: AnswerPayload
    deterministic_issues: list[CheckIssue] = Field(default_factory=list)
    model_review: ReviewPayload | None = None
    final_issues: list[CheckIssue] = Field(default_factory=list)
    changed: bool = False


class Citation(BaseModel):
    source: str
    chunk: int
    text: str
    score: float
    source_ids: list[int] = Field(default_factory=list)
    source_record_ids: list[int] = Field(default_factory=list)
    subject: str | None = None
    timestamp: int | None = None
    occurred_at: int | None = None
    recorded_at: int | None = None
    valid_from: int | None = None
    valid_to: int | None = None
    rerank_score: float | None = None
    external_url: str | None = None
    locations: list[int] = Field(default_factory=list)
    location_kind: str | None = None
    question_id: int | None = None

    @property
    def ref(self):
        return f'{self.source}#{self.chunk}'


class SkillResult(BaseModel):
    name: str
    ok: bool
    data: Any = None
    error: str | None = None


class Reply(BaseModel):
    agent: str
    original_question: str
    rewritten_question: str
    answer: str
    intent: str
    mode: str
    sources: list[Citation] = Field(default_factory=list)
    skills: list[SkillResult] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    style_examples: list[str] = Field(default_factory=list)
    profile_evidence: list[Citation] = Field(default_factory=list)
    tool_calls: list[SkillCall] = Field(default_factory=list)
    correction_count: int = Field(default=0, ge=0)
    retrieval_status: str | None = None
    retrieval_details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
    history_source_ids: list[int] = Field(default_factory=list)
    summary_source_ids: list[int] = Field(default_factory=list)
    decision: Decision | None = None
    references: list[ReferenceResolution] = Field(default_factory=list)
    draft_answer: str | None = None
    reviews: list[ReviewRecord] = Field(default_factory=list)
    execution: list[ExecutionStep] = Field(default_factory=list)
    errors: list[ErrorInfo] = Field(default_factory=list)
    model_calls: list[ModelCall] = Field(default_factory=list)
    supplemental_count: int = Field(default=0, ge=0, le=1)
    stop_reason: str = 'answered'
    answer_source_refs: list[str] = Field(default_factory=list)


class AgentState(TypedDict, total=False):
    resume_refreshed: bool
    statement_saved: bool
    plan_cursor: int
    supplemental_count: int
    retrieval_attempts: int
    supplement_query: str
    needs_supplement: bool
    references: list[ReferenceResolution]
    reviews: list[ReviewRecord]
    draft_answer: str | None
    execution: list[ExecutionStep]
    errors: list[ErrorInfo]
    model_calls: list[ModelCall]
    stop_reason: str
    request_id: str
    started_at: float
    context_expires_at: float
    history_source_ids: list[int]
    summary_source_ids: list[int]
    history_summary: dict[str, Any]
    user_statement: Any
    decision: Decision | None
    reply: Reply | None
    retrieval_status: str | None
    retrieval_details: dict[str, Any]
    personal_query: bool
    style_scope: str
    question_course: str
    response_scene: str | None
    style_details: list[Any]
    profile_details: list[Any]
    feedback_details: dict[str, Any]
    session: str
    agent: AgentProfile
    input: str
    original_question: str
    rewritten_question: str
    style_examples: list[str]
    profile_evidence: list[Citation]
    tool_calls: list[SkillCall]
    correction_count: int
    history: list[dict[str, str]]
    sources: list[Citation]
    skills: list[SkillResult]
    trace: list[str]
    warnings: list[str]
