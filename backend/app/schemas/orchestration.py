from pydantic import BaseModel, Field


class OrchestrationGenerateRequest(BaseModel):
    # 可选：覆盖默认固定重写预算（分区数）；不传走 settings.ORCHESTRATION_REWRITE_BUDGET
    rewrite_budget: int | None = Field(default=None, ge=1, le=50)
    note: str | None = Field(default=None, max_length=500)
