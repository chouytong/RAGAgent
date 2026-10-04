import json
import logging


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "event": record.getMessage(),
                "level": record.levelname,
                "request_id": getattr(record, "request_id", None),
                "trace_id": getattr(record, "trace_id", None),
                "error_code": getattr(record, "error_code", None),
                "latency_ms": getattr(record, "latency_ms", None),
            }
        )


def configure_logging() -> None:
    logger = logging.getLogger("ragagent")
    if not any(isinstance(h.formatter, JSONFormatter) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
