"""
Realistic OCR texts and the parse replies a well-behaved model would give
for them. Used only to drive the fake LLM; none of this is reviewed medicine
data, and it must never be copied into kb/.
"""

BIOGESIC_TEXT = "BIOGESIC\nParacetamol\n500 mg Tablet\nFor the relief of minor aches and pains\nUNILAB"
ALAXAN_TEXT = "ALAXAN FR\nIbuprofen 200 mg\nParacetamol 325 mg\nFast Relief Capsule"
ADVIL_TEXT = "Advil\nIbuprofen 200mg\nLiquigel"
NEOZEP_TEXT = ("NEOZEP Forte\nPhenylephrine HCl 10 mg\nChlorphenamine Maleate 2 mg\n"
               "Paracetamol 500 mg\nTablet\nFor the relief of clogged nose")
TWO_BOXES_TEXT = BIOGESIC_TEXT + "\n" + ADVIL_TEXT
ALSO_AVAILABLE_TEXT = BIOGESIC_TEXT + "\nAlso available: Biogesic for Kids 250 mg/5 mL"


def product(brand, generics, strengths):
    return {"brand_name": brand or "", "generic_names": list(generics), "strengths": list(strengths)}


def reply(*products, confidence=0.9):
    return {"products": list(products), "confidence": confidence}


BIOGESIC = product("Biogesic", ["Paracetamol"], ["500 mg"])
ALAXAN = product("Alaxan FR", ["Ibuprofen", "Paracetamol"], ["200 mg", "325 mg"])
ADVIL = product("Advil", ["Ibuprofen"], ["200mg"])
NEOZEP = product("Neozep Forte", ["Phenylephrine HCl", "Chlorphenamine Maleate", "Paracetamol"],
                 ["10 mg", "2 mg", "500 mg"])
BIOGESIC_KIDS = product("Biogesic for Kids", ["Paracetamol"], ["250 mg/5 mL"])

GOOD_DESCRIPTION = {"description": "A pain reliever and fever reducer.", "confidence": 0.5}
