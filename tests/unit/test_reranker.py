import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from ragagent.providers import model_identity
from ragagent.retrieval.reranker import CrossEncoderReranker
from tests.unit.helpers import candidate


@pytest.mark.parametrize("revision", [None, "fixed-model-revision"])
async def test_cross_encoder_revision_ranking_and_model_reuse(
    monkeypatch: pytest.MonkeyPatch, revision: str | None
) -> None:
    loaded: list[tuple[str, dict[str, Any]]] = []

    class Encoder:
        def __init__(self, model: str, **options: Any) -> None:
            loaded.append((model, options))

        def predict(self, pairs: list[tuple[str, str]]) -> Any:
            assert len(pairs) == 2
            return SimpleNamespace(tolist=lambda: [-2.0, 3.0])

    package = ModuleType("sentence_transformers")
    package.CrossEncoder = Encoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", package)
    resolved: list[tuple[str, str | None]] = []

    def resolve(model: str, revision: str | None) -> str:
        resolved.append((model, revision))
        return "a" * 40

    monkeypatch.setattr(model_identity, "resolve_hub_revision", resolve)
    reranker = CrossEncoderReranker("fixture-model", revision=revision)
    candidates = [candidate("first"), candidate("second")]
    first = await reranker.rerank("query", candidates, 1)
    second = await reranker.rerank("other query", candidates, 2)
    assert first[0].evidence.chunk_id == "second"
    assert [item.evidence.chunk_id for item in second] == ["second", "first"]
    assert first[0].evidence.scores["rerank"] == 3.0
    assert loaded == [("fixture-model", {"revision": "a" * 40})]
    assert resolved == [("fixture-model", revision)]
