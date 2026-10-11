import sqlite3

from pydantic import ValidationError

from .models import ErrorInfo


class AgentError(RuntimeError):
    def __init__(self, category, phase, code, message, status_code=None):
        self.info = ErrorInfo(category=category, phase=phase, code=code, message=message, status_code=status_code)
        super().__init__(message)


def error_info(exc, phase, category='model'):
    if isinstance(exc, AgentError):
        return exc.info.model_copy(update={'phase': phase})
    if isinstance(exc, sqlite3.Error):
        category = 'database'
    elif isinstance(exc, (ValidationError, ValueError)):
        category = 'structured'
    # Provider bodies and validation inputs can contain private text or tokens.
    return ErrorInfo(category=category, phase=phase, code=type(exc).__name__,
                     message=f'{phase} 失败：{type(exc).__name__}')
