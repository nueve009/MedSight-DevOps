"""Draft FDA registration values for the team's medicine list from the FDA portal.

Usage (from backend/):
    python -m scripts.fda_lookup
    python -m scripts.fda_lookup --brands "Biogesic,Neozep Forte"
    python -m scripts.fda_lookup --limit 5 --refresh

The team's list is every brand in kb/staging/brands.txt plus every brand_name in
kb/staging/tfd_products_draft.csv. A brand outside that list is refused.

Writes to kb/staging/ (gitignored). Each run replaces the two output files, and
they cover only the brands of that run:
    fda_cache/<brand>.json   the portal's raw reply for one brand
    fda_lookup_draft.csv     one row per drug registration the portal returned
    fda_not_found.txt        brands the portal returned no drug registration for

Limits (team rule in CLAUDE.md, Mon, 2026-11-10):
- only the team's medicine list, never a crawl of the portal
- at least MIN_INTERVAL_S seconds between requests, with a clear User-Agent
- a brand already in fda_cache/ is not looked up again unless --refresh is given
- run locally by the team, never from the deployed backend or at scan time
- the run stops on HTTP 401, 403, 419 or 429, on a redirect, and on anything
  that isn't the expected JSON (such as a login or CAPTCHA page). Never work
  around one of those: report it. Do the same if the portal adds terms that
  forbid this, which the script can't detect.

Every value is a draft. A brand usually has several registrations, and all of
them are written so the reviewer picks the right one; this script never picks.
A reviewer must confirm each value on the portal (screenshot) before approving.
Nothing here writes to tfd_products_draft.csv, kb/*.csv or Supabase.

Only the portal's "Drug CPR" results (the `cdrr` key) are used.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests

SEARCH_URL = "https://verification.fda.gov.ph/api/search"
USER_AGENT = "MedSight-dataset-lookup/1.0 (student assistive project; local, rate-limited)"
MIN_INTERVAL_S = 5
TIMEOUT_S = 60
DRUG_KEY = "cdrr"  # the portal's "Drug CPR" category
STOP_STATUSES = {401, 403, 419, 429}

BACKEND_DIR = Path(__file__).resolve().parent.parent
STAGING_DIR = BACKEND_DIR / "kb" / "staging"

DRAFT_FIELDS = ["brand_query", "brand_name", "generic_name", "strength", "dosage_form", "fda_reg_no", "reg_expiry",
                "classification", "classification_raw", "manufacturer", "portal_checked_on", "match_count", "note"]

NUM = r"\d+(?:[.,]\d+)?"
UNIT = r"(?:mg|mcg|µg|g|IU|mL|L|%)"
# "500 mg Tablet", "120 mg / 5 mL Suspension (Strawberry Flavor)", "10 mg/2 mg/500 mg Tablet"
STRENGTH_FORM_RE = re.compile(rf"^({NUM}\s*{UNIT}(?:\s*/\s*(?:{NUM}\s*)?{UNIT})*)\s+(\S.*)$", re.IGNORECASE)
AMOUNT_RE = re.compile(rf"{NUM}\s*{UNIT}(?![A-Za-z])", re.IGNORECASE)
DATE_FORMATS = ("%d %B %Y", "%d %b %Y", "%Y-%m-%d", "%B %d, %Y")


class StopRun(Exception):
    """The portal refused us or changed. The whole run stops; never work around it."""


class LookupFailed(Exception):
    """One brand couldn't be looked up (network or server error). Not the same as 'not found'."""


# ---------------------------------------------------------------- parsing ---

def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def text_of(value: object) -> str:
    return str(value).strip() if value is not None else ""


def parse_date(raw: str) -> str:
    """'28 October 2030' -> '2030-10-28'. Empty if the format isn't recognised."""
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def map_classification(raw: str) -> str:
    """OTC, Rx or HR when the portal's wording is clear, otherwise empty."""
    text = raw.lower()
    if re.search(r"\bnon[- ]?(?:rx|prescription)\b", text):
        return ""
    hits = set()
    if re.search(r"\botc\b|over[- ]the[- ]counter", text):
        hits.add("OTC")
    if re.search(r"\brx\b|prescription", text):
        hits.add("Rx")
    if re.search(r"\bhr\b|household", text):
        hits.add("HR")
    return hits.pop() if len(hits) == 1 else ""


def split_strength_form(raw: str) -> tuple[str, str]:
    """'120 mg / 5 mL Suspension' -> ('120 mg/5 mL', 'Suspension'). Both empty if unsure."""
    match = STRENGTH_FORM_RE.match(raw.strip())
    if match is None or AMOUNT_RE.search(match.group(2)):  # a second amount means the split isn't safe
        return "", ""
    return re.sub(r"\s*/\s*", "/", match.group(1)), match.group(2).strip()


def drug_records(response: object) -> list[dict]:
    """The Drug CPR records of a portal reply. The portal's own page accepts a list or an object."""
    if not isinstance(response, dict) or DRUG_KEY not in response:
        raise StopRun(f"the portal's reply has no '{DRUG_KEY}' results; its format may have changed")
    records = response[DRUG_KEY]
    if isinstance(records, dict):
        records = list(records.values())
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise StopRun(f"the portal's '{DRUG_KEY}' results aren't a list of records; its format may have changed")
    return records


def build_rows(query: str, payload: dict) -> list[dict]:
    """One draft row per registration. Anything unclear stays empty and is explained in `note`."""
    records = drug_records(payload["response"])
    checked_on = payload["retrieved_on"]
    rows = []
    for record in records:
        notes = []
        brand = text_of(record.get("brand_name"))
        if slug(query) not in slug(brand):
            notes.append(f"portal brand '{brand}' doesn't contain '{query}'")

        combined = text_of(record.get("dosage_strength_form"))
        strength, form = split_strength_form(combined)
        if not strength:
            notes.append(f"couldn't split strength and form: '{combined}'")

        expiry_raw = text_of(record.get("expiry_date"))
        expiry = parse_date(expiry_raw)
        if not expiry:
            notes.append(f"couldn't read expiry date: '{expiry_raw}'")
        elif expiry < checked_on:
            notes.append(f"registration expired on {expiry}")

        reg_no = text_of(record.get("registration_number"))
        class_raw = text_of(record.get("classification"))
        classification = map_classification(class_raw)
        if not classification:
            notes.append("classification unclear, see classification_raw")
        elif reg_no.upper().startswith("DRHR") and classification != "HR":
            notes.append(f"DRHR registration number, but the portal classifies it as {classification}")

        rows.append({
            "brand_query": query, "brand_name": brand, "generic_name": text_of(record.get("generic_name")),
            "strength": strength, "dosage_form": form, "fda_reg_no": reg_no, "reg_expiry": expiry,
            "classification": classification, "classification_raw": class_raw,
            "manufacturer": text_of(record.get("manufacturer")), "portal_checked_on": checked_on,
            "match_count": len(records), "note": "; ".join(notes),
        })
    return rows


# ---------------------------------------------------------------- fetching ---

class Portal:
    def __init__(self) -> None:
        self._last: float | None = None

    def _wait(self) -> None:
        if self._last is not None:
            remaining = MIN_INTERVAL_S - (time.monotonic() - self._last)
            if remaining > 0:
                time.sleep(remaining)

    def search(self, query: str) -> dict:
        self._wait()
        try:
            resp = requests.get(
                SEARCH_URL, params={"q": query}, timeout=TIMEOUT_S, allow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        except requests.RequestException as exc:
            raise LookupFailed(type(exc).__name__) from exc
        finally:
            self._last = time.monotonic()

        if resp.status_code in STOP_STATUSES:
            retry = resp.headers.get("Retry-After")
            detail = f" (Retry-After: {retry})" if retry else ""
            raise StopRun(f"the portal answered HTTP {resp.status_code}{detail}")
        if 300 <= resp.status_code < 400:
            raise StopRun(f"the portal redirected us (HTTP {resp.status_code}), possibly to a login page")
        if resp.status_code != 200:
            raise LookupFailed(f"HTTP {resp.status_code}")
        if "json" not in resp.headers.get("Content-Type", "").lower():
            raise StopRun("the portal didn't answer with JSON; it may be showing a login or CAPTCHA page")
        try:
            data = resp.json()
        except ValueError as exc:
            raise StopRun("the portal's reply isn't valid JSON") from exc
        drug_records(data)  # never cache a reply we couldn't use
        return data


def read_cache(path: Path) -> dict | None:
    """A cached lookup, or None if there is none or it can't be used."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        drug_records(payload["response"])
        date.fromisoformat(payload["retrieved_on"])
    except (ValueError, KeyError, TypeError, StopRun):
        print(f"warn cached file {path.name} is unusable; looking it up again")
        return None
    return payload


def lookup(query: str, portal: Portal, cache_dir: Path, refresh: bool = False) -> tuple[dict, bool]:
    """(payload, came from the cache). The date stored is the day the portal was actually asked."""
    path = cache_dir / f"{slug(query)}.json"
    if not refresh:
        cached = read_cache(path)
        if cached is not None:
            return cached, True
    payload = {"query": query, "retrieved_on": date.today().isoformat(), "response": portal.search(query)}
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload, False


# ---------------------------------------------------------------- brands ---

def team_brands(staging_dir: Path) -> list[str]:
    """The team's medicine list: brands.txt, then the scraper's drafts, without repeats."""
    names: list[str] = []
    brands_txt = staging_dir / "brands.txt"
    if brands_txt.exists():
        for line in brands_txt.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith(("http://", "https://")):
                names.append(line)
    drafts = staging_dir / "tfd_products_draft.csv"
    if drafts.exists():
        with drafts.open(newline="", encoding="utf-8-sig") as f:
            names += [row["brand_name"].strip() for row in csv.DictReader(f) if row.get("brand_name", "").strip()]
    unique: dict[str, str] = {}
    for name in names:
        unique.setdefault(slug(name), name)
    return list(unique.values())


def choose_brands(team: list[str], wanted: list[str] | None, limit: int | None) -> list[str]:
    """Restrict to --brands and --limit. Raises ValueError for a brand outside the team's list."""
    chosen = team
    if wanted is not None:
        by_slug = {slug(name): name for name in team}
        outside = [name for name in wanted if slug(name) not in by_slug]
        if outside:
            raise ValueError(f"not on the team's medicine list: {', '.join(outside)}")
        chosen = list(dict.fromkeys(by_slug[slug(name)] for name in wanted))
    return chosen[:limit] if limit is not None else chosen


# ---------------------------------------------------------------- main ---

def write_outputs(out_dir: Path, rows: list[dict], not_found: list[str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "fda_lookup_draft.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DRAFT_FIELDS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / "fda_not_found.txt").write_text("".join(f"{name}\n" for name in not_found), encoding="utf-8")


def run(brands: list[str], cache_dir: Path, out_dir: Path, refresh: bool = False) -> int:
    """Look up every brand. 0 = all done, 1 = some brands failed, 2 = the portal stopped the run."""
    portal = Portal()
    rows: list[dict] = []
    not_found: list[str] = []
    failed: list[str] = []
    stopped = ""
    done = 0
    for brand in brands:
        try:
            payload, cached = lookup(brand, portal, cache_dir, refresh)
        except LookupFailed as exc:  # report and move on; a failure is never "not found"
            failed.append(f"{brand}: {exc}")
            print(f"FAIL {brand}: {exc}")
            continue
        except StopRun as exc:
            stopped = str(exc)
            break
        done += 1
        found = build_rows(brand, payload)
        rows += found
        if not found:
            not_found.append(brand)
        print(f"{'cache' if cached else 'ok   '} {brand}: {len(found)} registration(s)")

    write_outputs(out_dir, rows, not_found)
    print(f"\nBrands done: {done} of {len(brands)}")
    print(f"Rows written: {len(rows)}")
    print(f"Not found: {len(not_found)}")
    print(f"Files are in {out_dir}. Every value is a draft: a reviewer confirms it on the portal before approving.")
    if failed:
        print("\nFailed (not cached; run again):")
        for line in failed:
            print(f"  {line}")
    if stopped:
        print(f"\nSTOPPED: {stopped}.\nThe output files are incomplete. Don't work around this; tell the team.")
        return 2
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Draft FDA registration values for the team's medicine list.")
    ap.add_argument("--brands", help='comma-separated brands from the team list, e.g. "Biogesic,Tempra"')
    ap.add_argument("--limit", type=int, help="look up only the first N brands")
    ap.add_argument("--refresh", action="store_true", help="ask the portal again even for cached brands")
    ap.add_argument("--staging-dir", type=Path, default=STAGING_DIR)
    args = ap.parse_args()
    if args.limit is not None and args.limit < 1:
        ap.error("--limit must be at least 1")

    team = team_brands(args.staging_dir)
    if not team:
        ap.error(f"no brands found in {args.staging_dir} (brands.txt or tfd_products_draft.csv)")
    wanted = [b.strip() for b in args.brands.split(",") if b.strip()] if args.brands else None
    try:
        brands = choose_brands(team, wanted, args.limit)
    except ValueError as exc:
        ap.error(str(exc))
    return run(brands, args.staging_dir / "fda_cache", args.staging_dir, args.refresh)


if __name__ == "__main__":
    sys.exit(main())
