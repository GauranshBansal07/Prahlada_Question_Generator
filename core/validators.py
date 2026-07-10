"""
core/validators.py — Premise-validity checks for generated chemistry problems.

Each validator is independent and returns:
  {"pass": bool, "flag": str, "detail": str}

Validators check structural/logical premises in the problem text,
NOT answer correctness (that's the verifier's job). A problem can have
a correct final answer but a broken reasoning scaffold — this module
catches that orthogonal failure mode.

Run all validators with: run_all(problem_text, solution_text, smiles_map)
"""

import re
from typing import Optional

# ── RDKit (optional but preferred for stereocenter checks) ────────────────────
try:
    from rdkit import Chem
    from rdkit.Chem import Descriptors
    RDKIT_OK = True
except ImportError:
    RDKIT_OK = False


# ── Stereochemistry keyword patterns ─────────────────────────────────────────
_STEREO_PHRASES = re.compile(
    r'\b('
    r'stereocenter|stereocentre|chiral|chirality|racemi[cs]|enantiomer|'
    r'diastereomer|R-|S-|\(R\)|\(S\)|retention|inversion|'
    r'consider.{0,30}stereo|stereo.{0,20}consider|'
    r'both faces|si face|re face|'
    r'configuration.{0,20}(alcohol|halide|product|intermediate)|'
    r'(predict|determine).{0,30}configuration'
    r')',
    re.IGNORECASE
)

_EZ_PHRASE = re.compile(r'\b(E|Z)-', re.IGNORECASE)

# Reactions that DESTROY the double bond (and hence erase E/Z information)
_EZ_ERASING_REACTIONS = re.compile(
    r'\b('
    r'hydrat|H₂O.*H\+|H2O.*acid|acid.*H2O|'
    r'hydrohalogena|HBr|HCl|HI|'
    r'hydrogenat|H₂.*Pd|Pd.*H₂|Lindlar|'
    r'halohydrin|Br₂|Cl₂.*CCl₄|'
    r'epoxid|mCPBA|'
    r'ozonolys|O₃|'
    r'dihydroxylat|OsO₄|KMnO₄.*cold'
    r')',
    re.IGNORECASE
)


# ═══════════════════════════════════════════════════════════════════════════════
# Validator 1: Stereocenter existence
# ═══════════════════════════════════════════════════════════════════════════════

def check_stereocenter_claims(
    problem: str,
    solution: str,
    smiles_map: Optional[dict] = None,
) -> dict:
    """
    If the problem invokes stereochemistry language (racemic, R/S, consider
    configuration, etc.) verify that at least one intermediate or product
    actually has a stereocenter.

    smiles_map: {label: smiles_string} for key intermediates, e.g.
        {"alcohol": "CCC(C)(O)CC", "product": "CCC(C)(Cl)CC"}
    If no smiles_map is supplied, falls back to LLM-side detection only
    (returns a softer flag).
    """
    stereo_claim = bool(_STEREO_PHRASES.search(problem))
    if not stereo_claim:
        return {"pass": True, "flag": "stereo_ok", "detail": "No stereochemistry claims in problem."}

    if not smiles_map:
        return {
            "pass": None,  # inconclusive — no SMILES to check
            "flag": "stereo_unverified",
            "detail": "Problem makes stereochemistry claims but no SMILES provided for RDKit check.",
        }

    if not RDKIT_OK:
        return {
            "pass": None,
            "flag": "stereo_unverified",
            "detail": "RDKit not available; cannot verify stereocenter existence.",
        }

    any_real_center = False
    mol_reports = []
    for label, smi in smiles_map.items():
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            mol_reports.append(f"{label}: invalid SMILES '{smi}'")
            continue
        centers = Chem.FindMolChiralCenters(mol, includeUnassigned=True, useLegacyImplementation=False)
        mol_reports.append(f"{label} ({smi}): stereocenters={centers}")
        if centers:
            any_real_center = True

    if not any_real_center:
        return {
            "pass": False,
            "flag": "phantom_stereocenter",
            "detail": (
                "Problem invokes stereochemistry language but no intermediate/product "
                f"has any real stereocenter.\n  Molecules checked: {'; '.join(mol_reports)}"
            ),
        }

    return {
        "pass": True,
        "flag": "stereo_ok",
        "detail": f"Stereochemistry claim verified. {'; '.join(mol_reports)}",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Validator 2: E/Z propagation
# ═══════════════════════════════════════════════════════════════════════════════

def check_ez_propagation(problem: str, solution: str) -> dict:
    """
    If the starting material carries an E/Z descriptor, check whether that
    geometry information actually propagates to the final answer. If the
    first reaction destroys the double bond (addition, hydrogenation,
    ozonolysis, etc.), the E/Z label is decorative — flag it.
    """
    if not _EZ_PHRASE.search(problem):
        return {"pass": True, "flag": "ez_ok", "detail": "No E/Z descriptor in problem."}

    # Check whether step 1 is a reaction that erases the double bond
    # Look at the first ~400 chars after "Step 1" (covers the reagent line)
    step1_match = re.search(r'[Ss]tep\s*1[:\.]?\s*(.{0,500})', problem)
    step1_text = step1_match.group(1) if step1_match else problem[:400]

    if _EZ_ERASING_REACTIONS.search(step1_text):
        # Check if E/Z appears in the answer or is discussed in solution
        ez_in_answer = bool(_EZ_PHRASE.search(solution[-500:]))  # last ~500 chars = conclusion
        if not ez_in_answer:
            return {
                "pass": False,
                "flag": "ez_decorative",
                "detail": (
                    "Starting material has E/Z descriptor but Step 1 destroys the double bond "
                    "(addition/hydrogenation/ozonolysis). E/Z geometry carries no information "
                    "to the final answer — the descriptor is decorative."
                ),
            }

    return {"pass": True, "flag": "ez_ok", "detail": "E/Z descriptor appears to propagate to answer."}


# ═══════════════════════════════════════════════════════════════════════════════
# Validator 3: Mechanistic coherence of reagent + mechanism claims
# ═══════════════════════════════════════════════════════════════════════════════

_PYRIDINE_SN1 = re.compile(r'pyridine.{0,80}SN1|SN1.{0,80}pyridine', re.IGNORECASE)
_TERTIARY_SN2 = re.compile(r'tertiar.{0,60}SN2|SN2.{0,60}tertiar', re.IGNORECASE)
_MARKOVNIKOV_ANTI = re.compile(r'Markovnikov.{0,80}anti-Markovnikov|anti-Markovnikov.{0,80}Markovnikov', re.IGNORECASE)

def check_mechanistic_coherence(problem: str, solution: str) -> dict:
    """
    Catch internally contradictory mechanistic claims in the solution:
    - Pyridine (SN2 promoter) invoked alongside SN1
    - Tertiary substrate described as going SN2
    - Simultaneous Markovnikov + anti-Markovnikov claims
    """
    combined = problem + "\n" + solution
    flags = []

    if _PYRIDINE_SN1.search(combined):
        flags.append(
            "Pyridine is an SN2 promoter (captures the chlorosulfite intermediate to force "
            "backside attack) — invoking SN1 in the same step is mechanistically incoherent."
        )
    if _TERTIARY_SN2.search(combined):
        flags.append(
            "Tertiary substrate described as SN2 — tertiary centres are essentially "
            "blocked to SN2 by steric hindrance; mechanism should be SN1 or E1/E2."
        )
    if _MARKOVNIKOV_ANTI.search(combined):
        flags.append("Simultaneous Markovnikov and anti-Markovnikov claims in the same step.")

    if flags:
        return {
            "pass": False,
            "flag": "mechanism_incoherent",
            "detail": " | ".join(flags),
        }
    return {"pass": True, "flag": "mechanism_ok", "detail": "No mechanistic contradictions detected."}


# ═══════════════════════════════════════════════════════════════════════════════
# Validator 4: Gratuitous / unsuppressable conditions
# ═══════════════════════════════════════════════════════════════════════════════

_GRATUITOUS_NO_REARRANGEMENT = re.compile(
    r'no rearrangement.{0,60}(tertiar|3°|3rd|stable)',
    re.IGNORECASE
)

def check_gratuitous_conditions(problem: str, solution: str) -> dict:
    """
    Flag conditions stated in the problem that have no chemical work to do.
    E.g. 'no rearrangement' when the carbocation is already maximally stable.
    """
    flags = []
    if _GRATUITOUS_NO_REARRANGEMENT.search(problem + solution):
        flags.append(
            "'No rearrangement' note is gratuitous when the carbocation is already tertiary "
            "(no hydride or alkyl shift would improve stability)."
        )
    if flags:
        return {"pass": False, "flag": "gratuitous_condition", "detail": " | ".join(flags)}
    return {"pass": True, "flag": "conditions_ok", "detail": "No gratuitous conditions detected."}


# ═══════════════════════════════════════════════════════════════════════════════
# Master runner
# ═══════════════════════════════════════════════════════════════════════════════

def run_all(
    problem: str,
    solution: str,
    smiles_map: Optional[dict] = None,
) -> dict:
    """
    Run all premise-validity validators. Returns:
    {
      "all_pass": bool,          # False if any validator hard-fails
      "results": {name: result}, # per-validator results
      "failures": [str],         # human-readable list of failure flags
      "feedback": str,           # combined feedback for the generator
    }
    """
    validators = {
        "stereocenter_existence": check_stereocenter_claims(problem, solution, smiles_map),
        "ez_propagation":         check_ez_propagation(problem, solution),
        "mechanistic_coherence":  check_mechanistic_coherence(problem, solution),
        "gratuitous_conditions":  check_gratuitous_conditions(problem, solution),
    }

    failures = []
    for name, result in validators.items():
        if result["pass"] is False:  # None = inconclusive, don't count as failure
            failures.append(f"[{result['flag']}] {result['detail']}")

    return {
        "all_pass": len(failures) == 0,
        "results": validators,
        "failures": failures,
        "feedback": "\n".join(failures) if failures else "All premise-validity checks passed.",
    }
