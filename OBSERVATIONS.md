# Pipeline Observations — Research Paper Log

> Running record of what we observed, what broke, what we changed, and why.
> Structured for use in the Methods and Discussion sections of the research paper.

---

## 1. Phantom Stereocenter Failure Mode

**Observed:** First accepted question (path: alkene→alcohol→alkyl_halide→Grignard) told the student
to "consider stereochemistry at C3 of 3-methylpentan-3-ol." The final answer was chemically correct
but the premise was false: C3 has two identical ethyl substituents and is NOT a stereocenter.

**Why it passed the answer-correctness gate:** The verifier checks whether the final product formula
and functional-group assignments are correct. A correct answer can sit on top of a broken reasoning
scaffold. Answer-correctness gating is necessary but not sufficient.

**Fix:** Added `core/validators.py` with `check_stereocenter_claims()`. After verifier PASS, the
pipeline runs RDKit `FindMolChiralCenters()` on each SMILES in the generator's `smiles_trace` field.
If the problem invokes stereochemistry language (racemic, R/S, chiral, etc.) but no intermediate
has a real stereocenter, the question is rejected and the failure is fed back to the generator.

**Implication for paper:** Demonstrates that a two-gate pipeline (generation + answer-correctness
verification) is insufficient for premise validity. A structural verification layer over the
reasoning scaffold — not just the answer — is needed.

---

## 2. Decorative E/Z Flag

**Observed:** Same accepted question used an (E)-alkene starting material but Step 1 was acid-
catalysed hydration, which adds across the double bond and destroys the geometry. The E/Z label
carried zero information to the final answer; it was a decorative difficulty signal.

**Fix:** Added `check_ez_propagation()` in `core/validators.py`. Detects E/Z descriptor in the
problem text; if Step 1 matches an addition/hydrogenation/ozonolysis reaction pattern, and the
solution does not reference E/Z in its conclusion, the question is rejected.

**Implication for paper:** Genuine difficulty should come from information that propagates through
the reaction chain to affect the final answer. Decorative complexity markers inflate apparent
difficulty without exercising real reasoning.

---

## 3. Mechanistic Incoherence (Pyridine + SN1)

**Observed:** Same question described the SOCl₂/pyridine step as proceeding via SN1. Pyridine acts
as a base that captures the chlorosulfite intermediate and forces **backside attack (SN2 with
inversion)**. Claiming SN1 in the same breath is a direct contradiction.

**Fix:** Added `check_mechanistic_coherence()` regex check: `pyridine.*SN1` or `SN1.*pyridine`
in the combined problem+solution text → hard reject.

**Implication for paper:** LLMs generating chemistry problems reproduce textbook terminology but
can combine incompatible mechanistic descriptors in a single step. Mechanistic incoherence is
invisible to formula-balance or functional-group checks; it requires knowledge-level pattern
matching.

---

## 4. Gratuitous "No Rearrangement" Condition

**Observed:** The same question included "assume no carbocation rearrangement" for a tertiary
carbocation that was already at maximum stability. A hydride or alkyl shift would only decrease
stability; the condition does no chemical work.

**Fix:** Added `check_gratuitous_conditions()`: flags "no rearrangement" when the context mentions
a tertiary carbocation (already stable → rearrangement suppressed naturally).

**Implication for paper:** Gratuitous conditions make questions longer without adding difficulty;
they may signal to students that rearrangement was considered, which adds false cognitive load.

---

## 5. Reaction Graph Errors (11 Edge Corrections)

**Observed during runs:** The verifier flagged several factually incorrect edges during live
generation — these only became visible when the generator sampled those paths and the verifier
rejected the resulting questions:

| Edge | Wrong `to` field | Corrected to |
|---|---|---|
| alkyne ozonolysis [O₃, Zn] | ketone | carboxylic_acid |
| alkyl_halide + O₂/hν | alcohol | alkyl_peroxide |
| RLi + R'X metal-halogen exchange | — | corrected product node |
| DMSO Kornblum oxidation | — | corrected product node |
| Iodoform reaction | CHI₃ (incorrect node) | iodoform (correct) |
| Bakelite formation | — | corrected node |
| Cannizzaro reaction | — | both product nodes |
| E2 elimination product | — | alkene node |
| Sulfonation product | — | sulfonic_acid |
| LAH reduction | — | corrected for ester vs aldehyde substrates |
| Carbocation vs alkene rearrangement | — | corrected |

**Decision:** All 199 edges were preserved (none removed). Corrections were made in-place.

**Implication for paper:** A reaction graph built from human-authored data still contains errors
that only surface when downstream generators are forced to construct coherent multi-step paths.
Live pipeline runs are an effective knowledge-graph audit mechanism.

---

## 6. Weak Solver Calibration Problem

**Observed:** First weak solver was llama3.2 (3B, local Ollama). It scored 0% on every organic
chemistry question regardless of difficulty. This makes the difficulty discrimination gate
degenerate — every question clears the weak ceiling (≤60%) trivially.

**Root cause:** A 3B parameter model has insufficient chemical knowledge to attempt any step of
multi-step organic synthesis. It provides no differential signal.

**Attempted:** nousresearch/hermes-3-llama-3.1-405b:free — rate-limited to the point of
unusability on the free tier.

**Attempted:** nvidia/nemotron-3-ultra-550b-a55b:free — returns empty content; does not support
`response_format={"type": "json_object"}`.

**Fix:** Switched to `meta-llama/llama-3.3-70b-instruct:free` with a constrained student persona:
"JEE student who knows standard reactions from NCERT and one coaching module, but has not drilled
multi-step advanced problems." Observed score of 20% on the cycloalkene→diol→dihalide path — a
meaningful signal (strong solver = 100%, weak = 20%, verifier PASS).

**Implication for paper:** Weak solver calibration is non-trivial. The model must be capable enough
to solve simple reactions (otherwise ≤60% ceiling is vacuous) but not so capable that it solves
everything (otherwise the ceiling distinguishes nothing). We use persona-constrained prompting as
a lightweight substitute for finding a model at exactly the right capability level.

---

## 7. Meta-Tag Computation — No Extra LLM Calls

**Question raised:** Which model computes meta-tags?

**Answer:** All 6 meta-tags in `core/meta_tags.py` are computed by deterministic heuristics — no
additional LLM calls are made:

- Tags 1–3 (Semantic Density, Step Count, Conceptual Fragility): regex and word-count on the
  problem text itself.
- Tags 4–6 (Reasoning Depth, Multi-Step Awareness, Mechanistic Precision): regex keyword counts on
  the **strong solver's reasoning trace**, which is already fetched as a pipeline gate output.

This means meta-tags are essentially free — they piggyback on existing API calls.

**Implication for paper:** The pipeline's meta-tag layer adds zero marginal cost because it recycles
the strong solver's chain-of-thought output rather than making fresh inference calls.

---

## 8. Empty-Content Retry Pattern

**Observed:** Several OpenRouter models (nemotron especially, but also llama-4-maverick under rate
pressure) returned HTTP 200 with an empty `choices[0].message.content`. This is distinct from a
`429 Too Many Requests` error — it does not raise an exception, it silently returns empty.

**Fix:** `api_call()` now checks for empty content after each attempt and applies exponential backoff
before retrying, treating empty response the same as rate-limiting.

**Implication for paper:** Free-tier LLM APIs exhibit silent empty-response degradation that is
invisible to standard exception handling. Robust pipelines must check content presence explicitly.

---

## 9. JSON Escape Errors in Chemistry Formulas

**Observed:** JSONDecodeError: `Invalid \\escape` — the generator included raw LaTeX-style chemical
formulas (`\C`, `\H`, `\Delta`) in JSON string fields, which are not valid JSON escape sequences.

**Fix:** `_parse_json_response()` applies `re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', content)` before
parsing — escapes any backslash not already part of a valid JSON escape sequence.

**Implication for paper:** Chemistry domain JSON generation introduces a systematic character-level
failure mode absent in general text generation. Domain-specific post-processing is necessary.

---

## 10. Edge Difficulty Encoding vs Opaque Meta-Tags

**Observed:** Original pipeline put numeric difficulty scores on edges (`conceptual_fragility: 3`).
The generator received these numbers as context but had no way to act on them — it cannot know
what makes fragility = 3 versus fragility = 2 without a definition.

**Fix:** Replaced numeric scores with `difficulty_notes` objects on 10 key edges, containing:
- `genuine_levers`: list of specific conditions the generator should exploit
- `ez_propagates`: bool — does E/Z geometry in the starting material affect the final answer?
- `creates_stereocenters`: bool/string — does this reaction create real stereocenters?
- `common_traps`: list of mistakes students make on this transformation
- `jee_difficulty_axis`: which JEE examination skill this step tests

These are injected into the generator prompt as "PER-STEP DIFFICULTY GUIDANCE."

**Implication for paper:** Difficulty annotations on a knowledge graph are only useful to a
generator if they are expressed in actionable natural language. Numeric meta-scores are
information-theoretically deficient for prompt-based generation.

---

## 11. Verdict String Format

**Observed:** Pipeline checked `ver["verdict"] == "PASS"` but the model consistently returned
`"PASS — formula balances, no stereochemistry errors..."` with an explanation appended.

**Fix:** All verdict checks changed to `startswith("PASS")` / `not ver["verdict"].startswith("PASS")`.

**Implication for paper:** Small string format assumptions in evaluation gates can cause systematic
false failures — questions that actually passed all checks were rejected because the verdict
comparison was too strict.

---

---

## 12. number_of_exceptions Meta-Tag Was Broken

**Observed:** `number_of_exceptions` was computed by counting regex keyword matches (however,
rejected, ruled out, anomalous, etc.) in the strong solver's reasoning trace. This is wrong for
two reasons:

1. The norm stats were fit on this proxy and had mean=0.03, std=0.25 — every generated question
   scored 3 (the z-score mean fallback) regardless of how many exceptions appeared on the path.
2. The solver's reasoning trace may use rejection language when exploring alternatives, which is
   not the same as the question actually involving a chemical exception.

**Fix:** `extract_exception_count()` now takes `path_edges` (the list of TX dicts sampled from the
graph) and counts edges where `is_exception=True`. This is the correct source of truth — `is_exception`
was assigned by `link_exceptions.py` using named-reagent matching against the 502 exceptions in
`concept_book.json`.

**Calibration:** New norm stats derived from 10,000 random walks through the expanded graph
(390 edges, 43.8% exception rate): mean=0.86, std=1.23. Scale: 0 exceptions→tag 2, 1→3, 2→4, 3+→5.

**Implication for paper:** Difficulty meta-tags that use solver traces as proxies for graph-level
properties are doubly unreliable — the proxy is noisy AND the calibration corpus mirrors the noise.
Sourcing each meta-tag from its canonical ground truth (graph structure, IUPAC density, actual
concept_book data) is strictly preferable even when the proxy is cheaper.

---

## 13. Graph Recovery: 288 Unmapped TXs

**Observed:** 288 of 657 transformations in `concept_book.json` failed normalization during graph
construction. They were stored in `graph_unmapped.json`. Investigation showed the failures were
mostly label format issues (e.g., "propene (CH₂=CH-CH₃)" didn't match canonical "alkene") rather
than genuinely exotic chemistry.

**Fix:** `tools/recover_unmapped.py` used DeepSeek V3.2 to map each unmapped TX's raw labels to
canonical node IDs. Result: 199 edges recovered, 89 skipped (test results, indicators, no-reaction,
exotic intermediates), 34 new nodes auto-added (24 legitimate, 10 garbage — later removed).

**Graph before:** 199 edges, 57 nodes.
**Graph after:** 390 edges, 81 nodes — nearly doubled.

**Implication for paper:** Strict string-matching during knowledge-graph construction discards a
substantial fraction of source data. An LLM-assisted normalization pass recovers most of it with
low error rate, since the underlying chemistry is standard — only the label format differs.

---

## Pipeline State as of 2026-07-11

| Role | Model |
|---|---|
| Generator | deepseek/deepseek-v3.2 |
| Verifier | deepseek/deepseek-v3.2 |
| Strong Solver | meta-llama/llama-4-maverick |
| Weak Solver | meta-llama/llama-3.3-70b-instruct:free |

| Gate | Threshold |
|---|---|
| Verifier | PASS (6-check suite) |
| Premise Validators | All pass (RDKit + regex) |
| Strong solver | ≥ 85% |
| Weak solver | ≤ 60% |
| Reference correct | True |

**Acceptance rate observed so far:** 1 question accepted across ~10 attempts (estimate; exact
count in `blackboard.json`). Rate limited frequently on the free tier; retry logic absorbs most
of it.
