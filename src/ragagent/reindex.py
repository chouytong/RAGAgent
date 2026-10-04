"""Explicit, atomic-per-paper embedding rebuild for operator-selected papers."""

import argparse
import asyncio
import json

from sqlalchemy import select

from ragagent.db.models import Paper
from ragagent.db.session import session_factory
from ragagent.errors import ApplicationError
from ragagent.ingestion.reembed import reembed_paper
from ragagent.providers.chat import Usage, usage_record
from ragagent.runtime import make_embedder
from ragagent.settings import get_settings


async def rebuild(paper_id: str | None, all_indexed: bool, batch_size: int) -> int:
    embedder = make_embedder(get_settings())
    with session_factory()() as session:
        ids = (
            list(
                session.scalars(
                    select(Paper.id).where(Paper.status == "indexed").order_by(Paper.id)
                )
            )
            if all_indexed
            else [paper_id]
        )
        session.rollback()
        for identifier in ids:
            assert identifier is not None
            try:
                result = await reembed_paper(session, identifier, embedder, batch_size=batch_size)
                session.commit()
                usage = getattr(embedder, "usage", None)
                output: dict[str, object] = dict(result)
                if isinstance(usage, Usage):
                    output.update(
                        usage=usage_record(usage), usage_scope="current_attempt_cumulative"
                    )
                print(json.dumps(output))
            except Exception as exc:
                session.rollback()
                code = exc.code if isinstance(exc, ApplicationError) else "reembedding_failed"
                print(json.dumps({"paper_id": identifier, "error_code": code}))
                usage = getattr(embedder, "usage", None)
                if isinstance(usage, Usage):
                    print(
                        json.dumps(
                            {
                                "usage": usage_record(usage),
                                "usage_scope": "current_attempt_cumulative",
                            }
                        )
                    )
                return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    selected = parser.add_mutually_exclusive_group(required=True)
    selected.add_argument("--paper-id")
    selected.add_argument("--all-indexed", action="store_true")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 256:
        parser.error("--batch-size must be between 1 and 256")
    raise SystemExit(asyncio.run(rebuild(args.paper_id, args.all_indexed, args.batch_size)))


if __name__ == "__main__":
    main()
