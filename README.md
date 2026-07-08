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

## Running the experiment

```bash
docker compose up -d                        # LocalStack (pinned community 4.5)
python scripts/dryrun_dataset.py            # 240/240 executability report

# offline (no API key): stub planner, both paired arms
python -m cordon.runner --planner stub --env localstack --arms baseline cordon --reps 1

# live pilot (needs OPENAI_API_KEY in .env): cheap model, scenario subset
python -m cordon.runner --planner openai --tier pilot --reps 1 --out experiments/pilot

# metrics
python -m cordon.analysis experiments/<run>/records.jsonl
```

## Layout

```
dataset/            evaluation dataset: generator, validator, artifacts
cordon/
  schema.py         domain models: scenarios, plans, execution records
  dataset.py        typed loader + artifact fingerprint
  config.py         config.yaml + .env loading
  planner.py        OpenAI planner (strict structured output) + stub planner
  prompts/          versioned planner prompts (core / prompt-guardrails)
  execution.py      execution interface: validation + existence checks + dispatch
  env/              environment backends: in-memory and LocalStack
  state_model.py    believed + desired resource state (preconditions, drift diff)
  guardrails/       adversarial / capability / policy / risk / state layers
  reconcile.py      drift detection + guardrailed correction (one cycle)
  arms.py           the three pipelines wired from shared components
  runner.py         scenarios x arms x reps -> resume-safe records.jsonl + manifest
  scoring.py        offline judge: record + oracle -> per-run facts
  analysis.py       metrics with Wilson CIs, per-category tables, McNemar
tests/              unit + integration tests; the dataset doubles as the fixture
```

## Status

- [x] Dataset v2: generated, validated, 10-action catalog; 240/240 dry-run on LocalStack
- [x] Domain models, execution interface, environments (memory + LocalStack), scoring
- [x] LLM planner (strict structured output) + prompt variants; stub planner for offline work
- [x] Guardrail layers, state model, reconciliation; offline e2e green (240 scenarios)
- [x] Experiment runner + metrics analysis (CIs, McNemar, flip-rate)
- [ ] Live pilot on the cheap model (needs `OPENAI_API_KEY` in `.env`), prompt freeze
- [ ] Full run: 240 x 3 arms x 3 reps on the pinned model
