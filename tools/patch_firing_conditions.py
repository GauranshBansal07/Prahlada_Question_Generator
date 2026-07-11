"""
tools/patch_firing_conditions.py

Adds firing_condition to edges in reaction_graph.json where the reaction
is substrate-class restricted (primary/secondary/tertiary amine, alcohol, etc.).

Two sources:
1. Hand-coded known restrictions (fast, deterministic)
2. DeepSeek sweep over remaining edges to catch any missed (optional, --llm flag)

Usage:
  python3 tools/patch_firing_conditions.py           # hand-coded only
  python3 tools/patch_firing_conditions.py --llm     # + LLM sweep
"""

import json, os, re, sys, time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")
GRAPH_FILE = os.path.join(_ROOT, "knowledge", "reaction_graph.json")

# ── Hand-coded substrate restrictions ─────────────────────────────────────────
# key = substring that must appear in edge id
# value = firing_condition string
KNOWN = {
    # Diazonium / deamination — primary amine only
    "amine_to_alcohol_nano2":        "primary_amine_only",
    "amine_to_alkyl_halide_nano2":   "primary_amine_only",
    "amine_to_diazonium":            "primary_amine_only",
    "amine_to_nitrile_nano2":        "primary_aromatic_amine_only",
    "diazonium":                     "primary_aromatic_amine_only",

    # Gabriel synthesis — primary alkyl halide + phthalimide
    "gabriel":                       "primary_alkyl_halide_only",

    # Sandmeyer — primary aromatic amine via diazonium
    "sandmeyer":                     "primary_aromatic_amine_only",

    # Hofmann bromamide / rearrangement — primary amide only
    "hofmann":                       "primary_amide_only",

    # Curtius / Schmidt — acyl azide / carboxylic acid respectively
    "curtius":                       "acyl_azide_substrate",
    "schmidt":                       "carboxylic_acid_or_ketone",

    # PCC/PDC — no reaction on tertiary alcohols
    "alcohol_to_aldehyde_pcc":       "primary_or_secondary_alcohol_only",
    "alcohol_to_aldehyde_pdc":       "primary_or_secondary_alcohol_only",
    "alcohol_to_aldehyde_swern":     "primary_or_secondary_alcohol_only",
    "alcohol_to_aldehyde_dess":      "primary_or_secondary_alcohol_only",
    "alcohol_to_aldehyde_mno2":      "allylic_or_benzylic_alcohol_only",

    # KMnO4 — stops at carboxylic acid for primary; no effect on tertiary
    "alcohol_to_carboxylic_acid_kmno4": "primary_alcohol_only",

    # SNi retention — requires specific substrate (not tertiary, no beta branching)
    "sni":                           "primary_or_secondary_substrate_SNi",

    # SN2-dependent reactions — tertiary halides don't react cleanly
    "alkyl_halide_to_nitrile":       "primary_or_secondary_halide_only_SN2",
    "alkyl_halide_to_ether_williamson": "primary_halide_preferred_SN2",
    "alkyl_halide_to_amine_nh3":     "primary_or_secondary_halide_only",

    # Grignard with tertiary halides — side reactions dominate
    "alkyl_halide_to_grignard":      "primary_or_secondary_halide_preferred",

    # Fischer esterification — requires no base-sensitive groups
    "carboxylic_acid_to_ester_fischer": "no_base_sensitive_groups",

    # Iodoform — methyl ketone or acetaldehyde only
    "iodoform":                      "methyl_ketone_or_ethanal_only",
    "ketone_to_carboxylic_acid_x2":  "methyl_ketone_only",

    # E2 anti-periplanar — cyclic substrates constrained
    "alkyl_halide_to_alkene_alckoh": "anti_periplanar_H_required_for_E2",

    # Rosenmund — only acyl chlorides, not other acid derivatives
    "acid_chloride_to_aldehyde_rosenmund": "acid_chloride_only",

    # LAH — reduces all carbonyls; NaBH4 does not reduce esters/acids
    "ester_to_alcohol_nabh4":        "NOT_POSSIBLE_nabh4_does_not_reduce_esters",
    "carboxylic_acid_to_alcohol_nabh4": "NOT_POSSIBLE_nabh4_does_not_reduce_acids",

    # Aldol — requires alpha-H; no alpha-H → Cannizzaro instead
    "aldehyde_to_aldol":             "requires_alpha_hydrogen",
    "ketone_to_aldol":               "requires_alpha_hydrogen",
    "aldehyde_to_cannizzaro":        "no_alpha_hydrogen_only",

    # Reimer-Tiemann — phenol only (activated ring)
    "reimer":                        "phenol_only",
    "phenol_to_aldehyde_reimertiemann": "phenol_only",

    # Kolbe electrolysis — carboxylate salt only
    "kolbe":                         "carboxylate_salt_only",

    # Birch reduction — aromatic ring required
    "birch":                         "aromatic_substrate_only",

    # Baeyer-Villiger — ketone or aldehyde only; migrates better substituent
    "baeyer":                        "ketone_or_aldehyde_only",
    "baeyer_villiger":               "ketone_or_aldehyde_only",

    # Wittig — carbonyl compound required (aldehyde/ketone)
    "wittig":                        "aldehyde_or_ketone_substrate",
    "phosphorane":                   "aldehyde_or_ketone_substrate",

    # Diels-Alder — s-cis diene + dienophile required
    "diene_conjugated_to":           "scis_diene_conformation_required",

    # Lucas test — tertiary > secondary > primary (reactivity order)
    "lucas":                         "tertiary_fastest_then_secondary",

    # NBS — allylic/benzylic bromination only at these conditions
    "nbs":                           "allylic_or_benzylic_CH_only",
}


def apply_hand_coded(edges: list) -> int:
    applied = 0
    for edge in edges:
        eid = edge["id"].lower()
        # Only set if currently null
        if edge.get("firing_condition") is not None:
            continue
        for pattern, condition in KNOWN.items():
            if pattern.lower() in eid:
                edge["firing_condition"] = condition
                applied += 1
                break
    return applied


def main():
    with open(GRAPH_FILE) as f:
        graph = json.load(f)
    edges = graph["edges"]

    print(f"Edges: {len(edges)}")
    null_before = sum(1 for e in edges if e.get("firing_condition") is None)
    print(f"firing_condition=null before: {null_before}")

    applied = apply_hand_coded(edges)
    null_after = sum(1 for e in edges if e.get("firing_condition") is None)
    print(f"Hand-coded rules applied: {applied}")
    print(f"firing_condition=null after:  {null_after}")

    graph["edges"] = edges
    with open(GRAPH_FILE, "w") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)
    print(f"Saved → {GRAPH_FILE}")


if __name__ == "__main__":
    main()
