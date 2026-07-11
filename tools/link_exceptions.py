"""
tools/link_exceptions.py — Link concept_book exceptions to reaction_graph edges.

For each of the 502 exceptions in concept_book.json, tries to match it to one or
more graph edges using keyword matching against edge reagents, from/to node names,
and conditions text.

Adds to matching edges:
  "is_exception": true
  "exception_description": "<standard_case> → <exception>"
  "exception_trap": "<trap text>"
  "exam_relevance": "High / Medium-High / Medium"

Edges with no matching exception get:
  "is_exception": false

Run once; re-running is safe (overwrites prior flags).

Usage:
  python3 tools/link_exceptions.py [--dry-run]
"""

import json
import os
import re
import sys

_HERE  = os.path.dirname(os.path.abspath(__file__))
_ROOT  = os.path.join(_HERE, "..")
GRAPH  = os.path.join(_ROOT, "knowledge", "reaction_graph.json")
CBOOK  = os.path.join(_ROOT, "knowledge", "concept_book.json")


# ── Keyword sets for matching exceptions to edges ─────────────────────────────
# Each entry: (exception_index_hint, keywords_that_trigger_on_edge_text)
# We match by checking if ANY keyword appears in the concatenated edge text
# (from + to + reagents + conditions + notes).

def _edge_text(edge: dict) -> str:
    parts = [
        edge.get("from", ""),
        edge.get("to", ""),
        " ".join(edge.get("reagents", [])),
        edge.get("conditions", ""),
        edge.get("notes", ""),
    ]
    return " ".join(parts).lower()


def _exception_keywords(exc: dict) -> list[str]:
    """
    Extract matchable keywords from an exception entry.
    Deliberately specific: only named reagents and named reaction types.
    Generic substrate-class words (primary, secondary, tertiary) are excluded
    because they appear in the conditions of nearly every edge.
    """
    text = " ".join([
        exc.get("standard_case", ""),
        exc.get("exception", ""),
        exc.get("trap", ""),
    ]).lower()

    # Only named reagents and reaction names — NOT substrate class words
    SPECIFIC_PATTERNS = [
        # Reagents — specific enough to target exactly one class of edges
        (r'\bperoxide\b',              "peroxide"),
        (r'\bpcc\b',                   "pcc"),
        (r'\bpdc\b',                   "pdc"),
        (r'\bdibal',                   "dibal"),
        (r'\brosenmund\b',             "rosenmund"),
        (r'\bcannizzaro\b',            "cannizzaro"),
        (r'\biodoform\b',              "iodoform"),
        (r'\blucas\b',                 "lucas"),
        (r'\bbaeyer.villiger\b',       "baeyer"),
        (r'\bbeckmann\b',              "beckmann"),
        (r'\bclaisen\b',               "claisen"),
        (r'\baldol\b',                 "aldol"),
        (r'\bgrignard\b',              "grignard"),
        (r'\breimer.tiemann\b',        "reimer"),
        (r'\bkolbe\b',                 "kolbe"),
        (r'\bwolff.kishner\b',         "wolff"),
        (r'\bclemmensen\b',            "clemmensen"),
        (r'\bcorey.house\b',           "corey"),
        (r'\bhydroboration\b',         "hydroboration"),
        (r'\bmcpba\b',                 "mcpba"),
        (r'\boso4\b',                  "oso4"),
        (r'\bkmno4\b',                 "kmno4"),
        (r'\bsandmeyer\b',             "sandmeyer"),
        (r'\bgabriel\b',               "gabriel"),
        (r'\bhell.volhard\b',          "hell"),
        (r'\bmalonic ester\b',         "malonic"),
        (r'\bacetoacetic ester\b',     "acetoacetic"),
        (r'\bdiazonium\b',             "diazonium"),
        (r'\bwurtz\b',                 "wurtz"),
        (r'\bfittig\b',                "fittig"),
        (r'\bstephen\b',               "stephen"),
        (r'\bgattermann\b',            "gattermann"),
        (r'\bbirch\b',                 "birch"),
        (r'\bswern\b',                 "swern"),
        (r'\bdess.martin\b',           "dess"),
        (r'\blindlar\b',               "lindlar"),
        (r'\bbirkman\b',               "birkman"),
        (r'\bnbbs\b',                  "nbbs"),
        (r'\bnbs\b',                   "nbs"),
        (r'\bwacker\b',                "wacker"),
        # Named reactions / rearrangements
        (r'\bhofmann rearrangement\b', "hofmann rearrangement"),
        (r'\bhofmann bromamide\b',     "hofmann bromamide"),
        (r'\bcurtius\b',               "curtius"),
        (r'\bschmidt\b',               "schmidt"),
        (r'\bfries\b',                 "fries"),
        (r'\bsomer.hauser\b',          "hauser"),
        (r'\bbaeyer\b',                "baeyer"),
        (r'\banti-markovnikov\b',      "anti-markovnikov"),
        (r'\bsaytzeff\b',              "saytzeff"),
        # Specific from/to node combinations (encoded as "from→to" substrings)
        (r'\bvinylic\b',               "vinylic"),
        (r'\baryl halide\b',           "aryl halide"),
        (r'\bpicric\b',                "picric"),
        (r'\bphenol\b',                "phenol"),
        (r'\baniline\b',               "aniline"),
    ]

    found = []
    for pat, kw in SPECIFIC_PATTERNS:
        if re.search(pat, text):
            found.append(kw)
    return found


def match_exception_to_edges(exc: dict, edges: list[dict]) -> list[str]:
    """Return list of edge IDs that match this exception."""
    kws = _exception_keywords(exc)
    if not kws:
        return []

    matched = []
    for edge in edges:
        et = _edge_text(edge)
        # Edge must match at least one keyword from the exception
        if any(re.search(kw, et) for kw in kws):
            matched.append(edge["id"])
    return matched


def exam_relevance_level(exc: dict) -> str:
    er = exc.get("exam_relevance", "")
    if er.startswith("High"):
        return "High"
    elif er.startswith("Medium-High"):
        return "Medium-High"
    elif er.startswith("Medium"):
        return "Medium"
    elif er.startswith("Low"):
        return "Low"
    return "Unknown"


def main(dry_run: bool = False):
    with open(GRAPH) as f:
        graph = json.load(f)
    with open(CBOOK) as f:
        cb = json.load(f)

    edges    = graph["edges"]
    edge_map = {e["id"]: e for e in edges}
    excs     = cb["interpretive_operators"]["number_of_exceptions"]["exceptions"]

    print(f"Graph edges:       {len(edges)}")
    print(f"Exceptions:        {len(excs)}")
    print()

    # Clear prior flags
    for edge in edges:
        edge["is_exception"]          = False
        edge.pop("exception_description", None)
        edge.pop("exception_trap", None)
        edge.pop("exam_relevance_exc", None)

    # Match each exception to edges
    match_count = 0
    no_match    = 0
    for exc in excs:
        matched_ids = match_exception_to_edges(exc, edges)
        if not matched_ids:
            no_match += 1
            continue

        for eid in matched_ids:
            edge = edge_map[eid]
            edge["is_exception"] = True

            # Append rather than overwrite — an edge can have multiple exceptions
            std  = exc.get("standard_case", "")
            excn = exc.get("exception", "")
            trap = exc.get("trap", "")
            er   = exam_relevance_level(exc)

            desc_line = f"{std} → {excn}"
            if "exception_description" not in edge:
                edge["exception_description"] = desc_line
                edge["exception_trap"]        = trap
                edge["exam_relevance_exc"]    = er
            else:
                # Multiple exceptions for this edge — append
                edge["exception_description"] += f" | {desc_line}"
                if er in ("High", "Medium-High") and edge["exam_relevance_exc"] not in ("High",):
                    edge["exam_relevance_exc"] = er  # escalate to higher relevance

        match_count += 1
        print(f"  Matched: {exc.get('standard_case','')[:60]:60s}  → {len(matched_ids)} edge(s)")

    exception_edges = [e for e in edges if e["is_exception"]]
    plain_edges     = [e for e in edges if not e["is_exception"]]

    print()
    print(f"Exceptions matched to at least one edge: {match_count}/{len(excs)}")
    print(f"Exceptions with no edge match:           {no_match}")
    print(f"Edges flagged as is_exception=True:      {len(exception_edges)}")
    print(f"Edges with is_exception=False:           {len(plain_edges)}")

    if dry_run:
        print("\n[DRY RUN] No files written.")
        print("\nSample flagged edges:")
        for e in exception_edges[:5]:
            print(f"  {e['id']}: {e.get('exception_description','')[:80]}")
        return

    graph["edges"] = edges
    with open(GRAPH, "w") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)
    print(f"\nWritten: {GRAPH}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    main(dry_run=dry_run)
