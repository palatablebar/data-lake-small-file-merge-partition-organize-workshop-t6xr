from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.database import Base


class OrchestrationItem(Base):
    """Per-table row inside an OrchestrationBatch.

    Every active table gets exactly one item per batch: either ``selected``
    (with the chosen partitions and a persisted CompactPlan once submitted) or
    ``skipped`` carrying the machine-readable ``reason_code`` shown on the page.
    """

    __tablename__ = "orchestration_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "table_id", name="uq_orchestration_item_table"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orchestration_batches.id", ondelete="CASCADE"), index=True
    )
    table_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lake_tables.id", ondelete="CASCADE"), index=True
    )
    # selected | executing | completed | failed | skipped
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="skipped")
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_partitions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # 该表全部候选分区（含未入选），每项 {partition_value, selected, reason_code, ...}
    partition_decisions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # selected 时的表级汇总；skipped 时为零值
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reason_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    reason_detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    plan_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("compact_plans.id", ondelete="SET NULL"), nullable=True
    )
    job_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("compact_jobs.id", ondelete="SET NULL"), nullable=True
    )
    snapshot_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("table_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    batch = relationship("OrchestrationBatch", back_populates="items")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OrchestrationItem batch={self.batch_id} table={self.table_id} status={self.status}>"
