import ast
import operator
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .base import Skill


class CalculateArgs(BaseModel):
    model_config = {'extra': 'forbid'}
    expression: str | None = Field(default=None, min_length=1, max_length=150)
    left: float | None = None
    right: float | None = None
    operation: Literal['+', '-', '*', '/'] = '*'

    @model_validator(mode='after')
    def operands(self):
        if self.expression is not None:
            if self.left is not None or self.right is not None:
                raise ValueError('算式与数值操作数只能选一种')
        elif self.left is None or self.right is None:
            raise ValueError('需要 expression 或 left/right 操作数')
        return self


class CalculateResult(BaseModel):
    value: float | int
    expression: str


class Calculator(Skill):
    name = 'calculator'
    description = '计算数字四则算式 expression；也可用 left/right/operation 接收前一步的 count，不支持函数或变量。'
    arguments = CalculateArgs
    result_type = CalculateResult

    def run(self, arguments):
        ops = {ast.Add: operator.add, ast.Sub: operator.sub,
               ast.Mult: operator.mul, ast.Div: operator.truediv}

        def bounded(value):
            import math
            if not math.isfinite(value) or abs(value) > 1e12:
                raise ValueError('数值或计算结果超出演示范围')
            return value

        def evaluate(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                return bounded(node.value)
            if isinstance(node, ast.BinOp) and type(node.op) in ops:
                return bounded(ops[type(node.op)](evaluate(node.left), evaluate(node.right)))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return evaluate(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            raise ValueError('仅支持数字、括号和 + - * /，不执行代码')

        if arguments.expression is not None:
            value = evaluate(ast.parse(arguments.expression, mode='eval').body)
        else:
            operation = {'+': operator.add, '-': operator.sub, '*': operator.mul, '/': operator.truediv}
            value = bounded(operation[arguments.operation](bounded(arguments.left), bounded(arguments.right)))
        expression = arguments.expression or f'{arguments.left}{arguments.operation}{arguments.right}'
        return {'value': value, 'expression': expression}


class EventArgs(BaseModel):
    model_config = {'extra': 'forbid'}
    text: str = Field(min_length=1, max_length=4000)


class EventResult(BaseModel):
    items: list[str]
    count: int


class EventParser(Skill):
    name = 'event_parser'
    description = '按行或分号拆分用户提供的公共事件文本 text，返回 items 和事项 count。'
    arguments = EventArgs
    result_type = EventResult

    def run(self, arguments):
        items = [s.strip() for s in arguments.text.replace('；', '\n').replace(';', '\n').splitlines() if s.strip()]
        return {'items': items, 'count': len(items)}
