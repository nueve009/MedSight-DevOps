# MedSight reviewed medicine dataset

This folder is the **only** source of what the app speaks as **"reviewed"**.
If a medicine isn't here, the app says it isn't in the reviewed list. It
never guesses. Every row is entered from evidence (a TheFilipinoDoctor page,
or the box or insert, plus an FDA portal lookup) and **checked by a second
teammate** before it counts.

| File | One row per | Holds |
|---|---|---|
| `products.csv` | product: one brand, strength(s) and dosage form | brand, every ingredient and strength, OTC/Rx, FDA registration, the **spoken description** |
| `ingredients.csv` | active ingredient | other names for it (synonyms) and, optionally, a spoken description of what it's for |
| `passages.csv` | piece of source text | the exact "Indications" (or warning) text the descriptions are based on, with its page URL |
| `eval/scans.jsonl` | labeled test scan | OCR text, plus the answer it should get |
| `staging/` | raw drafts (git-ignored) | scraper output and description drafts waiting to be checked |

**Sources (team decision, 2026-10-26):** TheFilipinoDoctor
(`thefilipinodoctor.com`) is the text source, `source_type` `tfd`. MIMS isn't
used (its drug directory needs an account). The box or insert (`box`,
`insert`) is still accepted. **The FDA verification portal is always looked
up by hand; never scrape or automate it.**

## Workflow

1. **Collect drafts into `staging/`** (not committed). The team's scraper
   writes `tfd_products_draft.csv`, `tfd_ingredients_draft.csv` and
   `tfd_passages_draft.csv`. Brands it can't find are listed in
   `tfd_missing.txt`; find their page or use the box.
2. **Decide the scope.** Keep only the strengths and forms a user would scan
   at home: drop injections, inhalation solutions and hospital packs.
3. **Look up each product on the FDA portal by hand**
   (https://verification.fda.gov.ph). Search the registration number printed
   on the box ("Reg. No." / "FDA Reg. No.", e.g. `DR-XY12345`) and take a
   screenshot. Fill in `fda_reg_no`, `reg_expiry`, `classification` (OTC/Rx,
   **from the portal, not the website**) and `portal_checked_on`. If it isn't
   found, it's for a different product, or it's registered as a food
   supplement or herbal, drop the product.
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
6. **Fill the three CSVs here** with the checked values (columns below).
7. **Second-person check** of every row against the page and the portal
   screenshot. Only then does the checker put their name in `checked_by`.
8. **Validate and build:**
   ```powershell
   python -m scripts.kb_build --check                      # validate kb/
   python -m scripts.kb_build --check --kb-dir <folder>    # validate a draft folder first
   python -m scripts.kb_build                              # validate + build data/kb.sqlite
   ```
   It lists every problem with its file and line. Fix them until it says the
   folder is valid. The backend loads `data/kb.sqlite` at startup.
9. **Evaluate** before every release (see "Test scans" below).
10. **Open a PR** for the `kb/` changes, reviewed by a second teammate, and
    list the pages checked.

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
missing or different strength or ingredient is never answered: the app asks
the user to turn the box ("I found Biogesic but couldn't read the strength").
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
python -m scripts.kb_build
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
