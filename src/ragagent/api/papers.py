import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ragagent.api.dependencies import get_db
from ragagent.api.queue import JobQueue, get_queue
from ragagent.api.schemas import (
    ArxivRequest,
    EntityAnnotation,
    PaperPatch,
    PaperResponse,
    RunResponse,
)
from ragagent.db.dispatch import JobDispatch
from ragagent.db.models import (
    Author,
    Chunk,
    ChunkEntity,
    Dataset,
    Entity,
    Method,
    Metric,
    Paper,
    PaperAuthor,
    Run,
    new_id,
)
from ragagent.jobs import dispatch_run
from ragagent.settings import get_settings

router = APIRouter(prefix="/api/papers", tags=["papers"])
DB = Annotated[Session, Depends(get_db)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]


def enqueue(session: Session, queue: JobQueue, kind: str, request: dict[str, object]) -> Run:
    run = Run(kind=kind, request=request)
    session.add(run)
    session.flush()
    session.add(JobDispatch(run_id=run.id))
    session.commit()
    dispatch_run(session, queue, run.id)
    return run


def ingestion_run(session: Session, queue: JobQueue, paper_id: str) -> RunResponse:
    run = session.scalar(
        select(Run)
        .where(Run.kind == "ingestion", Run.request["paper_id"].as_string() == paper_id)
        .order_by(Run.created_at.desc())
    )
    return RunResponse.model_validate(
        run if run is not None else enqueue(session, queue, "ingestion", {"paper_id": paper_id})
    )


def author_ids(session: Session, names: list[str]) -> dict[str, str]:
    result = {}
    # Stable insertion order avoids reciprocal waits when concurrent papers list
    # the same new authors in different orders. Output still follows paper order.
    for name in sorted(names):
        author_id = session.scalar(
            insert(Author)
            .values(name=name)
            .on_conflict_do_nothing(index_elements=[Author.name])
            .returning(Author.id)
        )
        if author_id is None:
            author_id = session.scalar(select(Author.id).where(Author.name == name))
        if author_id is None:
            raise HTTPException(409, "author_changed_retry")
        result[name] = author_id
    return result


def paper_response(session: Session, paper: Paper) -> PaperResponse:
    authors = list(
        session.scalars(
            select(Author.name)
            .join(PaperAuthor)
            .where(PaperAuthor.paper_id == paper.id)
            .order_by(PaperAuthor.position)
        )
    )
    count = (
        session.scalar(select(func.count()).select_from(Chunk).where(Chunk.paper_id == paper.id))
        or 0
    )
    return PaperResponse(
        id=paper.id,
        title=paper.title,
        authors=authors,
        year=paper.year,
        venue=paper.venue,
        arxiv_id=paper.arxiv_id,
        arxiv_family_id=paper.arxiv_family_id,
        arxiv_version=paper.arxiv_version,
        source_status=paper.source_status,
        status=paper.status,
        error_code=paper.error_code,
        chunk_count=count,
    )


@router.post("/upload", status_code=202)
async def upload(
    db: DB,
    queue: QueueDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(max_length=1000)] = "",
    authors: Annotated[str, Form(max_length=4000)] = "",
    year: Annotated[int | None, Form(ge=1000, le=2100)] = None,
    venue: Annotated[str | None, Form(max_length=256)] = None,
) -> RunResponse:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.data_dir / f"{new_id()}.pdf"
    digest, size, prefix = hashlib.sha256(), 0, b""
    try:
        with path.open("wb") as target:
            while part := await file.read(65536):
                prefix = (prefix + part)[:5]
                size += len(part)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, "pdf_too_large")
                digest.update(part)
                target.write(part)
        if prefix != b"%PDF-":
            raise HTTPException(422, "invalid_pdf")
        sha = digest.hexdigest()
        if any(len(name.strip()) > 256 for name in authors.split(";")):
            raise HTTPException(422, "author_name_too_long")
        # Distinct pinned arXiv versions can have identical PDF bytes. A plain
        # upload reuses an indexed copy first, then an uploaded/most recent copy.
        duplicate = (
            select(Paper)
            .where(Paper.sha256 == sha)
            .order_by(
                (Paper.status == "indexed").desc(),
                Paper.arxiv_id.is_(None).desc(),
                Paper.created_at.desc(),
                Paper.id,
            )
            .limit(1)
        )
        existing = db.scalar(duplicate)
        if existing:
            path.unlink(missing_ok=True)
            return ingestion_run(db, queue, existing.id)
        paper_id = db.scalar(
            insert(Paper)
            .values(
                title=(title or file.filename or "Untitled")[:1000],
                year=year,
                venue=venue,
                sha256=sha,
                original_path=str(path),
            )
            .on_conflict_do_nothing(
                index_elements=[Paper.sha256], index_where=Paper.arxiv_id.is_(None)
            )
            .returning(Paper.id)
        )
        if paper_id is None:
            existing = db.scalar(duplicate)
            if existing is None:
                raise HTTPException(409, "paper_changed_retry")
            path.unlink(missing_ok=True)
            return ingestion_run(db, queue, existing.id)
        names = list(dict.fromkeys(x.strip() for x in authors.split(";") if x.strip()))
        ids = author_ids(db, names)
        for position, name in enumerate(names):
            db.add(PaperAuthor(paper_id=paper_id, author_id=ids[name], position=position))
        # Paper, author links, Run and dispatch intent become visible together.
        return RunResponse.model_validate(enqueue(db, queue, "ingestion", {"paper_id": paper_id}))
    except Exception:
        db.rollback()
        # Keep the original if the paper transaction was committed (e.g. queue outage).
        if not db.scalar(select(Paper.id).where(Paper.original_path == str(path))):
            path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.post("/arxiv", status_code=202)
def arxiv(request: ArxivRequest, db: DB, queue: QueueDep) -> RunResponse:
    return RunResponse.model_validate(enqueue(db, queue, "arxiv", request.model_dump()))


@router.get("")
def papers(db: DB, limit: int = 50, offset: int = 0) -> list[PaperResponse]:
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(422, "invalid_pagination")
    return [
        paper_response(db, p)
        for p in db.scalars(
            select(Paper).order_by(Paper.created_at.desc()).limit(limit).offset(offset)
        )
    ]


@router.get("/{paper_id}")
def paper(paper_id: str, db: DB) -> PaperResponse:
    p = db.get(Paper, paper_id)
    if p is None:
        raise HTTPException(404, "paper_not_found")
    return paper_response(db, p)


@router.get("/{paper_id}/pdf")
def original_pdf(paper_id: str, db: DB) -> FileResponse:
    p = db.get(Paper, paper_id)
    if p is None:
        raise HTTPException(404, "paper_not_found")
    return FileResponse(
        p.original_path,
        media_type="application/pdf",
        filename=f"{p.id}.pdf",
        content_disposition_type="inline",
    )


@router.get("/{paper_id}/chunks")
def chunks(paper_id: str, db: DB, offset: int = 0, limit: int = 50) -> list[dict[str, object]]:
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(422, "invalid_pagination")
    return [
        {
            "chunk_id": c.id,
            "section_id": c.section_id,
            "section_path": c.section_path,
            "page_start": c.page_start,
            "page_end": c.page_end,
            "content": c.content,
            "element_type": c.element_type,
        }
        for c in db.scalars(
            select(Chunk)
            .where(Chunk.paper_id == paper_id)
            .order_by(Chunk.ordinal)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.post("/{paper_id}/chunks/{chunk_id}/entities")
def annotate(
    paper_id: str, chunk_id: str, request: list[EntityAnnotation], db: DB
) -> dict[str, str]:
    chunk = db.get(Chunk, chunk_id)
    if not chunk or chunk.paper_id != paper_id:
        raise HTTPException(404, "chunk_not_found")
    for annotation in request:
        entity = db.scalar(
            select(Entity).where(
                Entity.name == annotation.name, Entity.entity_type == annotation.entity_type
            )
        )
        if entity is None:
            entity = Entity(name=annotation.name, entity_type=annotation.entity_type)
            db.add(entity)
            db.flush()
            subtype = {"dataset": Dataset, "method": Method, "metric": Metric}.get(
                annotation.entity_type
            )
            if subtype:
                db.add(subtype(entity_id=entity.id))
        if db.get(ChunkEntity, (chunk_id, entity.id)) is None:
            db.add(ChunkEntity(chunk_id=chunk_id, entity_id=entity.id))
    db.commit()
    return {"status": "annotated"}


@router.post("/{paper_id}/retry", status_code=202)
def retry_ingestion(paper_id: str, db: DB, queue: QueueDep) -> RunResponse:
    paper = db.scalar(
        select(Paper)
        .where(Paper.id == paper_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if paper is None:
        raise HTTPException(404, "paper_not_found")
    if paper.status not in {"failed", "queued"}:
        raise HTTPException(409, "paper_not_retryable")
    paper.status, paper.error_code = "queued", None
    return RunResponse.model_validate(enqueue(db, queue, "ingestion", {"paper_id": paper.id}))


@router.patch("/{paper_id}")
def update_metadata(paper_id: str, request: PaperPatch, db: DB) -> PaperResponse:
    paper = db.scalar(
        select(Paper)
        .where(Paper.id == paper_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if paper is None:
        raise HTTPException(404, "paper_not_found")
    if "title" in request.model_fields_set and request.title is None:
        raise HTTPException(422, "title_cannot_be_null")
    if "source_status" in request.model_fields_set and request.source_status is None:
        raise HTTPException(422, "source_status_cannot_be_null")
    for field in ["title", "year", "venue", "source_status"]:
        if field in request.model_fields_set:
            setattr(paper, field, getattr(request, field))
    if "authors" in request.model_fields_set:
        names = list(dict.fromkeys(n.strip() for n in request.authors or []))
        if any(not n or len(n) > 256 for n in names):
            raise HTTPException(422, "invalid_author_name")
        db.execute(delete(PaperAuthor).where(PaperAuthor.paper_id == paper.id))
        ids = author_ids(db, names)
        for position, name in enumerate(names):
            db.add(PaperAuthor(paper_id=paper.id, author_id=ids[name], position=position))
    db.commit()
    return paper_response(db, paper)
