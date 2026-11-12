from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.database import Base


class OrchestrationBatch(Base):
    """One day's cross-table compaction orchestration.

    A batch is the unit of the fixed nightly rewrite budget: ``budget_total``
    partitions can be selected at most, and no single table may take more than
    ``max_per_table`` (half the budget by default). Re-posting the same day
    returns the existing batch unchanged — the budget allocation never moves.
    """

    __tablename__ = "orchestration_batches"
    __table_args__ = (UniqueConstraint("batch_date", name="uq_orchestration_batch_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="planned")
    budget_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    budget_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_per_table: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 跨表编排指纹：对当日候选分区与预算口径做确定性摘要，用于幂等说明
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    totals: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    items = relationship(
        "OrchestrationItem",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="OrchestrationItem.rank",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OrchestrationBatch {self.batch_date} status={self.status}>"
