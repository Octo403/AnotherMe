from pydantic import TypeAdapter

from ..models import SkillCall, SkillResult
from .base import Skill


class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._enabled: set[str] = set()

    def register(self, skill: Skill, *, enabled=True):
        self._skills[skill.name] = skill
        self.enable(skill.name, enabled)

    def enable(self, name: str, enabled: bool = True):
        if name not in self._skills:
            raise KeyError(name)
        if enabled:
            self._enabled.add(name)
        else:
            self._enabled.discard(name)

    def get(self, name, *, allow_disabled=False):
        if name not in self._skills or (name not in self._enabled and not allow_disabled):
            raise ValueError('技能不存在或已禁用：'+name)
        return self._skills[name]

    def catalog(self, *, include_disabled=False):
        return [{'name': s.name, 'description': s.description, 'read_only': s.read_only,
                 'enabled': name in self._enabled, 'parameters': s.arguments.model_json_schema(),
                 'result': s.result_type.model_json_schema() if s.result_type else None}
                for name, s in self._skills.items() if include_disabled or name in self._enabled]

    def validate_plan(self, calls, *, replayed=()):
        if len(calls) > 3:
            raise ValueError('每轮最多三次技能调用')
        for ordinal, call in enumerate(calls):
            skill = self.get(call.name, allow_disabled=ordinal in replayed)
            if not call.bindings:
                skill.arguments.model_validate(call.arguments)
                continue
            bound = set()
            for binding in call.bindings:
                if binding.call_index >= ordinal:
                    raise ValueError('依赖只能引用此前调用，不能引用自身或未来结果')
                source = self.get(calls[binding.call_index].name, allow_disabled=binding.call_index in replayed)
                if not source.result_type or binding.field not in source.result_type.model_fields:
                    raise ValueError('依赖字段不在前一步的结果类型中')
                if binding.argument not in skill.arguments.model_fields or binding.argument in bound or binding.argument in call.arguments:
                    raise ValueError('依赖参数未知、重复或同时含字面值')
                bound.add(binding.argument)
            for key, value in call.arguments.items():
                if key not in skill.arguments.model_fields:
                    raise ValueError('未知技能参数：'+key)
                TypeAdapter(skill.arguments.model_fields[key].annotation).validate_python(value)
            required = {key for key, field in skill.arguments.model_fields.items() if field.is_required()}
            if required - bound - call.arguments.keys():
                raise ValueError('缺少必要技能参数')

    def resolve(self, call, results, *, allow_disabled=False):
        skill = self.get(call.name, allow_disabled=allow_disabled)
        arguments = dict(call.arguments)
        for binding in call.bindings:
            if binding.call_index >= len(results) or not results[binding.call_index].ok:
                raise ValueError('前一步失败，依赖调用已跳过')
            source = self._skills[results[binding.call_index].name]
            data = source.result_type.model_validate(results[binding.call_index].data).model_dump()
            if binding.field not in data:
                raise ValueError('前一步缺少依赖字段')
            arguments[binding.argument] = data[binding.field]
        parsed = skill.arguments.model_validate(arguments)
        return SkillCall(name=call.name, arguments=parsed.model_dump(mode='json', exclude_none=True))

    def prompt_catalog(self):
        return [{'name': s.name, 'description': s.description, 'read_only': s.read_only,
                 'parameters': {key: {k: v for k, v in field.items() if k not in ('title', 'default')}
                                for key, field in s.arguments.model_json_schema()['properties'].items()},
                 'required': s.arguments.model_json_schema().get('required', []),
                 'result_fields': list(s.result_type.model_fields) if s.result_type else []}
                for s in self._skills.values() if s.name in self._enabled]

    def execute(self, call: SkillCall, context=None) -> SkillResult:
        try:
            skill = self.get(call.name)
            if not skill.read_only:
                raise ValueError('写入技能必须通过运行时事务接口执行')
            if call.bindings:
                raise ValueError('独立调用不能带未解析的结果依赖')
            value = skill.run_with_context(skill.arguments.model_validate(call.arguments), context)
            return SkillResult(name=call.name, ok=True, data=skill.result(value))
        except Exception as exc:
            return SkillResult(name=call.name, ok=False, error=str(exc))
