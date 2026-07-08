#!/usr/bin/env python3
"""Independent validator for the Cordon dataset. Checks structural integrity,
oracle consistency, and cross-references. Exits non-zero on any failure."""
import json
import sys
import re
from pathlib import Path
from collections import Counter

OUT = Path(__file__).parent
data = json.loads((OUT / "scenarios.json").read_text())
scen = data["scenarios"]
policy_ids = {p["policy_id"] for p in data["policies"]}
action_names = {a["action_name"] for a in data["actions"]}

errors = []
def check(cond, msg):
    if not cond:
        errors.append(msg)

# --- counts ---
check(len(scen) == 240, f"expected 240 scenarios, got {len(scen)}")
cats = Counter(s["category"] for s in scen)
for c, n in cats.items():
    check(n == 30, f"category {c} has {n}, expected 30")
check(len(cats) == 8, f"expected 8 categories, got {len(cats)}")

# --- unique ids ---
ids = [s["scenario_id"] for s in scen]
check(len(set(ids)) == len(ids), "duplicate scenario_id found")
check(all(re.fullmatch(r"S\d{3}", i) for i in ids), "malformed scenario_id")

# --- enums ---
VALID_DEC = {"allow", "block", "requires_approval", "reconcile", "fail_safe"}
VALID_SAFETY = {"safe", "policy_violation", "harmful", "adversarial", "invalid_request"}
for s in scen:
    sid = s["scenario_id"]
    check(s["expected_decision"] in VALID_DEC, f"{sid} bad decision {s['expected_decision']}")
    check(s["safety_label"] in VALID_SAFETY, f"{sid} bad safety_label {s['safety_label']}")
    # policy refs resolve
    for p in s["policy_refs"]:
        check(p in policy_ids, f"{sid} references unknown policy {p}")
    # a drift_spec implies a reconcile scenario in the drift category
    if s["drift_spec"] is not None:
        check(s["expected_decision"] == "reconcile", f"{sid} has drift_spec but decision != reconcile")
        check(s["category"] == "configuration_drift", f"{sid} has drift_spec but not drift category")
    # every scenario has an oracle
    check(len(s["expected_final_state"]["assertions"]) >= 1, f"{sid} empty oracle")
    # intent non-trivial and English-only (ASCII, no leftover Indonesian markers)
    intent = s["natural_language_intent"]
    check(len(intent) > 15, f"{sid} intent too short")
    check(intent.isascii(), f"{sid} intent has non-ascii")
    low = f" {intent.lower()} "
    for word in [" buat ", " hapus ", " dan ", " yang ", " dengan ", " tidak ", " pada "]:
        check(word not in low, f"{sid} intent contains Indonesian token '{word.strip()}'")

# --- category-level oracle logic ---
def cat_of(c):
    return [s for s in scen if s["category"] == c]

# normal_provisioning & configuration_change -> allow / safe
for c in ("normal_provisioning", "configuration_change"):
    for s in cat_of(c):
        check(s["expected_decision"] == "allow", f"{s['scenario_id']} {c} not allow")
        check(s["safety_label"] == "safe", f"{s['scenario_id']} {c} not safe")

# policy_violation & network_misconfiguration -> block
for c in ("policy_violation", "network_misconfiguration"):
    for s in cat_of(c):
        check(s["expected_decision"] == "block", f"{s['scenario_id']} {c} not block")
        check(len(s["policy_refs"]) >= 1, f"{s['scenario_id']} {c} has no policy_ref")

# high_risk_action -> requires_approval, harmful, has target in initial_state
for s in cat_of("high_risk_action"):
    check(s["expected_decision"] == "requires_approval", f"{s['scenario_id']} not requires_approval")
    check(len(s["initial_state"]["resources"]) >= 1, f"{s['scenario_id']} delete target missing")

# configuration_drift -> 20 reconcile + 10 allow-control
drift = cat_of("configuration_drift")
rec = [s for s in drift if s["expected_decision"] == "reconcile"]
ctrl = [s for s in drift if s["expected_decision"] == "allow"]
check(len(rec) == 20, f"drift reconcile count {len(rec)} != 20")
check(len(ctrl) == 10, f"drift control count {len(ctrl)} != 10")
for s in rec:
    check(s["drift_spec"] is not None, f"{s['scenario_id']} reconcile w/o drift_spec")
for s in ctrl:
    check(s["drift_spec"] is None, f"{s['scenario_id']} control w/ drift_spec")

# execution_failure -> fail_safe + failure_reason
for s in cat_of("execution_failure"):
    check(s["expected_decision"] == "fail_safe", f"{s['scenario_id']} not fail_safe")
    check("failure_reason" in s, f"{s['scenario_id']} no failure_reason")

# input_adversarial -> block + injection_type
adv = cat_of("input_adversarial")
for s in adv:
    check(s["expected_decision"] == "block", f"{s['scenario_id']} adversarial not block")
    check(s.get("injection_type") in {"direct", "indirect"}, f"{s['scenario_id']} no injection_type")
check(sum(s.get("injection_type") == "indirect" for s in adv) == 10, "expected 10 indirect")

# --- modify/delete categories must seed a target in initial_state ---
for s in scen:
    if s["category"] in ("configuration_change", "high_risk_action", "network_misconfiguration"):
        check(len(s["initial_state"]["resources"]) >= 1,
              f"{s['scenario_id']} {s['category']} with empty initial_state")

# provision "allow" scenarios should start clean (no name clash with target)
for s in cat_of("normal_provisioning"):
    ids_in_state = {r.get("resource_id") for r in s["initial_state"]["resources"]}
    for a in s["expected_final_state"]["assertions"]:
        if a.get("exists") is True and "resource_id" in a:
            check(a["resource_id"] not in ids_in_state,
                  f"{s['scenario_id']} provisions an already-present resource")

# --- report ---
print(f"scenarios: {len(scen)}")
print("category counts:", dict(cats))
print("decision distribution:", dict(Counter(s['expected_decision'] for s in scen)))
print("safety distribution:", dict(Counter(s['safety_label'] for s in scen)))
print("drift scenarios (drift_spec set):", sum(1 for s in scen if s['drift_spec'] is not None))
print("policy usage:", dict(Counter(p for s in scen for p in s['policy_refs'])))

if errors:
    print(f"\nFAILED with {len(errors)} error(s):")
    for e in errors[:40]:
        print("  -", e)
    sys.exit(1)
print("\nALL CHECKS PASSED")
