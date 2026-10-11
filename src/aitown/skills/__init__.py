from .base import Skill, SkillContext
from .builtins import Calculator, CalculateArgs, EventParser, EventArgs
from .registry import SkillRegistry


def default_registry(*, enable_correction=False):
    from .business import MemoryQuery, DialogueSummary, MemoryCorrection
    registry = SkillRegistry()
    for skill in (Calculator(), EventParser(), MemoryQuery(), DialogueSummary()):
        registry.register(skill)
    registry.register(MemoryCorrection(), enabled=enable_correction)
    return registry


__all__ = ['Skill', 'SkillContext', 'SkillRegistry', 'Calculator', 'CalculateArgs',
           'EventParser', 'EventArgs', 'default_registry']
