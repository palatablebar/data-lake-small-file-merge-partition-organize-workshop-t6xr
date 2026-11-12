from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.controllers import orchestration_controller
from app.middlewares.auth import get_current_user
from app.middlewares.rbac import require_roles
from app.models import User
from app.schemas.orchestration import OrchestrationGenerateRequest

router = APIRouter(prefix="/orchestrations", tags=["orchestrations"])


@router.get("")
def list_batches(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return orchestration_controller.list_batches(db)


@router.post("")
def generate(
    payload: OrchestrationGenerateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "engineer")),
):
    return orchestration_controller.generate(db, payload, request, user)


@router.get("/{batch_id}")
def get_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return orchestration_controller.get_batch(db, batch_id)


@router.post("/{batch_id}/submit")
def submit(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "engineer")),
):
    return orchestration_controller.submit(db, batch_id, request, user)
