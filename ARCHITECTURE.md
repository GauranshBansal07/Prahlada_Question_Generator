# AutoData — Organic Chemistry Question Generation
## Full Architecture Reference (Complete Granular State)

> Canonical reference as of end of June 2026 design session.
> Any agent or future Claude session working on this codebase should read this first.
> It is the single source of truth for what is built, what is designed, and what is pending.

---

## Table of Contents

1. System Purpose and Goals
2. Pipeline Overview
3. Component: run_live.py (Seed-based) — Full Code Detail
4. Component: run_groundup.py (Ground-up) — Full Code Detail
5. Component: blackboard.py
6. Component: meta_tags.py
7. Data Files — Exact Schemas
   - jeeadv_organic_seeds.json
   - jic_chemistry_stats.json
   - concept_book.json
   - meta_tag_norm_stats.json
   - solver_traces.json
   - generated_questions.json
8. API Configuration and Rate Limits
9. Calibrated Thresholds and Known Issues
10. Planned: Reaction Knowledge Graph
    - Design Motivation
    - Node Schema
    - Edge Schema (with conditions_variants redirect design)
    - Exceptions: When to Redirect vs When to Use a Separate Edge
    - Chemoselectivity as Node-Redirects
    - Traversal State
    - MCP Tool Interface
11. Planned: Orders JSON
12. Planned: Molecule Constructor
13. Planned: Coverage and Sampling Mechanism
14. File Inventory — Status of Every File
15. Pending Work
16. Operational Notes and Constraints

---

## 1. System Purpose and Goals

AutoData generates JEE Advanced-level organic chemistry questions programmatically.
The system is not a fine-tuner and not a simple prompt wrapper — it is a pipeline that:
- Constructs a question attempt
- Validates it chemically (verifier, blind-solve protocol)
- Confirms it is solvable by an expert (strong solver ≥ 85%)
- Confirms it is hard enough to challenge a non-expert (weak solver ≤ 60%)
- Labels it with 6 calibrated difficulty meta-tags
- Saves the full attempt history for every accepted question

Target exam: JEE Advanced organic chemistry (Paper 1 and Paper 2).
Seed corpus: 153 real JEE Advanced organic questions from 2013–2025.
Models: SambaNova / DeepSeek-V3.2 for all expert roles; Ollama / llama3.2 for weak solver.

---

## 2. Pipeline Overview

Two entry points exist and both run live against real APIs.

```
SEED-BASED (run_live.py):
  Pick seed from jeeadv_organic_seeds.json
  ↓
  Blackboard(seed, archetype, target_profile)
  ↓
  Loop (max 4 attempts: 1 initial + 3 retries):
    │
    ├── Concept Reasoner (DeepSeek-V3.2, temp=0.7)
    │     reads: seed, archetype, target_profile, blackboard.context_summary(),
    │            blackboard.operators_tried(), full concept_book.json dump
    │     writes: selected_operators (1-3), specific_constraints, reasoning
    │
    ├── Generator (DeepSeek-V3.2, temp=0.7)
    │     attempt 1: creates new augmented problem from seed + operators
    │     attempt 2+: "REFINE IN PLACE" — fixes only what verifier flagged
    │     writes: augmented_problem, augmented_solution, operators_applied
    │
    ├── Verifier (DeepSeek-V3.2, temp=0.0)
    │     blind-solve protocol: solves independently, then compares to candidate
    │     writes: verdict (PASS/FAIL), semantic_flaws, difficulty_rating, feedback_for_generator
    │
    ├── Weak Solver (llama3.2/Ollama, temp=0.7)
    │     persona: chemistry undergraduate
    │     writes: attempted_solution, score (0-100), reasoning
    │
    ├── Strong Solver (DeepSeek-V3.2, temp=0.0)
    │     persona: expert organic chemist
    │     writes: attempted_solution (= solver_trace), score (0-100), reasoning
    │
    └── 3-Gate Check:
          verifier.verdict == "PASS"   (chemical validity)
          strong.score >= 85           (solvable by expert)
          weak.score <= 60             (hard enough for non-expert)
          ↓ ALL PASS:
            compute_meta_tags(question, archetype_code, solver_trace, fragility_weight)
            build output record → append to generated_questions.json → DONE
          ↓ ANY FAIL:
            blackboard.record_attempt() → loop continues

GROUND-UP (run_groundup.py):
  No seed question. Topic anchor only (chapter + archetype).
  Filter concept_book.json to chapter + archetype subset.
  Concept Reasoner selects 3-4 TX ids from filtered list → chain description.
  Generator creates question FROM SCRATCH (no seed to augment).
  Same verifier + solver + 3-gate flow.
  Demonstrated: Hydrocarbons / Archetype I → accepted on attempt 4.
```

---

## 3. Component: run_live.py (Seed-based) — Full Code Detail

**Location:** `/Users/admin/dummy 2/run_live.py`
**Entry point:** `python3 run_live.py`
**Status:** LIVE.

### API Clients and Configuration

```python
SAMBANOVA_KEY = "f64c9602-436b-4855-9ab5-3e81933a7985"   # single key — no rotation here
strong_client = OpenAI(api_key=SAMBANOVA_KEY, base_url="https://api.sambanova.ai/v1")
weak_client   = OpenAI(api_key="ollama",      base_url="http://localhost:11434/v1")
STRONG_MODEL  = "DeepSeek-V3.2"
WEAK_MODEL    = "llama3.2"
WEAK_CEILING  = 60   # weak solver must score ≤ this
STRONG_FLOOR  = 85   # strong solver must score ≥ this
MAX_RETRIES   = 3    # total attempts = 4 (1 initial + 3 retries)
```

### sambanova_call(fn, retries=5, base_wait=15)

```
Wraps any SambaNova API call.
On 429: waits base_wait × 2^attempt seconds (exponential backoff).
Does NOT rotate keys (run_live.py has only 1 key — unlike run_groundup.py).
Raises RuntimeError after all retries exhausted.
```

### reason_over_concept_book_live(blackboard, concept_book)

```
Prompt includes:
  - seed["question"] (full seed question text)
  - blackboard.archetype (full label string)
  - blackboard.target_profile (JSON dump)
  - concept_book (FULL JSON dump — all 657 TXs + all other operators)
  - blackboard.operators_tried() (deduplicated list)
  - blackboard.context_summary() (one line per past attempt: verdict/scores/feedback)

Output (JSON):
  selected_operators: list[str]      (1-3 operator names)
  specific_constraints: dict         (operator → exact parameters)
  reasoning: str

Model: STRONG_MODEL, temp=0.7, response_format=json_object

NOTE: full concept_book dump to context is expensive (~657 TXs × full fields).
This is the primary target for replacement by MCP graph queries once the
knowledge graph is built.
```

### generate_live(blackboard, constraints)

```
Attempt 1 prompt: seed question, seed answer, archetype, target_profile, constraints.
Attempt 2+ prompt: adds "REFINE IN PLACE" block:
  - Previous problem (full text)
  - Previous solution (full text)
  - Verifier feedback (exact string from last_attempt())
  - Weak score% and Strong score% (numbers)
  - Explicit instruction: "Fix ONLY what was flagged. Keep everything else."

Hardcoded rule in prompt: "Use standard IUPAC nomenclature throughout."

Output (JSON):
  augmented_problem: str
  augmented_solution: str
  operators_applied: list[str]
  reasoning: str

Model: STRONG_MODEL, temp=0.7
```

### verify_live(problem, solution, archetype)

```
Blind-solve protocol (enforced in prompt):
  Step 1: Read problem only. Do NOT read candidate solution yet.
  Step 2: Solve independently.
  Step 3: Compare to candidate solution.
  Step 4: Check — functional group incompatibilities, infeasible mechanisms,
          wrong stereochemistry, reagent conflicts.
  Step 5: Rate difficulty within the archetype.

Output (JSON):
  independent_solution: str     (verifier's own solution)
  semantic_flaws: str           ("None found" if clean)
  verdict: "PASS" | "FAIL"
  difficulty_rating: "Beginner" | "Intermediate" | "Advanced" | "N/A"
  feedback_for_generator: str   (actionable if FAIL, else "")

Model: STRONG_MODEL, temp=0.0    ← deterministic, no creativity here
```

### solve_weak_live(problem, solution, archetype)

```
Persona: "chemistry undergraduate student"
Given: problem + reference solution (reference explicitly labeled "for scoring only")
Self-scores 0-100 against reference.

Output (JSON):
  attempted_solution: str
  score: int (0-100)
  reasoning: str

Model: WEAK_MODEL (llama3.2), temp=0.7
Client: weak_client → Ollama at localhost:11434
No retry handler — Ollama is local, no rate limits.
```

### solve_strong_live(problem, solution, archetype)

```
Persona: "expert organic chemist"
Prompt asks for full mechanistic reasoning.
Self-scores 0-100 against reference.

Output (JSON):
  attempted_solution: str    ← this IS the solver_trace used for meta-tag computation
  score: int (0-100)
  reasoning: str

Model: STRONG_MODEL, temp=0.0
```

### main() — Orchestration in run_live.py

```python
def main():
    seeds = json.load("jeeadv_organic_seeds.json")
    concept_book = json.load("concept_book.json")
    accepted_questions = load_or_create("generated_questions.json")

    seed = seeds[0]        # HARDCODED — no coverage/sampling logic yet
    target_profile = {     # HARDCODED — Advanced on all tags
        "structural":   {"question_length_scope": "Advanced", "model_solution_length": "Advanced"},
        "interpretive": {"conceptual_fragility": "Advanced", "number_of_exceptions": "Intermediate",
                         "semantic_obfuscation": "Advanced", "distractor_plausibility": "Advanced"}
    }
    blackboard = Blackboard(seed, seed["archetype"], target_profile)

    for attempt in range(1, MAX_RETRIES + 2):
        constraints = reason_over_concept_book_live(blackboard, concept_book)
        gen         = generate_live(blackboard, constraints)
        verifier    = verify_live(gen["augmented_problem"], gen["augmented_solution"], seed["archetype"])
        weak        = solve_weak_live(gen["augmented_problem"], gen["augmented_solution"], seed["archetype"])
        strong      = solve_strong_live(gen["augmented_problem"], gen["augmented_solution"], seed["archetype"])

        blackboard.record_attempt(problem=gen["augmented_problem"],
                                  solution=gen["augmented_solution"],
                                  operators_used=gen.get("operators_applied", constraints["selected_operators"]),
                                  verifier_result=verifier,
                                  weak_score=weak["score"],
                                  strong_score=strong["score"])

        accepted = (verifier["verdict"] == "PASS"
                    and strong["score"] >= STRONG_FLOOR
                    and weak["score"] <= WEAK_CEILING)

        if accepted:
            arch_code = archetype_code_from_label(seed["archetype"])
            meta = compute_meta_tags(question_text=gen["augmented_problem"],
                                     archetype_code=arch_code,
                                     solver_trace=strong.get("attempted_solution", ""),
                                     fragility_weight=seed.get("fragility_weight"))

            record = {
                "question_id":        f"GEN_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                "seed_id":            seed["question_index"],
                "year":               seed.get("year"),
                "paper":              seed.get("paper"),
                "topic":              seed.get("topic", "Organic Chemistry"),
                "sub_topic":          seed.get("sub_topic", ""),
                "archetype":          seed["archetype"],
                "archetype_code":     arch_code,
                "question":           gen["augmented_problem"],
                "solution":           gen["augmented_solution"],
                "operators_applied":  gen.get("operators_applied", constraints["selected_operators"]),
                "loops_run":          attempt,
                "strong_score":       strong["score"],
                "weak_score":         weak["score"],
                "verifier_verdict":   verifier["verdict"],
                "verifier_difficulty":verifier["difficulty_rating"],
                "meta_tags":          meta,
                "attempt_history":    blackboard.history(),
                "generated_at":       datetime.now(timezone.utc).isoformat(),
            }
            accepted_questions.append(record)
            json.dump(accepted_questions, "generated_questions.json")
            return
```

---

## 4. Component: run_groundup.py (Ground-up) — Full Code Detail

**Location:** `/Users/admin/dummy 2/run_groundup.py`
**Entry point:** `python3 run_groundup.py`
**Status:** LIVE. Demonstrated successfully — accepted on attempt 4.

### Key Differences vs run_live.py

**4-key rotation:** samba_call() rotates across all 4 keys before exponential wait.
```python
SAMBANOVA_KEYS = ["f64c9602...", "bd95b53d...", "f795fde0...", "d29cd56e..."]
_key_idx = 0    # global, mutated by samba_call()

def samba_call(fn, retries=3, base_wait=65):
    for attempt in range(retries * len(SAMBANOVA_KEYS)):
        try:
            return fn()
        except Exception as e:
            if "429" in str(e):
                next_idx = (_key_idx + 1) % len(SAMBANOVA_KEYS)
                if next_idx != _key_idx:
                    _key_idx = next_idx
                    sleep(2)      # try next key immediately
                else:
                    wait = base_wait * (2 ** (attempt // len(SAMBANOVA_KEYS)))
                    sleep(wait)   # all keys exhausted, wait before cycling again
```

**Filtered concept book — not the full dump:**
```python
HYDRO_TERMS = re.compile(r'\b(alkan|alken|alkyn|cycloalk|methane|...|hydroboration)\b', re.IGNORECASE)

def filter_hydrocarbon_txs(concept_book):
    txs = concept_book["structural_operators"]["add_reaction_step"]["valid_transformations"]
    return [tx for tx in txs
            if "I" in tx.get("archetype", [])
            and HYDRO_TERMS.search(f"{tx['from']} {tx['to']} {' '.join(tx['reagents'])}")]
    # Result: 110 TXs from 657 total
```

**No seed question — topic anchor:**
```python
topic_anchor = {
    "question_index": "GROUNDUP_HYDRO_001",
    "question":       None,   # no seed text
    "answer":         None,
    "archetype":      "Long Reaction Chains",
    "archetype_code": "I",
    "chapter":        "Hydrocarbons",
    "sub_topic":      "Hydrocarbons",
}
```

### concept_reasoner() — Ground-up Version

```
Prompt includes:
  - tx_summary: concise list of filtered TXs (id:int, from, to, reagents, conditions[:200])
  - blackboard.context_summary() (previous attempts)
  - blackboard.operators_tried()
  - Instructions to pick 3-4 TXs forming a chemically coherent chain

Output (JSON):
  selected_tx_ids: list[int]     (indices into hydro_txs list)
  chain_description: str
  difficulty_rationale: str
  reasoning: str

Model: STRONG_MODEL, temp=1.0    ← higher randomness (no seed to anchor)
```

### generator() — Ground-up Version

```
Attempt 1: Creates question from scratch using selected TXs and chain_description.
           Prompt explicitly says: "Do NOT base this on any existing question."
           IUPAC throughout, no diagrams, text-only question.

Attempt 2+: "REFINE IN PLACE" block — identical to run_live.py pattern.

Output (JSON):
  problem: str
  solution: str
  operators_applied: list[str]
  reasoning: str

Model: STRONG_MODEL, temp=0.7
```

### Demonstrated Run (Hydrocarbons / Archetype I)

```
Attempt 1: propene → allyl chloride → allyl alcohol → glycerol → propane
  Verifier: PASS, Intermediate
  Weak: 0% ✓    Strong: 70% ✗  (below 85 floor)
  → Refine

Attempt 2: terminal alkyne → amine → allyl chloride → cyclobutyl ring expansion
  Verifier: FAIL — cyclobutyl chemistry not supported by the chain; 4 specific flaws
  Weak: 0% ✓    Strong: 65% ✗
  → Refine

Attempt 3: alkane → alkyl halide → anti-Markovnikov → acetylide → trans-alkene
  Verifier: FAIL — NaNH2/NH3 on tertiary bromide gives elimination not alkyne formation
  Weak: 0% ✓    Strong: 70% ✗
  → Refine

Attempt 4: propene → allyl chloride → allyl alcohol → glycerol → propane (refined)
  Verifier: PASS, Intermediate
  Weak: 0% ✓    Strong: 100% ✓
  ✓ ACCEPTED

  Meta-tags: question_length_scope=5, semantic_obfuscation=2, conceptual_fragility=3,
             number_of_exceptions=3, model_solution_length=2, distractor_plausibility=1

  Accepted question asked students to:
  (a) IUPAC name + formula of final product D (propane)
  (b) Molecular formula of compound B (prop-2-en-1-ol)
  (c) If Br2/CCl4 at RT instead of Cl2 at 500°C — what product, how does stereochemistry differ?
```

Attempts 2 and 3 demonstrated exactly why the knowledge graph is needed.
The model invented chemistry not supported by the selected TXs.
Graph traversal prevents this: each hop is a valid edge, period.

---

## 5. Component: blackboard.py

**Location:** `/Users/admin/dummy 2/blackboard.py`
**Status:** LIVE, complete.

```python
class Blackboard:
    def __init__(self, seed: dict, archetype: str, target_profile: dict):
        self.seed = seed              # full seed dict or topic_anchor
        self.archetype = archetype    # full archetype label string
        self.target_profile = target_profile
        self.attempts = []

    def record_attempt(self, problem, solution, operators_used,
                       verifier_result, weak_score, strong_score):
        """Appends one attempt record to self.attempts."""
        self.attempts.append({
            "attempt_number":  len(self.attempts) + 1,
            "problem":         problem,
            "solution":        solution,
            "operators_used":  operators_used,
            "verifier_verdict":verifier_result.get("verdict"),
            "verifier_feedback":verifier_result.get("feedback_for_generator", ""),
            "difficulty_rating":verifier_result.get("difficulty_rating", "N/A"),
            "weak_score":      weak_score,
            "strong_score":    strong_score,
        })

    def operators_tried(self) -> list:
        """Deduplicated flat list of all operators used across all attempts."""
        seen = []
        for a in self.attempts:
            for op in a.get("operators_used", []):
                if op not in seen:
                    seen.append(op)
        return seen

    def last_attempt(self) -> dict | None:
        """Returns self.attempts[-1] or None."""
        return self.attempts[-1] if self.attempts else None

    def context_summary(self) -> str:
        """Human-readable one-line-per-attempt summary for Concept Reasoner prompt.
           Format: 'Attempt N: Verdict=X | Weak=Y% | Strong=Z% | Operators=[...] | Feedback=...'"""

    def history(self) -> list:
        """Returns full self.attempts list. Saved verbatim in output record."""
```

---

## 6. Component: meta_tags.py

**Location:** `/Users/admin/dummy 2/meta_tags.py`
**Status:** LIVE. All 6 tags compute. Two known bugs (see Section 9).

### compute_meta_tags() — Public API

```python
def compute_meta_tags(
    question_text: str,
    archetype_code: str,        # "I" | "II" | "III" | "IV"
    solver_trace: str = None,   # strong solver's attempted_solution field
    fragility_weight: float = None,  # 1 - (pct_full_marks/100), None for ground-up
) -> dict:
    # Returns: 6 keys, values 1-5 integers (or None if solver_trace missing)
```

### Z-score Formula

```python
def _zscore_to_15(z: float) -> int:
    return max(1, min(5, round(z * 1.5 + 3)))
# z=0  (corpus mean)  → 3
# z=+1 (1σ above)     → 4 or 5 (rounds to 4.5 → 5)
# z=-1 (1σ below)     → 1 or 2 (rounds to 1.5 → 2)
# z=-2 or worse       → 1 (clamped)
```

### Six Tags — Exact Computation

```
1. question_length_scope
   Raw value: word count of question_text
   Normalization: archetype-specific mean/std from meta_tag_norm_stats.json["per_archetype"][code]["question_length_scope"]

2. semantic_obfuscation
   Raw value: (count of IUPAC regex matches) / (total words)
   IUPAC regex: meth|eth|prop|but|pent|hex|hept|oct|non|dec|cyclo|benz|phenyl|naph|
                toluene|xylene|aniline|vinyl|allyl|acetyl|formyl|acetate|formate|
                chloro|bromo|iodo|fluoro|nitro|amino|hydroxy|oxo|carboxyl|aldehyde|
                ketone|ester|amide|anhydride|alkyl|aryl|acyl|alkene|alkyne|alkane|
                enantiomer|diastereomer|stereoisomer|racemic|meso|ortho|meta|para|
                cis|trans|[RSZEW]-
   Normalization: GLOBAL (same stats for all archetypes)
   Source: meta_tag_norm_stats.json["global"]["semantic_obfuscation"]

3. conceptual_fragility
   Raw value: fragility_weight = 1 - (pct_full_marks / 100) from JIC data
   If fragility_weight=None (ground-up): returns 3 (archetype mean estimate)
   Normalization: archetype-specific

4. number_of_exceptions
   Raw value: count of exception/rejection keyword matches in solver_trace
   Keywords: however|but not|cannot|incorrect|wrong|not applicable|anomalous|
             atypical|irregular|abnormal|unusual|special case|exception|except|
             excluding|unless|would not|does not|rejected|ruled out|discarded|
             eliminated|not the answer
   Normalization: archetype-specific
   KNOWN ISSUE: 150/153 seeds score 3 via this proxy — nearly degenerate.
   Real signal only comes from solver traces, not from question text directly.

5. model_solution_length
   Raw value: word count of solver_trace
   Normalization: archetype-specific (calibrated from solver_traces.json)
   BUG IN CODE: line 144-147 uses question_length stats × 8 as proxy.
   Correct: use meta_tag_norm_stats.json["per_archetype"][code]["model_solution_length"]
   The calibrated stats exist — the code just doesn't read them correctly.

6. distractor_plausibility
   Raw value: count of explicit path-rejection phrases in solver_trace
   Phrases: ruled out|not correct|incorrect because|wrong because|eliminated|
            discarded|rejected|cannot be|would not work|not applicable here|this is not
   Normalization: archetype-specific (calibrated from solver_traces.json)
   BUG IN CODE: line 154-155 uses hardcoded mean=2.0, std=1.5.
   Correct: use meta_tag_norm_stats.json["per_archetype"][code]["distractor_plausibility"]
```

---

## 7. Data Files — Exact Schemas

### 7.1 jeeadv_organic_seeds.json

**Path:** `/Users/admin/dummy 2/jeeadv_organic_seeds.json`
**Entries:** 153  |  **Status:** Fully calibrated. All 6 meta-tags present.

```json
{
  "question_index":    "JADV_2023_P1_C5",
  "year":              2023,
  "paper":             "P1",
  "q":                 5,
  "topic":             "Organic Chemistry",
  "sub_topic":         "Reactions of Benzene",
  "chapter":           "Aromatic Compounds",      // one of 14 canonical JEE chapters
  "archetype":         "Long Reaction Chains",    // full label
  "archetype_code":    "I",                       // "I"|"II"|"III"|"IV"
  "is_organic":        true,
  "question_text":     "...",                     // first ~400 chars
  "pct_full_marks":    34.2,
  "pct_wrong":         28.1,
  "pct_not_attempted": 12.4,
  "pct_partial_marks": 25.3,                      // null for non-MSQ questions
  "fragility_weight":  0.658,                     // 1 - (pct_full_marks/100)
  "meta_tags": {
    "question_length_scope":   3,
    "semantic_obfuscation":    4,
    "conceptual_fragility":    4,
    "number_of_exceptions":    3,
    "model_solution_length":   3,
    "distractor_plausibility": 2
  }
}
```

**14 Canonical JEE Chapters:**
Hydrocarbons | IUPAC & Isomerism / Stereochemistry | Aromatic Compounds |
Biomolecules | Aldehydes & Ketones | Amines | Alkyl Halides |
Named Reactions & Multi-step Synthesis | Alcohols, Phenols & Ethers | GOC |
Carboxylic Acids & Derivatives | Reaction Mechanisms | Polymers | Organometallics & Grignard

**Chapter × Archetype Distribution (153 seeds):**
```
Chapter                           Total  | I   II  III  IV
Hydrocarbons                        27   | 18   2   5    2
IUPAC & Isomerism/Stereochemistry   23   |  1  12   9    1
Aromatic Compounds                  15   |  7   3   5    0
Biomolecules                        13   |  1   4   7    1
Aldehydes & Ketones                 13   |  8   1   4    0
Amines                              12   |  8   0   3    1
Alkyl Halides                       11   |  5   0   5    1
Named Reactions & Multi-step         9   |  9   0   0    0
Alcohols, Phenols & Ethers           7   |  3   1   2    1
GOC                                  7   |  0   1   1    5
Carboxylic Acids & Derivatives       6   |  3   0   2    1
Reaction Mechanisms                  5   |  1   1   3    0
Polymers                             3   |  0   0   3    0
Organometallics & Grignard           2   |  2   0   0    0
TOTAL                              153   | 67  25  49   12
```

### 7.2 jic_chemistry_stats.json

**Path:** `/Users/admin/dummy 2/jic_chemistry_stats.json`
**Entries:** 474  |  **Purpose:** Source of fragility_weight for all JEE chemistry questions.

Same schema as seeds but no archetype/meta_tags fields. `is_organic` can be false.
Used for: looking up conceptual_fragility for seed questions and for JEE difficulty research.

### 7.3 concept_book.json

**Path:** `/Users/admin/dummy 2/concept_book.json`
**Status:** Built from teacher notes PDFs (Claude vision, no OCR).

**Top-level structure:**
```json
{
  "structural_operators": {
    "add_reaction_step":       { "valid_transformations":  [ ... 657 TX entries ] },
    "expand_comparison_set":   { "valid_modifications":   [ ... 470 entries ] }
  },
  "interpretive_operators": {
    "conceptual_fragility":    { "concepts":   [ ... 605 entries ] },
    "number_of_exceptions":    { "exceptions": [ ... 502 entries ] },
    "semantic_obfuscation":    { "rules":      [ ... 5 entries ] },
    "distractor_plausibility": { "distractors":[ ... 470 entries ] }
  }
}
```

**valid_transformations entry (657 total — the reaction TXs):**
```json
{
  "from":      "alkene",
  "to":        "alkyl_halide",
  "reagents":  ["HBr", "peroxides"],
  "archetype": ["I", "III"],
  "conditions":"anti-Markovnikov addition; radical mechanism; Br at less substituted carbon; ...",
  "notes":     "archetype I: include in chain; archetype III: ask why peroxides invert regiochemistry",
  "meta_tags": { "question_length_scope":3, "semantic_obfuscation":2, "conceptual_fragility":4,
                 "number_of_exceptions":5, "model_solution_length":3, "distractor_plausibility":4 }
}
```

**concepts entry (605 total):**
```json
{
  "name":        "Markovnikov's Rule",
  "definition":  "Hydrogen adds to the carbon bearing more hydrogens",
  "why_fragile": "Students apply mechanically without understanding the carbocation stability basis",
  "common_error":"Inverting the rule under peroxide conditions",
  "fragile_in":  ["I", "III"],
  "routine_in":  ["II"]
}
```

**exceptions entry (502 total):**
```json
{
  "standard_case":  "E2 elimination with KOH gives Zaitsev product (more substituted alkene)",
  "exception":      "With bulky base (t-BuOK), Hofmann product forms (less substituted alkene)",
  "trap":           "Students predict Zaitsev regardless of base size",
  "why_exception":  "Steric approach control: bulky base cannot access more substituted H",
  "exam_relevance": "High",
  "archetype":      ["III", "I"]
}
```

**distractors entry (470 total):**
```json
{
  "type":             "wrong_markovnikov",
  "examples":         ["HBr adds Br to more substituted without checking for peroxides"],
  "mechanism_trap":   "Ignoring reaction conditions",
  "why_attractive":   "Markovnikov is the default; peroxide inversion is a special case",
  "correct_resolution":"Check for peroxides first; if present, anti-Markovnikov applies",
  "archetype":        ["I", "III"]
}
```

### 7.4 meta_tag_norm_stats.json

**Path:** `/Users/admin/dummy 2/meta_tag_norm_stats.json`
**Status:** Fully calibrated. `pending_calibration: []`.

```json
{
  "global": {
    "semantic_obfuscation": { "mean": 0.187, "std": 0.063, "metric": "iupac_token_density" }
  },
  "per_archetype": {
    "I":   {
      "question_length_scope":   { "mean": 82.3,  "std": 31.4,   "metric": "word_count" },
      "conceptual_fragility":    { "mean": 0.653,  "std": 0.142,  "metric": "fragility_weight" },
      "number_of_exceptions":    { "mean": 4.1,    "std": 2.8,    "metric": "exception_keyword_count" },
      "model_solution_length":   { "mean": 1338,   "std": 1087,   "metric": "word_count_of_strong_solver_trace" },
      "distractor_plausibility": { "mean": 0.85,   "std": 1.16,   "metric": "rejected_path_count_in_strong_solver_trace" }
    },
    "II":  { "model_solution_length": {"mean":1599,"std":1021}, "distractor_plausibility":{"mean":0.39,"std":0.98}, ... },
    "III": { "model_solution_length": {"mean":1243,"std":1216}, "distractor_plausibility":{"mean":1.24,"std":2.03}, ... },
    "IV":  { "model_solution_length": {"mean":1084,"std":1133}, "distractor_plausibility":{"mean":0.40,"std":0.84}, ... }
  },
  "pending_calibration": []
}
```

Calibration sample sizes: I=34, II=18, III=29, IV=10.

### 7.5 solver_traces.json

**Path:** `/Users/admin/dummy 2/solver_traces.json`
**Entries:** 153  |  **Purpose:** Raw calibration data for model_solution_length and distractor_plausibility.

```json
{
  "JADV_2023_P1_C5": {
    "arch_code":         "I",
    "trace_words":       2145,
    "distractor_count":  2,
    "confidence":        88
  }
}
```
Some entries: `{"skipped": true, ...}` (no question text) or `{"error": "...", ...}`.

### 7.6 generated_questions.json

**Path:** `/Users/admin/dummy 2/generated_questions.json`
**Current entries:** 1 (first accepted ground-up question, Hydrocarbons/Archetype I)

```json
[{
  "question_id":          "GEN_20260622T143521",
  "seed_id":              "GROUNDUP_HYDRO_001",
  "generation_mode":      "ground_up",
  "chapter":              "Hydrocarbons",
  "archetype":            "Long Reaction Chains",
  "archetype_code":       "I",
  "question":             "...",
  "solution":             "...",
  "operators_applied":    ["allylic_radical_chlorination", "SN2_substitution", ...],
  "chain_description":    "propene → allyl chloride → allyl alcohol → glycerol → propane",
  "loops_run":            4,
  "strong_score":         100,
  "weak_score":           0,
  "verifier_verdict":     "PASS",
  "verifier_difficulty":  "Intermediate",
  "meta_tags": { "question_length_scope":5, "semantic_obfuscation":2, ... },
  "attempt_history":      [ {all 4 attempt records from blackboard} ],
  "generated_at":         "2026-06-22T14:35:21+00:00"
}]
```

---

## 8. API Configuration and Rate Limits

### SambaNova

```
Base URL:   https://api.sambanova.ai/v1
Model:      DeepSeek-V3.2
SDK:        openai.OpenAI (drop-in compatible)

Keys (rotate on 429):
  Key 1: f64c9602-436b-4855-9ab5-3e81933a7985  (all 4 scripts)
  Key 2: bd95b53d-9107-42aa-886f-318d3ec68fd9  (run_groundup.py + calibrate only)
  Key 3: f795fde0-3a0b-4fd4-9fd6-abf761ab8eb0  (run_groundup.py + calibrate only)
  Key 4: d29cd56e-3164-460b-afe2-08f4c36dcb90  (run_groundup.py + calibrate only)

Rate limit: ~37–40 calls/day per key (free tier, empirically observed)
Reset: approximately midnight UTC

Key rotation in run_groundup.py/calibrate:
  On 429: rotate to next key index, sleep 2s, retry immediately.
  If all 4 keys exhausted: sleep base_wait × 2^(attempt // n_keys) seconds.
  base_wait = 65s (both scripts). Max cycles = 3 × 4 = 12 total attempts.

Key rotation in run_live.py: NONE — single key only. Upgrade needed.

Per-role temperatures:
  Concept Reasoner: 0.7 (seed-based) or 1.0 (ground-up — needs more randomness)
  Generator:        0.7
  Verifier:         0.0  ← deterministic
  Strong Solver:    0.0  ← deterministic
```

### Ollama (Weak Solver Only)

```
Base URL:   http://localhost:11434/v1
Model:      llama3.2
API key:    "ollama" (literal string, required by SDK)
Temp:       0.7
No rate limits. No retry handler needed.
Must be running: check with curl http://localhost:11434
```

---

## 9. Calibrated Thresholds and Known Issues

### Acceptance Thresholds

```
STRONG_FLOOR = 85   (mean - 1σ of strong solver on JEE organic: 96.4 - 12.1 ≈ 85)
WEAK_CEILING = 60   (mean + 0.5σ of weak solver: 41.7 + 24.6 ≈ 66 → conservative 60)
MAX_RETRIES  = 3    (4 total attempts)
```

### Known Code Bugs in meta_tags.py

**Bug 1 — model_solution_length (lines 144-147):**
Uses question_length stats × 8 as proxy for solver trace length.
Fix: Read `norm_stats["per_archetype"][archetype_code]["model_solution_length"]` directly.
The calibrated mean/std are in meta_tag_norm_stats.json — just not accessed.

**Bug 2 — distractor_plausibility (lines 154-155):**
Uses hardcoded `mean=2.0, std=1.5`.
Fix: Read `norm_stats["per_archetype"][archetype_code]["distractor_plausibility"]` directly.

### number_of_exceptions Degeneracy

150/153 seeds score 3 via the current keyword-proxy method.
This tag is nearly useless in its current form.
Signal only comes from a model-based approach on solver traces.
For now: treat as informational only, do not optimize for it.

---

## 10. Planned: Reaction Knowledge Graph

**Status:** Fully designed (June 2026). Not yet built.
**Files to create:** `reaction_graph.json` + `graph_traversal.py`

### 10.1 Design Motivation

Ground-up attempts 2 and 3 failed because the model invented chemistry
not supported by the selected TXs. A knowledge graph prevents this:
nodes are functional group states, edges are reactions. Traversal is
guaranteed coherent — each hop is a valid edge by construction.
The model's only job becomes question framing, not validity checking.

### 10.2 Node Schema

```json
{
  "id": "alkene",
  "type": "stable",
  "functional_group": "carbon-carbon double bond (C=C)",
  "can_be_start": true,
  "can_be_end": true,
  "stereochemistry_note": "E/Z isomerism; distinction activated by specific edges",
  "ranking_dimensions": ["alkene_stability", "nucleophilicity"]
}
```

`type`:
- "stable" — persistent compound class. Can be question start/end.
- "intermediate" — carbocation, carbanion, radical, acetylide, enolate, etc.
  `can_be_start: false`, `can_be_end: false`.
  Appear as pass-through nodes in traversal chains; mentioned in questions
  ("proceeds via a secondary carbocation") but the student answer is always
  the next stable node.

`ranking_dimensions`: pointers into Orders JSON.
  Which ordering dimensions are valid for this compound class (used for Archetype IV).

**Stable nodes (~50):**
alkane, alkene, alkyne, cycloalkane, cycloalkene, benzene, substituted_benzene,
naphthalene, alkyl_halide, vinyl_halide, aryl_halide, alcohol_primary,
alcohol_secondary, alcohol_tertiary, phenol, ether, epoxide, aldehyde, ketone,
enol, carboxylic_acid, ester, amide, acid_chloride, anhydride, amine_primary,
amine_secondary, amine_tertiary, nitrile, isocyanate, diazonium, organoborane,
grignard, diene_conjugated, diene_isolated, amino_acid, peptide, glucose, polymer

**Intermediate nodes (~20):**
carbocation_primary, carbocation_secondary, carbocation_tertiary, carbocation_benzylic,
carbanion, radical_allylic, radical_benzylic, acetylide_ion, enolate, imine, oxime,
bromonium_ion, mercurinium_ion, cyclic_manganate_ester

### 10.3 Edge Schema — With conditions_variants Redirect Design

**Core insight from June 2026 design session:**
Many "exceptions" are not new reactions — they are the SAME reaction mechanism
routing to a different destination node depending on conditions.
These should NOT be modeled as separate edges with firing_conditions.
They should be modeled as `conditions_variants` on a single edge, where each
variant specifies a different destination node.

This is computationally cheaper (fewer edges, smaller graph) and more readable
(the exception is encoded on the edge itself, not as a separate entity).

```json
{
  "id": "alkene_HBr",
  "from": "alkene",
  "to": "alkyl_halide",
  "default_conditions": {
    "to": "alkyl_halide",
    "reagents": ["HBr"],
    "mechanism": "electrophilic addition; Markovnikov; Br at more substituted carbon; via carbocation intermediate",
    "stereo": "anti addition from cyclic substrate"
  },
  "conditions_variants": {
    "peroxides_present": {
      "to": "alkyl_halide_anti_mk",
      "mechanism": "radical chain; anti-Markovnikov; Br at less substituted carbon",
      "stereo": "anti addition maintained (radical attacks from outside)",
      "jee_trap": "students apply Markovnikov by default, forget to check for peroxides"
    },
    "cyclic_substrate": {
      "to": "trans_alkyl_halide",
      "mechanism": "same electrophilic addition; but ring constrains to anti addition",
      "stereo": "trans product only (ring prevents syn addition)",
      "jee_trap": "students ignore ring constraint on addition geometry"
    }
  },
  "chemoselectivity": {
    "requires_absent": [],
    "node_redirect": {
      "carbonyl": "aldehyde",
      "redirect_note": "if carbonyl also present in molecule, HBr acts on carbonyl first (nucleophilic addition) — traversal redirects to aldehyde node's HBr edge instead"
    },
    "priority_ref": "nucleophilicity_order"
  },
  "chapter": "Hydrocarbons",
  "archetype": ["I", "III"],
  "tx_id": 42
}
```

**`conditions_variants` key:** condition string (what must be true).
**`conditions_variants` value:** partial edge override — only `to` and any fields that differ.
  Everything else inherits from `default_conditions`.

**`chemoselectivity.node_redirect`:**
  If the listed functional group is in the traversal state's `also_present` list,
  the traversal redirects to THAT NODE'S relevant edge for this reagent set.
  For example: alkene + HBr, but aldehyde also present → redirect to aldehyde node,
  look up aldehyde's HBr edge → destination becomes `hemiacetal` (or `alpha_haloaldehyde`).
  The traversal engine handles this lookup. The model just gets the resulting destination node.

### 10.4 Exceptions: When to Redirect vs When to Use a Separate Edge

**Use conditions_variants (node redirect):**
- Same reaction class, same reagents, conditions change the destination node.
- Examples:
  - HBr + alkene: Markovnikov vs anti-Markovnikov (peroxides)
  - KMnO4 + alkene: dihydroxylation (cold, dilute) vs oxidative cleavage (hot, conc.)
  - E2 + alkyl halide: Zaitsev (small base) vs Hofmann (bulky base)
  - SN1 vs SN2 (solvent and nucleophile conditions change destination node)

**Use a separate edge:**
- Genuinely different reaction mechanism with different reagents entirely.
- Examples:
  - Ozonolysis (O3 + Zn/H2O or O3 + H2O2) → completely different from KMnO4 oxidation
  - Birch reduction (Na/NH3/ROH) → different reagent class from catalytic hydrogenation
  - Pinacol rearrangement → different from simple E1 elimination
  - Baeyer-Villiger → no overlap with any standard oxidation

**Rule of thumb:** If a student would say "this is the same reaction but under different conditions,"
it's a conditions_variant. If they'd say "this is a completely different named reaction," it's a
separate edge.

### 10.5 Chemoselectivity as Node-Redirects (Detailed)

The central use case: a molecule has two functional groups. Which one reacts?

The answer lives in Orders JSON (nucleophilicity_order, electrophilicity_order, etc.)
and is implemented as a node-redirect on the edge:

```
Example: LiAlH4 + molecule containing both alkene AND ketone
  Edge: alkene → alcohol_primary (LiAlH4 reduces alkene via hydride addition)
  Chemoselectivity node_redirect: { "ketone": "alcohol_secondary" }
  Logic: if also_present includes "ketone", LiAlH4 acts on ketone preferentially
         (hydride addition to carbonyl) → redirect to alcohol_secondary node
         instead of traversing the alkene → alcohol_primary edge.

Example: HBr + molecule containing both alkene AND aldehyde
  Edge: alkene → alkyl_halide (HBr addition)
  Chemoselectivity node_redirect: { "aldehyde": "hemiacetal" }
  Logic: if also_present includes "aldehyde", HBr's nucleophilic character
         leads to addition to carbonyl → redirect to hemiacetal node.

Example: NaBH4 + molecule containing both ketone AND ester
  Edge: ketone → alcohol_secondary (NaBH4 reduction)
  Chemoselectivity node_redirect: {} (no redirect — NaBH4 does NOT reduce esters)
  Chemoselectivity requires_absent: []  (no blocking condition)
  Note: this is a GOC-level fact; the chemoselectivity is that NaBH4 is selective
        for ketones/aldehydes and leaves esters intact.
```

**How the traversal engine handles this:**
```python
def get_next_node(edge, traversal_state):
    # 1. Check conditions_variants
    for condition, variant in edge["conditions_variants"].items():
        if condition_is_met(condition, traversal_state):
            return variant["to"]  # redirect to variant destination

    # 2. Check chemoselectivity node_redirect
    for fg, redirect_node in edge["chemoselectivity"]["node_redirect"].items():
        if fg in traversal_state["also_present"]:
            return redirect_node  # redirect to the other functional group's product

    # 3. Check requires_absent
    for fg in edge["chemoselectivity"]["requires_absent"]:
        if fg in traversal_state["also_present"]:
            return None  # edge cannot fire at all

    # 4. Default
    return edge["to"]
```

### 10.6 Traversal State

```json
{
  "current_node": "alkene",
  "also_present": ["carbonyl", "amine"],
  "path_so_far": [
    {
      "edge_id": "alkane_radical_bromination",
      "conditions_variant_used": null,
      "substrate_note": "2-methylpropane → 2-bromo-2-methylpropane (tertiary H selected)",
      "destination_node": "alkyl_halide"
    },
    {
      "edge_id": "alkyl_halide_E2_elimination",
      "conditions_variant_used": "bulky_base",
      "substrate_note": "alc. KOH/Δ → Hofmann product (less substituted alkene)",
      "destination_node": "alkene"
    }
  ],
  "instantiated_as": "2-methylbut-2-ene"
}
```

`also_present`: updated at each hop. If the reaction changes the second functional group,
  also_present is updated accordingly.
`instantiated_as`: the IUPAC name of the specific molecule the traversal is tracking.
  Set at start by molecule constructor. All subsequent intermediate names derived from it.

### 10.7 MCP Tool Interface

The graph is never loaded into model context in full.
All queries via MCP tool calls. The model sees only what it asks for (5-15 edges max per query).

```
get_edges_from(node_id, molecular_state)
  Returns: all edges that can fire from node_id given molecular_state.
  Processing: filters by conditions_variant availability + chemoselectivity checks.
  Typical output: 8-12 edges with their default destinations.

get_paths(start_node, depth, archetype_filter, chapter_filter)
  Returns: all valid paths of specified depth matching archetype/chapter filters.
  Used at generation start to pre-plan the full chain.
  Processing: BFS up to depth; filters by archetype label on edges.

get_coverage()
  Returns: {edge_id: usage_count} for all edges in the graph.
  Used by coverage tracker to weight next edge selection.

get_node(node_id)
  Returns: full node object, including ranking_dimensions pointers.

get_ordering(dimension_id)
  Returns: full ordering dimension from Orders JSON (tiers, exceptions, traps).
  Used by Archetype IV question generation.
```

---

## 11. Planned: Orders JSON

**Status:** Designed (June 2026). Not yet built.
**File to create:** `reaction_orders.json`

### 11.1 Two Uses

1. **Edge redirect priority reference** — when a chemoselectivity redirect specifies
   `priority_ref: "nucleophilicity_order"`, the traversal engine calls `get_ordering("nucleophilicity_order")`
   to retrieve the tier list and determine which functional group "wins" the reaction.

2. **Archetype IV (GOC) question generation** — pick a dimension, instantiate compounds
   that test the exceptions, generate the comparison question.

### 11.2 Schema

```json
{
  "dimension_id": "carbocation_stability",
  "display_name": "Carbocation Stability Order",
  "applies_to":   ["SN1", "E1", "electrophilic_addition", "rearrangements", "pinacol"],
  "general_rule": "tertiary > secondary > primary > methyl",
  "reasoning":    "hyperconjugation + inductive electron donation from alkyl groups",
  "tiers": [
    {
      "rank":     1,
      "compounds":["tertiary carbocation", "benzylic carbocation", "allylic secondary carbocation"],
      "reasoning":"alkyl hyperconjugation (tertiary); resonance (benzylic/allylic)",
      "relative": "benzylic ≈ tertiary (both excellent) > allylic secondary"
    },
    {
      "rank":     2,
      "compounds":["secondary carbocation"],
      "reasoning":"two alkyl groups; less hyperconjugation than tertiary"
    },
    {
      "rank":     3,
      "compounds":["primary carbocation"],
      "reasoning":"one alkyl group; very unstable, rarely free"
    },
    {
      "rank":     4,
      "compounds":["methyl carbocation"],
      "reasoning":"no alkyl groups; extremely unstable"
    }
  ],
  "exceptions": [
    {
      "condition":       "adjacent to aromatic ring",
      "modified_order":  "benzylic > tertiary",
      "reasoning":       "resonance across aromatic ring adds stabilization beyond simple tertiary",
      "jee_trap":        "students apply simple alkyl order and miss benzylic resonance"
    },
    {
      "condition":       "cyclopropylmethyl position",
      "modified_order":  "cyclopropylmethyl > tertiary > allylic",
      "reasoning":       "Walsh orbital overlap with cyclopropyl ring provides exceptional stabilization",
      "jee_trap":        "rare but appears in JEE Advanced papers"
    }
  ],
  "common_traps":    ["confusing inductive and resonance effects", "applying simple alkyl order to resonance-stabilized systems"],
  "exam_relevance":  "High",
  "chapter":         "GOC",
  "archetype":       ["IV", "III"]
}
```

### 11.3 All Dimensions to Cover (~20)

```
Acidity (pKa order)
Basicity
Carbocation stability
Carbanion stability
Radical stability
Alkene stability (Zaitsev / hyperconjugation)
Nucleophilicity in polar protic solvent  ← I > Br > Cl > F
Nucleophilicity in polar aprotic solvent ← F > Cl > Br > I  (INVERTED — key JEE trap)
Electrophilicity
Leaving group ability
SN1 vs SN2 preference
E1 vs E2 vs E1cb preference
EAS reactivity rate
EAS regioselectivity (ortho/para vs meta directors)
NAS activation conditions
Migration aptitude (Wagner-Meerwein, pinacol, Baeyer-Villiger)
Oxidizing agent selectivity (PCC vs KMnO4 vs OsO4 etc.)
Bond dissociation energy (radical halogenation selectivity: Cl• vs Br•)
Ring strain order
Aromaticity / antiaromaticity criteria
```

### 11.4 Archetype IV Generation Mode

Does NOT traverse the reaction graph. Separate generation path:
```
1. pick ranking_dimension (inverse-frequency from coverage counter)
2. pick exception from dimension.exceptions (prefer untested exceptions)
3. instantiate 4-5 compound exemplars that test that specific exception
4. Generator: "Arrange the following in order of decreasing X: A, B, C, D"
5. Same verifier + solver gates apply
```

---

## 12. Planned: Molecule Constructor

**Status:** Designed (June 2026). Not yet built.
**File to create:** `molecule_constructor.json`

### 12.1 Design Principle

NOT a fixed pool of molecules. A set of generative rules.
Defines WHAT IS VALID for each starting node + first edge combination.
The model picks the specific IUPAC name within those constraints.

**Why not a fixed pool:** organic molecules are combinatorial; enumeration is impossible.
**Why not totally unconstrained:** without guidance, trivial or ambiguous molecules appear.

### 12.2 Schema

```json
{
  "node": "alkene",
  "skeleton_options": ["open_chain", "branched", "cyclic_5", "cyclic_6", "vinyl"],
  "interesting_when": {
    "edge_alkene_HBr": {
      "features": ["trisubstituted", "has_second_functional_group"],
      "why": "trisubstituted forces Markovnikov regiochemistry question; bifunctional enables chemoselectivity"
    },
    "edge_alkene_HBr_antimarkovnikov": {
      "features": ["terminal_alkene"],
      "why": "terminal alkene makes anti-Markovnikov vs Markovnikov distinction clear in product"
    },
    "edge_alkene_ozonolysis": {
      "features": ["internal_alkene", "symmetric_if_easy_unsymmetric_if_hard"],
      "why": "unsymmetric internal alkene gives two distinct carbonyls — richer question"
    },
    "edge_dihydroxylation_cold": {
      "features": ["cyclic_substrate"],
      "why": "cyclic substrate gives cis-diol with defined stereochemistry"
    }
  },
  "valid_combinations": [
    "alkene + allylic_alcohol (tests selective oxidation)",
    "alkene + carbonyl (tests chemoselectivity: which group reacts first)",
    "alkene + halide (vinyl vs allylic distinction)"
  ],
  "forbidden_combos": [
    "cyclopropyl directly fused to alkene (ring-opening complicates question intent)",
    "too many functional groups (>2 makes chemoselectivity underdetermined)"
  ],
  "chain_length_guidance": "no hardcoded range. C3+ for Markovnikov/anti-Markovnikov (two different products). C4+ for radical halogenation selectivity (multiple allylic positions). C6 cyclic for stereochemistry. Let the first-edge interesting_when drive this."
}
```

---

## 13. Planned: Coverage and Sampling Mechanism

**Status:** Designed (June 2026). Not yet built.
**File to create:** `coverage.py`

### 13.1 Four Independent Counters

```python
coverage = {
    "archetype": {"I": 0, "II": 0, "III": 0, "IV": 0},
    "chapter":   {ch: 0 for ch in ALL_14_CHAPTERS},
    "edge":      {edge_id: 0 for edge_id in ALL_GRAPH_EDGES},
    "concept":   {concept_name: 0 for concept_name in ALL_605_CONCEPTS}
}
```

All persist to `coverage_state.json` after each accepted question.

### 13.2 Sampling Algorithm

```python
def pick_next_target(coverage):
    # Step 0: archetype — hard inverse-frequency
    arch_weights = {k: 1/(v+1) for k,v in coverage["archetype"].items()}
    archetype = weighted_choice(arch_weights)

    # Step 1: chapter — inverse-frequency, filtered to archetype
    eligible = {k: 1/(v+1) for k,v in coverage["chapter"].items()
                if k in CHAPTERS_WITH_SEEDS_FOR[archetype]}
    chapter = weighted_choice(eligible)

    # Step 2: entry edge — zero-count first, then weighted
    candidates = [e for e in graph.edges_for(chapter, archetype)]
    zeros = [e for e in candidates if coverage["edge"][e["id"]] == 0]
    if zeros:
        entry_edge = random.choice(zeros)  # strict coverage sweep
    else:
        edge_weights = {e["id"]: 1/(coverage["edge"][e["id"]]+1) for e in candidates}
        entry_edge = weighted_choice(edge_weights)

    return archetype, chapter, entry_edge
```

### 13.3 The 1/(count+1) Formula

```
count=0  → weight=1.000  (priority — never used yet)
count=1  → weight=0.500
count=5  → weight=0.167
count=10 → weight=0.091
```

No mode switch between "strict coverage" and "weighted random."
After all edges are covered once, zero-count filter passes nothing
and weighted random takes over naturally.

### 13.4 On Acceptance

```python
def on_acceptance(record, coverage, traversal_path_edge_ids):
    coverage["archetype"][record["archetype_code"]] += 1
    coverage["chapter"][record["chapter"]] += 1
    for edge_id in traversal_path_edge_ids:
        coverage["edge"][edge_id] += 1
    for concept in activated_concepts(traversal_path_edge_ids):
        coverage["concept"][concept] += 1
    save_coverage("coverage_state.json")
```

`activated_concepts`: looks up the concept_book fragile_concepts linked to each edge's
chapter and reaction type. Increments those concepts' coverage counters.

---

## 14. File Inventory

### LIVE — Use These

| File | Purpose | Run With |
|------|---------|----------|
| run_live.py | Seed-based pipeline. Full API. reads seeds[0] (hardcoded). | python3 run_live.py |
| run_groundup.py | Ground-up pipeline. Filters TXs. Creates question from scratch. | python3 run_groundup.py |
| blackboard.py | Blackboard class. Imported by run_*.py. | (not a script) |
| meta_tags.py | compute_meta_tags(). Imported by run_*.py. | (not a script) |

### DONE — One-Shot Scripts (Do Not Re-Run)

| File | What It Did | Output |
|------|-------------|--------|
| calibrate_solver_metatags.py | Strong solver on 153 seeds. 4-key rotation. Resume support. | solver_traces.json, updated meta_tag_norm_stats.json, backfilled seeds meta_tags |
| calibrate_thresholds.py | Calibrated 85/60 thresholds from test set. | hardcoded into run_*.py |
| extract_notes.py | Claude vision extraction from teacher notes PDFs. | concept_book.json |

### DEAD — Mock Implementations (Do Not Use)

| File | Why Dead |
|------|----------|
| concept_reasoner.py | MockRLM — no real API call |
| generator.py | MockOpenAI — hardcoded strings |
| strong_solver.py | MockOpenAI — hardcoded score |
| weak_solver.py | MockOpenAI — hardcoded score |
| verifier.py | MockOpenAI — always returns PASS |
| pipeline.py | Calls all the above mocks. Cannot run live. |
| classifier.py | MockOpenAI archetype classifier — seeds already have archetypes |
| evaluate.py | Hardcoded 65/60 thresholds (wrong). External pilot metrics. |

### DATA FILES

| File | Entries | Status |
|------|---------|--------|
| jeeadv_organic_seeds.json | 153 | Complete. All 6 meta-tags calibrated. |
| jic_chemistry_stats.json | 474 | Complete. All years 2013–2025. |
| concept_book.json | 657 TXs / 605 concepts / 502 exc / 470 dist | Complete. |
| meta_tag_norm_stats.json | 4 archetypes × 6 tags | Complete. pending_calibration: []. |
| solver_traces.json | 153 | Complete. trace_words + distractor_count per seed. |
| generated_questions.json | 1 (growing) | Appended on each pipeline run. |

### LIVE — Also Available (Built This Session)

| File | Status |
|------|--------|
| reaction_graph.json | LIVE. 56 nodes, 199 edges, 56% TX coverage (369/657). Built by build_graph.py. |
| graph_traversal.py | LIVE. 5 query functions + GRAPH_TOOLS OpenAI schema. See Section 10.7 for tool specs. |
| reaction_orders.json | LIVE. 14 ordering dimensions with tiers, jee_traps, exceptions. |
| molecule_constructor.json | LIVE. 20 nodes with concrete IUPAC molecule pools and interesting_when annotations. |
| coverage.py | LIVE. Coverage class: weight(), on_acceptance(), save/load, top_saturated(). |
| graph_unmapped.json | REFERENCE. 288 TXs that couldn't be normalized — review to expand graph coverage. |

### Code Fixes Applied This Session

**meta_tags.py bug 1 (FIXED):** Line 144 was `_archetype_stats(ns, archetype_code, "question_length_scope")` × 8.
Now correctly: `_archetype_stats(ns, archetype_code, "model_solution_length")` (reads calibrated values directly).

**meta_tags.py bug 2 (FIXED):** Line 155 was hardcoded `mean=2.0, std=1.5`.
Now correctly: `_archetype_stats(ns, archetype_code, "distractor_plausibility")` (reads per-archetype calibrated values).

**run_live.py (FIXED):** Replaced single-key client with 4-key rotation (same pattern as run_groundup.py).
Keys rotate on 429; exponential backoff when all 4 exhausted. `samba_client()` now called per-request.

---

## 15. Remaining Work

### Priority 1 — Wire Graph Into Generation Pipeline

Replace the full concept_book JSON dump in run_groundup.py's RLM prompt with targeted `get_paths()` calls:
1. Concept Reasoner calls `get_paths(start_node, depth=3, archetype=arch, chapter=ch)` to get a candidate chain.
2. Coverage.weight() scores the candidate. Low-weight → resample start_node.
3. Generator receives the path (nodes + edge reagents) instead of the full concept_book.
4. On acceptance: `coverage.on_acceptance(archetype, chapter, edges, tx_ids)` + `coverage.save()`.

### Priority 2 — Archetype IV (GOC) Generation Path

Separate from graph traversal. Picks a dimension from reaction_orders.json, then:
Pick dimension → pick exception/JEE_trap → instantiate compounds via molecule_constructor.json → Generator → same 3-gate.

### Priority 3 — Expand Reaction Graph Coverage

Current: 56% (369/657 TXs). The 288 unmapped TXs in graph_unmapped.json are recoverable in batches:
- `sec/pri/tert` descriptors (20 TXs): add to leading_class_patterns in build_graph.py
- `acid_deriv` specifics (43 TXs): extend iupac_map with hydroxamic_acid, keto_acid, etc.
- Rearrangement products (3 TXs): add conditions_variants on existing edges manually

---

## 16. Operational Notes and Constraints

```
Working directory:    /Users/admin/dummy 2/
Python command:       python3   (NOT python — does not exist on this Mac)
Shell:                zsh

Reaction Mechanism PDFs (TRAILING SPACE in folder name — required):
  /Users/admin/Downloads/Notes/Reaction Mechanism /
  Access with: ls "/Users/admin/Downloads/Notes/Reaction Mechanism /"

JEE Advanced paper PDFs:
  /Users/admin/Downloads/JEE_Adv_Papers/JEEAdv_{year}_P{1|2}.pdf

JIC Report PDFs:
  /Users/admin/Downloads/JIC_Reports/JIC_{year}.pdf
  Coverage: 2013–2025

Vision policy:
  NO OCR tools anywhere in the pipeline.
  Use Claude vision only: Read tool with file_path + pages parameter for PDFs.
  Explicit user constraint. Do not violate.

IUPAC convention:
  All generated questions must use IUPAC nomenclature throughout.
  This is a design principle, not just a meta-tag.
  Generator prompt explicitly enforces: "Use standard IUPAC nomenclature throughout."

Code transparency:
  Always show code or explain exactly what tools and APIs are being used.
  Never run silent black-box operations.

Concept book notes origin:
  Teacher notes PDFs in /Users/admin/Downloads/Notes/
  Folders: Reaction Mechanism / , GOC, Named Reactions, Isomerism, etc.
  Extracted via Claude vision (Read + pages parameter). No OCR.

Whenever working on this project, read this file first.
```
