from ragagent.domain.research import Candidate, EvidenceRecord, PaperMetadata


def evidence(
    cid: str = "c1", eid: str = "00000000-0000-0000-0000-000000000001", paper: str = "p1"
) -> EvidenceRecord:
    text = "The method uses contrastive training."
    return EvidenceRecord(
        evidence_id=eid,
        paper=PaperMetadata(paper_id=paper, title="Paper"),
        chunk_id=cid,
        section_id="s1",
        section_path="Methods",
        page_start=2,
        page_end=2,
        content=text,
        quote=text,
        span_start=0,
        span_end=len(text),
        scores={"rerank": 2.0},
    )


def candidate(cid: str) -> Candidate:
    return Candidate(evidence=evidence(cid), score=1)
