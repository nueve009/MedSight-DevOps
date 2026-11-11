"""
The review console's API. Teammates check rows of the reviewed dataset, compare
them with their evidence and FDA candidates, and approve them. An approval lands
in Supabase, and the deployed backend serves it within KB_REFRESH_S: no deploy.

    GET    /admin/kb/meta                      fields and checklists for the page
    GET    /admin/kb/queue?table=&q=           pending rows you didn't enter or last edit
    GET    /admin/kb/mine                      rows you entered or last edited
    GET    /admin/kb/{table}/{key}             a row, its evidence, spoken preview and history
    POST   /admin/kb/{table}                   add a row (pending; you are entered_by)
    PATCH  /admin/kb/{table}/{key}             edit (back to pending; you are last_edited_by)
    DELETE /admin/kb/{table}/{key}             only the person who entered it, only if never approved
    POST   /admin/kb/{table}/{key}/approve     every checklist item ticked, and all the rules pass
    POST   /admin/kb/{table}/{key}/request-changes
    POST   /admin/kb/{table}/{key}/evidence    a JPEG or PNG, 5 MB at most
    GET    /admin/kb/products/{key}/fda-candidates
    POST   /admin/kb/products/{key}/fda-pick   copy one candidate's FDA values in, as an edit
    GET    /admin/review/stats

Off unless ADMIN_ENABLED is on. Every route needs a reviewer (app/review_auth.py).
Nothing here can approve what the database refuses (migrations/0006).
"""
import logging
import uuid
from datetime import date, timedelta
from typing import Annotated, Callable, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app import config, speech
from app.kb_validate import split_list, validate_rows
from app.llm_parse import ParsedProduct
from app.review_auth import Reviewer, require_reviewer
from app.review_store import TABLES, Conflict, RowNotFound, RuleRefused, Table, Unavailable, get_storage, get_store

log = logging.getLogger(__name__)

CHECKLISTS = {
    "products": {
        "brand_matches_box": "The brand matches the box photo",
        "ingredients_strengths_match_box": "Every ingredient and strength matches the box",
        "fda_matches_box_and_portal": "The FDA number matches the box and the FDA portal",
        "description_matches_source": "The spoken description says only what the source says",
        "no_dosing": "The spoken description gives no dose or schedule",
    },
    "ingredients": {
        "description_matches_source": "The description says only what the source says",
        "no_dosing": "The description gives no dose or schedule",
    },
    "passages": {"text_matches_source": "The text matches the source word for word"},
}
EVIDENCE_KINDS = {"box_photo", "insert", "portal_screenshot"}
SIGNED_LINK_S = 300
# A draft may still be waiting for these; approving needs them all.
DRAFT_MAY_BE_EMPTY = {"aliases", "dosage_form", "classification", "fda_reg_no", "reg_expiry", "manufacturer",
                      "portal_checked_on", "spoken_description"}
NOT_YET_CHECKABLE = ("has no 'indications' passage", "is not in ingredients.csv", "is not in products.csv")
FDA_PICK_FIELDS = ("fda_reg_no", "reg_expiry", "classification", "manufacturer", "portal_checked_on")


class _ErrorRoute(APIRoute):
    """Turns the store's errors into responses, for every route on this router."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            try:
                return await handler(request)
            except RowNotFound as e:
                raise HTTPException(status_code=404, detail="Row not found.") from e
            except RuleRefused as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
            except Conflict as e:
                raise HTTPException(status_code=409, detail=str(e)) from e
            except Unavailable as e:
                raise HTTPException(status_code=503, detail=str(e)) from e

        return wrapped


router = APIRouter(prefix="/admin", route_class=_ErrorRoute)
ReviewerDep = Annotated[Reviewer, Depends(require_reviewer)]


class RowValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, str]


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checklist: dict[str, bool]
    version: str = Field(..., max_length=64, description="updated_at of the row the reviewer read")


class ChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(..., max_length=2000)


class FdaPick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: int


def _table(name: str) -> Table:
    table = TABLES.get(name)
    if table is None:
        raise HTTPException(status_code=404, detail="Unknown table.")
    return table


def _same(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def _own_row(row: dict, actor: str) -> bool:
    return _same(row["entered_by"], actor) or _same(row.get("last_edited_by", ""), actor)


def _existing(store, table: Table, key: str) -> dict:
    row = store.get_row(table, key)
    if row is None:
        raise HTTPException(status_code=404, detail="Row not found.")
    return row


def _labelled(table: Table, row: dict, context: Optional[dict] = None) -> tuple[list, list, list, str]:
    """Rows as validate_rows wants them, and the label its errors about this row start with."""
    context = context or {}
    where = f"{table.name}:{row[table.key]}"
    products, ingredients, passages = [], [], []
    if table.api_name == "products":   # a product's rules look at its ingredients and passages
        ingredients = [(f"context:{r['generic_name']}", r) for r in context.get("ingredients", [])]
        passages = [(f"context:{r['passage_id']}", r) for r in context.get("passages", [])]
    elif table.api_name == "passages" and context.get("product"):
        products = [("context", context["product"])]
    bucket = {"products": products, "ingredients": ingredients, "passages": passages}[table.api_name]
    bucket.append((where, {f: row.get(f, "") for f in table.fields}))
    return products, ingredients, passages, where


def draft_errors(table: Table, row: dict) -> list[str]:
    """What's wrong with a draft, leaving out what simply hasn't been filled in or added yet."""
    products, ingredients, passages, where = _labelled(table, row)
    prefix = f"{where}: "
    v = validate_rows(products, ingredients, passages, allow_empty=True, for_review=True)
    errors = [e[len(prefix):] for e in v.errors if e.startswith(prefix)]
    waiting = tuple(f"'{field}' is empty" for field in DRAFT_MAY_BE_EMPTY)
    return [e for e in errors if not e.endswith(NOT_YET_CHECKABLE) and not e.startswith(waiting)]


def approval_errors(table: Table, row: dict, actor: str, context: dict) -> list[str]:
    """Every dataset rule, on the row exactly as it would be once approved."""
    checked = {**row, "checked_by": actor}
    if table.api_name == "products":
        checked["description_checked_by"] = actor
    products, ingredients, passages, where = _labelled(table, checked, context)
    prefix = f"{where}: "
    v = validate_rows(products, ingredients, passages, allow_empty=True)
    return [e[len(prefix):] for e in v.errors if e.startswith(prefix)]


def spoken_preview(table: Table, row: dict) -> Optional[str]:
    """Exactly what a user would hear for this row once it's approved. Passages aren't read aloud."""
    description = row.get("spoken_description", "")
    if not description:
        return None
    if table.api_name == "products":
        product = ParsedProduct(row["brand_name"], tuple(split_list(row["generic_names"])),
                                tuple(split_list(row["strengths"])))
        return speech.answer(product, speech.REVIEWED, description, rx=row["classification"] == "Rx")
    if table.api_name == "ingredients":
        return speech.answer(ParsedProduct(None, (row["generic_name"],), ()), speech.INGREDIENT, description)
    return None


def candidate_flags(candidates: list[dict], today: Optional[date] = None) -> list[dict]:
    """Each candidate with the warnings a reviewer must see. Never picks one."""
    today = today or date.today()
    soon = (today + timedelta(days=90)).isoformat()

    def same_product(c: dict) -> tuple[str, str]:
        return "".join(c["strength"].lower().split()), " ".join(c["dosage_form"].lower().split())

    classes: dict[tuple[str, str], set[str]] = {}
    for c in candidates:
        if c["strength"]:
            classes.setdefault(same_product(c), set()).add(c["classification"])

    out = []
    for c in candidates:
        flags = []
        if c["strength"] and {"OTC", "Rx"} <= classes[same_product(c)]:
            flags.append("otc_and_rx_same_product")
        if c["export_only"]:
            flags.append("export_only")
        expiry = c["reg_expiry"] or ""
        if expiry and expiry < today.isoformat():
            flags.append("expired")
        elif expiry and expiry <= soon:
            flags.append("expires_within_90_days")
        if c["fda_reg_no"].upper().startswith("DRHR") and c["classification"] != "HR":
            flags.append("drhr_mismatch")
        if not c["strength"]:
            flags.append("unsplit_strength")
        out.append({**c, "flags": flags})
    return out


def _clean_values(table: Table, values: dict[str, str], allowed: tuple[str, ...]) -> dict[str, str]:
    refused = sorted(set(values) - set(allowed))
    if refused:
        raise HTTPException(status_code=422, detail=f"These fields can't be set here: {', '.join(refused)}")
    return {k: v.strip() for k, v in values.items()}


def _refuse_draft(table: Table, row: dict) -> None:
    errors = draft_errors(table, row)
    if errors:
        raise HTTPException(status_code=422, detail=errors)


@router.get("/kb/meta")
def meta(reviewer: ReviewerDep) -> dict:
    return {"reviewer": reviewer.name,
            "tables": {name: {"key": t.key, "fields": list(t.editable),
                              "checklist": [{"key": k, "label": v} for k, v in CHECKLISTS[name].items()]}
                       for name, t in TABLES.items()}}


@router.get("/kb/queue")
def queue(request: Request, reviewer: ReviewerDep, table: Optional[str] = None,
          q: str = Query("", max_length=100)) -> list[dict]:
    return get_store(request).queue(_table(table) if table else None, q.strip(), reviewer.name)


@router.get("/kb/mine")
def mine(request: Request, reviewer: ReviewerDep) -> list[dict]:
    return get_store(request).mine(reviewer.name)


@router.get("/review/stats")
def stats(request: Request, reviewer: ReviewerDep) -> dict:
    return get_store(request).stats()


@router.post("/kb/{table_name}", status_code=201)
def add_row(table_name: str, body: RowValues, request: Request,
            reviewer: ReviewerDep) -> dict:
    table = _table(table_name)
    values = _clean_values(table, body.values, (table.key, *table.editable))
    key = values.get(table.key, "")
    if not key or "/" in key:
        raise HTTPException(status_code=422, detail=f"'{table.key}' is required and can't contain a slash.")
    row = {**dict.fromkeys(table.fields, ""), **({"pnf_listed": "no"} if table.api_name == "ingredients" else {}),
           **values, "entered_by": reviewer.name}
    _refuse_draft(table, row)
    get_store(request).add_row(table, row, reviewer.name)
    log.info("review add", extra={"fields": {"table": table.name, "key": key, "reviewer": reviewer.name}})
    return {"key": key}


@router.get("/kb/products/{key}/fda-candidates")
def fda_candidates(key: str, request: Request, reviewer: ReviewerDep) -> list[dict]:
    store = get_store(request)
    row = _existing(store, TABLES["products"], key)
    return candidate_flags(store.fda_candidates(row["brand_name"]))


@router.post("/kb/products/{key}/fda-pick")
def fda_pick(key: str, body: FdaPick, request: Request, reviewer: ReviewerDep) -> dict:
    store = get_store(request)
    table = TABLES["products"]
    row = _existing(store, table, key)
    candidate = store.fda_candidate(body.candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    if not (_same(candidate["brand_query"], row["brand_name"]) or _same(candidate["brand_name"], row["brand_name"])):
        raise HTTPException(status_code=422, detail="That candidate is for a different brand.")
    if candidate["export_only"]:
        raise HTTPException(status_code=422, detail="That registration is for export only, so it can't be used.")
    changes = {f: candidate[f] or "" for f in FDA_PICK_FIELDS}
    _refuse_draft(table, {**row, **changes})
    store.update_row(table, key, changes, reviewer.name)
    log.info("review fda pick", extra={"fields": {"key": key, "reviewer": reviewer.name}})
    return {"key": key, "copied": changes}


@router.get("/kb/{table_name}/{key}")
def read_row(table_name: str, key: str, request: Request, reviewer: ReviewerDep) -> dict:
    table = _table(table_name)
    store = get_store(request)
    row = _existing(store, table, key)
    storage = get_storage(request) if config.SUPABASE_URL else None
    evidence = [{**e, "url": storage.signed_url(reviewer.token, e["storage_path"], SIGNED_LINK_S) if storage else None}
                for e in store.evidence(table, key)]
    return {"table": table.api_name, "key": key, "row": row, "version": row["updated_at"],
            "own_row": _own_row(row, reviewer.name),
            "checklist": [{"key": k, "label": v} for k, v in CHECKLISTS[table.api_name].items()],
            "spoken_preview": spoken_preview(table, row), "evidence": evidence, "history": store.history(table, key)}


@router.patch("/kb/{table_name}/{key}")
def edit_row(table_name: str, key: str, body: RowValues, request: Request,
             reviewer: ReviewerDep) -> dict:
    table = _table(table_name)
    store = get_store(request)
    row = _existing(store, table, key)
    changes = _clean_values(table, body.values, table.editable)
    if not changes:
        raise HTTPException(status_code=422, detail="Nothing to change.")
    _refuse_draft(table, {**row, **changes})
    store.update_row(table, key, changes, reviewer.name)
    log.info("review edit", extra={"fields": {"table": table.name, "key": key, "reviewer": reviewer.name}})
    return {"key": key}


@router.delete("/kb/{table_name}/{key}")
def delete_row(table_name: str, key: str, request: Request, reviewer: ReviewerDep) -> dict:
    table = _table(table_name)
    get_store(request).delete_row(table, key, reviewer.name)
    log.info("review delete", extra={"fields": {"table": table.name, "key": key, "reviewer": reviewer.name}})
    return {"deleted": key}


@router.post("/kb/{table_name}/{key}/approve")
def approve(table_name: str, key: str, body: Approval, request: Request,
            reviewer: ReviewerDep) -> dict:
    table = _table(table_name)
    store = get_store(request)
    row = _existing(store, table, key)
    if _own_row(row, reviewer.name):
        raise HTTPException(status_code=403,
                            detail="You entered or last edited this row, so someone else must check it.")
    required = CHECKLISTS[table.api_name]
    missing = [item for item in required if body.checklist.get(item) is not True]
    if missing:
        raise HTTPException(status_code=422, detail=f"Not ticked: {', '.join(missing)}")
    errors = approval_errors(table, row, reviewer.name, store.approval_context(table, key))
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    store.approve(table, key, reviewer.name, {item: True for item in required}, body.version)
    log.info("review approve", extra={"fields": {"table": table.name, "key": key, "reviewer": reviewer.name}})
    return {"approved": key}


@router.post("/kb/{table_name}/{key}/request-changes")
def request_changes(table_name: str, key: str, body: ChangeRequest, request: Request,
                    reviewer: ReviewerDep) -> dict:
    table = _table(table_name)
    note = body.note.strip()
    if not note:
        raise HTTPException(status_code=422, detail="Say what needs changing.")
    get_store(request).request_changes(table, key, reviewer.name, note)
    log.info("review changes requested", extra={"fields": {"table": table.name, "key": key, "reviewer": reviewer.name}})
    return {"key": key}


def image_type(data: bytes) -> Optional[tuple[str, str]]:
    """(extension, content type) from the file's own first bytes; what the upload claims isn't trusted."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    return None


@router.post("/kb/{table_name}/{key}/evidence", status_code=201)
def add_evidence(table_name: str, key: str, request: Request, kind: Annotated[str, Form()],
                 file: Annotated[UploadFile, File()], reviewer: ReviewerDep) -> dict:
    table = _table(table_name)
    if kind not in EVIDENCE_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {sorted(EVIDENCE_KINDS)}.")
    store = get_store(request)
    _existing(store, table, key)
    data = file.file.read(config.MAX_EVIDENCE_BYTES + 1)
    if len(data) > config.MAX_EVIDENCE_BYTES:
        raise HTTPException(status_code=413, detail="The file is over 5 MB.")
    detected = image_type(data)
    if detected is None:
        raise HTTPException(status_code=415, detail="Only JPEG and PNG images can be uploaded.")
    extension, content_type = detected
    path = f"{table.api_name}/{uuid.uuid4().hex}.{extension}"   # a random name, so nothing from the upload is used
    get_storage(request).upload(reviewer.token, path, data, content_type)
    store.add_evidence(table, key, kind, path, reviewer.name)
    log.info("review evidence", extra={"fields": {"table": table.name, "key": key, "reviewer": reviewer.name}})
    return {"storage_path": path}
