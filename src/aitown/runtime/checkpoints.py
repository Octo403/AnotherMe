import sqlite3
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver


class TurnPaused(RuntimeError):
    def __init__(self, request_id, next_nodes):
        self.request_id, self.next_nodes = request_id, tuple(next_nodes)
        super().__init__(f'轮次 {request_id} 已暂停；待执行 {", ".join(next_nodes)}，可使用 resume 继续。')


class Checkpoints:
    def __init__(self, path):
        from .. import models
        from ..storage import models as storage_models
        from ..style import models as style_models
        from . import models as runtime_models
        from pydantic import BaseModel
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        # Checkpoint class construction happens before state validation. Restrict
        # deserialization to the known state types instead of arbitrary imports.
        classes = [(value.__module__, value.__name__) for module in (models, storage_models, style_models, runtime_models)
                   for value in vars(module).values() if isinstance(value, type) and issubclass(value, BaseModel) and value is not BaseModel]
        serde = JsonPlusSerializer(allowed_msgpack_modules=classes, allowed_json_modules=classes, pickle_fallback=False)
        self.saver = SqliteSaver(self.conn, serde=serde)
        self.saver.setup()

    def close(self):
        self.conn.close()
