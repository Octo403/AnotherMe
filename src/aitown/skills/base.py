from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel


@dataclass(frozen=True)
class SkillContext:
    repository: Any
    scope: str = ''
    question: str = ''
    session: str = ''
    agent_id: str = 'anotherme'
    runtime: Any = None


class Skill(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    arguments: ClassVar[type[BaseModel]]
    result_type: ClassVar[type[BaseModel] | None] = None
    read_only: ClassVar[bool] = True

    @abstractmethod
    def run(self, arguments: BaseModel): ...

    def run_with_context(self, arguments, context):
        return self.run(arguments)

    def run_transaction(self, arguments, connection):
        raise ValueError('写入技能必须实现同一 SQLite 事务中的 run_transaction；不自动重放外部写入')

    def run_transaction_with_context(self, arguments, connection, context):
        return self.run_transaction(arguments, connection)

    def result(self, value):
        return self.result_type.model_validate(value).model_dump() if self.result_type else value
