from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ragagent.domain.research import QueryExpansion, QueryPlan
from ragagent.graphs.common import constrain_filters
from ragagent.graphs.state import (
    AnalysisResult,
    MultiAgentState,
    ResearchPlan,
    ResearchUpdate,
    ReviewResult,
)
from ragagent.providers.chat import ChatProvider
from ragagent.retrieval.evidence import render_claims, verify_claims
from ragagent.retrieval.service import SearchPort


def supervisor_route(
    state: MultiAgentState, max_retrievals: int, max_revisions: int, max_iterations: int
) -> str:
    if state.review_result and state.review_result.decision == "PASS":
        return "finish"
    if state.iteration >= max_iterations:
        return "stop"
    if state.review_result and state.review_result.decision == "NEED_REVISION":
        return "analysis" if state.revision_count < max_revisions else "stop"
    return "retriever" if state.retrieval_count < max_retrievals else "stop"


def build_research(
    search: SearchPort,
    supervisor: ChatProvider,
    retriever: ChatProvider,
    analyst: ChatProvider,
    reviewer: ChatProvider,
    max_retrievals: int = 3,
    max_revisions: int = 2,
    max_iterations: int = 12,
) -> CompiledStateGraph[MultiAgentState, None, MultiAgentState, MultiAgentState]:
    async def plan(state: MultiAgentState) -> ResearchUpdate:
        result = await supervisor.complete(
            "Create a scientific ResearchPlan with distinct required aspects and bounded "
            "retrieval subtasks. Cover methods, datasets and metrics when requested. "
            "Each task must map to an aspect. Do not produce conclusions.",
            {"research_question": state.research_question, "filters": state.filters.model_dump()},
            ResearchPlan,
        )
        for task in result.subtasks:
            task.filters = constrain_filters(task.filters, state.filters)
        return {"research_plan": result, "subtasks": result.subtasks, "status": "retrieving"}

    def control(state: MultiAgentState) -> ResearchUpdate:
        # All specialist calls pass through the supervisor's deterministic policy.
        pending = [s.task_id for s in state.subtasks if s.task_id not in state.completed_tasks]
        return {"current_tasks": pending or [s.task_id for s in state.subtasks]}

    async def retrieve(state: MultiAgentState) -> ResearchUpdate:
        pool = {e.evidence_id: e for e in state.evidence_pool}
        completed = set(state.completed_tasks)
        updated_tasks = []
        for task in state.subtasks:
            if task.task_id not in state.current_tasks:
                updated_tasks.append(task)
                continue
            queries = task.queries
            if state.retrieval_count:
                expansion = await retriever.complete(
                    "Expand queries for this subtask to find missing evidence. Do not answer.",
                    {
                        "task": task.model_dump(),
                        "issues": state.review_result.issues if state.review_result else [],
                    },
                    QueryExpansion,
                )
                queries = list(dict.fromkeys(queries + expansion.queries))[-6:]
            task = task.model_copy(update={"queries": queries})
            updated_tasks.append(task)
            result = await search.search(
                QueryPlan(
                    queries=queries,
                    question_type="synthesis",
                    required_aspects=[task.aspect],
                    filters=task.filters,
                )
            )
            if result.evidence:
                completed.add(task.task_id)
            for evidence in result.evidence:
                pool[evidence.evidence_id] = evidence
        return {
            "evidence_pool": list(pool.values()),
            "completed_tasks": sorted(completed),
            "subtasks": updated_tasks,
            "retrieval_count": state.retrieval_count + 1,
            "iteration": state.iteration + 1,
            "status": "analyzing",
        }

    async def analyze(state: MultiAgentState) -> ResearchUpdate:
        assert state.research_plan is not None
        if not state.evidence_pool:
            result = AnalysisResult(claims=[], limitations=["No indexed evidence found"])
        else:
            result = await analyst.complete(
                "Extract structured facts only from supplied evidence. Compare methods, "
                "datasets and metrics, detect contradictory findings. Every factual claim "
                "must include existing evidence IDs and an exact required aspect. "
                "Do not invent facts; add unsupported questions to limitations. "
                "Revise prior claims using reviewer feedback when provided.",
                {
                    "question": state.research_question,
                    "plan": state.research_plan.model_dump(),
                    "evidence": [e.model_dump() for e in state.evidence_pool],
                    "previous_analysis": [a.model_dump() for a in state.analysis_results],
                    "feedback": state.review_result.model_dump() if state.review_result else None,
                },
                AnalysisResult,
            )
        revision = (
            state.review_result is not None and state.review_result.decision == "NEED_REVISION"
        )
        return {
            "analysis_results": [result],
            "revision_count": state.revision_count + int(revision),
            "iteration": state.iteration + 1,
            "status": "synthesizing",
        }

    def synthesize(state: MultiAgentState) -> ResearchUpdate:
        # Deterministic Report Synthesis Node: cannot add model-memory conclusions.
        report = "# Research report\n\n" + "\n\n".join(
            render_claims(a.claims) for a in state.analysis_results
        )
        # Limitations/contradictions remain structured, not released as unchecked factual prose.
        return {"draft_report": report, "status": "reviewing"}

    async def review(state: MultiAgentState) -> ResearchUpdate:
        assert state.research_plan is not None
        claims = [c for a in state.analysis_results for c in a.claims]
        validation = await verify_claims(
            claims,
            state.evidence_pool,
            state.research_plan.required_aspects,
            reviewer,
            state.research_question,
        )
        contradictions = [x.text for a in state.analysis_results for x in a.contradictions]
        invalid = [v.claim_id for v in validation.verdicts if not v.supported or v.contradiction]
        if validation.valid and not contradictions:
            decision = "PASS"
        elif not claims or any(
            a not in {c.aspect for c in claims} for a in validation.missing_aspects
        ):
            decision = "NEED_MORE_EVIDENCE"
        else:
            decision = "NEED_REVISION"
        issues = invalid + validation.missing_aspects + contradictions
        result = ReviewResult(decision=decision, validation=validation, issues=issues)
        return {"review_result": result, "iteration": state.iteration + 1}

    def finish(state: MultiAgentState) -> ResearchUpdate:
        return {"status": "completed", "current_tasks": []}

    def stop(state: MultiAgentState) -> ResearchUpdate:
        return {
            "status": "insufficient_evidence",
            "draft_report": "Insufficient evidence or "
            "unresolved claims after the configured research/revision limits.",
            "current_tasks": [],
        }

    graph = StateGraph(MultiAgentState)
    for name, node in [
        ("plan", plan),
        ("supervisor", control),
        ("retriever", retrieve),
        ("analysis", analyze),
        ("synthesis", synthesize),
        ("reviewer", review),
        ("finish", finish),
        ("stop", stop),
    ]:
        graph.add_node(name, node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        lambda state: supervisor_route(state, max_retrievals, max_revisions, max_iterations),
    )
    graph.add_conditional_edges(
        "retriever", lambda state: "stop" if state.iteration >= max_iterations else "analysis"
    )
    graph.add_conditional_edges(
        "analysis", lambda state: "stop" if state.iteration >= max_iterations else "synthesis"
    )
    graph.add_edge("synthesis", "reviewer")
    graph.add_edge("reviewer", "supervisor")
    graph.add_edge("finish", END)
    graph.add_edge("stop", END)
    return graph.compile()
