from fastapi import Request
from sqlalchemy.orm import Session

from app.middlewares import audit_logger
from app.models import User
from app.services import orchestration_service


def generate(db: Session, payload, request: Request, user: User) -> dict:
    result = orchestration_service.generate_batch(db, user, payload)
    batch = result["batch"]
    audit_logger.record(
        request,
        "orchestration.generated",
        "orchestration_batch",
        batch["id"],
        after={
            "batch_date": batch["batch_date"],
            "status": batch["status"],
            "budget_total": batch["budget_total"],
            "budget_used": batch["budget_used"],
            "idempotent": result["idempotent"],
            "fingerprint": batch["fingerprint"],
        },
    )
    return result


def list_batches(db: Session) -> dict:
    return {"items": orchestration_service.list_batches(db)}


def get_batch(db: Session, batch_id: int) -> dict:
    batch = orchestration_service.get_batch_or_404(db, batch_id)
    return {"batch": orchestration_service.serialize_batch(db, orchestration_service.refresh_batch_status(db, batch))}


def submit(db: Session, batch_id: int, request: Request, user: User) -> dict:
    result = orchestration_service.submit_batch(db, user, batch_id)
    audit_logger.record(
        request,
        "orchestration.submitted",
        "orchestration_batch",
        batch_id,
        after={
            "status": result["batch"]["status"],
            "dispatched": result["dispatched"],
            "blocked": result["blocked"],
        },
    )
    return result
