# Prahlada — JEE Advanced Organic Chemistry Question Generator

## Quick Start

**Prerequisites**
- Python 3.11+
- [Ollama](https://ollama.com/) running locally with `llama3.2` pulled (`ollama pull llama3.2`)
- SambaNova API keys (get them at [cloud.sambanova.ai](https://cloud.sambanova.ai))

**Setup**
```bash
git clone https://github.com/GauranshBansal07/Prahlada_Question_Generator.git
cd Prahlada_Question_Generator
pip install -r requirements.txt
cp .env.example .env
# Edit .env and fill in your SAMBANOVA_KEY_1 through SAMBANOVA_KEY_4
```

**Run the graph-wired pipeline (main entrypoint)**
```bash
python3 run_graph.py
```
Generates one accepted JEE Advanced-level organic chemistry question and saves it to `generated_questions.json`. The pipeline retries up to 3 times; accepted questions must pass a 4-gate filter (verifier PASS + strong solver ≥85% + weak solver ≤60% + reference key confirmed correct).

**Fallback (no graph, uses full concept_book TX dump)**
```bash
python3 run_groundup.py
```

---

## How It Works — End-to-End Workflow

The system has three phases. Phase 0 and 1 are already complete and their outputs are committed to the repo. You only need to run Phase 2.

```
╔══════════════════════════════════════════════════════════════════════╗
║  PHASE 0 — Knowledge Build  (one-time; outputs committed to repo)   ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║  Teacher PDFs (local, not in repo)                                   ║
║         │                                                            ║
║         ▼  tools/extract_notes.py                                   ║
║  knowledge/concept_book.json        ← 657 reaction transforms       ║
║         │                                                            ║
║         ▼  tools/build_graph.py                                     ║
║  knowledge/reaction_graph.json      ← 57 nodes, 199 edges           ║
║  knowledge/graph_unmapped.json      ← diagnostic: 288 unmapped TXs  ║
║                                                                      ║
╠══════════════════════════════════════════════════════════════════════╣
║  PHASE 1 — Calibration  (one-time; outputs committed to repo)        ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║  data/jee_advanced/  (JEE Advanced papers 2012–2025)                 ║
║         │                                                            ║
║         ▼  calibration/calibrate_thresholds.py                      ║
║  Sets STRONG_FLOOR=85, WEAK_CEILING=60 (baked into run_graph.py)    ║
║                                                                      ║
║  data/seeds/jeeadv_organic_seeds.json  (153 real JEE questions)      ║
║         │                                                            ║
║         ▼  calibration/calibrate_solver_metatags.py                 ║
║  calibration/meta_tag_norm_stats.json  ← z-score params per arch.   ║
║                                                                      ║
╠══════════════════════════════════════════════════════════════════════╣
║  PHASE 2 — Generation  (run repeatedly; this is what you run)        ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║  knowledge/reaction_graph.json  ──┐                                  ║
║  knowledge/node_labels.json       │                                  ║
║  knowledge/reaction_orders.json   ├──► run_graph.py  (main)         ║
║  knowledge/qualitative_tests.json │         │                        ║
║  calibration/meta_tag_norm_stats  ┘         │                        ║
║                                             │  uses core/ modules:   ║
║                                             │  graph_traversal.py    ║
║                                             │  coverage.py           ║
║                                             │  blackboard.py         ║
║                                             │  meta_tags.py          ║
║                                             ▼                        ║
║                                    generated_questions.json          ║
║                                    coverage_state.json               ║
║                                                                      ║
║  (run_groundup.py uses concept_book.json instead of reaction_graph   ║
║   and skips graph traversal — same core/ modules, same output files) ║
╚══════════════════════════════════════════════════════════════════════╝
```

**Inside each generation loop** (one accepted question = 1–4 API calls):

```
  coverage.py picks the least-covered edge/chapter/archetype
        │
        ▼
  graph_traversal.py BFS → candidate reaction path (e.g. alkene → alcohol → aldehyde)
        │
        ▼
  Generator  (DeepSeek-V3.2, SambaNova) → writes question + solution
        │
        ▼
  Verifier   (DeepSeek-R1, SambaNova)   → blind-solves, checks chemistry, PASS/FAIL
  Weak Solver (llama3.2, Ollama local)  → score ≤ 60% required (problem is hard enough)
  Strong Solver (Llama-3.1-405B)        → score ≥ 85% required (problem is solvable)
        │
        ▼  all three gates must pass
  meta_tags.py computes 6-axis difficulty label
        │
        ▼
  record appended to generated_questions.json
  coverage_state.json updated
```

---

## Repository Layout

Files are organized by their role in the workflow above.

```
run_graph.py              ← MAIN ENTRYPOINT — run this to generate questions
run_groundup.py           ← FALLBACK ENTRYPOINT — no graph, uses concept_book dump directly
```

### `core/` — Runtime pipeline modules

These are imported by the entrypoints and do the actual generation work. Every file here is active code that runs on each `python3 run_graph.py` call.

| File | What it does in the workflow |
|------|------------------------------|
| `graph_traversal.py` | Loads `reaction_graph.json` and answers queries: BFS paths, node lookup, edge-from-node list. The entrypoint calls `get_paths()` to pick the reaction chain for this run. |
| `coverage.py` | Tracks how many times each edge, chapter, and archetype has been used. Weights the next selection toward least-covered areas. Saves state to `coverage_state.json` after each accepted question. |
| `blackboard.py` | Shared state object passed between all pipeline stages. Accumulates the full attempt history (problem text, scores, verifier feedback) so the generator can "refine in place" on retries without losing context. |
| `meta_tags.py` | Computes the 6-axis difficulty label for each accepted question using z-scores against the calibration corpus. Reads `calibration/meta_tag_norm_stats.json` at runtime. |
| `concept_reasoner.py` | Selects which reaction operators to apply (used by the ground-up fallback path). |
| `generator.py` | Question/solution generation wrapper (modular form; the entrypoints also inline this logic directly). |
| `verifier.py` | Chemical correctness verifier (blind-solve protocol). |
| `strong_solver.py` | Expert-level solver — must score ≥85% for acceptance. |
| `weak_solver.py` | Undergraduate-level solver (Ollama/local) — must score ≤60% for acceptance. |

### `knowledge/` — Structured chemistry knowledge (edit to extend coverage)

These JSON files are the "brain" of the system. Phase 0 tools build them; Phase 2 reads them.

| File | Role | Built by |
|------|------|----------|
| `reaction_graph.json` | 57 nodes (functional group classes) + 199 directed edges (reactions). Each edge carries reagents, conditions, chapter, archetype tags, scope restrictions, and JEE-trap notes. The traversal engine queries this at runtime. | `tools/build_graph.py` (rebuild after edits to concept_book) |
| `node_labels.json` | IUPAC class name, common names, and example molecules for each graph node. Used to instantiate concrete IUPAC molecules in generated questions. | hand-authored |
| `reaction_orders.json` | 14 ordering dimensions (carbocation stability, nucleophilicity, acidity, etc.) with tiers, exceptions, and JEE traps. Used for Archetype IV (GOC) questions. | hand-authored |
| `molecule_constructor.json` | Concrete IUPAC molecule pools per node + "interesting_when" annotations telling the generator which structural features make a reaction chain question-worthy. | hand-authored |
| `qualitative_tests.json` | 20 qualitative chemical tests (Tollens, Fehling, iodoform, carbylamine, Lucas, Victor Meyer, Hinsberg, etc.) with scope, exceptions, and JEE traps. Referenced from node notes; not traversal edges. | `tools/extract_notes.py` + hand-authored |
| `concept_book.json` | 657 reaction transforms + 605 fragile concepts + 502 exceptions + 470 distractors. Source of truth for all chemistry knowledge; the graph is built from this. `run_groundup.py` reads this directly as a fallback. | `tools/extract_notes.py` from teacher notes PDFs |
| `graph_unmapped.json` | Diagnostic file: 288 TXs from concept_book that couldn't be mapped to graph edges during the last `build_graph.py` run. Review this when expanding graph coverage. | `tools/build_graph.py` (auto-generated) |

### `calibration/` — One-time calibration scripts (already ran; don't re-run)

These scripts ran once against the JEE corpus to set numerical thresholds and norm stats. Their outputs are committed. You only need these if you're re-calibrating for a new subject (physics, inorganic).

| File | What it did | Output |
|------|-------------|--------|
| `calibrate_thresholds.py` | Ran strong + weak solvers on real JEE Advanced organic questions. Computed that strong mean ≈ 96%, weak mean ≈ 42%. Recommended STRONG_FLOOR=85, WEAK_CEILING=60. | `calibration_results.json` + threshold values baked into `run_graph.py` |
| `calibrate_solver_metatags.py` | Ran the strong solver on all 153 seeds to measure solution length (word count) and distractor count per archetype. These calibrate the `model_solution_length` and `distractor_plausibility` meta-tags. | `calibration/meta_tag_norm_stats.json`, `calibration/solver_traces.json` |
| `meta_tag_norm_stats.json` | Per-archetype z-score parameters used by `core/meta_tags.py` at runtime. **This file is read every generation run.** | (output of calibrate_solver_metatags.py) |
| `calibration_results.json` | Raw solver scores from threshold calibration. Reference only. | (output of calibrate_thresholds.py) |
| `solver_traces.json` | Per-seed strong solver traces used to compute norm stats. Reference only. | (output of calibrate_solver_metatags.py) |

### `tools/` — Utility scripts (run manually when rebuilding knowledge)

These are not part of the generation loop. Run them when you need to rebuild or extend the knowledge base.

| File | When to run |
|------|-------------|
| `extract_notes.py` | When new teacher notes PDFs arrive. Reads PDFs via Claude vision (no OCR), extracts reaction transforms and concepts, merges into `knowledge/concept_book.json`. |
| `build_graph.py` | After editing `concept_book.json` or adding aliases. Rebuilds `knowledge/reaction_graph.json` and `knowledge/graph_unmapped.json` from scratch. |
| `classifier.py` | Labels raw JEE questions by archetype → writes `data/seeds/classified_seeds.json`. Run when adding new seed questions. |
| `evaluate.py` | Scores a batch of `generated_questions.json` entries for quality metrics. Run manually to audit pipeline output. |
| `make_approach_pdf.py` | Generates an architecture overview PDF for stakeholder review. |

### `data/` — External question corpora (read-only inputs, never modified by the pipeline)

| Folder | Contents |
|--------|----------|
| `jee_advanced/` | JEE Advanced papers 2012–2025 as JSON. Used by `calibrate_thresholds.py`. |
| `seeds/` | Processed seed corpora: `jeeadv_organic_seeds.json` (153 real JEE organic questions, all 6 meta-tags calibrated), `jic_chemistry_stats.json` (474 entries with fragility weights), Arihant question bank. |
| `chapterwise/` | Full chapterwise question banks across all JEE subjects. Reference data. |

### `archive/` — Deprecated; kept for reference

| File | Why archived |
|------|-------------|
| `run_live.py` | Original seed-based pipeline from before the graph was built. Single-seed, no coverage tracking. Superseded by `run_graph.py`. |
| `pipeline.py` | Old augmentation pipeline using mock implementations of all pipeline stages (MockOpenAI — hardcoded scores). Cannot run live. Kept to show the original architecture. |

---

## Models Used

| Role | Model | Provider | Why |
|------|-------|----------|-----|
| Generator | DeepSeek-V3.2 | SambaNova | Strong instruction-following for IUPAC chemistry |
| Verifier | DeepSeek-R1 | SambaNova | Reasoning model — blind-solves before checking candidate |
| Strong Solver | Meta-Llama-3.1-405B-Instruct | SambaNova | Different model family from generator (avoids correlated errors); very large |
| Weak Solver | llama3.2 | Ollama (local) | Free, no rate limits, represents undergraduate ability |

All SambaNova calls use 4-key rotation — on a 429 rate limit, the client rotates to the next key before waiting.

---

## Full Architecture Reference

> Technical deep-dive for contributors. The sections below document exact schemas, calibration numbers, known issues, and design decisions made during the June 2026 build session.

---

## Table of Contents

1. System Purpose and Goals
2. Pipeline Overview
3. Component: run_groundup.py — Full Code Detail
4. Component: blackboard.py
5. Component: meta_tags.py
6. Data Files — Exact Schemas
   - jeeadv_organic_seeds.json
   - concept_book.json
   - meta_tag_norm_stats.json
   - generated_questions.json
7. API Configuration and Rate Limits
8. Calibrated Thresholds
9. Reaction Knowledge Graph — Design Reference
   - Node Schema
   - Edge Schema (conditions_variants + chemoselectivity)
   - Traversal State
10. Orders JSON
11. Molecule Constructor
12. Coverage and Sampling
13. Pending Work

---

## 1. System Purpose and Goals

Prahlada generates JEE Advanced-level organic chemistry questions programmatically.
The system is not a fine-tuner and not a simple prompt wrapper — it is a pipeline that:
- Constructs a question attempt along a valid reaction graph path
- Validates it chemically (verifier, blind-solve protocol)
- Confirms it is solvable by an expert (strong solver ≥ 85%)
- Confirms it is hard enough to challenge a non-expert (weak solver ≤ 60%)
- Labels it with 6 calibrated difficulty meta-tags
- Saves the full attempt history for every accepted question

Target exam: JEE Advanced organic chemistry (Paper 1 and Paper 2).
Seed corpus: 153 real JEE Advanced organic questions from 2013–2025.

---

## 2. Pipeline Overview

```
GRAPH-WIRED (run_graph.py):
  coverage.py picks target: archetype + chapter + entry edge (inverse-frequency weighting)
  ↓
  graph_traversal.py BFS → candidate path (3–5 edges)
  ↓
  Blackboard(path, archetype, target_profile)
  ↓
  Loop (max 4 attempts: 1 initial + 3 retries):
    │
    ├── Generator (DeepSeek-V3.2, temp=0.7)
    │     attempt 1: creates question from reaction path
    │     attempt 2+: "REFINE IN PLACE" — fixes only what verifier flagged
    │
    ├── Verifier (DeepSeek-R1, temp=0.0)
    │     blind-solve protocol: solves independently, then compares to candidate
    │     writes: verdict (PASS/FAIL), semantic_flaws, feedback_for_generator
    │
    ├── Weak Solver (llama3.2/Ollama, temp=0.7)
    │     score ≤ 60% required
    │
    ├── Strong Solver (Meta-Llama-3.1-405B, temp=0.0)
    │     score ≥ 85% required
    │
    └── 4-Gate Check:
          verifier.verdict == "PASS"
          strong.score >= 85
          weak.score <= 60
          reference_correct == True
          ↓ ALL PASS:
            compute_meta_tags() → build output record → append to generated_questions.json
            coverage.on_acceptance() → save coverage_state.json
          ↓ ANY FAIL:
            blackboard.record_attempt() → loop continues

GROUND-UP FALLBACK (run_groundup.py):
  No graph. Topic anchor only (chapter + archetype).
  Filter concept_book.json to chapter + archetype subset (e.g. 110 TXs for Hydrocarbons/I).
  Concept Reasoner selects 3-4 TX ids → chain description.
  Generator creates question FROM SCRATCH (no graph constraints).
  Same verifier + solver + 4-gate flow.
  Demonstrated: Hydrocarbons / Archetype I → accepted on attempt 4.
```

---

## 3. Component: run_groundup.py — Full Code Detail

**Entry point:** `python3 run_groundup.py`
**Status:** LIVE. Demonstrated successfully.

### 4-key SambaNova rotation

```python
SAMBANOVA_KEYS = [os.environ.get("SAMBANOVA_KEY_1",""), ...]  # loaded from .env
_key_idx = 0

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

### Filtered concept book — not the full dump

```python
HYDRO_TERMS = re.compile(r'\b(alkan|alken|alkyn|cycloalk|methane|...|hydroboration)\b', re.IGNORECASE)

def filter_hydrocarbon_txs(concept_book):
    txs = concept_book["structural_operators"]["add_reaction_step"]["valid_transformations"]
    return [tx for tx in txs
            if "I" in tx.get("archetype", [])
            and HYDRO_TERMS.search(f"{tx['from']} {tx['to']} {' '.join(tx['reagents'])}")]
    # Result: 110 TXs from 657 total
```

### Demonstrated Run (Hydrocarbons / Archetype I)

```
Attempt 1: propene → allyl chloride → allyl alcohol → glycerol → propane
  Verifier: PASS, Intermediate
  Weak: 0% ✓    Strong: 70% ✗  (below 85 floor) → Refine

Attempt 2: terminal alkyne → amine → allyl chloride → cyclobutyl ring expansion
  Verifier: FAIL — cyclobutyl chemistry not supported by the chain; 4 specific flaws
  Weak: 0% ✓    Strong: 65% ✗ → Refine

Attempt 3: alkane → alkyl halide → anti-Markovnikov → acetylide → trans-alkene
  Verifier: FAIL — NaNH2/NH3 on tertiary bromide gives elimination not alkyne formation
  Weak: 0% ✓    Strong: 70% ✗ → Refine

Attempt 4: propene → allyl chloride → allyl alcohol → glycerol → propane (refined)
  Verifier: PASS, Intermediate
  Weak: 0% ✓    Strong: 100% ✓  → ACCEPTED

  Meta-tags: question_length_scope=5, semantic_obfuscation=2, conceptual_fragility=3,
             number_of_exceptions=3, model_solution_length=2, distractor_plausibility=1
```

Attempts 2 and 3 show exactly why the knowledge graph is needed: the model invented
chemistry not supported by the selected TXs. Graph traversal prevents this — each hop
is a valid edge by construction.

---

## 4. Component: blackboard.py

**Location:** `core/blackboard.py`

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

    def operators_tried(self) -> list:
        """Deduplicated flat list of all operators used across all attempts."""

    def last_attempt(self) -> dict | None:
        """Returns self.attempts[-1] or None."""

    def context_summary(self) -> str:
        """One-line-per-attempt summary for Generator prompt on retries.
           Format: 'Attempt N: Verdict=X | Weak=Y% | Strong=Z% | Operators=[...] | Feedback=...'"""

    def history(self) -> list:
        """Returns full self.attempts list. Saved verbatim in output record."""
```

---

## 5. Component: meta_tags.py

**Location:** `core/meta_tags.py`

### compute_meta_tags() — Public API

```python
def compute_meta_tags(
    question_text: str,
    archetype_code: str,        # "I" | "II" | "III" | "IV"
    solver_trace: str = None,   # strong solver's attempted_solution field
    fragility_weight: float = None,  # 1 - (pct_full_marks/100); None for ground-up
) -> dict:
    # Returns: 6 keys, values 1-5 integers
```

### Z-score Formula

```python
def _zscore_to_15(z: float) -> int:
    return max(1, min(5, round(z * 1.5 + 3)))
# z=0 (corpus mean) → 3 | z=+1 → 5 | z=-1 → 1
```

### Six Tags

| Tag | Raw Signal | Normalization |
|-----|-----------|---------------|
| `question_length_scope` | word count of question_text | per-archetype from norm_stats |
| `semantic_obfuscation` | IUPAC token density (IUPAC words / total words) | global |
| `conceptual_fragility` | fragility_weight = 1 − (pct_full_marks/100) from JIC data | per-archetype |
| `number_of_exceptions` | exception/rejection keyword count in solver_trace | per-archetype |
| `model_solution_length` | word count of solver_trace | per-archetype (from solver_traces.json) |
| `distractor_plausibility` | explicit path-rejection phrase count in solver_trace | per-archetype (from solver_traces.json) |

---

## 6. Data Files — Exact Schemas

### jeeadv_organic_seeds.json

**Path:** `data/seeds/jeeadv_organic_seeds.json`
**Entries:** 153 | All 6 meta-tags calibrated.

```json
{
  "question_index":    "JADV_2023_P1_C5",
  "year":              2023,
  "paper":             "P1",
  "topic":             "Organic Chemistry",
  "chapter":           "Aromatic Compounds",
  "archetype":         "Long Reaction Chains",
  "archetype_code":    "I",
  "question_text":     "...",
  "pct_full_marks":    34.2,
  "fragility_weight":  0.658,
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

### concept_book.json

**Path:** `knowledge/concept_book.json`

```json
{
  "structural_operators": {
    "add_reaction_step":       { "valid_transformations":  [ ...657 TX entries... ] },
    "expand_comparison_set":   { "valid_modifications":   [ ...470 entries... ] }
  },
  "interpretive_operators": {
    "conceptual_fragility":    { "concepts":   [ ...605 entries... ] },
    "number_of_exceptions":    { "exceptions": [ ...502 entries... ] },
    "semantic_obfuscation":    { "rules":      [ ...5 entries... ] },
    "distractor_plausibility": { "distractors":[ ...470 entries... ] }
  }
}
```

Each TX entry:
```json
{
  "from":      "alkene",
  "to":        "alkyl_halide",
  "reagents":  ["HBr", "peroxides"],
  "archetype": ["I", "III"],
  "conditions":"anti-Markovnikov addition; radical mechanism...",
  "notes":     "archetype I: include in chain; archetype III: ask why peroxides invert regiochemistry",
  "meta_tags": { "question_length_scope":3, "semantic_obfuscation":2, "conceptual_fragility":4,
                 "number_of_exceptions":5, "model_solution_length":3, "distractor_plausibility":4 }
}
```

### meta_tag_norm_stats.json

**Path:** `calibration/meta_tag_norm_stats.json`

```json
{
  "global": {
    "semantic_obfuscation": { "mean": 0.187, "std": 0.063, "metric": "iupac_token_density" }
  },
  "per_archetype": {
    "I":   {
      "question_length_scope":   { "mean": 82.3,  "std": 31.4  },
      "conceptual_fragility":    { "mean": 0.653,  "std": 0.142 },
      "number_of_exceptions":    { "mean": 4.1,    "std": 2.8   },
      "model_solution_length":   { "mean": 1338,   "std": 1087  },
      "distractor_plausibility": { "mean": 0.85,   "std": 1.16  }
    },
    "II":  { ... },
    "III": { ... },
    "IV":  { ... }
  }
}
```

Sample sizes for calibration: I=34, II=18, III=29, IV=10.

### generated_questions.json

Output file appended on each accepted question:
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
  "loops_run":            4,
  "strong_score":         100,
  "weak_score":           0,
  "verifier_verdict":     "PASS",
  "meta_tags": { "question_length_scope":5, "semantic_obfuscation":2, ... },
  "attempt_history":      [ ...all attempt records from blackboard... ],
  "generated_at":         "2026-06-22T14:35:21+00:00"
}]
```

---

## 7. API Configuration and Rate Limits

### SambaNova

```
Base URL:   https://api.sambanova.ai/v1
SDK:        openai.OpenAI (drop-in compatible)
Keys:       SAMBANOVA_KEY_1 through SAMBANOVA_KEY_4 (set in .env)

Rate limit: ~37–40 calls/day per key (free tier, empirically observed)
Reset: approximately midnight UTC

Key rotation:
  On 429: rotate to next key, sleep 2s, retry immediately.
  If all 4 keys exhausted: sleep base_wait × 2^(attempt // n_keys) seconds.
  base_wait = 65s. Max cycles = 3 × 4 = 12 total attempts.

Per-role temperatures:
  Generator:     0.7
  Verifier:      0.0  ← deterministic
  Strong Solver: 0.0  ← deterministic
  Weak Solver:   0.7  (Ollama, not SambaNova)
```

### Ollama (Weak Solver Only)

```
Base URL:   http://localhost:11434/v1
Model:      llama3.2  (pull with: ollama pull llama3.2)
API key:    "ollama" (literal string, required by SDK)
No rate limits. No retry handler needed.
Must be running: check with curl http://localhost:11434
```

---

## 8. Calibrated Thresholds

```
STRONG_FLOOR = 85   (strong solver mean 96.4% − 1σ 12.1% ≈ 85)
WEAK_CEILING = 60   (weak solver mean 41.7% + 0.5σ 24.6% ≈ 60, conservatively)
MAX_RETRIES  = 3    (4 total attempts per question)
```

---

## 9. Reaction Knowledge Graph — Design Reference

### Node Schema

```json
{
  "id": "alkene",
  "type": "stable",
  "can_be_start": true,
  "can_be_end": true,
  "description": "Compound class with C=C double bond"
}
```

`type: "stable"` — persistent compound class; can be question start/end.
`type: "intermediate"` — carbocation, carbanion, etc.; `can_be_start: false`, `can_be_end: false`.

### Edge Schema — conditions_variants + chemoselectivity

```json
{
  "id": "alkene_HBr_markovnikov",
  "from": "alkene",
  "to": "alkyl_halide",
  "reagents": ["HBr"],
  "conditions": "electrophilic addition; Markovnikov; via carbocation intermediate",
  "firing_condition": null,
  "chapter": "Hydrocarbons",
  "archetype": ["I", "III"],
  "chemoselectivity": {
    "requires_absent": [],
    "node_redirect": {},
    "priority_ref": null
  },
  "tx_ids": [42],
  "meta_tags": { "question_length_scope":3, ... },
  "notes": "EXCEPTION: with peroxides, anti-Markovnikov via radical mechanism"
}
```

**`conditions_variants`**: same reaction, conditions change destination. Used for:
- HBr ± peroxides (Markovnikov vs anti-Markovnikov)
- KMnO₄ cold dilute vs hot concentrated (dihydroxylation vs cleavage)
- E2 small base (Zaitsev) vs bulky base (Hofmann)

**`chemoselectivity.node_redirect`**: if the substrate has a second functional group, the traversal engine redirects to that group's product instead (e.g. LiAlH₄ reduces ketone before alkene).

### Traversal State

```json
{
  "current_node": "alkene",
  "also_present": ["carbonyl"],
  "path_so_far": [
    {
      "edge_id": "alkane_radical_bromination",
      "destination_node": "alkyl_halide"
    }
  ],
  "instantiated_as": "2-methylbut-2-ene"
}
```

---

## 10. Orders JSON

**Path:** `knowledge/reaction_orders.json`

14 ordering dimensions with tiers, exceptions, JEE traps:

```json
{
  "dimension_id": "carbocation_stability",
  "general_rule": "tertiary > secondary > primary > methyl",
  "tiers": [ { "rank":1, "compounds":["tertiary", "benzylic", "allylic secondary"], ... } ],
  "exceptions": [
    {
      "condition": "adjacent to aromatic ring",
      "modified_order": "benzylic > tertiary",
      "jee_trap": "students apply simple alkyl order and miss benzylic resonance"
    }
  ]
}
```

Dimensions covered: carbocation stability, carbanion stability, radical stability, acidity,
basicity, nucleophilicity (polar protic), nucleophilicity (polar aprotic — INVERTED, key JEE trap),
electrophilicity, leaving group ability, SN1/SN2 preference, EAS reactivity, EAS regioselectivity,
oxidizing agent selectivity, migration aptitude.

---

## 11. Molecule Constructor

**Path:** `knowledge/molecule_constructor.json`

Defines what structural features make a given starting node + first edge combination question-worthy:

```json
{
  "node": "alkene",
  "interesting_when": {
    "edge_alkene_HBr": {
      "features": ["trisubstituted", "has_second_functional_group"],
      "why": "trisubstituted forces Markovnikov regiochemistry question"
    }
  },
  "valid_combinations": ["alkene + carbonyl (tests chemoselectivity)"],
  "forbidden_combos": ["too many functional groups (>2 makes chemoselectivity underdetermined)"]
}
```

---

## 12. Coverage and Sampling

**Location:** `core/coverage.py`

Four independent inverse-frequency counters:
```python
coverage = {
    "archetype": {"I": 0, "II": 0, "III": 0, "IV": 0},
    "chapter":   {ch: 0 for ch in ALL_14_CHAPTERS},
    "edge":      {edge_id: 0 for edge_id in ALL_GRAPH_EDGES},
    "concept":   {concept_name: 0 for concept_name in ALL_605_CONCEPTS}
}
```

Weight formula: `1/(count+1)` — count=0 → weight=1.0, count=10 → weight=0.09.

Edges with zero coverage are strictly prioritized until all edges are visited once; then pure weighted sampling.

---

## 13. Pending Work

### Priority 1 — Wire Graph Coverage Into run_graph.py

Replace the fixed-seed selection with `coverage.py` weight-driven selection:
1. `coverage.pick_next_target()` → archetype + chapter + entry edge
2. `graph_traversal.get_paths()` → candidate chain
3. On acceptance: `coverage.on_acceptance()` → `coverage.save()`

### Priority 2 — Archetype IV (GOC) Generation Path

Separate from graph traversal. Pick a dimension from `reaction_orders.json`, pick an exception, instantiate compounds, generate comparison question. Same 4-gate filter applies.

### Priority 3 — Expand Graph Coverage

Current: ~57% TX coverage. The 288 unmapped TXs in `knowledge/graph_unmapped.json` are recoverable:
- Additional class-level aliases in `tools/build_graph.py` (~60 TXs)
- Qualitative test outcomes → encode as notes in `knowledge/qualitative_tests.json` (~150 TXs)
- Remaining carbocation rearrangement intermediates → new nodes (~20 TXs)

### Extending to Physics / Inorganic

The calibration scripts (`calibrate_thresholds.py`, `calibrate_solver_metatags.py`) are subject-independent — re-run them on physics/inorganic seeds to get new norm stats. The graph, concept_book, and node_labels are subject-specific and would need new files.

---

## Operational Notes

```
Python command:   python3  (NOT python — does not exist on this Mac)
Shell:            zsh

Vision policy:
  NO OCR tools anywhere in the pipeline.
  Use Claude vision only (Read tool with pages parameter for PDFs).

IUPAC convention:
  All generated questions use IUPAC nomenclature throughout.
  Generator prompt enforces: "Use standard IUPAC nomenclature throughout."

Code transparency:
  Always show code or explain exactly what tools and APIs are being used.
```
