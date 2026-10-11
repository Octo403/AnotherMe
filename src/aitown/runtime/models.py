import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class UserStatement(BaseModel):
    kind: Literal['user_statement', 'user_correction'] = 'user_statement'
    subject: Literal['self'] = 'self'
    attribute: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2000)
    scope: str = Field(min_length=1, max_length=100)
    corrects_fact_id: int | None = None

    @model_validator(mode='after')
    def correction_target(self):
        if (self.kind == 'user_correction') != (self.corrects_fact_id is not None):
            raise ValueError('A correction requires a fact ID; a statement must not replace a fact implicitly')
        return self


def explicit_statement(text, scope):
    """Only an explicit current-user request is interpreted; never model output."""
    match = re.fullmatch(r'(?:记住|记一下)[：:]\s*我(?:现在)?(不喜欢|喜欢|住在|是)([^？?\n]+)', text.strip())
    if not match or not scope:
        return None
    predicate, value = match.groups()
    attribute = {'喜欢': 'preference', '不喜欢': 'preference', '住在': 'residence', '是': 'identity'}[predicate]
    return UserStatement(attribute=attribute, value=('不喜欢：' if predicate == '不喜欢' else '')+value.strip().rstrip('。'), scope=scope)
