from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.exceptions import NotFoundError, ValidationError
from app.models.models import Client


def list_clients(
    db: Session,
    firm_id: int,
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
    is_active: Optional[bool] = None,
):
    query = db.query(Client).filter(Client.firm_id == firm_id)
    if q:
        query = query.filter(
            or_(
                Client.name.ilike(f"%{q}%"),
                Client.code.ilike(f"%{q}%"),
            )
        )
    if is_active is not None:
        query = query.filter(Client.is_active == is_active)
    total = query.count()
    items = query.order_by(Client.name).limit(limit).offset(offset).all()
    return items, total


def get_client(db: Session, client_id: int, firm_id: int | None = None) -> Client:
    query = db.query(Client).filter(Client.id == client_id)
    if firm_id is not None:
        query = query.filter(Client.firm_id == firm_id)
    client = query.first()
    if not client:
        raise NotFoundError(f"Client {client_id} not found")
    return client


def create_client(db: Session, data: dict, firm_id: int | None = None) -> Client:
    firm_id = firm_id if firm_id is not None else data.get("firm_id")
    if firm_id is None:
        raise ValidationError("An active firm is required")
    data = {**data, "firm_id": firm_id}
    if data.get("code"):
        existing = db.query(Client).filter(Client.code == data["code"]).first()
        if existing:
            raise ValidationError(f"Client code {data['code']} already exists")
    client = Client(**data)
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


def update_client(db: Session, client_id: int, data: dict, firm_id: int) -> Client:
    client = get_client(db, client_id, firm_id=firm_id)
    for key, value in data.items():
        if key != "firm_id" and value is not None:
            setattr(client, key, value)
    db.commit()
    db.refresh(client)
    return client


def soft_delete_client(db: Session, client_id: int, firm_id: int) -> Client:
    client = get_client(db, client_id, firm_id=firm_id)
    client.is_active = False
    db.commit()
    db.refresh(client)
    return client
