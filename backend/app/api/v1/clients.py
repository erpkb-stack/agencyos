from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.enums import ContractStatus
from app.models.identity import User
from app.schemas.crm import ClientCreate, ClientUpdate
from app.security.deps import get_current_user, require_admin, require_writer
from app.services import client_service

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("")
def list_clients(
    q: str | None = Query(None, max_length=200),
    contract_status: ContractStatus | None = None,
    sort: Literal["company_name", "created_at", "monthly_fee", "start_date"] = "company_name",
    order: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict[str, Any]:
    items, total = client_service.search(db, q=q, contract_status=contract_status, sort=sort, order=order,
                                         offset=(page - 1) * page_size, limit=page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_client(body: ClientCreate, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    c = client_service.create(db, user, body.model_dump())
    db.commit()
    return client_service.serialize(c)


@router.get("/{client_id}")
def get_client(client_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return client_service.detail(db, client_service.get(db, client_id))


@router.patch("/{client_id}")
def update_client(client_id: int, body: ClientUpdate, db: Session = Depends(get_db),
                  user: User = Depends(require_writer)) -> dict[str, Any]:
    c = client_service.update(db, user, client_service.get(db, client_id), body.model_dump(exclude_unset=True))
    db.commit()
    return client_service.serialize(c)


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(client_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> Response:
    client_service.soft_delete(db, user, client_service.get(db, client_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
