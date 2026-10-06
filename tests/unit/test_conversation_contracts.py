from uuid import uuid4

import pytest
from pydantic import ValidationError

from ragagent.domain.conversation import (
    ConversationCreate,
    ConversationFilters,
    MemoryCreate,
    MessageCreate,
)
from ragagent.domain.research import MetadataFilter

FILTER_TEXT_FIELDS = (
    "paper_ids",
    "authors",
    "venues",
    "sections",
    "entity_types",
    "datasets",
    "methods",
    "metrics",
)


def test_conversation_inputs_trim_validate_uuid_and_preserve_scientific_token_text() -> None:
    message = MessageCreate(content="  Compare token counts in DANN.  ", client_request_id=uuid4())
    assert message.content == "Compare token counts in DANN."
    assert ConversationCreate().mode == "rag"
    with pytest.raises(ValidationError):
        MessageCreate(content="   ", client_request_id=uuid4())
    with pytest.raises(ValidationError):
        MessageCreate(content="q", client_request_id="invalid")


@pytest.mark.parametrize(
    "content",
    [
        "sk-" + "x" * 30,
        "ghp_" + "a" * 30,
        "github_pat_" + "b" * 30,
        "AKIA" + "C" * 16,
        "-----BEGIN PRIVATE KEY-----\nsynthetic fixture\n-----END PRIVATE KEY-----",
        "OPENAI_API_KEY=" + "syntheticcredential12345",
        "api_key: syntheticcredential12345",
        "https://user:synthetic-password@example.org",
    ],
)
def test_recognizable_credentials_never_enter_persistable_context(content: str) -> None:
    for contract, arguments in [
        (MessageCreate, {"content": content, "client_request_id": uuid4()}),
        (MemoryCreate, {"kind": "goal", "content": content}),
        (ConversationCreate, {"title": content}),
    ]:
        with pytest.raises(ValidationError, match="credential_content_not_allowed"):
            contract.model_validate(arguments)


def test_secrets_arbitrary_metadata_and_system_roles_are_not_accepted() -> None:
    for extra in ({"api_key": "fixture"}, {"metadata": {"secret": "fixture"}}, {"role": "system"}):
        with pytest.raises(ValidationError):
            MessageCreate.model_validate(
                {"content": "q", "client_request_id": str(uuid4()), **extra}
            )
    with pytest.raises(ValidationError):
        MessageCreate.model_validate(
            {"content": "q", "client_request_id": str(uuid4()), "filters": {"api_key": "fixture"}}
        )


def test_structured_filters_are_explicit_constraints_only() -> None:
    memory = MemoryCreate(
        kind="constraint", content="Only recent papers", filters={"year_start": 2023}
    )
    assert memory.filters is not None and memory.filters.year_start == 2023
    with pytest.raises(ValidationError, match="memory_filters_require_constraint"):
        MemoryCreate(kind="preference", content="Recent", filters={"year_start": 2023})
    with pytest.raises(ValidationError):
        MemoryCreate(
            kind="constraint",
            content="invalid years",
            filters={"year_start": 2025, "year_end": 2020},
        )


@pytest.mark.parametrize("field", FILTER_TEXT_FIELDS)
def test_credentials_cannot_enter_message_or_memory_through_filter_values(field: str) -> None:
    filters = {field: ["sk-" + "synthetic" * 5]}
    for contract, arguments in (
        (MessageCreate, {"content": "Methods?", "client_request_id": uuid4()}),
        (MemoryCreate, {"kind": "constraint", "content": "Focused corpus"}),
    ):
        with pytest.raises(ValidationError, match="credential_content_not_allowed"):
            contract.model_validate({**arguments, "filters": filters})
    # The restriction is local to conversation persistence, not a global change
    # to scientific metadata contracts or parser behavior.
    assert getattr(MetadataFilter.model_validate(filters), field)


def test_filter_guard_preserves_normal_scientific_token_words_and_fields() -> None:
    values = {
        "authors": ["Alice Token"],
        "datasets": ["Token Count Dataset"],
        "methods": ["tokenization", "API key distribution study"],
        "metrics": ["token accuracy", "access_token frequency"],
        "sections": ["Methods / Token counting"],
        "year_start": 2023,
    }
    filters = ConversationFilters.model_validate(values)
    for field, expected in values.items():
        assert getattr(filters, field) == expected
