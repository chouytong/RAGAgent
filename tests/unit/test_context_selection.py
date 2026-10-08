"""Intent-only multilingual gating and independently bounded memory selection."""

import pytest

from ragagent.conversations.context import (
    ContextBuilder,
    approximate_tokens,
    contextualize,
    query_needs_context,
)
from ragagent.domain.conversation_context import (
    ContextConfig,
    ContextMessage,
    ConversationState,
    ResolvedReferent,
    StructuredMemory,
)
from ragagent.domain.research import MetadataFilter
from ragagent.providers.chat import MockProvider


@pytest.mark.parametrize(
    "query",
    [
        "DANN 是什么？",
        "DANN 使用哪些数据集？",
        "Explain DANN's training method.",
        "Compare DANN and MMD on MNIST.",
        "What are the limitations of MMD?",
    ],
)
async def test_independent_question_skips_rewrite_even_with_history_and_memory(query: str) -> None:
    provider = MockProvider([])
    bundle = ContextBuilder().build(
        query,
        [ContextMessage(id="old", ordinal=0, role="assistant", content="Unrelated paper notes.")],
        memories=[
            StructuredMemory(
                id="hard",
                kind="constraint",
                content="Recent papers",
                filters=MetadataFilter(year_start=2020),
            )
        ],
    )
    result = await contextualize(bundle, provider)
    assert result.contextualized_query == query and not provider.calls
    assert result.filters.year_start == 2020
    assert result.metadata["context_gate"] == "standalone"


@pytest.mark.parametrize(
    "query",
    [
        "它的数据集是什么？",
        "前者有什么限制？",
        "上一篇论文使用什么方法？",
        "第二个样本更大吗？",
        "How was it trained?",
        "What about the second paper?",
        "And the datasets?",
        "Compare them again.",
        "Which one is better?",
    ],
)
def test_dependent_question_requires_resolution(query: str) -> None:
    assert query_needs_context(query)


def test_top_k_selects_relevant_memory_but_merges_every_hard_filter() -> None:
    memories = [
        StructuredMemory(
            id=f"m{i:03}",
            kind="constraint",
            content=f"Unrelated topic {i}",
            filters=MetadataFilter(year_start=1900 + i),
        )
        for i in range(98)
    ] + [
        StructuredMemory(id="target", kind="term", key="DANN 数据集", content="MNIST"),
        StructuredMemory(
            id="last",
            kind="constraint",
            content="Mandatory Methods section",
            filters=MetadataFilter(sections=["Methods"]),
        ),
    ]
    config = ContextConfig(memory_top_k=4, memory_tokens=128)
    bundle = ContextBuilder(config).build("DANN 数据集", [], memories=memories)
    assert bundle.metadata["stored_memory_count"] == 100
    assert len(bundle.metadata["selected_memory_ids"]) <= 4
    assert "target" in bundle.metadata["selected_memory_ids"]
    assert bundle.filters.year_start == 1997 and bundle.filters.sections == ["Methods"]
    assert len(bundle.metadata["hard_constraint_memory_ids"]) == 99
    assert bundle.metadata["memory_text_estimated_tokens"] <= 128
    assert (
        ContextBuilder(config).build("DANN 数据集", [], memories=list(reversed(memories))).payload
        == bundle.payload
    )


def test_estimator_distinguishes_chinese_latin_and_number_runs() -> None:
    assert approximate_tokens("科研中文") == 8
    assert approximate_tokens("abcdef") == 2
    assert approximate_tokens("123456") == 2
    assert approximate_tokens("!?") == 2


def test_old_validated_entities_survive_summary_loss_without_importing_facts() -> None:
    messages = [ContextMessage(id="anchor", ordinal=0, role="assistant", content="DANN and MMD.")]
    messages += [
        ContextMessage(id=f"m{i}", ordinal=i, role="user", content="Later context " * 100)
        for i in range(1, 100)
    ]
    state = ConversationState(
        resolved_entities=[
            ResolvedReferent(
                mention="it", resolved_text="DANN", source_kind="message", source_id="anchor"
            ),
            ResolvedReferent(
                mention="it",
                resolved_text="Fake has 500 participants",
                source_kind="message",
                source_id="missing",
            ),
        ]
    )
    config = ContextConfig(recent_message_limit=2, recent_tokens=128, summary_tokens=128)
    bundle = ContextBuilder(config).build("How was it trained?", messages, state=state)
    assert "DANN" in bundle.message_sources["anchor"]
    assert bundle.payload["resolved_intent_entities_not_evidence"] == [
        state.resolved_entities[0].model_dump()
    ]
    assert "500" not in str(bundle.payload)
    assert bundle.metadata["recent_estimated_tokens"] <= 128
    assert bundle.metadata["summary_estimated_tokens"] <= 128
    messages[0].is_effective = False
    changed = ContextBuilder(config).build("How was it trained?", messages, state=state)
    assert "anchor" not in changed.message_sources
    assert not changed.payload["resolved_intent_entities_not_evidence"]
