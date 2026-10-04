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
from ragagent.retrieval.evidence import (
    accepted_evidence,
    merge_evidence,
    render_claims,
    verify_claims,
)
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
    *,
    min_rerank_score: float = 0.0,
    max_evidence_records: int = 96,
) -> CompiledStateGraph[MultiAgentState, None, MultiAgentState, MultiAgentState]:
    if max_evidence_records < 1:
        raise ValueError("evidence_budget_must_be_positive")

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

    async def control(state: MultiAgentState) -> ResearchUpdate:
        tasks = state.subtasks
        completed = set(state.completed_tasks)
        update: ResearchUpdate = {}
        route = supervisor_route(state, max_retrievals, max_revisions, max_iterations)
        if (
            state.review_result
            and state.review_result.decision == "NEED_MORE_EVIDENCE"
            and route == "retriever"
        ):
            assert state.research_plan is not None
            revised = await supervisor.complete(
                "Replan missing research aspects using reviewer feedback. Preserve the original "
                "question and all existing required aspects; generate focused retrieval subtasks. "
                "Do not add conclusions or relax user constraints.",
                {
                    "question": state.research_question,
                    "previous_plan": state.research_plan.model_dump(),
                    "review": state.review_result.model_dump(),
                },
                ResearchPlan,
            )
            aspects = list(
                dict.fromkeys(state.research_plan.required_aspects + revised.required_aspects)
            )
            revised = ResearchPlan(
                objective=revised.objective, required_aspects=aspects, subtasks=revised.subtasks
            )
            for task in revised.subtasks:
                task.filters = constrain_filters(task.filters, state.filters)
            previous = {task.task_id: task for task in state.subtasks}
            completed = {
                task.task_id
                for task in revised.subtasks
                if task.task_id in completed and previous.get(task.task_id) == task
            }
            tasks = revised.subtasks
            update["research_plan"] = revised
            update["subtasks"] = tasks
            update["iteration"] = state.iteration + 1
            update["completed_tasks"] = sorted(completed)
        pending = [s.task_id for s in tasks if s.task_id not in completed]
        update["current_tasks"] = pending or [s.task_id for s in tasks]
        return update

    async def retrieve(state: MultiAgentState) -> ResearchUpdate:
        pool = accepted_evidence(state.evidence_pool, min_rerank_score)
        exhausted = False
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
            evidence = accepted_evidence(result.evidence, min_rerank_score)
            pool, over_budget = merge_evidence(
                pool, evidence, min_rerank_score, max_evidence_records
            )
            retained = {item.evidence_id for item in pool}
            if any(item.evidence_id in retained for item in evidence):
                completed.add(task.task_id)
            exhausted = exhausted or over_budget
        return {
            "evidence_pool": pool,
            "completed_tasks": sorted(completed),
            "subtasks": updated_tasks,
            "retrieval_count": state.retrieval_count + 1,
            "iteration": state.iteration + 1,
            "status": "analyzing",
            "errors": list(
                dict.fromkeys(state.errors + (["evidence_budget_exhausted"] if exhausted else []))
            ),
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
                "Keep citation markers out of claim text; use only the evidence_ids field. "
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
            render_claims(a.factual_claims()) for a in state.analysis_results
        )
        # Limitations/contradictions remain structured, not released as unchecked factual prose.
        return {"draft_report": report, "status": "reviewing"}

    async def review(state: MultiAgentState) -> ResearchUpdate:
        assert state.research_plan is not None
        claims = [c for a in state.analysis_results for c in a.factual_claims()]
        validation = await verify_claims(
            claims,
            state.evidence_pool,
            state.research_plan.required_aspects,
            reviewer,
            state.research_question,
        )
        contradictions = [x.text for a in state.analysis_results for x in a.contradictions]
        invalid = [v.claim_id for v in validation.verdicts if not v.supported or v.contradiction]
        if validation.valid:
            decision = "PASS"
        elif not claims or any(
            a not in {c.aspect for c in claims} for a in validation.missing_aspects
        ):
            decision = "NEED_MORE_EVIDENCE"
        else:
            decision = "NEED_REVISION"
        issues = invalid + validation.missing_aspects
        if any(v.contradiction for v in validation.verdicts):
            issues += contradictions
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
