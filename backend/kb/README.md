# MedSight verified medicine dataset

This folder is the **only** source of medicine facts the backend speaks. If a
medicine isn't here, the app says it can't verify it. It never guesses.
Every row is entered from evidence (box photos and an FDA portal lookup) and
**checked by a second teammate** before it counts.

| File | One row per | Holds |
|---|---|---|
| `products.csv` | FDA registration (one brand, strength and form) | identity: brand, ingredients, strengths, Rx/OTC, FDA registration number |
| `ingredients.csv` | generic ingredient | other names for it, and whether the PNF lists it |
| `passages.csv` | piece of label text | the exact "Indications" or warning text from the box or insert |
| `eval/scans.jsonl` | test scan | OCR text, plus which product it should match |

## Workflow for each medicine

Do these steps for each of the 10 medicines. Split the 10 between teammates;
whoever enters a medicine must not be the one who checks it.

### 1. Get the evidence
Photograph the **front, back and sides of the box**, the **package insert**
(if there is one) and the **blister foil**. The photos must be sharp enough to
read the small print. Save them in the shared drive under
`evidence/<product_id>/`, for example `evidence/biogesic-500-tab/back.jpg`.
Don't commit the photos to git.

### 2. Find the FDA registration number
It's printed on the box, usually near the manufacturer details, as
**"Reg. No." or "FDA Reg. No."**, in a form like `DR-XY12345`.

### 3. Look it up on the FDA portal
Open https://verification.fda.gov.ph in a browser, search for that
registration number, and **take a screenshot** of the result. Save it in the
same evidence folder.
- **Search by hand only.** Don't script or automate the portal; it has
  bot protection.
- **If the number isn't found**, or the record is for a different product,
  **drop this medicine** and choose another. It isn't verifiable.
- **If it's registered as a food supplement or herbal rather than a drug**,
  drop it too for this prototype.

### 4. Fill in `products.csv`
Copy the fields **from the portal record**, then confirm that each one matches
the box.

| Column | What to enter | Example |
|---|---|---|
| `product_id` | lowercase-with-dashes: brand-strength-form | `biogesic-500-tab` |
| `brand_name` | brand as on the portal | `Biogesic` |
| `aliases` | other spellings seen on the box or foil, separated by `\|` (optional) | `BIOGESIC` |
| `generic_names` | every active ingredient, separated by `\|` | `Paracetamol` |
| `strengths` | one strength per ingredient, **in the same order**, separated by `\|` | `500 mg` |
| `dosage_form` | as on the portal | `Tablet` |
| `classification` | `OTC`, `Rx` (prescription) or `HR` (household remedy) | `OTC` |
| `fda_reg_no` | exactly as on the portal and box | `DR-XY12345` |
| `reg_expiry` | registration expiry date from the portal, as YYYY-MM-DD | `2028-05-31` |
| `manufacturer` | from the portal (optional) | |
| `portal_checked_on` | date of your lookup, as YYYY-MM-DD | `2026-10-08` |
| `entered_by` / `checked_by` | names of the two different teammates | |

Combination products keep **every** ingredient. For example, a product with
three ingredients has three `generic_names` and three `strengths`, in the same
order.

### 5. Fill in `ingredients.csv`
Add one row for each generic used in `products.csv`, if it isn't there already.
- `synonyms`: other names the same ingredient goes by, separated by `|`, e.g.
  `Acetaminophen` for Paracetamol, or `Chlorpheniramine` for Chlorphenamine.
  These help match generic scans.
- `pnf_listed`: `yes` or `no`. Search the
  [PNF Essential Medicines List PDF](https://www.philhealth.gov.ph/partners/providers/pdf/PNF-EML_11022022.pdf)
  for the ingredient name.
- `pnf_forms` and `pnf_page`: the forms and strengths the PNF lists, and the
  page number. Fill these in if it's listed.

### 6. Fill in `passages.csv`
Type the box or insert's **"Indications"** section (sometimes headed "Uses" or
"What is this medicine for") **word for word**.
- **Don't summarize, fix grammar, or add anything.** The backend may only say
  what's in this text.
- **Add separate `warnings` rows** for warnings that matter when spoken, e.g.
  "Not for children below 12 years old" or "Do not use with other
  paracetamol-containing products".
- **Leave out dosage instructions.** The app doesn't give dosing advice.

| Column | What to enter |
|---|---|
| `passage_id` | `<product_id>-ind-1`, `<product_id>-warn-1`, … |
| `product_id` | the matching ID from `products.csv` |
| `section` | `indications` or `warnings` |
| `text` | the exact text; **wrap it in double quotes if it contains a comma** |
| `source_type` | `box`, `insert` or `pnf` |
| `source_ref` | the evidence photo it came from, e.g. `evidence/biogesic-500-tab/back.jpg` |
| `entered_by` / `checked_by` | two different teammates |

### 7. Second-person check
The checker opens the photos and the portal screenshot side by side with the
row, and checks every field: **spelling, numbers, units, and every
ingredient.** Only then do they put their name in `checked_by`.

### 8. Validate
From the repo root:
```powershell
.\.venv\Scripts\python -m scripts.kb_build --check
```
It lists every problem with its file and line number, e.g. a missing
`checked_by`, ingredient and strength counts that don't match, or an expired
registration. Fix them and run it again until it says `kb/ is valid`. Then
build the database the backend reads:
```powershell
.\.venv\Scripts\python -m scripts.kb_build
```

## Editing tips
- **Excel or Google Sheets** both work. Save as **CSV UTF-8**, and keep the
  header row exactly as it is.
- **Commas:** a value with a comma must be in double quotes. Sheets and Excel
  do this automatically.
- **Dates:** use YYYY-MM-DD (Excel may reformat dates; check before saving).

## Test scans (`eval/scans.jsonl`)
These measure whether scans match the right product, and whether unknown
medicines are refused.

1. Draft them from the dataset photos:
   ```powershell
   .\.venv\Scripts\python -m scripts.kb_make_scans sorted_output/packaging
   ```
   This writes `eval/scans_draft.jsonl`, with the OCR text of each image.
2. For each line, set `expected_product_id` to the right `product_id`, or to
   `"unsupported"` for a medicine that isn't among the 10.
3. Move the reviewed lines into `eval/scans.jsonl`.
4. Also add **negative** examples by hand. Real medicines outside the 10 (e.g.
   Neozep, Alaxan) and garbled text must all be `"unsupported"`:
   ```json
   {"id": "neg-neozep-1", "ocr_text": "NEOZEP Forte\nPhenylephrine HCl 10mg", "ocr_confidence": null, "expected_product_id": "unsupported"}
   ```
5. Add phone and blister-foil captures once the app can export its OCR text.

## Example rows (FAKE: format reference only, don't copy into the CSVs)

```
products.csv
testflu-tab,Testflu,TEST FLU|TESTFLU,Fakeamol|Fakephrine,500 mg|10 mg,Tablet,OTC,DR-XY00000,2030-01-01,Test Co,2026-10-08,Ana,Ben

passages.csv
testflu-tab-ind-1,testflu-tab,indications,"<exact text from the box, in quotes>",box,evidence/testflu-tab/back.jpg,Ana,Ben
```
