from sqlalchemy import select
from sqlalchemy.orm import Session

from ragagent.db.models import Chunk, Paper
from ragagent.errors import EvaluationError
from ragagent.evaluation.schema import EvaluationDataset


def validate_references(dataset: EvaluationDataset, session: Session) -> None:
    chunks = sorted({cid for case in dataset.cases for cid in case.relevant_chunk_ids})
    papers = sorted({pid for case in dataset.cases for pid in case.relevant_paper_ids})
    for ids, column in [(chunks, Chunk.id), (papers, Paper.id)]:
        known: set[str] = set()
        for start in range(0, len(ids), 1000):
            known.update(
                session.scalars(select(column).where(column.in_(ids[start : start + 1000])))
            )
        if known != set(ids):
            raise EvaluationError("dataset_references_unknown_chunks_or_papers")
