"""Course-only model preferences, independent of the personal-agent model."""
import json
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from ..providers import ProviderConfig
from .secrets import protect, unprotect


class ModelPreferences(BaseModel):
    model_config = {'extra': 'forbid'}
    search_mode: Literal['none', 'api', 'codex_chatgpt'] = 'none'
    api: ProviderConfig = Field(default_factory=ProviderConfig)
    codex_model: str = Field(default='', max_length=200)
    codex_images: bool = True
    codex_path: str = Field(default='', max_length=4096)
    extractor: Literal['basic', 'mineru', 'mineru_api'] = 'basic'
    mineru_model: Literal['vlm', 'pipeline'] = 'vlm'
    mineru_token: str = Field(default='', repr=False, exclude=True, max_length=8192)
    remember_keys: bool = False
    clear_api_key: bool = False
    clear_mineru_token: bool = False

    def public(self):
        result = self.model_dump(exclude={'clear_api_key', 'clear_mineru_token'})
        result['api']['has_key'] = bool(self.api.api_key)
        result['mineru_has_token'] = bool(self.mineru_token)
        result['data_scope'] = '仅题库内容与找题消息；个人资料不发送到在线服务'
        return result

    def stored(self):
        result = self.model_dump(exclude={'clear_api_key', 'clear_mineru_token'})
        if self.remember_keys:
            result['protected_api_key'] = protect(self.api.api_key)
            result['protected_mineru_token'] = protect(self.mineru_token)
        return json.dumps(result, ensure_ascii=False)

    @classmethod
    def restore(cls, value):
        data = json.loads(value)
        data.setdefault('api', {})['api_key'] = unprotect(data.pop('protected_api_key', ''))
        data['mineru_token'] = unprotect(data.pop('protected_mineru_token', ''))
        result = cls.model_validate(data)
        # Session-only keys deliberately disappear after restarting the service.
        if result.search_mode == 'api' and not result.api.ready():
            result.search_mode = 'none'
        if result.extractor == 'mineru_api' and not result.mineru_token:
            result.extractor = 'basic'
        return result


def api_operation(config, operation):
    from urllib.parse import urlparse
    credentials=bool(config.api_key or urlparse(config.base_url).hostname in ('localhost','127.0.0.1','::1'))
    if not credentials or (operation!='models' and not config.model.strip()):
        raise ValueError('请填写具体模型型号和 API Key；本机无鉴权接口可留空 Key。')
    try:
        with config.client() as client:
            if operation == 'models':
                response = client.get('models')
                response.raise_for_status()
                data = response.json()
                models = data.get('data') if isinstance(data, dict) else None
                if not isinstance(models, list):
                    raise ValueError('服务商未返回型号列表，请检查是否支持 /models 接口。')
                choices = list(dict.fromkeys(m['id'] for m in models[:1000]
                    if isinstance(m, dict) and isinstance(m.get('id'), str) and m['id'].strip() and len(m['id']) <= 200))
                if not choices:
                    raise ValueError('服务商未返回可选择的型号，请检查 Key 权限或 API 地址。')
                return {'models': choices}
            # A small structured synthetic request tests the actual protocol;
            # no question, history, private memory or images are transmitted.
            class Probe(BaseModel):
                ok: bool
            result = config.structured([{'role': 'user', 'content': '合成连接测试。只返回 JSON：{"ok":true}'}], Probe)
            if not result.ok:
                raise ValueError('模型未通过 JSON 连接测试，请检查型号与响应格式。')
            return {'ok': True, 'model': config.model, 'message': '连接与 JSON 输出测试通过；尚未验证真实题目识别质量。'}
    except httpx.HTTPStatusError as exc:
        raise ValueError(f'模型接口请求失败（HTTP {exc.response.status_code}），请检查 Key、型号、地址和额度。') from None
    except (httpx.RequestError, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError('模型接口连接或返回格式异常，请检查地址和服务协议。') from None
