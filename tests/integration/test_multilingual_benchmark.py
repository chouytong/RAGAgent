"""Real PG scratch-schema isolation with SCRIPTED models; NOT QUALITY METRICS."""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from ragagent.evaluation import multilingual
from ragagent.evaluation.multilingual import MultilingualBenchmark
from tests.integration.test_retrieval import Embedder, FixtureReranker
from tests.unit.test_multilingual_benchmark import fixture_spec


@pytest.mark.integration
async def test_scripted_harness_uses_core_evaluator_and_removes_owned_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_engine(url, hide_parameters=True)
    count_query = text(
        "SELECT count(*) FROM information_schema.schemata WHERE schema_name LIKE 'ragagent_bench_%'"
    )
    with engine.connect() as connection:
        before = connection.scalar(count_query)
    monkeypatch.setattr(multilingual.OfflineModel, "available", lambda self: True)
    monkeypatch.setattr(multilingual, "configure_cpu_benchmark", lambda seed: None)
    monkeypatch.setattr(multilingual, "LocalEmbedder", lambda *args: Embedder())

    class ScriptedReranker(FixtureReranker):
        revision = "SCRIPTED HARNESS / NOT A MODEL"

    monkeypatch.setattr(multilingual, "CrossEncoderReranker", lambda *args: ScriptedReranker())
    spec = MultilingualBenchmark.model_validate(fixture_spec(tmp_path))
    report = await multilingual.run_matrix(spec, url, tmp_path)
    assert report["matrix"]["probe"]["status"] == "completed"
    dimensions = report["matrix"]["probe"]["metrics_by_direction"]
    assert set(dimensions) == {"en-en", "zh-en", "zh-zh"}
    assert all(
        row["case_count"] == row["completed_count"] == 1
        for modes in dimensions.values()
        for row in modes.values()
    )
    assert (tmp_path / "probe" / "results.json").is_file()
    with engine.connect() as connection:
        assert connection.scalar(count_query) == before
    engine.dispose()
