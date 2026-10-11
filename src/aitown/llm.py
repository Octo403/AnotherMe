import json
import time
from copy import deepcopy
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from .config import Settings
from .errors import AgentError, error_info
from .models import AnswerPayload, ModelCall


class CompatibleModel:
    """Small HTTP adapter for providers exposing /chat/completions and /embeddings."""
    supports_tool_reference_schema = True
    def __init__(self, settings: Settings):
        self.settings = settings
        if not settings.api_key or not settings.model:
            raise ValueError("live 模式需要配置 LLM_API_KEY 和 LLM_MODEL")
        self.client = httpx.Client(
            base_url=settings.base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.api_key}"}, timeout=60,
            trust_env=urlparse(settings.base_url).hostname not in ("127.0.0.1", "localhost", "::1"),
        )
        self.events = []
        self.call_mode = 'validated_json_plan'

    def _post(self, endpoint, payload, headers=None):
        try:
            response = self.client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            raise AgentError('model', 'provider', 'http_error', f'模型 HTTP 请求失败（{code}）', code) from exc
        except httpx.RequestError as exc:
            raise AgentError('model', 'provider', 'transport_error', '模型服务连接或等待失败') from exc
        except ValueError as exc:
            raise AgentError('structured', 'provider', 'response_json', '模型服务返回了无效 JSON') from exc

    def structured(self, messages: list[dict], schema):
        return self._structured(messages, schema, schema.model_json_schema())

    def structured_evidence(self, messages, schema, sources, *, tools=None):
        response_schema = schema.model_json_schema()
        refs = [source.ref for source in sources]
        properties = response_schema['properties']
        claim = response_schema.get('$defs', {}).get('Claim', {}).get('properties', {})
        for fields in (properties, claim):
            if 'source_refs' in fields:
                if refs:
                    fields['source_refs']['items']['enum'] = refs
                else:
                    fields['source_refs']['maxItems'] = 0
        if 'subject_id' in claim:
            claim['subject_id'] = {'enum': [None, *sorted({s.subject for s in sources if s.subject})], 'default': None}
        if tools is not None and 'tool_refs' in claim:
            valid = [i for i, tool in enumerate(tools) if tool.ok]
            if valid:
                claim['tool_refs']['items']['enum'] = valid
            else:
                claim['tool_refs']['maxItems'] = 0
        if claim:
            # Each personal claim carries its own source rather than relying on
            # the answer's top-level citations. The ordinary checker still runs.
            base = deepcopy(response_schema['$defs']['Claim'])
            general = deepcopy(base)
            general['properties']['subject'] = {'enum': ['general', 'task']}
            options = [general]
            for actor in sorted({s.subject for s in sources if s.subject}):
                personal = deepcopy(base)
                personal['properties']['subject'] = {'const': 'self' if actor == 'self' else 'other'}
                personal['properties']['subject_id'] = {'const': actor}
                personal['properties']['source_refs']['minItems'] = 1
                personal['properties']['source_refs']['items']['enum'] = [s.ref for s in sources if s.subject == actor]
                personal['required'] = list(dict.fromkeys([*personal.get('required', []), 'source_refs', 'subject', 'subject_id']))
                options.append(personal)
            response_schema['$defs']['Claim']['anyOf'] = options
        return self._structured(messages, schema, response_schema)

    def _structured(self, messages, schema, response_schema):
        instruction = '输出符合 Schema 的数据实例，不要复述 Schema，不要输出 properties、$defs 或 type。'+\
                      '只输出完整 JSON。以下 Schema 仅约束结构，不是回答内容：'+json.dumps(response_schema, ensure_ascii=False)
        request_messages = [{"role": "system", "content": instruction}, *messages]
        for attempt in range(1, self.settings.json_repair_attempts+2):
            started = time.perf_counter()
            metric = ModelCall(phase=schema.__name__, attempt=attempt)
            try:
                payload = {
                    'model': self.settings.model, 'temperature': self.settings.temperature,
                    'max_tokens': self.settings.max_tokens, 'messages': request_messages,
                }
                if self.settings.llm_response_format == 'json_schema':
                    payload['response_format'] = {'type': 'json_schema', 'json_schema': {'name': schema.__name__, 'schema': response_schema}}
                elif self.settings.llm_response_format == 'json_object':
                    payload['response_format'] = {'type': 'json_object'}
                data = self._post('chat/completions', payload)
                metric.input_tokens = data.get('usage', {}).get('prompt_tokens')
                metric.output_tokens = data.get('usage', {}).get('completion_tokens')
                try:
                    content = data['choices'][0]['message']['content']
                except (KeyError, IndexError, TypeError) as exc:
                    raise AgentError('structured', 'provider', 'response_shape', '模型回复缺少结构化内容') from exc
                try:
                    return schema.model_validate_json(content)
                except (ValidationError, TypeError, ValueError) as exc:
                    metric.status = 'repair'
                    if attempt > self.settings.json_repair_attempts:
                        metric.status = 'failed'
                        raise AgentError('structured', 'provider', 'json_validation', '结构化回复校验失败，已达到修复次数上限') from exc
                    fields = [str(error['loc']) for error in exc.errors(include_input=False, include_url=False)] if isinstance(exc, ValidationError) else []
                    # Repair goes to the same provider already authorized for this
                    # turn; neither the malformed text nor tokens enter logs.
                    request_messages = [*request_messages,
                        {'role': 'assistant', 'content': str(content)[:2000]},
                        {'role': 'user', 'content': '仅修复 JSON 格式和字段类型，不改变任务；返回完整 JSON。错误字段：'+json.dumps(fields)}]
            except AgentError as exc:
                metric.status, metric.status_code = 'failed', exc.info.status_code
                raise
            finally:
                metric.seconds = time.perf_counter()-started
                self.events.append(metric)

    def embeddings(self, texts: list[str]):
        data = self._post("embeddings", {
            "model": self.settings.embedding_model, "input": texts,
        })
        try:
            return [item["embedding"] for item in sorted(data['data'], key=lambda x: x['index'])]
        except (KeyError, TypeError) as exc:
            raise AgentError('structured', 'embedding', 'response_shape', '向量接口返回格式不正确') from exc

    def probe(self):
        """Synthetic capability measurements; no tools are executed."""
        report = {'selected_call_mode': self.call_mode, 'response_format': self.settings.llm_response_format, 'observations': {}}
        def measure(name, operation):
            started = time.perf_counter()
            try:
                result = operation()
                report['observations'][name] = {'supported': bool(result), 'seconds': time.perf_counter()-started, 'result': result}
            except AgentError as exc:
                report['observations'][name] = {'supported': False, 'seconds': time.perf_counter()-started,
                    'error': exc.info.model_dump()}
            except Exception as exc:
                report['observations'][name] = {'supported': False, 'seconds': time.perf_counter()-started,
                    'error': error_info(exc, 'capability', 'structured').model_dump()}
        common = {'model': self.settings.model, 'max_tokens': 160, 'temperature': 0}
        measure('chat', lambda: self._post('chat/completions', {**common, 'messages': [{'role': 'user', 'content': '用中文打个招呼。'}]})['choices'][0]['message']['content'])
        measure('json', lambda: self.structured([{'role': 'user', 'content': '只回答你好，不做事实断言。'}], AnswerPayload).model_dump())
        def native():
            message = self._post('chat/completions', {**common,
                'messages': [{'role': 'user', 'content': '调用 probe_sum 计算2加3。'}],
                'tools': [{'type': 'function', 'function': {'name': 'probe_sum', 'description': 'Synthetic sum, not executed',
                    'parameters': {'type': 'object', 'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}}, 'required': ['a', 'b']}}}],
                'tool_choice': {'type': 'function', 'function': {'name': 'probe_sum'}}})['choices'][0]['message']
            calls = message.get('tool_calls') or []
            if not calls:
                return False
            function = calls[0].get('function', {})
            try:
                arguments = json.loads(function.get('arguments', ''))
            except (ValueError, TypeError):
                return False
            return function.get('name') == 'probe_sum' and arguments == {'a': 2, 'b': 3}
        measure('native_tools', native)
        measure('embedding', lambda: len(self._post('embeddings', {**common, 'input': ['合成能力测试句子']})['data'][0]['embedding']))
        for name, payload in [('invalid_request', {**common, 'messages': 'invalid-synthetic-value'})]:
            try:
                self._post('chat/completions', payload)
                report['observations'][name] = {'status_code': 200}
            except AgentError as exc:
                report['observations'][name] = {'status_code': exc.info.status_code, 'code': exc.info.code}
        try:
            self._post('chat/completions', {**common, 'messages': [{'role': 'user', 'content': '合成鉴权测试'}]},
                       headers={'Authorization': 'Bearer synthetic-invalid-probe'})
            report['observations']['invalid_auth'] = {'status_code': 200}
        except AgentError as exc:
            report['observations']['invalid_auth'] = {'status_code': exc.info.status_code, 'code': exc.info.code}
        if not report['observations']['json']['supported']:
            report['selected_call_mode'] = 'unavailable'
        return report

    def close(self):
        self.client.close()
