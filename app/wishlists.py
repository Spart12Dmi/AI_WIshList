import time
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.auth import current_user
from app.catalog import product_detail
from app.database import connection
from app.schemas import SearchRequest

router = APIRouter(prefix="/api/wishlists", tags=["wishlists"])


class ListName(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"\S")


class ItemInput(BaseModel):
    product_id: str | None = Field(default=None, max_length=64)
    title: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
    target_price: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    target_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    region: str = "czechia"

    @model_validator(mode="after")
    def validate_item(self):
        SearchRequest(query="validate", region=self.region)
        if not self.product_id and len(self.title.strip()) < 2:
            raise ValueError("Enter a product name or select a search result")
        if self.target_price is not None and not self.target_currency:
            raise ValueError("Target currency is required with a target price")
        return self


def owned(db, list_id, user_id):
    row = db.execute("SELECT * FROM wishlists WHERE id=? AND user_id=?", (list_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "Wishlist not found")
    return dict(row)


@router.get("")
def lists(user=Depends(current_user)):
    with connection() as db:
        return [
            dict(row)
            for row in db.execute(
                """SELECT w.id,w.name,COUNT(i.id) item_count FROM wishlists w
            LEFT JOIN wishlist_items i ON i.wishlist_id=w.id WHERE w.user_id=? GROUP BY w.id ORDER BY w.id""",
                (user["id"],),
            )
        ]


@router.post("", status_code=201)
def create(data: ListName, user=Depends(current_user)):
    with connection() as db:
        lid = db.execute(
            "INSERT INTO wishlists(user_id,name,created_at) VALUES(?,?,?)",
            (user["id"], data.name.strip(), time.time()),
        ).lastrowid
    return {"id": lid, "name": data.name.strip(), "item_count": 0}


@router.patch("/{list_id}")
def rename(list_id: int, data: ListName, user=Depends(current_user)):
    with connection() as db:
        owned(db, list_id, user["id"])
        db.execute("UPDATE wishlists SET name=? WHERE id=?", (data.name.strip(), list_id))
    return {"id": list_id, "name": data.name.strip()}


@router.delete("/{list_id}", status_code=204)
def delete(list_id: int, user=Depends(current_user)):
    with connection() as db:
        owned(db, list_id, user["id"])
        db.execute("DELETE FROM wishlists WHERE id=?", (list_id,))


@router.get("/{list_id}")
def detail(list_id: int, user=Depends(current_user)):
    with connection() as db:
        result = owned(db, list_id, user["id"])
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM wishlist_items WHERE wishlist_id=? ORDER BY created_at DESC", (list_id,)
            )
        ]
    for row in rows:
        row["product"] = product_detail(row["product_id"], row["region"]) if row["product_id"] else None
    return {**result, "items": rows}


@router.post("/{list_id}/items", status_code=201)
def add(list_id: int, data: ItemInput, user=Depends(current_user)):
    product = product_detail(data.product_id) if data.product_id else None
    if data.product_id and not product:
        raise HTTPException(404, "Product not found")
    if product:
        data.product_id = product["id"]
    with connection() as db:
        owned(db, list_id, user["id"])
        if data.product_id:
            existing = db.execute(
                """SELECT i.id FROM wishlist_items i WHERE i.wishlist_id=? AND
                (i.product_id=? OR i.product_id IN (
                    SELECT m.product_id FROM product_members m WHERE m.identity_key=(
                        SELECT identity_key FROM product_members WHERE product_id=?)))""",
                (list_id, data.product_id, data.product_id),
            ).fetchone()
            if existing:
                return {"id": existing["id"], "already_saved": True}
        iid = db.execute(
            """INSERT INTO wishlist_items(wishlist_id,product_id,title,notes,target_price,target_currency,region,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (
                list_id,
                data.product_id,
                product["title"] if product else data.title.strip(),
                data.notes,
                str(data.target_price) if data.target_price else None,
                data.target_currency,
                data.region,
                time.time(),
            ),
        ).lastrowid
    return {"id": iid}


@router.delete("/{list_id}/items/{item_id}", status_code=204)
def remove(list_id: int, item_id: int, user=Depends(current_user)):
    with connection() as db:
        owned(db, list_id, user["id"])
        if not db.execute(
            "DELETE FROM wishlist_items WHERE id=? AND wishlist_id=?", (item_id, list_id)
        ).rowcount:
            raise HTTPException(404, "Item not found")


class ItemEdit(BaseModel):
    notes: str = Field(default="", max_length=2000)
    target_price: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    target_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def paired_target(self):
        if self.target_price is not None and not self.target_currency:
            raise ValueError("Target currency is required with a target price")
        return self


@router.patch("/{list_id}/items/{item_id}")
def edit_item(list_id: int, item_id: int, data: ItemEdit, user=Depends(current_user)):
    with connection() as db:
        owned(db, list_id, user["id"])
        changed = db.execute(
            "UPDATE wishlist_items SET notes=?,target_price=?,target_currency=? WHERE id=? AND wishlist_id=?",
            (
                data.notes,
                str(data.target_price) if data.target_price else None,
                data.target_currency if data.target_price else None,
                item_id,
                list_id,
            ),
        ).rowcount
        if not changed:
            raise HTTPException(404, "Item not found")
    return {"id": item_id}
