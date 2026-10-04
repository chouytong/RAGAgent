from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ragagent.db.models import Base


class JobDispatch(Base):
    """A durable dispatch intent, committed in the same transaction as its Run."""

    __tablename__ = "job_dispatches"
    __table_args__ = (CheckConstraint("attempts >= 0"),)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
