from typing import Protocol

from redis import Redis
from rq import Queue
from rq.job import Callback

from ragagent.settings import get_settings


class JobQueue(Protocol):
    def submit(self, run_id: str) -> None: ...


class RQQueue:
    def submit(self, run_id: str) -> None:
        connection = Redis.from_url(get_settings().redis_url.get_secret_value())
        Queue("research", connection=connection).enqueue(
            "ragagent.worker.execute",
            run_id,
            job_id=run_id,
            job_timeout=1800,
            result_ttl=0,
            failure_ttl=86400,
            on_failure=Callback("ragagent.worker.on_failure"),
        )


def get_queue() -> JobQueue:
    return RQQueue()
