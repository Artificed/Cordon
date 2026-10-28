# Cordon

A guardrailed intent-driven control plane for cloud infrastructure management.

An LLM translates natural-language intent into a structured action plan, but never
executes anything itself. Deterministic layers decide whether a plan may run:
adversarial detection (does the input contain a recognized override pattern?),
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
| `cordon` | planner → adversarial → capability → policy → risk → state check → execute → observe → reconcile |

## Paper and data

Reference implementation for **Cordon: A Guardrailed Intent-Driven Control Plane
for Cloud Infrastructure Management**, by Paul Tsai Adinata and Benfano Soewito,
Bina Nusantara University, accepted at IEEE TIME-E 2026 (paper 1571355495).

The standalone scenario dataset has reserved Zenodo DOI
[10.5281/zenodo.23723174](https://doi.org/10.5281/zenodo.23723174); the deposit is
pending publication. It contains the same `scenarios.json` used here and is
licensed under CC BY 4.0. That data license does not license this repository's
source code. The repository provides the implementation, prompts, experiment
harness, and tests. Execution logs, cached model plans, credentials, and local
research notes are excluded. Generate fresh plans to repeat the experiments;
LLM outputs and timing can differ from the reported runs.

## Setup

```bash
# Run from the repository root, using Python 3.12 or newer.
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest
```

Set `OPENAI_API_KEY` in your local `.env` only if generating new LLM plans.
The offline example needs no API key or Docker. Docker is required for the
LocalStack experiments. Run LocalStack in a dedicated test instance: the harness
clears its buckets, instances, and non-default security groups between scenarios.
The default endpoint is `http://localhost:4566`; use no production AWS credentials.

## Dataset

`dataset/scenarios.json` holds 240 scenarios (8 risk categories × 30), each a test
case: an initial state, a natural-language intent, and a machine-checkable oracle
(expected decision + expected final state). Policies include selected CIS AWS
Foundations Benchmark v3.0 controls and project-specific rules. Two historical
source labels in the unchanged dataset need clarification: `POL-STO-001` maps to
control 2.1.4; `POL-STO-002` is a project-specific encryption rule, while control
2.1.1 concerns TLS. The dataset is generated deterministically —
never edit the JSON artifacts by hand:

```bash
python dataset/generate_dataset.py   # regenerate from templates x parameter grids
python dataset/validate_dataset.py   # independent integrity checks (must pass)
```

## Running the experiment

```bash
docker compose up -d                        # LocalStack (pinned community 4.5)
python scripts/dryrun_dataset.py            # 240/240 executability report

# 1. pre-generate + cache LLM plans concurrently (needs OPENAI_API_KEY in .env)
python scripts/pregenerate_plans.py --model gpt-5-mini --variants core promptguard --reps 3 --out experiments/full

# 2. replay cached plans through all three arms on LocalStack (no API calls)
python -m cordon.runner --planner cached --model gpt-5-mini --plans experiments/full \
    --arms baseline prompt_guardrails cordon --reps 3 --env localstack --out experiments/full

# 3. metrics (cluster-bootstrap CIs over scenarios, per-category, McNemar, flip-rate)
python -m cordon.analysis experiments/full/records.jsonl

# ancillary studies (all replay cached plans — free)
python scripts/run_ablations.py --model gpt-5-mini --plans experiments/full --reps 3 --out experiments/full-ablations
python scripts/compare_runs.py nano=.../records.jsonl mini=.../records.jsonl   # model ladder
python scripts/make_figures.py experiments/full/records.jsonl --out figures    # thesis figures
python scripts/cost_report.py experiments                                      # spend so far
```

For all three planners evaluated in the paper, repeat steps 1 and 2 with
`--model gpt-5-nano`, `--model gpt-5-mini`, and `--model gpt-5.5`, using a separate
output directory for each. This gives 240 scenarios × 3 arms × 3 repetitions ×
3 planners = 6,480 main runs. The gpt-5-mini ablation command produces
240 × 7 configurations × 3 repetitions = 5,040 runs. Plan generation calls the
OpenAI API and incurs usage charges; cached replay does not call it. Model
availability depends on your API account. To regenerate plots, install
`matplotlib` separately before running `make_figures.py`.

Offline development without an API key uses the deterministic stub planner:
`python -m cordon.runner --planner stub --env memory --arms baseline cordon --reps 1`.
This checks the harness with deterministic plans; it does not reproduce the
paper's LLM results. Unit tests use offline fixtures; LocalStack integration
tests are skipped when the local service is unavailable.

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
  analysis.py       metrics with cluster-bootstrap CIs (scenario = unit), McNemar
tests/              unit + integration tests; the dataset doubles as the fixture
```

## Status

- [x] Dataset v2: generated, validated, 10-action catalog; 240/240 dry-run on LocalStack
- [x] Domain models, execution interface, environments (memory + LocalStack), scoring
- [x] LLM planner (strict structured output) + prompt variants; plan cache; stub planner
- [x] Guardrail layers, state model, reconciliation; offline e2e green (240 scenarios)
- [x] Experiment runner + metrics analysis (CIs, McNemar, flip-rate) + figures
- [x] Pilot (gpt-5-mini) → prompt frozen to v2
- [x] Full run: 240 × 3 arms × 3 reps (gpt-5-mini); guardrail ablation study
- [x] Planner-model ladder (gpt-5-nano, gpt-5-mini, gpt-5.5)
- [x] Independent two-model review; claims + statistics corrected
- [x] Second independent audit (gpt-5.6); claims narrowed, overhead figure corrected

Reported results: executed directly, a **policy-unaware** planner's plans
carry out 83–98% of harmful requests at every planner scale (98.3% nano, 94.4% mini, 83.3%
gpt-5.5); any policy layer collapses that to ~0%. On categories all arms can contest, a prompt
enforces about as well as the control plane (94.6% vs 98.1% on mini; **tied at 94.4% on gpt-5.5**,
both arms failing the same ten indirect-injection scenarios). The control plane's distinctive
value is what a prompt cannot see or say: observing actual state to reconcile drift (100% vs 1.7%
on mini) and an implemented graduated-escalation channel — the five decision layers cost ~0.1 ms
per run (total overhead including observation I/O: 12.0–14.3 ms, 0.28–0.56% of planner latency).
