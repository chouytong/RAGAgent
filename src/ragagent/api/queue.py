from typing import Protocol

from redis import Redis
from rq import Queue
from rq.command import send_stop_job_command
from rq.exceptions import DuplicateJobError, NoSuchJobError
from rq.job import Callback, Job

from ragagent.errors import ApplicationError
from ragagent.jobs import JOB_TIMEOUT_SECONDS
from ragagent.queues import queue_name
from ragagent.settings import get_settings


class JobQueue(Protocol):
    def submit(self, run_id: str) -> None: ...


class RQQueue:
    def connection(self) -> Redis:
        return Redis.from_url(
            get_settings().redis_url.get_secret_value(), socket_connect_timeout=3, socket_timeout=3
        )

    def status(self, run_id: str) -> str | None:
        try:
            job = Job.fetch(run_id, connection=self.connection())
            return str(job.get_status(refresh=True).value)
        except NoSuchJobError:
            return None

    def submit(self, run_id: str) -> None:
        self.submit_with_timeout(run_id, JOB_TIMEOUT_SECONDS)

    def cancel(self, run_id: str) -> None:
        connection = self.connection()
        try:
            job = Job.fetch(run_id, connection=connection)
            if job.get_status(refresh=True).value == "started":
                send_stop_job_command(connection, run_id)
            else:
                job.cancel()
        except NoSuchJobError:
            return

    def submit_with_timeout(self, run_id: str, timeout: int) -> None:
        self.submit_for_run(run_id, queue_name("interactive"), timeout)

    def submit_for_run(self, run_id: str, selected_queue: str, timeout: int) -> None:
        try:
            Queue(selected_queue, connection=self.connection()).enqueue(
                "ragagent.worker.execute",
                run_id,
                job_id=run_id,
                unique=True,
                job_timeout=timeout,
                result_ttl=0,
                failure_ttl=86400,
                on_failure=Callback("ragagent.worker.on_failure"),
                on_stopped=Callback("ragagent.worker.on_stopped"),
            )
        except DuplicateJobError:
            if self.status(run_id) not in {"queued", "started", "deferred", "scheduled"}:
                raise ApplicationError("queue_job_not_active") from None


def get_queue() -> JobQueue:
    return RQQueue()
