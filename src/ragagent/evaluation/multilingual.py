"""Offline real-model matrix using the existing PG/FTS/RRF/reranking evaluator.

No gold labels are generated. Scratch-schema indexes never replace user indexes.
"""

import hashlib
import time
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ragagent.db.models import Base, Chunk, Paper, Section
from ragagent.domain.privacy import SensitiveInput, reject_credentials
from ragagent.evaluation.artifacts import canonical_hash
from ragagent.evaluation.metrics import average
from ragagent.evaluation.retrieval import evaluate_retrieval
from ragagent.evaluation.schema import EvaluationCase, EvaluationDataset
from ragagent.providers.benchmark import configure_cpu_benchmark
from ragagent.providers.embedding import LocalEmbedder
from ragagent.retrieval.reranker import CrossEncoderReranker
from ragagent.retrieval.service import HybridRetriever
from ragagent.settings import Settings

Language = Literal["en", "zh"]


class BilingualCase(EvaluationCase):
    query_language: Language
    paper_language: Language
    translated_query: str | None = None
    translated_by: str | None = None
    translated_at: str | None = None


class BenchmarkChunk(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    paper_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    language: Language
    source_url: str = Field(pattern=r"^https://")
    source_version: str = Field(min_length=1)
    source_status: Literal["active", "unknown"]
    license: str = Field(min_length=1)
    year: int | None = None
    section: str = Field(min_length=1)
    page: int = Field(ge=1)


class OfflineModel(BaseModel):
    name: str = Field(min_length=1)
    directory: Path
    license_file: Path
    license_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    license_spdx: Literal["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause"]
    upstream_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")

    def available(self) -> bool:
        return (
            self.directory.is_dir()
            and self.license_file.is_file()
            and hashlib.sha256(self.license_file.read_bytes()).hexdigest() == self.license_sha256
            and (
                any(self.directory.rglob("*.safetensors"))
                or any(self.directory.rglob("pytorch_model*.bin"))
            )
        )


class MatrixEntry(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    embedding: OfflineModel
    reranker: OfflineModel
    translate_zh_en: bool = False


class MultilingualBenchmark(SensitiveInput):
    dataset_id: str = Field(min_length=1)
    annotation_version: str = Field(min_length=1)
    cases: list[BilingualCase] = Field(min_length=3, max_length=1000)
    corpus: list[BenchmarkChunk] = Field(min_length=1, max_length=10000)
    matrix: list[MatrixEntry] = Field(min_length=1, max_length=20)
    seed: int = 0

    @model_validator(mode="after")
    def checked_gold(self) -> "MultilingualBenchmark":
        dataset = EvaluationDataset(
            dataset_id=self.dataset_id,
            label_source="human",
            description=self.annotation_version,
            cases=[case for case in self.cases],
        )
        dataset.runnable()
        ids = {chunk.id: chunk for chunk in self.corpus}
        if len(ids) != len(self.corpus) or len({m.name for m in self.matrix}) != len(self.matrix):
            raise ValueError("duplicate_benchmark_identity")
        paper_metadata: dict[str, tuple[Any, ...]] = {}
        for chunk in self.corpus:
            metadata = (
                chunk.title,
                chunk.language,
                chunk.source_url,
                chunk.source_version,
                chunk.source_status,
                chunk.year,
                chunk.license,
            )
            if chunk.paper_id in paper_metadata and paper_metadata[chunk.paper_id] != metadata:
                raise ValueError("inconsistent_paper_metadata")
            paper_metadata[chunk.paper_id] = metadata
        required = {("en", "en"), ("zh", "en"), ("zh", "zh")}
        if not required.issubset({(c.query_language, c.paper_language) for c in self.cases}):
            raise ValueError("missing_language_direction")
        for case in self.cases:
            if set(case.relevant_paper_ids) - paper_metadata.keys():
                raise ValueError("gold_paper_identity_invalid")
            if any(
                cid not in ids or ids[cid].language != case.paper_language
                for cid in case.relevant_chunk_ids
            ):
                raise ValueError("gold_chunk_language_or_identity_invalid")
            if any(
                case.filters.model_dump().get(field)
                for field in ("authors", "venues", "entity_types", "datasets", "methods", "metrics")
            ):
                raise ValueError("benchmark_corpus_lacks_requested_metadata")
            if any(m.translate_zh_en for m in self.matrix) and (
                case.query_language,
                case.paper_language,
            ) == ("zh", "en"):
                if not all((case.translated_query, case.translated_by, case.translated_at)):
                    raise ValueError("translation_requires_human_provenance")
        return self


def not_measured(reason: str) -> dict[str, Any]:
    return {"status": "Not measured", "error_code": reason, "metrics": None}


async def run_matrix(
    spec: MultilingualBenchmark, database_url: str, directory: Path
) -> dict[str, Any]:
    reject_credentials(spec.model_dump(mode="json"))
    report: dict[str, Any] = {
        "dataset_hash": canonical_hash(spec.model_dump(mode="json")),
        "annotation_warning": "User-supplied human gold/translation, not independently verified.",
        "seed": spec.seed,
        "device": "cpu",
        "index_method": "existing exact pgvector + English FTS/RRF",
        "corpus_sources": [c.model_dump(exclude={"content"}) for c in spec.corpus],
        "matrix": {},
    }
    for entry in spec.matrix:
        if not entry.embedding.available() or not entry.reranker.available():
            report["matrix"][entry.name] = not_measured("licensed_offline_model_artifacts_missing")
            continue
        # Imports only after availability; CI never installs/downloads weights.
        configure_cpu_benchmark(spec.seed)
        embedder = LocalEmbedder(str(entry.embedding.directory.resolve()), 384)
        reranker = CrossEncoderReranker(str(entry.reranker.directory.resolve()))
        engine = create_engine(database_url, hide_parameters=True)
        schema = "ragagent_bench_" + uuid4().hex
        try:
            with engine.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            isolated = engine.execution_options(schema_translate_map={None: schema})
            with isolated.begin() as connection:
                Base.metadata.create_all(connection)
            with Session(isolated) as session:
                start = time.perf_counter()
                vectors = await embedder.embed([c.content for c in spec.corpus])
                fingerprint = embedder.fingerprint
                papers, sections = set(), set()
                for ordinal, (chunk, vector) in enumerate(zip(spec.corpus, vectors, strict=True)):
                    if chunk.paper_id not in papers:
                        session.add(
                            Paper(
                                id=chunk.paper_id,
                                title=chunk.title,
                                year=chunk.year,
                                source_status=chunk.source_status,
                                source_url=chunk.source_url,
                                sha256=hashlib.sha256(chunk.paper_id.encode()).hexdigest(),
                                original_path="benchmark-manifest",
                                status="indexed",
                                embedding_model=fingerprint,
                            )
                        )
                        session.flush()
                        papers.add(chunk.paper_id)
                    sid = str(uuid5(NAMESPACE_URL, chunk.paper_id + ":" + chunk.section))
                    if sid not in sections:
                        session.add(
                            Section(
                                id=sid,
                                paper_id=chunk.paper_id,
                                title=chunk.section,
                                path=chunk.section,
                                ordinal=ordinal,
                            )
                        )
                        session.flush()
                        sections.add(sid)
                    session.add(
                        Chunk(
                            id=chunk.id,
                            paper_id=chunk.paper_id,
                            section_id=sid,
                            section_path=chunk.section,
                            page_start=chunk.page,
                            page_end=chunk.page,
                            element_type="text",
                            content=chunk.content,
                            token_count=max(1, len(chunk.content.split())),
                            ordinal=ordinal,
                            embedding=vector,
                        )
                    )
                session.commit()
                indexing_ms = (time.perf_counter() - start) * 1000
                cases = [EvaluationCase.model_validate(c.model_dump()) for c in spec.cases]
                for original, case in zip(spec.cases, cases, strict=True):
                    if entry.translate_zh_en and (
                        original.query_language,
                        original.paper_language,
                    ) == ("zh", "en"):
                        assert original.translated_query is not None
                        case.query = original.translated_query
                dataset = EvaluationDataset(
                    dataset_id=spec.dataset_id,
                    label_source="human",
                    description=spec.annotation_version,
                    cases=cases,
                )
                results = await evaluate_retrieval(
                    dataset,
                    HybridRetriever(session, embedder, reranker),
                    session,
                    Settings(embedding_dimension=384),
                    directory / entry.name,
                )
                by_direction: dict[str, Any] = {}
                for direction in ("en-en", "zh-en", "zh-zh"):
                    selected = {
                        c.id
                        for c in spec.cases
                        if f"{c.query_language}-{c.paper_language}" == direction
                    }
                    by_direction[direction] = {}
                    for mode, rows in results["per_query"].items():
                        successful = [
                            row
                            for row in rows
                            if row["id"] in selected and row["evaluation_status"] == "completed"
                        ]
                        by_direction[direction][mode] = {
                            "case_count": len(selected),
                            "completed_count": len(successful),
                            "failed_count": sum(
                                row["id"] in selected and row["evaluation_status"] == "failed"
                                for row in rows
                            ),
                            "metrics": average(row["metrics"] for row in successful)
                            if successful
                            else None,
                        }
                report["matrix"][entry.name] = {
                    "status": results["status"],
                    "indexing_and_embedding_load_ms": indexing_ms,
                    "embedding_identity": fingerprint,
                    "reranker_revision": reranker.revision,
                    "embedding_declared_upstream_revision": entry.embedding.upstream_revision,
                    "reranker_declared_upstream_revision": entry.reranker.upstream_revision,
                    "translation": "human_pretranslated" if entry.translate_zh_en else "none",
                    "metrics_by_direction": by_direction,
                    "raw_report": str(directory / entry.name / "results.json"),
                }
        finally:
            try:
                with engine.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            finally:
                engine.dispose()
    return report
