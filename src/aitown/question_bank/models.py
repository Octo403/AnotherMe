from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from ..providers import ProviderConfig

COURSE_ID = 'fdu-set-graph-theory'
COURSE_NAME = '集合与图论'


class BankConfig(BaseModel):
    root: Path = Path('data/question_bank')
    repository: str = 'victkk/FDU-Sharing'
    timeout: float = 40
    max_download_bytes: int = 100 * 1024 * 1024
    max_archive_members: int = 300
    max_archive_bytes: int = 250 * 1024 * 1024
    embedding_dir: Path | None = None
    reranker_dir: Path | None = None
    similar_provider: Literal['none','codex_chatgpt','api'] = 'none'
    api: ProviderConfig = Field(default_factory=ProviderConfig)
    similar_model: str = ''
    similar_images: bool = True
    extractor: Literal['basic','mineru','mineru_api'] = 'basic'
    mineru_api_key: str = Field(default='', repr=False, exclude=True)
    mineru_api_model: Literal['pipeline','vlm'] = 'vlm'
    mineru_python: Path | None = None
    mineru_tier: Literal['flash','basic','standard','advanced'] = 'flash'
    mineru_timeout: float = 900
    pdf_ocr: Literal['none','windows'] = 'none'
    ocr_columns: int = Field(default=1, ge=1, le=2)
    ocr_timeout: float = Field(default=180, gt=0)
    word_timeout: float = Field(default=180, gt=0)

    @property
    def database(self):
        return self.root / 'question_bank.sqlite3'


class Source(BaseModel):
    course_id: str
    repository: str
    path: str
    url: str
    size: int | None = None
    member: str = ''
    category_hint: str = 'unknown'
    year_hint: int | None = None
    exam_hint: str | None = None


class Page(BaseModel):
    ordinal: int
    text: str
    image: str | None = None
    location_kind: str = 'page'
    printed_label: str | None = None


class ImageRegion(BaseModel):
    index: int = Field(ge=0)
    page: int = Field(ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    printed_label: str | None = None


class Question(BaseModel):
    number: str
    text: str
    pages: list[int]
    subquestions: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    question_type: str | None = None
    images: list[str] = Field(default_factory=list)
    image_regions: list[ImageRegion] = Field(default_factory=list)
    rendered_text: str = ''
    dedup_layout: str = ''
    missing: list[str] = Field(default_factory=list)
    # Answers are intentionally absent. They live in a separate table.


class ParsedSource(BaseModel):
    pages: list[Page]
    rendered_pages: list[Page] = Field(default_factory=list)
    questions: list[Question]
    category: str = 'unknown'
    year: int | None = None
    exam: str | None = None
    status: Literal['ready', 'partial', 'unsupported', 'needs_review'] = 'ready'
    notes: list[str] = Field(default_factory=list)


class AnswerPair(BaseModel):
    """Reviewed mapping; answer text is read from current source locations."""
    model_config = {'extra': 'forbid'}
    question_number: str = Field(min_length=1)
    question_text: str = Field(min_length=1)
    locations: list[int] = Field(min_length=1, max_length=1000)
    basis: str = Field(min_length=1)
    missing: list[str] = Field(default_factory=list)


class SearchArgs(BaseModel):
    model_config = {'extra': 'forbid'}
    course: str = COURSE_ID
    query: str = Field(min_length=1, max_length=1000)
    year: int | None = Field(default=None, ge=1900, le=2100)
    exam: Literal['final', 'midterm'] | None = None
    question_type: str | None = None
    category: str | None = None
    limit: int = Field(default=3, ge=1, le=10)
    exclude_ids: list[int] = Field(default_factory=list, max_length=100)
    reference_ids: list[int] = Field(default_factory=list, max_length=3, description='第二轮相似题查询：本会话已返回并选定的题目 ID')


class GetArgs(BaseModel):
    model_config = {'extra': 'forbid'}
    question_ids: list[int] = Field(min_length=1, max_length=10)
    include_answer: bool = False


class StatusArgs(BaseModel):
    model_config = {'extra': 'forbid'}
    course: str = COURSE_ID


class QuestionResult(BaseModel):
    status: str
    question_ids: list[int] = Field(default_factory=list)
    results: list[dict] = Field(default_factory=list)
    count: int = 0
    coverage: dict = Field(default_factory=dict)
    method: str = 'keyword'
    truncated: bool = False
    missing_ids: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    model_calls: list[dict] = Field(default_factory=list)


class StatusResult(BaseModel):
    status: str
    course: str
    coverage: dict = Field(default_factory=dict)
    jobs: list[dict] = Field(default_factory=list)
    files: list[dict] = Field(default_factory=list)
