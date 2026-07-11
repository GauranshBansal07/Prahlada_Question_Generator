"""
tools/recover_unmapped.py

Uses DeepSeek to normalize the 288 unmapped TXs in graph_unmapped.json
back to canonical node IDs, recovering them as graph edges.

For each TX it decides:
  - from_node: canonical node ID, or "NEW:<name>" if genuinely new node needed
  - to_node:   canonical node ID, "NEW:<name>", or "NO_REACTION"
  - firing_condition: substrate class constraint if applicable, else null
  - skip: true if this TX is a test result, indicator, or too niche to be useful

Saves progress to tools/recover_progress.json (resume-safe).
Writes recovered edges into reaction_graph.json and new nodes where needed.
"""

import json, os, re, time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")

UNMAPPED_FILE  = os.path.join(_ROOT, "knowledge", "graph_unmapped.json")
GRAPH_FILE     = os.path.join(_ROOT, "knowledge", "reaction_graph.json")
CBOOK_FILE     = os.path.join(_ROOT, "knowledge", "concept_book.json")
PROGRESS_FILE  = os.path.join(_HERE, "recover_progress.json")

_env = os.path.join(_ROOT, ".env")
if os.path.exists(_env):
    with open(_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from openai import OpenAI

def client():
    return OpenAI(api_key=os.environ["OPENROUTER_KEY"],
                  base_url="https://openrouter.ai/api/v1")

MODEL = "deepseek/deepseek-v3.2"

def api_call(fn, retries=5, base_wait=15):
    for attempt in range(retries):
        try:
            r = fn()
            content = (r.choices[0].message.content or "").strip()
            if not content:
                time.sleep(base_wait * (2**attempt)); continue
            return r
        except Exception as e:
            if "429" in str(e) or "rate_limit" in str(e).lower():
                time.sleep(base_wait * (2**attempt))
            else:
                raise
    raise RuntimeError("Exhausted retries")

def parse_json(resp):
    c = resp.choices[0].message.content.strip()
    if c.startswith("```"):
        c = re.sub(r"^```(?:json)?\s*", "", c)
        c = re.sub(r"\s*```$", "", c)
        c = c.strip()
    try:
        return json.loads(c)
    except json.JSONDecodeError:
        c = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', c)
        return json.loads(c)


CANONICAL_NODES = [
    "acetal","acetylide_ion","acid_chloride","alcohol","aldehyde",
    "alkane","alkene","alkyl_halide","alkyne","amide","amine","amino_acid",
    "anhydride","aniline","aryl_halide","azo_compound","benzene","bromonium_ion",
    "carbanion","carbene","carbocation","carbohydrate","carboxylic_acid",
    "cyanohydrin","cycloalkane","cycloalkene","diazonium_salt","diene_conjugated",
    "diene_isolated","diol","enol","enolate","epoxide","ester","ether",
    "geminal_dihalide","grignard","halohydrin","hydrazone","imine","isocyanate",
    "isocyanide","ketone","lactam","lactone","naphthalene","nitrile",
    "nitro_compound","organolithium","oxime","phenol","polymer","radical",
    "substituted_benzene","sulfonate","vicinal_dihalide","vinyl_halide",
]

NODES_BLOCK = "\n".join(f"  - {n}" for n in sorted(CANONICAL_NODES))


def normalize_tx(tx: dict) -> dict:
    prompt = f"""You are an expert organic chemist normalizing reaction descriptions into
a canonical knowledge graph.

CANONICAL NODE LIST (use these IDs exactly):
{NODES_BLOCK}

TASK: Map the raw from/to labels below to canonical node IDs.

Rules:
1. If the from_raw or to_raw clearly corresponds to a canonical node, use that node ID.
   Examples:
     "propene (CH2=CH-CH3)"         → "alkene"
     "isocyanide (R-NC)"            → "isocyanide"
     "alkene (Hofmann, E1cb)"       → "alkene"
     "primary amine (Gabriel)"      → "amine"  (with firing_condition "gabriel_phthalimide")
     "R-Cl (retention, SNi)"        → "alkyl_halide"  (with firing_condition "SNi_retention")
     "fat/oil (triglyceride)"       → "ester"  (triglycerides are triesters)
     "alkoxide + RX (Williamson)"   → use "alcohol" as from (alkoxide is deprotonated alcohol)

2. If the to_raw is "no reaction" or a test colour/indicator result (CAN test, Tollens',
   Fehling's, colour change), set to_node = "NO_REACTION" and skip = true.

3. If the compound is genuinely new (not mappable to any canonical node and chemically
   distinct enough to be a real intermediate), return "NEW:<short_snake_case_name>".
   Examples: "NEW:hydroxamic_acid", "NEW:pyrazoline", "NEW:nitrene"
   Only use NEW if truly necessary — prefer existing nodes.

4. firing_condition: if this reaction only works for a specific substrate class
   (primary amine only, tertiary alcohol only, activated aryl halide only, etc.),
   state it here as a short string. Null if no restriction.

5. skip: true if this TX is a test/indicator result, a qualitative colour observation,
   "no reaction", or too mechanistically exotic to be useful in multi-step synthesis
   chains (e.g., nitrene insertions, singlet/triplet carbene).

TX to normalize:
  tx_index:  {tx['tx_index']}
  from_raw:  {tx['from_raw']}
  to_raw:    {tx['to_raw']}
  from_mapped (prior attempt): {tx['from_mapped']}
  to_mapped (prior attempt):   {tx['to_mapped']}

Return JSON:
{{
  "from_node": "canonical_id or NEW:name or null",
  "to_node": "canonical_id or NEW:name or NO_REACTION or null",
  "firing_condition": "string or null",
  "skip": true or false,
  "reasoning": "one sentence"
}}"""

    resp = api_call(lambda: client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are an organic chemistry graph normalizer. Output JSON only."},
            {"role": "user",   "content": prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=256,
        temperature=0.0,
    ))
    return parse_json(resp)


def main():
    with open(UNMAPPED_FILE) as f:
        unmapped = json.load(f)
    with open(GRAPH_FILE) as f:
        graph = json.load(f)
    with open(CBOOK_FILE) as f:
        cb = json.load(f)

    txs_by_index = {i: t for i, t in enumerate(cb["structural_operators"]["add_reaction_step"]["valid_transformations"])}

    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            progress = json.load(f)
        print(f"Resuming — {len(progress)} already processed.")
    else:
        progress = {}

    to_process = [t for t in unmapped if str(t["tx_index"]) not in progress]
    print(f"Remaining to normalize: {len(to_process)}/288")
    print()

    for i, tx in enumerate(to_process):
        idx = str(tx["tx_index"])
        print(f"[{i+1}/{len(to_process)}] tx{tx['tx_index']}: "
              f"{tx['from_raw'][:40]} → {tx['to_raw'][:40]}", end="", flush=True)
        try:
            result = normalize_tx(tx)
            progress[idx] = result
            flag = "SKIP" if result.get("skip") else f"{result.get('from_node')} → {result.get('to_node')}"
            print(f"  → {flag}")
        except Exception as e:
            print(f"  ERROR: {e}")
            progress[idx] = {"error": str(e), "skip": True}

        with open(PROGRESS_FILE, "w") as f:
            json.dump(progress, f, indent=2)
        time.sleep(1.5)

    # ── Apply recovered edges to graph ────────────────────────────────────────
    existing_edge_map = {e["id"]: e for e in graph["edges"]}
    existing_node_ids = {n["id"] for n in graph["nodes"]}

    new_nodes_needed = set()
    recovered = 0
    skipped   = 0

    for idx_str, result in progress.items():
        if result.get("skip") or result.get("error"):
            skipped += 1
            continue
        fn = result.get("from_node")
        tn = result.get("to_node")
        if not fn or not tn or tn == "NO_REACTION":
            skipped += 1
            continue

        # Track new nodes needed
        for node_id in [fn, tn]:
            if node_id.startswith("NEW:"):
                new_nodes_needed.add(node_id[4:])

        # Resolve NEW: prefix
        fn_clean = fn[4:] if fn.startswith("NEW:") else fn
        tn_clean = tn[4:] if tn.startswith("NEW:") else tn

        # Get original TX data
        tx_idx = int(idx_str)
        orig_tx = txs_by_index.get(tx_idx, {})

        edge_id = f"{fn_clean}_to_{tn_clean}_{tx_idx}"
        if edge_id in existing_edge_map:
            skipped += 1
            continue

        edge = {
            "id":              edge_id,
            "from":            fn_clean,
            "to":              tn_clean,
            "reagents":        orig_tx.get("reagents", []),
            "conditions":      str(orig_tx.get("conditions", ""))[:500],
            "firing_condition": result.get("firing_condition"),
            "chapter":         orig_tx.get("conditions", "")[:30],
            "archetype":       orig_tx.get("archetype", ["I"]),
            "chemoselectivity": {"requires_absent": [], "node_redirect": {}, "priority_ref": None},
            "tx_ids":          [tx_idx],
            "meta_tags":       orig_tx.get("meta_tags", {}),
            "notes":           orig_tx.get("notes", ""),
            "is_exception":    False,
        }
        graph["edges"].append(edge)
        existing_edge_map[edge_id] = edge
        recovered += 1

    # Add new nodes
    for node_name in sorted(new_nodes_needed):
        if node_name not in existing_node_ids:
            graph["nodes"].append({
                "id":          node_name,
                "label":       node_name.replace("_", " "),
                "description": f"Auto-added from unmapped TX recovery",
                "archetype":   ["I"],
            })
            existing_node_ids.add(node_name)
            print(f"  New node added: {node_name}")

    with open(GRAPH_FILE, "w") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)

    print(f"\nRecovered: {recovered} edges")
    print(f"Skipped:   {skipped} (no reaction / exotic / error)")
    print(f"New nodes: {len(new_nodes_needed)}")
    print(f"Saved → {GRAPH_FILE}")


if __name__ == "__main__":
    main()
