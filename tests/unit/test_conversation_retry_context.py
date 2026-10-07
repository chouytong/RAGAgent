"""Retry audit records remain stored but cannot become effective context."""

from ragagent.conversations.context import ContextBuilder, roll_summary
from ragagent.domain.conversation_context import ContextConfig, ContextMessage, RollingSummary


def test_only_effective_retry_answer_enters_context() -> None:
    messages = [
        ContextMessage(id="u", ordinal=0, role="user", content="Compare DANN and MMD."),
        ContextMessage(
            id="a1",
            ordinal=1,
            role="assistant",
            content="Draft",
            status="failed",
            is_effective=False,
        ),
        ContextMessage(
            id="a2",
            ordinal=2,
            role="assistant",
            content="Old refusal about DANN",
            status="insufficient_evidence",
            retry_of_message_id="a1",
            attempt_number=2,
            is_effective=False,
        ),
        ContextMessage(
            id="a3",
            ordinal=3,
            role="assistant",
            content="Latest answer about MMD",
            retry_of_message_id="a2",
            attempt_number=3,
        ),
    ]
    bundle = ContextBuilder().build("What about the second one?", messages)
    assert bundle.metadata["history_message_ids"] == ["u", "a3"]
    assert bundle.metadata["superseded_message_ids"] == ["a1", "a2"]
    assert "Old refusal" not in str(bundle.payload)
    assert "Latest answer" in str(bundle.payload)
    assert messages[1].content == "Draft" and messages[2].content == "Old refusal about DANN"


def test_superseded_summary_anchor_is_rebuilt_from_effective_history() -> None:
    old = [
        ContextMessage(id="u", ordinal=0, role="user", content="Compare DANN and MMD."),
        ContextMessage(
            id="a1",
            ordinal=1,
            role="assistant",
            content="Old refusal states 999 samples",
            status="insufficient_evidence",
        ),
        ContextMessage(id="u2", ordinal=2, role="user", content="Discuss the experimental setup."),
    ]
    summary = roll_summary(RollingSummary(), old, 1024)
    assert "999" in summary.content and "a1" in summary.source_message_ids
    history = [
        old[0],
        old[1].model_copy(update={"is_effective": False}),
        old[2],
        ContextMessage(
            id="a2",
            ordinal=3,
            role="assistant",
            content="Latest answer about MMD",
            retry_of_message_id="a1",
            attempt_number=2,
        ),
        ContextMessage(id="u3", ordinal=4, role="user", content="Discuss metrics."),
        ContextMessage(id="a3", ordinal=5, role="assistant", content="Metric descriptions."),
    ]
    bundle = ContextBuilder(ContextConfig(recent_message_limit=2, summary_max_bytes=1024)).build(
        "What about the second one?",
        history,
        summary,
    )
    assert "999" not in bundle.summary.content and "999" not in str(bundle.payload)
    assert "a1" not in bundle.summary.source_message_ids
    assert "a1" not in bundle.message_sources
    assert "MMD" in bundle.summary.content
    assert bundle.summary.version == summary.version + 2
    assert bundle.summary.through_ordinal == 3
    assert bundle.metadata["recent_message_ids"] == ["u3", "a3"]
    # Reusing the rebuilt summary must not reintroduce the old audit record.
    again = ContextBuilder(ContextConfig(recent_message_limit=2, summary_max_bytes=1024)).build(
        "What about the second one?",
        history,
        bundle.summary,
    )
    assert again.summary == bundle.summary
    assert "a1" not in again.message_sources


def test_all_superseded_summary_sources_leave_empty_valid_coverage() -> None:
    message = ContextMessage(
        id="a",
        ordinal=0,
        role="assistant",
        content="Unsupported 999",
        status="insufficient_evidence",
    )
    summary = roll_summary(RollingSummary(), [message], 1024)
    bundle = ContextBuilder().build(
        "Explain DANN.",
        [message.model_copy(update={"is_effective": False})],
        summary,
    )
    assert bundle.summary.content == ""
    assert bundle.summary.source_message_ids == []
    assert bundle.summary.through_ordinal == 0
    assert bundle.summary.version == summary.version + 1
    assert not bundle.has_context


def test_existing_context_message_contract_defaults_remain_compatible() -> None:
    message = ContextMessage(id="legacy", ordinal=0, role="assistant", content="Legacy answer")
    assert message.is_effective and message.attempt_number == 1
    assert message.retry_of_message_id is None
    assert ContextBuilder().build("Explain DANN.", [message]).message_sources == {
        "legacy": "Legacy answer"
    }
