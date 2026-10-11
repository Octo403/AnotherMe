"""User-selected OpenAI-compatible endpoints; secrets never serialize."""
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, model_validator


class ProviderConfig(BaseModel):
    model_config = {'extra': 'forbid'}
    provider: str = 'deepseek'
    base_url: str = 'https://api.deepseek.com'
    model: str = ''
    api_key: str = Field(default='', repr=False, exclude=True, max_length=8192)
    temperature: float = Field(default=.2, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=256, le=32768)
    response_format: str = Field(default='json_object', pattern='^(json_object|plain_json|json_schema)$')
    images: bool = False

    @model_validator(mode='after')
    def endpoint(self):
        url = urlparse(self.base_url)
        local = url.hostname in ('localhost', '127.0.0.1', '::1')
        if not url.hostname or url.username or url.password or url.query or url.fragment or not (
            url.scheme == 'https' or (url.scheme == 'http' and local)
        ):
            raise ValueError('模型地址须为 HTTPS；本机服务可使用 HTTP。地址中不能包含 Key 或查询参数。')
        return self

    def client(self):
        return httpx.Client(base_url=self.base_url.rstrip('/')+'/', timeout=60,
            headers={'Authorization': 'Bearer '+self.api_key} if self.api_key else {},
            trust_env=urlparse(self.base_url).hostname not in ('localhost', '127.0.0.1', '::1'))

    def ready(self):
        return bool(self.model.strip() and (self.api_key.strip() or
            urlparse(self.base_url).hostname in ('localhost', '127.0.0.1', '::1')))

    def structured(self, messages, schema):
        from .config import Settings
        from .llm import CompatibleModel
        settings = Settings(mode='demo', base_url=self.base_url, api_key=self.api_key or 'local',
            model=self.model, temperature=self.temperature, max_tokens=self.max_tokens,
            llm_response_format=self.response_format)
        model = CompatibleModel(settings)
        try:
            return model.structured(messages, schema)
        finally:
            model.close()
