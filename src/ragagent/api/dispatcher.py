import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ragagent.api.queue import RQQueue
from ragagent.db.session import session_factory
from ragagent.jobs import reconcile_jobs

logger = logging.getLogger("ragagent.dispatcher")


def reconcile() -> None:
    with session_factory()() as session:
        reconcile_jobs(session, RQQueue())


async def dispatch_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(reconcile)
        except Exception:
            logger.error("dispatch_reconcile_failed", extra={"error_code": "queue_unavailable"})
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except TimeoutError:
            pass


@asynccontextmanager
async def dispatcher_lifespan(app: FastAPI) -> AsyncIterator[None]:
    stop = asyncio.Event()
    task = asyncio.create_task(dispatch_loop(stop))
    try:
        yield
    finally:
        stop.set()
        await task
