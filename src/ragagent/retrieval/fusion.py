from ragagent.domain.research import Candidate


class RRFusion:
    def __init__(self, k: int = 60) -> None:
        if k < 1:
            raise ValueError("rrf_k_must_be_positive")
        self.k = k

    def fuse(self, rankings: list[list[Candidate]], top_n: int) -> list[Candidate]:
        scores: dict[str, float] = {}
        records: dict[str, Candidate] = {}
        for ranking in rankings:
            seen: set[str] = set()
            for rank, candidate in enumerate(ranking, 1):
                cid = candidate.evidence.chunk_id
                if cid in seen:
                    continue
                seen.add(cid)
                records.setdefault(cid, candidate)
                scores[cid] = scores.get(cid, 0) + 1 / (self.k + rank)
                records[cid].evidence.scores.update(candidate.evidence.scores)
        ids = sorted(scores, key=lambda cid: (-scores[cid], cid))[:top_n]
        return [
            Candidate(
                evidence=records[cid].evidence.model_copy(
                    update={"scores": {**records[cid].evidence.scores, "rrf": scores[cid]}}
                ),
                score=scores[cid],
            )
            for cid in ids
        ]
