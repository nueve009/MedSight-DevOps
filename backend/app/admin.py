"""
Admin routes for the answer cache (plan M7): the team reviews web answers,
fixes or removes a bad one, and reads who changed what.

    GET    /admin/answers?status=&limit=   list cached answers (any status, expired too)
    GET    /admin/answers/{cache_key}      one answer
    PATCH  /admin/answers/{cache_key}      {"status": ..., "description": ...}: approve
                                           ("active"), reject ("rejected") or fix the text
    DELETE /admin/answers/{cache_key}      remove it; the next scan answers afresh
    GET    /admin/audit?limit=             every admin write, newest first

Rules (plan section 7, #7):
- Off unless ADMIN_ENABLED is on: every /admin path answers 404 before any
  other check (api.RouteGateMiddleware), so production doesn't expose them by
  accident.
- X-Admin-Key, one key per person (ADMIN_KEYS "name=key,..."), compared in
  constant time. The app's keys never work here, even if one is also listed.
- Every write goes to audit_log with the admin's name, in the same
  transaction as the change.
- An admin can change only status and description. The tier can't be set,
  so nothing can be made "reviewed" here: reviewed answers come only from kb/.
- An edited description passes the same gates as a model's: no dosing, 300
  characters at most.
"""
import logging
import secrets
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app import config
from app.ratelimit import parse_pairs
from app.store import StoreUnavailable
from app.utils import contains_dosing

log = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 300


def _app_keys() -> set[str]:
    return set(parse_pairs(config.MEDSIGHT_API_KEYS).values()) | ({config.MEDSIGHT_API_KEY} - {""})


def _admin_keys() -> dict[str, str]:
    app_keys = _app_keys()
    keys = parse_pairs(config.ADMIN_KEYS)
    shared = [name for name, key in keys.items() if key in app_keys]
    if shared:
        log.warning("admin keys equal to an app key are ignored", extra={"fields": {"names": shared}})
    return {name: key for name, key in keys.items() if name not in shared}


def require_admin(request: Request, x_admin_key: str = Header("")) -> str:
    if not config.ADMIN_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    keys = _admin_keys()
    if not keys:
        raise HTTPException(status_code=503, detail="No admin keys are configured.")
    matched = None
    for name, key in keys.items():   # every key is compared, so timing doesn't reveal which matched
        if secrets.compare_digest(x_admin_key.encode(), key.encode()):
            matched = name
    if matched is None:
        log.warning("admin key refused")
        raise HTTPException(status_code=401, detail="Invalid or missing admin key.")
    return matched


router = APIRouter(prefix="/admin")


class AnswerChange(BaseModel):
    # extra="forbid": a body that tries to set "tier" (or anything else) is a 422.
    model_config = ConfigDict(extra="forbid")
    status: Optional[Literal["active", "pending_review", "rejected"]] = None
    description: Optional[str] = Field(None, max_length=1000)


def _store(request: Request):
    return request.app.state.cache


def _unavailable(e: StoreUnavailable) -> HTTPException:
    # Admin writes never fail open: the admin is told, and nothing changed.
    log.error("admin store error", extra={"fields": {"error_class": type(e).__name__}})
    return HTTPException(status_code=503, detail="The database is unavailable; nothing was changed.")


@router.get("/answers")
def list_answers(request: Request, admin: str = Depends(require_admin),
                 status: Optional[Literal["active", "pending_review", "rejected"]] = None,
                 limit: int = Query(100, ge=1, le=500)) -> dict:
    try:
        return {"answers": _store(request).list_answers(status=status, limit=limit)}
    except StoreUnavailable as e:
        raise _unavailable(e) from e


@router.get("/answers/{cache_key}")
def read_answer(cache_key: str, request: Request, admin: str = Depends(require_admin)) -> dict:
    try:
        answer = _store(request).read_answer(cache_key)
    except StoreUnavailable as e:
        raise _unavailable(e) from e
    if answer is None:
        raise HTTPException(status_code=404, detail="No such answer.")
    return answer


@router.patch("/answers/{cache_key}")
def update_answer(cache_key: str, change: AnswerChange, request: Request,
                  admin: str = Depends(require_admin)) -> dict:
    description = None
    if change.description is not None:
        description = " ".join(change.description.split())
        if not description or len(description) > MAX_DESCRIPTION_CHARS or contains_dosing(description):
            raise HTTPException(status_code=422, detail="A description must be 1 to 300 characters with no "
                                                        "doses, amounts or schedules.")
    if change.status is None and description is None:
        raise HTTPException(status_code=422, detail="Nothing to change: give a status or a description.")
    try:
        result = _store(request).admin_update_answer(cache_key, admin, status=change.status,
                                                     description=description)
    except StoreUnavailable as e:
        raise _unavailable(e) from e
    if result is None:
        raise HTTPException(status_code=404, detail="No such answer.")
    before, after = result
    log.info("admin update", extra={"fields": {"admin": admin, "cache_key": cache_key,
                                               "status": [before["status"], after["status"]],
                                               "description_changed": before["description"] != after["description"]}})
    return {"before": before, "after": after}


@router.delete("/answers/{cache_key}")
def delete_answer(cache_key: str, request: Request, admin: str = Depends(require_admin)) -> dict:
    try:
        before = _store(request).admin_delete_answer(cache_key, admin)
    except StoreUnavailable as e:
        raise _unavailable(e) from e
    if before is None:
        raise HTTPException(status_code=404, detail="No such answer.")
    log.info("admin delete", extra={"fields": {"admin": admin, "cache_key": cache_key}})
    return {"deleted": before}


@router.get("/audit")
def audit(request: Request, admin: str = Depends(require_admin), limit: int = Query(100, ge=1, le=500)) -> dict:
    try:
        return {"audit": _store(request).list_audit(limit=limit)}
    except StoreUnavailable as e:
        raise _unavailable(e) from e
