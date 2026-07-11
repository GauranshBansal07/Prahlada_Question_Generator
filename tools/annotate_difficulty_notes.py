"""
tools/annotate_difficulty_notes.py

Sweeps all edges missing difficulty_notes in reaction_graph.json and
generates them using DeepSeek V3.2 via OpenRouter.

Output format matches the 10 hand-written examples:
  genuine_levers        — list of specific conditions that create real discrimination
  ez_propagates         — bool: does E/Z geometry survive this step?
  creates_stereocenters — bool or str: does this step create real stereocenters?
  common_traps          — list of named student mistakes for this reaction
  jee_difficulty_axis   — "Structural" / "Interpretive" / both, with specifics

Saves progress incrementally to tools/difficulty_notes_progress.json.
Re-running is safe (skips already-annotated edges).

Usage:
  python3 tools/annotate_difficulty_notes.py
"""

import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")

GRAPH_FILE    = os.path.join(_ROOT, "knowledge", "reaction_graph.json")
PROGRESS_FILE = os.path.join(_HERE, "difficulty_notes_progress.json")

# Load .env
_env = os.path.join(_ROOT, ".env")
if os.path.exists(_env):
    with open(_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from openai import OpenAI

def openrouter_client():
    return OpenAI(
        api_key=os.environ["OPENROUTER_KEY"],
        base_url="https://openrouter.ai/api/v1",
    )

MODEL = "deepseek/deepseek-v3.2"


def api_call(fn, retries=5, base_wait=15):
    for attempt in range(retries):
        try:
            result = fn()
            content = (result.choices[0].message.content or "").strip()
            if not content:
                time.sleep(base_wait * (2 ** attempt))
                continue
            return result
        except Exception as e:
            if "429" in str(e) or "rate_limit" in str(e).lower():
                wait = base_wait * (2 ** attempt)
                print(f"    [rate limit] waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Exhausted retries")


def parse_json(resp) -> dict:
    content = resp.choices[0].message.content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        content = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', content)
        return json.loads(content)


# The 10 reference examples shown verbatim so the model matches the format exactly
REFERENCE_EXAMPLES = """
REFERENCE FORMAT (match exactly):

Edge: alcohol → aldehyde [PCC, CH₂Cl₂]
{
  "genuine_levers": ["PCC stops at aldehyde (primary alcohol); KMnO4 over-oxidises to carboxylic acid — oxidant choice is a genuine lever"],
  "ez_propagates": false,
  "creates_stereocenters": false,
  "common_traps": ["PCC does NOT oxidise secondary alcohols to carboxylic acids (stops at ketone for 2°, aldehyde for 1°)", "Tertiary alcohols are NOT oxidised by PCC — if starting material is tertiary, this step gives no reaction"],
  "jee_difficulty_axis": "Interpretive (oxidant selectivity: PCC vs KMnO4 vs CrO3)"
}

Edge: alkene → alcohol [H₂O, H⁺]
{
  "genuine_levers": ["Markovnikov regiochemistry — compare carbocation stability at each carbon to determine OH placement"],
  "ez_propagates": false,
  "creates_stereocenters": "conditional — only if the two faces of the resulting sp3 carbon bear four different groups; check substituents before asserting chirality",
  "common_traps": ["E/Z of the starting alkene is ERASED by addition — do not invoke it as a difficulty lever or stereochemistry hook", "'No rearrangement' note is gratuitous unless a competing migration is genuinely possible"],
  "jee_difficulty_axis": "Structural (regiochemistry only); Interpretive only if the formed alcohol is genuinely chiral"
}

Edge: alkene → diol [OsO₄, NMO]
{
  "genuine_levers": ["Syn dihydroxylation — both OH added to same face; E-alkene → (R,S) diol (meso); Z-alkene → (R,R)/(S,S) diol (racemic)"],
  "ez_propagates": true,
  "creates_stereocenters": true,
  "common_traps": ["E/Z of starting alkene IS a genuine lever — it determines meso vs chiral diol", "Anti dihydroxylation (Br2/H2O → halohydrin → epoxide → diol) gives the opposite stereochemistry"],
  "jee_difficulty_axis": "Interpretive — one of the few places where E/Z genuinely propagates to stereochemistry"
}

Edge: alkyl_halide → alkene [alc. KOH, heat, E2]
{
  "genuine_levers": ["alc. KOH = E2 elimination → alkene (Zaitsev product unless bulky base)", "aq. KOH = SN2 substitution → alcohol"],
  "ez_propagates": false,
  "creates_stereocenters": false,
  "common_traps": ["alc. KOH gives alkene, NOT carbocation — carbocation is only a transient intermediate in E1", "E2 is anti-periplanar: if substrate is cyclic, product geometry is constrained"],
  "jee_difficulty_axis": "Interpretive (alc. vs aq. KOH discrimination is a classic JEE lever)"
}
"""


def annotate_edge(edge: dict) -> dict:
    prompt = f"""\
You are an expert JEE Advanced organic chemistry educator annotating a reaction knowledge graph.

For the reaction edge below, produce difficulty_notes in EXACTLY the JSON format shown
in the reference examples. No extra fields. No markdown outside the JSON block.

{REFERENCE_EXAMPLES}

NOW ANNOTATE THIS EDGE:
  from:      {edge['from']}
  to:        {edge['to']}
  reagents:  {edge.get('reagents', [])}
  conditions (excerpt): {str(edge.get('conditions', ''))[:600]}
  notes (excerpt):      {str(edge.get('notes', ''))[:400]}
  is_exception: {edge.get('is_exception', False)}
  exception_description: {edge.get('exception_description', 'none')}

Rules:
- genuine_levers: list of strings. Each string names ONE specific condition, reagent choice,
  or substrate feature that, if changed, gives a DIFFERENT product. Be concrete.
- ez_propagates: true ONLY if E/Z geometry in the starting material survives this step
  and determines the product stereochemistry. Most addition/substitution steps: false.
- creates_stereocenters: false, true, or a short string explaining which centres form and under
  what substrate conditions. Do not assert stereocenters that require specific substitution patterns
  without noting the conditionality.
- common_traps: list of named student mistakes. Be specific — name the wrong product or wrong
  mechanism the student would write.
- jee_difficulty_axis: one of "Structural", "Interpretive", or both with a parenthetical
  specifying exactly what skill is tested (reagent selectivity, mechanism choice,
  regiochemistry, stereochemistry).

Return only valid JSON matching the reference format."""

    resp = api_call(lambda: openrouter_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a chemistry educator. Output valid JSON only."},
            {"role": "user",   "content": prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=1024,
        temperature=0.2,
    ))
    return parse_json(resp)


def main():
    with open(GRAPH_FILE) as f:
        graph = json.load(f)
    edges = graph["edges"]

    # Load progress
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            progress = json.load(f)
        print(f"Resuming — {len(progress)} edges already annotated.")
    else:
        progress = {}

    to_annotate = [e for e in edges if not e.get("difficulty_notes")]
    print(f"Edges to annotate: {len(to_annotate)}")
    print()

    for i, edge in enumerate(to_annotate):
        eid = edge["id"]
        if eid in progress:
            continue

        print(f"[{i+1}/{len(to_annotate)}] {eid}  ({edge['from']} → {edge['to']})", end="", flush=True)
        try:
            notes = annotate_edge(edge)
            progress[eid] = notes
            print(f"  ✓")
        except Exception as e:
            print(f"  ERROR: {e}")
            progress[eid] = None

        # Save after every edge
        with open(PROGRESS_FILE, "w") as f:
            json.dump(progress, f, indent=2)

        time.sleep(2)  # stay under rate limit

    # Write back to graph
    edge_map = {e["id"]: e for e in edges}
    applied = 0
    for eid, notes in progress.items():
        if notes and eid in edge_map:
            edge_map[eid]["difficulty_notes"] = notes
            applied += 1

    graph["edges"] = list(edge_map.values())
    with open(GRAPH_FILE, "w") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)

    print(f"\nApplied difficulty_notes to {applied} edges → {GRAPH_FILE}")


if __name__ == "__main__":
    main()
