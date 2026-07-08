# Cordon

A guardrailed intent-driven control plane for cloud infrastructure management.

An LLM translates natural-language intent into a structured action plan, but never
executes anything itself. Deterministic layers decide whether a plan may run:
capability control (is the action in the predefined catalog and context?), policy
enforcement (does any parameter violate a policy-as-code rule?), risk tiering
(does this need human approval?), and a state model check (do preconditions hold
against known state?). Approved actions execute through a limited execution
interface; an observer then reads actual state and a reconciliation layer detects
and corrects configuration drift, with corrections re-entering the same pipeline.

This repository contains the reference implementation and the experiment harness
comparing three configurations on a 240-scenario evaluation dataset:

| Arm | Pipeline |
|---|---|
| `baseline` | planner → syntax validation → execute |
| `prompt_guardrails` | same as baseline, but the policies are stated in the planner prompt |
| `cordon` | planner → capability → policy → risk → state check → execute → observe → reconcile |

## Setup

```bash
conda create -n cordon python=3.13 -y
conda activate cordon
pip install -e ".[dev]"
pytest
```

## Dataset

`dataset/scenarios.json` holds 240 scenarios (8 risk categories × 30), each a test
case: an initial state, a natural-language intent, and a machine-checkable oracle
(expected decision + expected final state). Policies are anchored to CIS AWS
Foundations Benchmark v3.0 controls. The dataset is generated deterministically —
never edit the JSON artifacts by hand:

```bash
python dataset/generate_dataset.py   # regenerate from templates x parameter grids
python dataset/validate_dataset.py   # independent integrity checks (must pass)
```

## Layout

```
dataset/            evaluation dataset: generator, validator, artifacts
cordon/
  schema.py         domain models: scenarios, plans, execution records
  dataset.py        typed loader + artifact fingerprint
  planner_stub.py   deterministic offline planner (LLM planner replaces it)
  execution.py      execution interface: validation + existence checks + dispatch
  env/              environment backends (in-memory now; LocalStack next)
  scoring.py        offline judge: record + oracle -> per-run facts
tests/              unit tests; the dataset doubles as the fixture
```

## Status

- [x] Dataset v2: generated, validated, 10-action catalog
- [x] Domain models, execution interface, in-memory environment, scoring, stub planner
- [ ] LLM planner (structured output) + prompt variants
- [ ] LocalStack environment backend
- [ ] Guardrail layers, state model, reconciliation
- [ ] Experiment runner + metrics analysis
