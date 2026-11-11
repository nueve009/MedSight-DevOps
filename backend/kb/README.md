# MedSight reviewed medicine dataset

This folder is the **only** source of what the app speaks as **"reviewed"**.
If a medicine isn't here, the app says it isn't in the reviewed list. It
never guesses. Every row is entered from evidence (a TheFilipinoDoctor page,
or the box or insert, plus an FDA portal lookup) and **checked by a second
teammate** before it counts.

| File | One row per | Holds |
|---|---|---|
**Where it lives (2026-11-09): Supabase.** The rows are kept in the
`kb_products`, `kb_ingredients` and `kb_passages` tables, and the backend
reads them from there. A row counts only once a second teammate **approves
it in Supabase** (see "Approving rows in Supabase" below). The CSV files here
have the same columns and are used to import drafts and to keep a reviewed
copy in git.

| `products.csv` | product: one brand, strength(s) and dosage form | brand, every ingredient and strength, OTC/Rx, FDA registration, the **spoken description** |
| `ingredients.csv` | active ingredient | other names for it (synonyms) and, optionally, a spoken description of what it's for |
| `passages.csv` | piece of source text | the exact "Indications" (or warning) text the descriptions are based on, with its page URL |
| `eval/scans.jsonl` | labeled test scan | OCR text, plus the answer it should get |
| `staging/` | raw drafts (git-ignored) | scraper output and description drafts waiting to be checked |

**Sources (team decision, 2026-10-26):** TheFilipinoDoctor
(`thefilipinodoctor.com`) is the text source, `source_type` `tfd`. MIMS isn't
used (its drug directory needs an account). The box or insert (`box`,
`insert`) is still accepted. **FDA portal values** may be drafted by the
team's FDA lookup script (within the limits in `CLAUDE.md`, decision
2026-11-10) or looked up by hand. Either way, a reviewer confirms each value
on the portal before approving.

## Workflow

1. **Collect drafts into `staging/`** (not committed). The team's scraper
   writes `tfd_products_draft.csv`, `tfd_ingredients_draft.csv` and
   `tfd_passages_draft.csv`. Brands it can't find are listed in
   `tfd_missing.txt`; find their page or use the box.
2. **Decide the scope.** Keep only the strengths and forms a user would scan
   at home: drop injections, inhalation solutions and hospital packs.
3. **Look up each product on the FDA portal**
   (https://verification.fda.gov.ph), by hand or drafted into `staging/` by
   the FDA lookup script. Search the registration number printed
   on the box ("Reg. No." / "FDA Reg. No.", e.g. `DR-XY12345`) and take a
   screenshot. Fill in `fda_reg_no`, `reg_expiry`, `classification` (OTC/Rx,
   **from the portal, not the website**) and `portal_checked_on`. If it isn't
   found, it's for a different product, or it's registered as a food
   supplement or herbal, drop the product.

   **FDA lookup script:** `python -m scripts.fda_lookup` (or `--brands
   "Biogesic,Tempra"`) searches the portal for the brands in
   `staging/brands.txt` and `staging/tfd_products_draft.csv`, one request
   every 5 seconds at most. It writes `staging/fda_lookup_draft.csv` (every
   registration the portal returned for each brand), `staging/fda_not_found.txt`
   and a cache in `staging/fda_cache/`, so a brand is only asked once
   (`--refresh` asks again). It never picks a registration and never touches
   `kb/*.csv` or Supabase. The reviewer still chooses the right row by box
   number, confirms each value on the portal and takes the screenshot. If it
   stops on a refusal, a login or a CAPTCHA, don't work around it: tell the
   team.
4. **Draft the spoken descriptions:**
   ```powershell
   python -m scripts.kb_draft_descriptions
   ```
   This writes `staging/descriptions_review.csv`: a plain-language draft per
   product and per ingredient, made by an LLM from the indications text, with
   flags (`dose`, `too long`, `new words: …`, `no source`). **Nothing is
   written to `kb/`.**
5. **Check every draft against its source text.** Fix it so it says only
   what the source says, in everyday words, with no doses. Pay most
   attention to flagged rows. Write the final sentence in
   `approved_description` and your name in `checked_by`.
6. **Put the rows in Supabase**, one batch at a time (for example the pain
   and fever medicines first). Either type them into the Table Editor
   (ingredients first, then products, then passages), or fill a draft
   folder with the three CSVs and import it:
   ```powershell
   python -m scripts.kb_build --check --kb-dir kb/staging/batch1   # optional: see every problem first
   $env:KB_ADMIN_DATABASE_URL = "<owner connection string>"        # never commit it
   python -m scripts.kb_build --push --kb-dir kb/staging/batch1
   ```
   `--push` adds new rows and updates changed ones by their ID. It never
   approves anything, and leave `checked_by` empty: the checker fills it.
   An approved row that the import changes goes back to unapproved; rows
   that didn't change stay approved. Rows that are only in Supabase are
   kept (it lists them).
7. **Second-person check and approval in Supabase.** See below. The backend
   picks up approved rows within a minute (`KB_REFRESH_S`), no redeploy.
8. **Evaluate** before every release (see "Test scans" below):
   `python -m scripts.kb_build --pull` builds `data/kb.sqlite` from the
   approved rows, then run `eval_kb`.
9. **Keep a copy in git:** `python -m scripts.kb_build --pull --csv-out kb`
   writes the approved rows into the CSVs here; open a PR with them.

## From staging to the console

Once `staging/` holds the scraper's drafts and the description sheet, the owner
connection in `KB_ADMIN_DATABASE_URL` puts them in the review queue:

```powershell
python -m scripts.fda_lookup --push --by "Ana"          # 1. FDA candidates
python -m scripts.kb_import_staging --by "Ana"          # 2. write staging/import/*.csv and print a summary; read it
python -m scripts.kb_import_staging --by "Ana" --push   # 3. add the rows to Supabase as pending
```

`--by` is recorded as `entered_by`, so that person can't approve those rows.
Then review them in the console.

- **Left empty for review:** classification, FDA number, expiry, manufacturer
  and portal date (pick them from the FDA candidates), and the checker's
  columns. `pnf_listed` isn't in the drafts, and the table's default `no`
  stands until a reviewer sets it.
- **Descriptions** come from `descriptions_review.csv`, the `approved_description`
  if someone wrote one, or else the draft. One with a dose or over 300
  characters is left empty and listed. The summary also lists the drafts the
  sheet flagged: check those first.
- **Skipped and listed:** a product whose ingredient count and strengths
  disagree, with an ingredient not in the drafts, or with no indications text,
  and its passages. Two different products with one ID are never merged.
- **Importing again is safe.** Rows that were approved, sent back or edited in
  the console are left alone, and the columns reviewers fill in are never
  written. Only untouched pending rows get the new draft text.

The import doesn't apply the scope rule in step 2 of the workflow: a reviewer
still rejects injections, inhalation solutions and hospital packs.

## Reviewing in the console

The console is a web page for checking rows, comparing them with box photos
and FDA candidates, hearing what users will hear, and approving them. It runs
on your own laptop and writes straight to Supabase. **Approvals reach the app
within about a minute (`KB_REFRESH_S`), with no deploy**, as long as the
deployed backend uses `KB_BACKEND=postgres`.

### First-time setup (once, by the project owner)

1. **Apply migration 0006** (needs `MIGRATION_DATABASE_URL`, see
   `migrations/README.md`):
   ```powershell
   python -m scripts.migrate --check
   python -m scripts.migrate
   ```
2. **Create the console's database login** in the Supabase SQL editor, with a
   long random password that never goes in git:
   ```sql
   CREATE ROLE medsight_review_app LOGIN PASSWORD '<random password>' IN ROLE medsight_review;
   ```
3. **Create the evidence bucket:** see "Two-step sign-in setting" below.
4. **Add each reviewer's account:** in Supabase, Authentication, Users, "Add
   user" with their email and a password, with Auto Confirm User ticked, and
   copy their user ID. (TOTP under Authentication, Multi-Factor matters only
   with two-step sign-in on.)
5. **List the reviewers** in the SQL editor. The name here is what gets
   recorded on every approval:
   ```sql
   INSERT INTO reviewers (user_id, name) VALUES ('<user id>', 'Ana'), ('<user id>', 'Ben');
   ```
   To remove someone: `UPDATE reviewers SET active = false WHERE name = 'Ana';`

### Each reviewer's laptop

Install the packages (`pip install -r requirements.txt` picks up the two new
ones), then add this to `backend/.env`. Get the values from the owner:

```
REVIEW_DATABASE_URL=postgresql://medsight_review_app.<project-ref>:<password>@<pooler host>:5432/postgres
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<the project's public anon key>
```

Leave out `SUPABASE_JWT_SECRET` unless the project still signs sessions with a
shared secret; the console normally uses the project's published keys. Then:

```powershell
python -m scripts.review_console
```

Open http://127.0.0.1:8100/review/ and sign in with your email and password.
Press Ctrl+C to stop it.

### Two-step sign-in setting

The authenticator code is controlled by `REVIEW_REQUIRE_MFA`. It defaults to on,
so a hosted console stays strict. `python -m scripts.review_console` turns it off
for its own process, unless you add `--require-mfa`, and says which mode it's in
when it starts. With it off, an email and password are enough. Every other check
stays: the account must be active in `reviewers`.

The evidence bucket's rules must match the setting, or uploads are refused. The
commented block in `migrations/0006_review_console.sql` requires the code, so
paste one of these in the SQL editor.

**(a) Two-step off (local use):**
```sql
DROP POLICY IF EXISTS kb_evidence_add ON storage.objects;
DROP POLICY IF EXISTS kb_evidence_read ON storage.objects;
CREATE POLICY kb_evidence_add ON storage.objects FOR INSERT TO authenticated
    WITH CHECK (bucket_id = 'kb-evidence' AND public.kb_is_active_reviewer());
CREATE POLICY kb_evidence_read ON storage.objects FOR SELECT TO authenticated
    USING (bucket_id = 'kb-evidence' AND public.kb_is_active_reviewer());
```

**(b) Two-step back on (before hosting):**
```sql
DROP POLICY IF EXISTS kb_evidence_add ON storage.objects;
DROP POLICY IF EXISTS kb_evidence_read ON storage.objects;
CREATE POLICY kb_evidence_add ON storage.objects FOR INSERT TO authenticated
    WITH CHECK (bucket_id = 'kb-evidence' AND auth.jwt()->>'aal' = 'aal2' AND public.kb_is_active_reviewer());
CREATE POLICY kb_evidence_read ON storage.objects FOR SELECT TO authenticated
    USING (bucket_id = 'kb-evidence' AND auth.jwt()->>'aal' = 'aal2' AND public.kb_is_active_reviewer());
```

**If you never ran the storage block,** run this first, then (a):
```sql
INSERT INTO storage.buckets (id, name, public) VALUES ('kb-evidence', 'kb-evidence', false)
    ON CONFLICT (id) DO NOTHING;

CREATE OR REPLACE FUNCTION public.kb_is_active_reviewer() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT EXISTS (SELECT 1 FROM reviewers WHERE user_id = auth.uid() AND active)
$$;
REVOKE ALL ON FUNCTION public.kb_is_active_reviewer() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_is_active_reviewer() TO authenticated;
```

### FDA candidates

```powershell
python -m scripts.fda_lookup                       # drafts, as in step 3 of the workflow
python -m scripts.fda_lookup --push --by "Ana"     # upload them (needs KB_ADMIN_DATABASE_URL)
```

On a product's page, the console lists the candidates for its brand with
warnings: both OTC and Rx exist, export only, expired or expiring soon, a DRHR
number not classified as a household remedy, or a strength the script couldn't
read. **Match the number printed on the box and confirm it on the portal.**
"Use these values" counts as your edit, so someone else must approve the row.

### The rules

- You can't approve a row you entered or last edited; it isn't even in your
  queue.
- Approve unlocks only when every checklist item is ticked, and the dataset
  rules must still pass.
- If a row changed while you were reading it, the approval is refused.
- Every decision is kept permanently in `kb_reviews`, with a copy of the row.

### Web answers

The "Web answers" tab lists answers from the allowed sites. Users hear them
with a warning while they wait here, so you can leave one for later.

- **Reject** stops it being spoken. The next scan searches again, so it can
  come back if the page still says the same thing.
- **Send to kb** creates a pending product, its indications passage and any
  missing ingredients, entered under your name. Someone else must approve them.
  A web page alone can't back a reviewed medicine: you need the box, the insert
  or a TheFilipinoDoctor page, and the text must be word for word from it. FDA
  values come from the portal. The brand may have no candidates, since the
  lookup script only covers the team's list. The answer stays spoken until the
  product is approved, then the reviewed answer takes over by itself.

### Closing the Table Editor route

Approving in the Table Editor (below) keeps working as a fallback. Once the
console is in regular use, switch it off in the SQL editor:

```sql
UPDATE kb_settings SET allow_table_editor_approval = false;
```

## Approving rows in Supabase

In the Supabase dashboard, open **Table Editor**, then `kb_ingredients`,
`kb_products` or `kb_passages`.

1. Check the row against the TheFilipinoDoctor page, the box photo and the
   FDA portal screenshot (and, for a product, the spoken description against
   the source text).
2. Type **your name** in `checked_by` (for a product, also in
   `description_checked_by`) and save.
3. Set `approved` to **true** and save. That's the approval.

The database refuses the approval, with the reason, when:
- `checked_by` is empty or the same name as `entered_by`
- the row was edited in the same save (save the edit first; the approval is
  its own step)
- the row breaks a rule: a spoken description over 300 characters or with a
  dose or schedule, a count of strengths that doesn't match the ingredients,
  no classification, an expired registration, an ingredient that isn't in
  `kb_ingredients`, a `tfd` passage without its page URL, ...

**Changing an approved row un-approves it** and clears `checked_by`, so every
edit is checked again. `review_note` is for the checker's comments and can
be changed without un-approving. A product is spoken as reviewed only when
the product, every one of its ingredients and at least one `indications`
passage are approved. Every change is kept in `kb_review_log`.

Supabase can't tell which teammate clicked (the Table Editor works as the
database owner), so `checked_by` is the name you type: only type your own.

## `products.csv`

| Column | What to enter | Example |
|---|---|---|
| `product_id` | lowercase-with-dashes: brand-strength-form | `biogesic-500-tab` |
| `brand_name` | brand as on the box | `Biogesic` |
| `aliases` | other spellings seen on the box or foil, separated by `\|` (optional) | `BIOGESIC` |
| `generic_names` | **every** active ingredient, separated by `\|` | `Phenylephrine HCl\|Chlorphenamine maleate\|Paracetamol` |
| `strengths` | one strength per ingredient, **same order**, separated by `\|` | `10 mg\|2 mg\|500 mg` |
| `dosage_form` | as on the portal | `Tablet` |
| `classification` | `OTC`, `Rx` (prescription) or `HR` (household remedy), **from the portal** | `OTC` |
| `fda_reg_no` | exactly as on the portal and box | `DR-XY12345` |
| `reg_expiry` | registration expiry from the portal, YYYY-MM-DD | `2028-05-31` |
| `manufacturer` | from the portal (optional) | |
| `portal_checked_on` | date of your portal lookup, YYYY-MM-DD | `2026-10-27` |
| `entered_by` / `checked_by` | two different teammates | |
| `spoken_description` | the checked sentence the app reads aloud (max 300 characters, no doses) | `Used to relieve minor aches and pains, and to reduce fever.` |
| `description_checked_by` | who checked that sentence against the source | |

A product marked `Rx` is spoken with "This is a prescription medicine."

## `ingredients.csv`

| Column | What to enter |
|---|---|
| `generic_name` | the ingredient name used in `products.csv` |
| `synonyms` | other names on boxes, separated by `\|`, e.g. `Acetaminophen` for Paracetamol, `Chlorpheniramine maleate` for Chlorphenamine maleate, `Phenylephrine hydrochloride` for Phenylephrine HCl. **These matter:** a box that spells an ingredient differently only matches through them. |
| `pnf_listed` | `yes` or `no`, from the [PNF list](https://www.philhealth.gov.ph/partners/providers/pdf/PNF-EML_11022022.pdf) |
| `pnf_forms`, `pnf_page` | the forms and page in the PNF, if listed |
| `spoken_description` | optional: a checked sentence about the ingredient, starting with its name (`Paracetamol is used to …`) |
| `source_url` | the page the description came from (required with a description) |
| `entered_by` / `checked_by` | two different teammates (required with a description) |

An ingredient can be listed for its names only (description columns empty).
The **ingredient tier** ("Our team hasn't reviewed this product yet. Based on
its ingredients, …") is used for an unlisted brand or a generic only when
**every** ingredient it lists has a checked description.

## `passages.csv`

| Column | What to enter |
|---|---|
| `passage_id` | `<product_id>-ind-1`, `<product_id>-warn-1`, … |
| `product_id` | the matching ID from `products.csv` |
| `section` | `indications` (required for every product) or `warnings` |
| `text` | the source text **word for word**; wrap it in double quotes if it contains a comma |
| `source_type` | `tfd`, `box`, `insert` or `pnf` |
| `source_ref` | for `tfd`, the page URL (`https://thefilipinodoctor.com/drug/...`); for `box`/`insert`, the evidence photo |
| `entered_by` / `checked_by` | two different teammates |

Passages are the evidence for each spoken description; they aren't read
aloud. Leave out dosage instructions.

## How a scan is matched

A scan gets the reviewed answer only when the **brand (or an alias) and
every ingredient and strength** agree with one product. A slightly misread
brand is accepted only when everything else agrees. A listed brand with a
missing strength or different ingredients is never answered as reviewed: the
app asks the user to turn the box ("I found Biogesic but couldn't read the
strength"). After three tries it says what the brand is, if every approved
product of that brand has the same ingredients, description and OTC/Rx, and
asks the user to have the label checked; it never says a strength. A strength
read with confidence that isn't listed for that brand is treated as an
unlisted product, with that tier's warning.
A different brand with the same ingredients (for example two cold medicines
with identical formulas) never gets the other brand's answer.

## Test scans (`eval/scans.jsonl`)

These measure whether real scans get the right answer. One JSON object per
line:

```json
{"id": "biogesic-phone-1", "ocr_text": "BIOGESIC\nParacetamol 500 mg", "ocr_confidence": 0.91, "expected": "biogesic-500-tab"}
```

- `expected`: a `product_id`, or `not_listed` (a medicine outside the
  dataset), `multiple` (more than one medicine in view) or `rescan` (an
  unreadable picture).
- `blocks` (optional): the phone's text blocks with positions, as in the API.
- Collect real phone scans (the app's debug export), including blister foil,
  poor light, unlisted medicines and two products in view.
- `python -m scripts.kb_make_scans sorted_output/packaging` drafts lines
  from dataset photos into `eval/scans_draft.jsonl` for labeling.

Run them (this calls the real LLM, so use a key or local Ollama):

```powershell
python -m scripts.kb_build --pull     # data/kb.sqlite from the approved rows in Supabase
python -m scripts.eval_kb --out eval_report.json
```

**Release gate:** 0 wrong reviewed answers on at least 100 labeled scans,
and at least 95% of unlisted medicines not matched. The script exits with
an error if any reviewed answer is wrong. Record each run in `CHANGES.md`
(date, commit, model, `kb_version`).

## Editing tips
- **Excel or Google Sheets** both work. Save as **CSV UTF-8**, and keep the
  header row exactly as it is.
- **Commas:** a value with a comma must be in double quotes. Sheets and Excel
  do this automatically.
- **Dates:** use YYYY-MM-DD (Excel may reformat dates; check before saving).

## Example rows (FAKE: format reference only, don't copy into the CSVs)

```
products.csv
testflu-tab,Testflu,TEST FLU|TESTFLU,Fakeamol|Fakephrine,500 mg|10 mg,Tablet,OTC,DR-XY00000,2030-01-01,Test Co,2026-10-08,Ana,Ben,Used to relieve test symptoms.,Cy

ingredients.csv
Fakeamol,Fakeaminophen,no,,,Fakeamol is used to relieve test pain.,https://thefilipinodoctor.com/drug/fakemol,Ana,Ben
Fakephrine,,no,,,,,,

passages.csv
testflu-tab-ind-1,testflu-tab,indications,"<exact text from the page, in quotes>",tfd,https://thefilipinodoctor.com/drug/testflu,Ana,Ben
```
