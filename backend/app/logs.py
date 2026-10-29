"""Structured JSON logs: one object per line, with the job id on every line logged while a
job runs (set by app.worker) so one user's job can be followed across processes."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

job_id: ContextVar[str | None] = ContextVar("job_id", default=None)
# Attributes every LogRecord has; anything else was passed with `extra=` and is logged.
_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if (jid := job_id.get()) is not None:
            out["job_id"] = jid
        out.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Library loggers that install their own plain-text handlers.
    for name in ("rq.worker", "uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    # The API logs each request itself (with route and latency). Uvicorn decides whether to
    # write access lines from hasHandlers(), so this logger must not reach the root handler.
    access = logging.getLogger("uvicorn.access")
    access.handlers[:] = []
    access.propagate = False
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request otherwise
