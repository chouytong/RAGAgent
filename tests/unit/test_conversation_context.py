"""Conversation context is bounded, reproducible and never scientific evidence."""

import json
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel, ValidationError

from ragagent.conversations.context import (
    CONTEXT_INSTRUCTION,
    ContextBuilder,
    ContextConfig,
    ContextMessage,
    RollingSummary,
    StructuredMemory,
    contextualize,
    estimated_context_tokens,
    merge_context_filters,
    roll_summary,
)
from ragagent.domain.research import (
    AnswerDraft,
    Claim,
    ClaimEvidencePair,
    ClaimVerdict,
    MetadataFilter,
    QueryPlan,
    SearchResult,
    VerificationResponse,
)
from ragagent.errors import ApplicationError
from ragagent.graphs.rag import build_rag
from ragagent.graphs.research import build_research
from ragagent.graphs.state import (
    AnalysisResult,
    MultiAgentState,
    RAGState,
    ResearchPlan,
    SubTask,
)
from ragagent.providers.chat import MockProvider
from tests.unit.helpers import evidence

T = TypeVar("T", bound=BaseModel)


class CapturingProvider(MockProvider):
    def __init__(self, responses: list[BaseModel | dict[str, Any]]) -> None:
        super().__init__(responses)
        self.payloads: list[dict[str, Any]] = []
        self.instructions: list[str] = []

    async def complete(self, instruction: str, payload: dict[str, Any], schema: type[T]) -> T:
        self.payloads.append(payload)
        self.instructions.append(instruction)
        return await super().complete(instruction, payload, schema)


def history() -> list[ContextMessage]:
    return [
        ContextMessage(id="m0", ordinal=0, role="user", content="What datasets were used?"),
        ContextMessage(
            id="m1",
            ordinal=1,
            role="assistant",
            content=(
                "1. Dataset A has 500 participants.\n2. Dataset B has 200 participants. [E:old]"
            ),
        ),
    ]


def rewrite(
    query: str, mention: str = "it", name: str = "Dataset A", source: str = "m1"
) -> dict[str, Any]:
    return {
        "status": "resolved",
        "contextualized_query": query,
        "used_message_ids": [source],
        "used_memory_ids": [],
        "referents": [
            {
                "mention": mention,
                "resolved_text": name,
                "source_kind": "message",
                "source_id": source,
            }
        ],
    }


async def test_standalone_query_uses_no_contextualizer_call() -> None:
    bundle = ContextBuilder().build("What datasets does DANN use?", [])
    provider = MockProvider([])
    result = await contextualize(bundle, provider)
    assert result.contextualized_query == result.original_query
    assert result.metadata["rewrite_status"] == "standalone"
    assert not provider.calls


async def test_first_turn_ambiguous_reference_fails_instead_of_guessing() -> None:
    provider = MockProvider([])
    with pytest.raises(ApplicationError, match="context_resolution_ambiguous"):
        await contextualize(ContextBuilder().build("Which one is largest?", []), provider)
    assert not provider.calls


async def test_numbered_followup_preserves_order_and_records_provenance() -> None:
    bundle = ContextBuilder().build("What about the second one?", history())
    provider = CapturingProvider([rewrite("What about Dataset B?", "the second one", "Dataset B")])
    result = await contextualize(bundle, provider)
    assert result.contextualized_query == "What about Dataset B?"
    assert result.metadata["original_query"] == "What about the second one?"
    assert result.metadata["used_message_ids"] == ["m1"]
    assert provider.instructions == [CONTEXT_INSTRUCTION]
    assert provider.payloads[0]["conversation_context_not_scientific_evidence"] is True
    assert "500" not in result.contextualized_query


async def test_research_multiple_referents_are_grounded_in_conversation_entities() -> None:
    messages = history() + [
        ContextMessage(id="m2", ordinal=2, role="user", content="Compare DANN and MMD."),
        ContextMessage(id="m3", ordinal=3, role="assistant", content="Comparison was incomplete."),
    ]
    provider = CapturingProvider(
        [
            {
                "status": "resolved",
                "contextualized_query": "Between DANN and MMD, which performs better on Dataset B?",
                "used_message_ids": ["m2", "m1"],
                "referents": [
                    {
                        "mention": "Which one",
                        "resolved_text": "DANN",
                        "source_kind": "message",
                        "source_id": "m2",
                    },
                    {
                        "mention": "Which one",
                        "resolved_text": "MMD",
                        "source_kind": "message",
                        "source_id": "m2",
                    },
                    {
                        "mention": "the second dataset",
                        "resolved_text": "Dataset B",
                        "source_kind": "message",
                        "source_id": "m1",
                    },
                ],
            }
        ]
    )
    result = await contextualize(
        ContextBuilder().build("Which one performs better on the second dataset?", messages),
        provider,
    )
    assert "DANN and MMD" in result.contextualized_query
    assert "Dataset B" in result.contextualized_query


@pytest.mark.parametrize(
    "response,code",
    [
        ({"status": "ambiguous"}, "context_resolution_ambiguous"),
        (
            {
                "status": "resolved",
                "contextualized_query": "How many participants does it contain?",
            },
            "context_resolution_ambiguous",
        ),
        (
            rewrite("Does Dataset A contain 500 participants?"),
            "context_resolution_invalid",
        ),
        (
            rewrite("Does Dataset A has 500 participants?", name="Dataset A has 500 participants"),
            "context_resolution_invalid",
        ),
        (
            rewrite(
                "How many participants does an invented entity contain?", name="invented entity"
            ),
            "context_resolution_invalid",
        ),
        (
            rewrite("How many participants does Dataset A contain? [E:old]"),
            "context_resolution_invalid",
        ),
        (
            rewrite("How many participants does Dataset A contain?", source="unknown"),
            "context_resolution_invalid",
        ),
    ],
)
async def test_bad_rewrite_fails_closed(response: dict[str, Any], code: str) -> None:
    with pytest.raises(ApplicationError, match=code):
        await contextualize(
            ContextBuilder().build("How many participants does it contain?", history()),
            MockProvider([response]),
        )


async def test_provider_error_never_falls_back_to_unresolved_question() -> None:
    with pytest.raises(ApplicationError, match="context_resolution_failed"):
        await contextualize(
            ContextBuilder().build("What about the second one?", history()), MockProvider([])
        )


async def test_rewrite_cannot_resolve_only_one_of_two_ambiguous_mentions() -> None:
    response = rewrite(
        "Between Dataset A and MMD, which performs better on the second dataset?",
        "Which one",
        "Dataset A",
    )
    with pytest.raises(ApplicationError, match="context_resolution_ambiguous"):
        await contextualize(
            ContextBuilder().build("Which one performs better on the second dataset?", history()),
            MockProvider([response]),
        )


async def test_rewrite_cannot_select_a_comparison_winner_from_history() -> None:
    response = rewrite("Dataset A has the largest sample size?", "Which one", "Dataset A")
    with pytest.raises(ApplicationError, match="context_resolution_invalid"):
        await contextualize(
            ContextBuilder().build("Which one has the largest sample size?", history()),
            MockProvider([response]),
        )


async def test_rewrite_with_which_cannot_drop_the_requested_superlative() -> None:
    response = rewrite("Which sample size is reported for Dataset A?", "Which one", "Dataset A")
    with pytest.raises(ApplicationError, match="context_resolution_invalid"):
        await contextualize(
            ContextBuilder().build("Which one has the largest sample size?", history()),
            MockProvider([response]),
        )


async def test_rewrite_with_largest_cannot_preselect_one_candidate() -> None:
    response = rewrite(
        "Which largest sample size is reported for Dataset A?", "Which one", "Dataset A"
    )
    with pytest.raises(ApplicationError, match="context_resolution_invalid"):
        await contextualize(
            ContextBuilder().build("Which one has the largest sample size?", history()),
            MockProvider([response]),
        )


async def test_superlative_followup_keeps_both_source_grounded_candidates() -> None:
    response = rewrite(
        "Which of Dataset A and Dataset B has the largest sample size?", "Which one", "Dataset A"
    )
    response["referents"].append(
        {
            "mention": "Which one",
            "resolved_text": "Dataset B",
            "source_kind": "message",
            "source_id": "m1",
        }
    )
    result = await contextualize(
        ContextBuilder().build("Which one has the largest sample size?", history()),
        MockProvider([response]),
    )
    assert "Dataset A and Dataset B" in result.contextualized_query
    assert "largest" in result.contextualized_query
    assert len(result.metadata["referents"]) == 2
    assert "500" not in result.contextualized_query


async def test_digits_in_verbatim_dataset_name_are_allowed() -> None:
    messages = [
        ContextMessage(id="m1", ordinal=0, role="assistant", content="The dataset is CIFAR-10.")
    ]
    result = await contextualize(
        ContextBuilder().build("What metrics were used for it?", messages),
        MockProvider([rewrite("What metrics were used for CIFAR-10?", name="CIFAR-10")]),
    )
    assert "CIFAR-10" in result.contextualized_query


def test_recent_history_excludes_drafts_failed_and_internal_messages() -> None:
    messages = history() + [
        ContextMessage(id="draft", ordinal=2, role="assistant", content="draft", status="running"),
        ContextMessage(id="fail", ordinal=3, role="assistant", content="error", status="failed"),
        ContextMessage(id="sys", ordinal=4, role="system", content="hidden instructions"),
    ]
    bundle = ContextBuilder().build("What about the second one?", messages)
    assert bundle.metadata["history_message_ids"] == ["m0", "m1"]
    assert "hidden instructions" not in json.dumps(bundle.payload)
    assert "draft" not in json.dumps(bundle.payload)


def test_rolling_summary_is_bounded_updates_once_and_retains_initial_entities() -> None:
    builder = ContextBuilder(ContextConfig(recent_message_limit=2, summary_max_bytes=1024))
    messages = history() + [
        ContextMessage(id=f"m{index}", ordinal=index, role="user", content=f"Topic update {index}.")
        for index in range(2, 30)
    ]
    bundle = builder.build("What about the second one?", messages)
    assert bundle.summary.through_ordinal == 27
    assert bundle.summary.version == 1
    assert len(bundle.summary.content.encode("utf-8")) <= 1024
    assert "Dataset B" in bundle.summary.content
    assert bundle.summary.source_message_ids[:2] == ["m0", "m1"]
    assert bundle.metadata["recent_message_ids"] == ["m28", "m29"]
    assert "m2" not in bundle.summary.source_message_ids
    again = builder.build("What about the second one?", messages, bundle.summary)
    assert again.summary == bundle.summary
    messages.append(
        ContextMessage(id="m30", ordinal=30, role="user", content="More topic context.")
    )
    advanced = builder.build("What about the second one?", messages, bundle.summary)
    assert advanced.summary.version == 2
    assert advanced.summary.through_ordinal == 28
    assert len(advanced.summary.content.encode("utf-8")) <= 1024
    assert "Dataset B" in advanced.summary.content


async def test_old_numbered_referent_can_be_resolved_from_persisted_rolling_summary() -> None:
    builder = ContextBuilder(ContextConfig(recent_message_limit=2, summary_max_bytes=1024))
    first = builder.build(
        "Independent question",
        history()
        + [
            ContextMessage(id="m2", ordinal=2, role="user", content="Discuss metrics."),
            ContextMessage(id="m3", ordinal=3, role="assistant", content="Metrics context."),
        ],
    )
    # A restarted worker need not replay the raw old message rows to the model.
    second = builder.build(
        "What about the second one?",
        [
            ContextMessage(id="m4", ordinal=4, role="user", content="New topic question."),
            ContextMessage(id="m5", ordinal=5, role="assistant", content="Recent unrelated text."),
        ],
        first.summary,
    )
    result = await contextualize(
        second, MockProvider([rewrite("What about Dataset B?", "the second one", "Dataset B")])
    )
    assert result.contextualized_query == "What about Dataset B?"
    assert second.metadata["summary_used"]
    assert "m1" in second.message_sources


def test_oversized_recent_window_is_summarized_and_entire_request_budgeted() -> None:
    config = ContextConfig(
        recent_message_limit=8,
        max_context_tokens=5000,
        summary_max_bytes=512,
        per_message_max_bytes=512,
    )
    messages = [
        ContextMessage(id=f"m{i}", ordinal=i, role="user", content="实验数据 " * 300)
        for i in range(8)
    ]
    bundle = ContextBuilder(config).build("Which dataset is largest?", messages)
    assert bundle.metadata["estimated_context_tokens"] == estimated_context_tokens(bundle.payload)
    assert bundle.metadata["estimated_context_tokens"] <= 5000
    assert bundle.metadata["actual_token_count_available"] is False
    assert "utf8" in bundle.metadata["estimation_method"]
    assert len(bundle.payload["recent_messages"]) < 8
    assert bundle.summary.through_ordinal >= 0
    assert all(
        len(item["content"].encode("utf-8")) <= 512 for item in bundle.payload["recent_messages"]
    )
    assert all("�" not in item["content"] for item in bundle.payload["recent_messages"])


def test_original_question_and_structured_filters_are_never_truncated_to_fit() -> None:
    with pytest.raises(ApplicationError, match="context_budget_exceeded"):
        ContextBuilder(ContextConfig(max_context_tokens=4096)).build("论文" * 1000, [])
    with pytest.raises(ApplicationError, match="context_budget_exceeded"):
        ContextBuilder(ContextConfig(max_context_tokens=4096)).build(
            "Paper methods?",
            [],
            filters=MetadataFilter(paper_ids=[f"paper-{i}" for i in range(1000)]),
        )


def test_minimum_context_budget_accepts_a_short_first_turn() -> None:
    bundle = ContextBuilder(ContextConfig(max_context_tokens=4096)).build(
        "What datasets were used?", []
    )
    assert bundle.original_query == "What datasets were used?"
    assert bundle.metadata["estimated_context_tokens"] <= 4096
    assert bundle.metadata["max_context_tokens"] == 4096
    assert not bundle.has_context


def test_budget_below_fixed_context_overhead_is_rejected_as_configuration() -> None:
    with pytest.raises(ValidationError):
        ContextConfig(max_context_tokens=2048)


def test_summary_compacts_after_config_budget_is_lowered() -> None:
    messages = history() + [
        ContextMessage(id="m2", ordinal=2, role="user", content="Further research context. " * 20)
    ]
    first = roll_summary(RollingSummary(), messages, 2048)
    assert len(first.content.encode("utf-8")) > 256
    smaller = roll_summary(first, [], 256)
    assert len(smaller.content.encode("utf-8")) <= 256
    assert smaller.through_ordinal == first.through_ordinal
    assert smaller.version == first.version + 1


def test_structured_constraints_intersect_and_cannot_be_relaxed_by_model() -> None:
    memories = [
        StructuredMemory(
            id="y",
            kind="constraint",
            content="2023 onwards",
            filters=MetadataFilter(
                year_start=2023, venues=["Nature", "Science"], sections=["Results"]
            ),
        ),
        StructuredMemory(
            id="scope",
            kind="constraint",
            content="Focused scope",
            filters=MetadataFilter(
                year_start=2024, venues=["science"], sections=["Results / Accuracy"]
            ),
        ),
    ]
    filters = merge_context_filters(MetadataFilter(year_end=2025, paper_ids=["p1"]), memories)
    assert filters.year_start == 2024 and filters.year_end == 2025
    assert filters.venues == ["Science"]
    assert filters.sections == ["Results / Accuracy"]
    assert filters.paper_ids == ["p1"]


@pytest.mark.parametrize(
    "requested,memory",
    [
        (MetadataFilter(year_end=2022), MetadataFilter(year_start=2023)),
        (MetadataFilter(paper_ids=["p1"]), MetadataFilter(paper_ids=["p2"])),
        (MetadataFilter(sections=["Results"]), MetadataFilter(sections=["Methods"])),
    ],
)
def test_conflicting_constraints_fail_closed(
    requested: MetadataFilter, memory: MetadataFilter
) -> None:
    with pytest.raises(ApplicationError, match="context_filters_conflict"):
        merge_context_filters(
            requested,
            [StructuredMemory(id="m", kind="constraint", content="scope", filters=memory)],
        )


def test_nonconstraint_memory_cannot_smuggle_filters() -> None:
    with pytest.raises(ApplicationError, match="context_memory_filters_invalid"):
        ContextBuilder().build(
            "What methods?",
            [],
            memories=[
                StructuredMemory(
                    id="m",
                    kind="preference",
                    content="style",
                    filters=MetadataFilter(year_start=2024),
                )
            ],
        )


async def test_explicit_term_memory_resolves_reference_and_can_be_removed() -> None:
    memory = StructuredMemory(id="term", kind="term", key="target method", content="DANN")
    response = {
        "status": "resolved",
        "contextualized_query": "How is DANN trained?",
        "used_memory_ids": ["term"],
        "referents": [
            {"mention": "it", "resolved_text": "DANN", "source_kind": "memory", "source_id": "term"}
        ],
    }
    resolved = await contextualize(
        ContextBuilder().build("How is it trained?", [], memories=[memory]),
        MockProvider([response]),
    )
    assert resolved.metadata["used_memory_ids"] == ["term"]
    with pytest.raises(ApplicationError, match="context_resolution_ambiguous"):
        await contextualize(ContextBuilder().build("How is it trained?", []), MockProvider([]))


@pytest.mark.parametrize(
    "source",
    [
        "query",
        "history",
        "history_id",
        "memory",
        "memory_key",
        "memory_id",
        "summary",
        "summary_source_id",
    ],
)
def test_recognizable_credentials_are_not_context_or_summary(source: str) -> None:
    secret = "sk-" + "a" * 40
    arguments: dict[str, Any] = {"original_query": "What methods?", "messages": []}
    if source == "query":
        arguments["original_query"] = secret
    elif source == "history":
        arguments["messages"] = [ContextMessage(id="m", ordinal=0, role="user", content=secret)]
    elif source == "history_id":
        arguments["messages"] = [
            ContextMessage(id=secret, ordinal=0, role="user", content="Scientific topic")
        ]
    elif source == "memory":
        arguments["memories"] = [StructuredMemory(id="m", kind="task", content=secret)]
    elif source == "memory_key":
        arguments["memories"] = [
            StructuredMemory(id="m", kind="task", key=secret, content="Scientific topic")
        ]
    elif source == "memory_id":
        arguments["memories"] = [
            StructuredMemory(id=secret, kind="task", content="Scientific topic")
        ]
    elif source == "summary":
        arguments["summary"] = RollingSummary(content=secret)
    else:
        arguments["summary"] = RollingSummary(source_message_ids=[secret])
    with pytest.raises(ApplicationError, match="credential_content_not_allowed"):
        ContextBuilder().build(**arguments)


@pytest.mark.parametrize(
    "field",
    [
        "paper_ids",
        "authors",
        "venues",
        "sections",
        "entity_types",
        "datasets",
        "methods",
        "metrics",
    ],
)
@pytest.mark.parametrize("source", ["requested_filters", "memory_filters"])
def test_direct_context_dtos_cannot_transmit_credentials_in_filters(
    field: str, source: str
) -> None:
    filters = MetadataFilter.model_validate({field: ["sk-" + "synthetic" * 5]})
    arguments: dict[str, Any] = {"original_query": "Scientific topic?", "messages": []}
    if source == "requested_filters":
        arguments["filters"] = filters
    else:
        arguments["memories"] = [
            StructuredMemory(
                id="scope", kind="constraint", content="Focused scope", filters=filters
            )
        ]
    with pytest.raises(ApplicationError, match="credential_content_not_allowed"):
        ContextBuilder().build(**arguments)


def test_context_checks_full_text_before_clipping_or_excluding_messages() -> None:
    message = ContextMessage(
        id="draft",
        ordinal=0,
        role="assistant",
        status="running",
        content="Scientific draft. " * 500 + "sk-" + "synthetic" * 5,
    )
    with pytest.raises(ApplicationError, match="credential_content_not_allowed"):
        ContextBuilder().build("Methods?", [message])


async def test_scientific_token_words_in_context_reach_provider_without_false_positive() -> None:
    provider = CapturingProvider(
        [{"status": "resolved", "contextualized_query": "Compare token counts in DANN."}]
    )
    result = await contextualize(
        ContextBuilder().build(
            "Compare token counts in DANN.",
            [ContextMessage(id="m", ordinal=0, role="user", content="Token accuracy is relevant.")],
            memories=[
                StructuredMemory(id="p", kind="preference", key="token count", content="Be brief")
            ],
            filters=MetadataFilter(methods=["tokenization"], metrics=["access_token frequency"]),
        ),
        provider,
    )
    assert result.contextualized_query == "Compare token counts in DANN."
    assert provider.payloads[0]["enforced_filters"]["metrics"] == ["access_token frequency"]


class FreshSearch:
    def __init__(self) -> None:
        self.calls: list[QueryPlan] = []
        self.item = evidence().model_copy(
            update={
                "content": "Dataset A has 23 participants.",
                "quote": "Dataset A has 23 participants.",
                "span_end": 30,
            }
        )
        self.item.span_end = len(self.item.quote)

    async def search(self, plan: QueryPlan, rerank: bool = True) -> SearchResult:
        self.calls.append(plan)
        return SearchResult(dense=[], lexical=[], fused=[], evidence=[self.item])


@pytest.mark.parametrize("mode", ["rag", "research"])
async def test_false_history_never_enters_fresh_scientific_evidence(mode: str) -> None:
    builder = ContextBuilder()
    rewriter = CapturingProvider([rewrite("How many participants does Dataset A contain?")])
    resolved = await contextualize(
        builder.build("How many participants does it contain?", history()), rewriter
    )
    search = FreshSearch()
    claim = Claim(
        claim_id="count",
        text=search.item.quote,
        evidence_ids=[search.item.evidence_id],
        aspect="sample_size",
    )
    verdict = VerificationResponse(
        verdicts=[ClaimVerdict(claim_id="count", supported=True, reason="Fresh exact quote")],
        supported_pairs=[ClaimEvidencePair(claim_id="count", evidence_id=search.item.evidence_id)],
        question_answered=True,
    )
    analyst = CapturingProvider(
        [AnswerDraft(claims=[claim]) if mode == "rag" else AnalysisResult(claims=[claim])]
    )
    reviewer = CapturingProvider([verdict])
    if mode == "rag":
        planner = MockProvider(
            [QueryPlan(queries=["Dataset A sample size"], required_aspects=["sample_size"])]
        )
        state = RAGState.model_validate(
            await build_rag(search, planner, MockProvider([]), analyst, reviewer).ainvoke(
                RAGState(query=resolved.contextualized_query, filters=resolved.filters)
            )
        )
        answer = state.answer
    else:
        planner = MockProvider(
            [
                ResearchPlan(
                    objective="Sample size",
                    required_aspects=["sample_size"],
                    subtasks=[
                        SubTask(
                            task_id="count",
                            question=resolved.contextualized_query,
                            aspect="sample_size",
                            queries=["Dataset A sample size"],
                        )
                    ],
                )
            ]
        )
        research = MultiAgentState.model_validate(
            await build_research(search, planner, MockProvider([]), analyst, reviewer).ainvoke(
                MultiAgentState(
                    research_question=resolved.contextualized_query, filters=resolved.filters
                )
            )
        )
        assert research.status == "completed"
        answer = research.draft_report
    assert search.calls, "Every follow-up must enter the existing fresh retrieval pipeline"
    assert "23 participants" in answer and "500" not in answer
    assert "500" in json.dumps(rewriter.payloads), (
        "The false historical statement was actually present"
    )
    for provider in (analyst, reviewer):
        serialized = json.dumps(provider.payloads)
        assert "500" not in serialized and "[E:old]" not in serialized
        supplied = provider.payloads[0]["evidence"]
        assert len(supplied) == 1 and supplied[0]["evidence_id"] == search.item.evidence_id
        assert "conversation_summary" not in provider.payloads[0]
        assert "recent_messages" not in provider.payloads[0]


@pytest.mark.parametrize("mode", ["rag", "research"])
async def test_historical_citation_cannot_substitute_for_current_retrieved_evidence(
    mode: str,
) -> None:
    resolved = await contextualize(
        ContextBuilder().build("How many participants does it contain?", history()),
        MockProvider([rewrite("How many participants does Dataset A contain?")]),
    )
    search = FreshSearch()
    # An adversarial/model-memory draft tries to cite the conversation's old
    # marker. That ID is absent from the current search, so verification must
    # reject it before asking a semantic reviewer to endorse the claim.
    bad = Claim(
        claim_id="old-claim",
        text="Dataset A has 500 participants.",
        evidence_ids=["old"],
        aspect="sample_size",
    )
    reviewer = MockProvider([])
    if mode == "rag":
        state = RAGState.model_validate(
            await build_rag(
                search,
                MockProvider(
                    [QueryPlan(queries=["Dataset A count"], required_aspects=["sample_size"])]
                ),
                MockProvider([]),
                MockProvider([AnswerDraft(claims=[bad])]),
                reviewer,
                max_retries=0,
            ).ainvoke(RAGState(query=resolved.contextualized_query))
        )
        assert state.status == "insufficient_evidence" and not state.claims
        assert "500" not in state.answer
    else:
        research = MultiAgentState.model_validate(
            await build_research(
                search,
                MockProvider(
                    [
                        ResearchPlan(
                            objective="Sample size",
                            required_aspects=["sample_size"],
                            subtasks=[
                                SubTask(
                                    task_id="size",
                                    question=resolved.contextualized_query,
                                    aspect="sample_size",
                                    queries=["Dataset A count"],
                                )
                            ],
                        )
                    ]
                ),
                MockProvider([]),
                MockProvider([AnalysisResult(claims=[bad])]),
                reviewer,
                max_retrievals=1,
                max_revisions=0,
            ).ainvoke(MultiAgentState(research_question=resolved.contextualized_query))
        )
        assert research.status == "insufficient_evidence"
        assert "500" not in research.draft_report
    assert not reviewer.calls
