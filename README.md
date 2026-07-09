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

Offline development without an API key uses the deterministic stub planner:
`python -m cordon.runner --planner stub --env memory --arms baseline cordon --reps 1`.

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

Headline (post independent review; see `../.claude-research/06_external_review.md`):
an **ungoverned** agent executes 83–98% of harmful actions at every planner scale (98.3% nano,
94.4% mini, 83.3% gpt-5.5); any policy layer collapses that to ~0%. On categories all arms can
contest, a prompt enforces about as well as the control plane (94.6% vs 98.1% on mini; **tied at
94.4% on gpt-5.5**). The control plane's distinctive value is what a prompt cannot do: stateful
drift reconciliation (100% vs 1.7%) and graduated escalation — at 9-12 ms overhead.
