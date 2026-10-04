from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ragagent.domain.research import AnswerDraft, QueryExpansion, QueryPlan, Sufficiency
from ragagent.graphs.common import constrain_filters
from ragagent.graphs.state import RAGState, RAGUpdate
from ragagent.providers.chat import ChatProvider
from ragagent.retrieval.evidence import evidence_gate, merge_evidence, render_claims, verify_claims
from ragagent.retrieval.service import SearchPort


def build_rag(
    search: SearchPort,
    planner: ChatProvider,
    retriever: ChatProvider,
    analyst: ChatProvider,
    reviewer: ChatProvider,
    max_retries: int = 2,
    min_rerank_score: float = 0.0,
    *,
    max_evidence_records: int = 48,
) -> CompiledStateGraph[RAGState, None, RAGState, RAGState]:
    if max_evidence_records < 1:
        raise ValueError("evidence_budget_must_be_positive")

    async def plan(state: RAGState) -> RAGUpdate:
        result = await planner.complete(
            "Plan an internal scientific literature search. Identify question type and all "
            "required aspects. Generate focused queries and metadata filters; do not answer.",
            {"query": state.query, "filters": state.filters.model_dump()},
            QueryPlan,
        )
        result.filters = constrain_filters(result.filters, state.filters)
        return {"query_plan": result, "query_type": result.question_type, "status": "retrieving"}

    async def retrieve(state: RAGState) -> RAGUpdate:
        assert state.query_plan is not None
        result = await search.search(state.query_plan)
        evidence, exhausted = merge_evidence(
            state.reranked_evidence, result.evidence, min_rerank_score, max_evidence_records
        )
        gate = evidence_gate(state.query_plan, evidence, minimum_rerank_score=min_rerank_score)
        return {
            "retrieved_candidates": result.fused,
            "reranked_evidence": evidence,
            "sufficiency": gate,
            "retrieval_attempt": state.retrieval_attempt + 1,
            "claims": [],
            "answer": "",
            "citation_validation": None,
            "errors": list(
                dict.fromkeys(state.errors + (["evidence_budget_exhausted"] if exhausted else []))
            ),
        }

    def after_retrieve(state: RAGState) -> str:
        assert state.sufficiency is not None
        if state.sufficiency.status == Sufficiency.SUFFICIENT:
            return "answer"
        return "expand" if state.retrieval_attempt <= max_retries else "refuse"

    async def answer(state: RAGState) -> RAGUpdate:
        assert state.query_plan is not None
        result = await analyst.complete(
            "Answer only using supplied evidence. Each factual claim must cite existing "
            "evidence_ids and exactly one required aspect. Do not infer facts from memory. "
            "Keep citation markers out of claim text; use only the evidence_ids field. "
            "For unsupported aspects omit claims and state limitations.",
            {
                "query": state.query,
                "required_aspects": state.query_plan.required_aspects,
                "evidence": [e.model_dump() for e in state.reranked_evidence],
            },
            AnswerDraft,
        )
        return {"claims": result.claims, "status": "reviewing"}

    async def verify(state: RAGState) -> RAGUpdate:
        assert state.query_plan is not None
        validation = await verify_claims(
            state.claims,
            state.reranked_evidence,
            state.query_plan.required_aspects,
            reviewer,
            state.query,
        )
        gate = evidence_gate(
            state.query_plan, state.reranked_evidence, validation, min_rerank_score
        )
        complete = validation.valid and gate.status == Sufficiency.SUFFICIENT
        return {
            "citation_validation": validation,
            "sufficiency": gate,
            "status": "completed" if complete else "retrieving",
            "answer": render_claims(state.claims) if complete else "",
        }

    def after_verify(state: RAGState) -> str:
        if state.status == "completed":
            return END
        return "expand" if state.retrieval_attempt <= max_retries else "refuse"

    async def expand(state: RAGState) -> RAGUpdate:
        assert state.query_plan is not None
        expansion = await retriever.complete(
            "Expand internal literature queries to fill missing evidence/aspects. "
            "Use synonyms, methods, datasets and metrics; preserve question scope.",
            {
                "query": state.query,
                "previous_queries": state.query_plan.queries,
                "validation": state.citation_validation.model_dump()
                if state.citation_validation
                else None,
            },
            QueryExpansion,
        )
        queries = list(dict.fromkeys(state.query_plan.queries + expansion.queries))[-6:]
        return {
            "query_plan": state.query_plan.model_copy(update={"queries": queries}),
            "status": "retrieving",
        }

    def refuse(state: RAGState) -> RAGUpdate:
        return {
            "status": "insufficient_evidence",
            "answer": "Insufficient evidence in the indexed "
            "literature to provide a verified answer.",
            "claims": [],
        }

    graph = StateGraph(RAGState)
    graph.add_node("plan", plan)
    graph.add_node("retrieve", retrieve)
    graph.add_node("answer", answer)
    graph.add_node("verify", verify)
    graph.add_node("expand", expand)
    graph.add_node("refuse", refuse)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "retrieve")
    graph.add_conditional_edges("retrieve", after_retrieve)
    graph.add_edge("answer", "verify")
    graph.add_conditional_edges("verify", after_verify)
    graph.add_edge("expand", "retrieve")
    graph.add_edge("refuse", END)
    return graph.compile()
