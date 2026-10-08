"""SYNTHETIC CONTRACT FIXTURES, no model inference or scientific quality claims."""

from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from ragagent.evaluation.multilingual import MultilingualBenchmark, not_measured, run_matrix


def fixture_spec(tmp_path: Path) -> dict[str, Any]:
    chunks = []
    cases = []
    for index, (query_lang, paper_lang) in enumerate((("en", "en"), ("zh", "en"), ("zh", "zh")), 1):
        chunk_id, paper_id = str(UUID(int=index)), str(UUID(int=index + 10))
        chunks.append(
            {
                "id": chunk_id,
                "paper_id": paper_id,
                "title": "SYNTHETIC TEST",
                "content": "SCRIPTED TEST / NOT SCIENTIFIC DATA",
                "language": paper_lang,
                "source_url": "https://example.org/fixture",
                "source_version": "TEST",
                "source_status": "unknown",
                "license": "SYNTHETIC TEST",
                "section": "Methods",
                "page": 1,
            }
        )
        cases.append(
            {
                "id": f"q{index}",
                "query": "SCRIPTED TEST",
                "question_type": "fact",
                "query_language": query_lang,
                "paper_language": paper_lang,
                "relevant_chunk_ids": [chunk_id],
                "expected_answer": "NOT A SCIENTIFIC GOLD",
                "annotated_by": "SCRIPTED HARNESS",
                "annotated_at": "2026-10-08",
            }
        )
    model = {
        "name": "NOT A REAL MODEL",
        "directory": str(tmp_path / "missing-model"),
        "license_file": str(tmp_path / "missing-LICENSE"),
        "license_sha256": "0" * 64,
        "license_spdx": "MIT",
    }
    return {
        "dataset_id": "SYNTHETIC HARNESS / NOT A BENCHMARK",
        "annotation_version": "TEST",
        "cases": cases,
        "corpus": chunks,
        "matrix": [{"name": "probe", "embedding": model, "reranker": deepcopy(model)}],
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_direction",
        "missing_annotation",
        "wrong_language",
        "missing_translation",
        "bad_name",
        "inconsistent_paper",
    ],
)
def test_matrix_rejects_missing_or_misleading_provenance(tmp_path: Path, mutation: str) -> None:
    spec = fixture_spec(tmp_path)
    if mutation == "missing_direction":
        spec["cases"][2]["query_language"] = "en"
    elif mutation == "missing_annotation":
        spec["cases"][0]["annotated_by"] = None
    elif mutation == "wrong_language":
        spec["cases"][0]["paper_language"] = "zh"
    elif mutation == "missing_translation":
        spec["matrix"][0]["translate_zh_en"] = True
    elif mutation == "bad_name":
        spec["matrix"][0]["name"] = "../../escape"
    else:
        spec["corpus"][1]["paper_id"] = spec["corpus"][0]["paper_id"]
        spec["corpus"][1]["source_version"] = "different"
    with pytest.raises(ValidationError):
        MultilingualBenchmark.model_validate(spec)


async def test_missing_models_do_not_open_database_or_invent_zero_metrics(tmp_path: Path) -> None:
    spec = MultilingualBenchmark.model_validate(fixture_spec(tmp_path))
    report = await run_matrix(spec, "NOT A DATABASE URL", tmp_path)
    assert report["matrix"]["probe"] == not_measured("licensed_offline_model_artifacts_missing")
    assert report["matrix"]["probe"]["metrics"] is None
