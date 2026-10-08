import pytest
from pydantic import ValidationError

from ragagent import queues
from ragagent.db.models import Run
from ragagent.settings import Settings


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("rag", "interactive"),
        ("research", "interactive"),
        ("ingestion", "ingestion"),
        ("arxiv", "ingestion"),
        ("eval_rag", "evaluation"),
        ("eval_multi_agent", "evaluation"),
        ("eval_conversation", "evaluation"),
        ("eval_retrieval", "evaluation"),
    ],
)
def test_run_routing_is_frozen_across_reconciliation_and_config_changes(
    monkeypatch: pytest.MonkeyPatch, kind: str, expected: str
) -> None:
    settings = Settings(_env_file=None)
    monkeypatch.setattr(queues, "get_settings", lambda: settings)
    run = Run(kind=kind, request={"query": "Synthetic fixture"})
    assert queues.freeze_queue(run) == expected
    settings.interactive_queue = "new_interactive"
    settings.ingestion_queue = "new_ingestion"
    settings.evaluation_queue = "new_evaluation"
    assert queues.freeze_queue(run) == expected
    assert run.request["_queue_name"] == expected
    assert run.request["query"] == "Synthetic fixture"


@pytest.mark.parametrize(
    "names",
    [
        {"interactive_queue": "evaluation"},
        {"ingestion_queue": "research"},
        {"evaluation_queue": "invalid/queue"},
    ],
)
def test_queue_configuration_cannot_silently_collapse_isolation(names: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **names)
