import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

from .models import AgentProfile
from .providers import ProviderConfig


class Settings(BaseModel):
    model_config = {"validate_assignment": True}
    agent: AgentProfile = Field(default_factory=AgentProfile)
    mode: Literal["demo", "live"] = "demo"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = Field(default="", repr=False, exclude=True)
    model: str = ""
    embedding_model: str = ""
    knowledge_dir: Path = Path("data/knowledge")
    memory_db: Path = Path("data/runtime/memory.sqlite3")
    history_turns: int = Field(default=8, ge=5, le=100)
    ttl_hours: float = Field(default=168, gt=0)
    threshold: float = Field(default=0.15, ge=0, le=1)
    source_scope: Literal["synthetic", "local_documents"] = "synthetic"
    temperature: float = Field(default=0.3, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=1)
    style_database: Path | None = None
    style_profile: Path | None = None
    style_scope: str = ""
    style_feedback: Path | None = None
    retrieval_database: Path | None = None
    retrieval_index_dir: Path = Path("data/runtime/retrieval")
    local_embedding_dir: Path | None = None
    local_reranker_dir: Path | None = None
    semantic_threshold: float = Field(default=.45, ge=0, le=1)
    rerank_threshold: float = Field(default=.01, ge=0, le=1)
    retrieval_top_n: int = Field(default=20, ge=1)
    retrieval_top_k: int = Field(default=4, ge=1)
    runtime_database: Path | None = None
    checkpoint_db: Path | None = None
    context_ttl_seconds: float = Field(default=600, gt=0)
    context_max_chars: int = Field(default=6500, ge=2000, le=30000)
    json_repair_attempts: int = Field(default=1, ge=0, le=1)
    enable_memory_correction: bool = False
    question_bank_dir: Path = Path('data/question_bank')
    question_similar_provider: Literal['none','codex_chatgpt','api'] = 'none'
    question_api: ProviderConfig = Field(default_factory=ProviderConfig)
    question_extractor: Literal['basic','mineru','mineru_api'] = 'basic'
    mineru_api_key: str = Field(default='', repr=False, exclude=True)
    mineru_api_model: Literal['pipeline','vlm'] = 'vlm'
    mineru_python: Path | None = None
    mineru_tier: Literal['flash','basic','standard','advanced'] = 'flash'
    question_pdf_ocr: Literal['none','windows'] = 'none'
    question_ocr_columns: int = Field(default=1, ge=1, le=2)
    question_similar_model: str = ''
    question_similar_images: bool = True
    question_codex_path: str = Field(default='', max_length=4096)
    llm_response_format: Literal['json_schema', 'json_object', 'plain_json'] = 'json_object'

    @model_validator(mode="after")
    def validate_live(self):
        if self.mode == "live" and (not self.api_key.strip() or not self.model.strip()):
            raise ValueError("live 模式需要配置 LLM_API_KEY 和 LLM_MODEL")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("LLM_BASE_URL 必须为 http:// 或 https:// 地址")
        if bool(self.style_database) != bool(self.style_profile):
            raise ValueError("STYLE_DATABASE 与 STYLE_PROFILE 必须同时指定")
        if self.style_feedback and not self.style_database:
            raise ValueError("STYLE_FEEDBACK 需要 STYLE_DATABASE 和 STYLE_PROFILE")
        if self.retrieval_top_k > self.retrieval_top_n:
            raise ValueError("RETRIEVAL_TOP_K 不能大于 RETRIEVAL_TOP_N")
        if (self.local_embedding_dir or self.local_reranker_dir) and not self.retrieval_database:
            raise ValueError("本地检索模型需要 RETRIEVAL_DATABASE")
        runtime = self.runtime_database or self.retrieval_database or self.style_database or self.memory_db.with_suffix('.application.sqlite3')
        checkpoint = self.checkpoint_db or runtime.with_name(runtime.stem+'-checkpoints.sqlite3')
        application_paths = (runtime, self.memory_db, self.retrieval_database, self.style_database)
        if checkpoint.resolve() in {path.resolve() for path in application_paths if path is not None}:
            raise ValueError('CHECKPOINT_DB 必须独立于应用库和旧历史库')
        return self

    @classmethod
    def from_env(cls, mode: str | None = None):
        load_dotenv()
        return cls(
            agent=AgentProfile(name=os.getenv("AGENT_NAME", "AnotherMe"),
                               language=os.getenv("AGENT_LANGUAGE", "中文")),
            mode=mode or os.getenv("AITOWN_MODE", "demo"),
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
            api_key=os.getenv("LLM_API_KEY", ""),
            model=os.getenv("LLM_MODEL", ""),
            embedding_model=os.getenv("EMBEDDING_MODEL", ""),
            knowledge_dir=os.getenv("KNOWLEDGE_DIR", "data/knowledge"),
            memory_db=os.getenv("MEMORY_DB", "data/runtime/memory.sqlite3"),
            history_turns=os.getenv("HISTORY_TURNS", "8"),
            ttl_hours=os.getenv("MEMORY_TTL_HOURS", "168"),
            threshold=os.getenv("RETRIEVAL_THRESHOLD", "0.15"),
            source_scope=os.getenv("SOURCE_SCOPE", "synthetic"),
            temperature=os.getenv("LLM_TEMPERATURE", "0.3"),
            max_tokens=os.getenv("LLM_MAX_TOKENS", "1024"),
            style_database=os.getenv("STYLE_DATABASE") or None,
            style_profile=os.getenv("STYLE_PROFILE") or None,
            style_scope=os.getenv("STYLE_SCOPE", ""),
            style_feedback=os.getenv("STYLE_FEEDBACK") or None,
            retrieval_database=os.getenv("RETRIEVAL_DATABASE") or None,
            retrieval_index_dir=os.getenv("RETRIEVAL_INDEX_DIR", "data/runtime/retrieval"),
            local_embedding_dir=os.getenv("LOCAL_EMBEDDING_DIR") or None,
            local_reranker_dir=os.getenv("LOCAL_RERANKER_DIR") or None,
            semantic_threshold=os.getenv("SEMANTIC_THRESHOLD", ".45"),
            rerank_threshold=os.getenv("RERANK_THRESHOLD", ".01"),
            retrieval_top_n=os.getenv("RETRIEVAL_TOP_N", "20"),
            retrieval_top_k=os.getenv("RETRIEVAL_TOP_K", "4"),
            runtime_database=os.getenv('RUNTIME_DATABASE') or None,
            checkpoint_db=os.getenv('CHECKPOINT_DB') or None,
            context_ttl_seconds=os.getenv('CONTEXT_TTL_SECONDS', '600'),
            context_max_chars=os.getenv('CONTEXT_MAX_CHARS', '6500'),
            json_repair_attempts=os.getenv('JSON_REPAIR_ATTEMPTS', '1'),
            enable_memory_correction=os.getenv('ENABLE_MEMORY_CORRECTION', 'false'),
            question_bank_dir=os.getenv('QUESTION_BANK_DIR', 'data/question_bank'),
            question_similar_provider=os.getenv('QUESTION_SIMILAR_PROVIDER', 'none'),
            question_api=ProviderConfig(base_url=os.getenv('QUESTION_API_BASE_URL', 'https://api.deepseek.com'),
                provider=os.getenv('QUESTION_API_PROVIDER', 'deepseek'), model=os.getenv('QUESTION_API_MODEL', ''),
                api_key=os.getenv('QUESTION_API_KEY', ''), images=os.getenv('QUESTION_API_IMAGES', 'false')),
            question_extractor=os.getenv('QUESTION_EXTRACTOR', 'basic'),
            mineru_api_key=os.getenv('MINERU_API_KEY', ''),
            mineru_api_model=os.getenv('MINERU_API_MODEL', 'vlm'),
            mineru_python=os.getenv('MINERU_PYTHON') or None,
            mineru_tier=os.getenv('MINERU_TIER', 'flash'),
            question_pdf_ocr=os.getenv('QUESTION_PDF_OCR', 'none'),
            question_ocr_columns=os.getenv('QUESTION_OCR_COLUMNS', '1'),
            question_similar_model=os.getenv('QUESTION_SIMILAR_MODEL', ''),
            question_similar_images=os.getenv('QUESTION_SIMILAR_IMAGES', 'true'),
            question_codex_path=os.getenv('AITOWN_CODEX_PATH', ''),
            llm_response_format=os.getenv('LLM_RESPONSE_FORMAT', 'json_object'),
        )
