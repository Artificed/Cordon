"""Plan cache: pre-generate LLM plans once, replay them for free.

The planner is the only expensive, slow, nondeterministic part of the
pipeline. This module runs it concurrently over every (scenario, rep) for a
given model + prompt variant and stores the results as JSONL, so the
enforcement pipeline (and any later architecture experiment) can replay the
exact plans without further API calls. Experiment semantics are unchanged —
these are the same calls the inline runner would make, only scheduled up
front and persisted.

Cache layout: experiments/plans/<model>/<variant>.jsonl, one record per
(scenario_id, rep):

    {"scenario_id": "S001", "rep": 0, "variant": "core", "model": "...",
     "prompt_sha": "...", "plan": {...}, "planner": {...}, "error": null}
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from cordon.planner import CachedPlanner, OpenAIPlanner, PlannerError
from cordon.schema import Dataset, Plan, PlannerInfo, Scenario


def cache_path(root: Path | str, model: str, variant: str) -> Path:
    safe_model = model.replace("/", "_")
    return Path(root) / "plans" / safe_model / f"{variant}.jsonl"


def _load_existing(path: Path) -> dict[tuple[str, int], dict]:
    if not path.exists():
        return {}
    out: dict[tuple[str, int], dict] = {}
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("error") is None:  # only successful plans are reusable
                out[(rec["scenario_id"], rec["rep"])] = rec
    return out


def pregenerate_plans(
    dataset: Dataset,
    planner: OpenAIPlanner,
    *,
    reps: int,
    out_root: Path | str,
    concurrency: int = 8,
    scenarios: list[Scenario] | None = None,
    progress_every: int = 40,
) -> Path:
    """Fill (or resume) the plan cache for planner.model + planner.variant."""
    scenarios = scenarios if scenarios is not None else dataset.scenarios
    path = cache_path(out_root, planner.model, planner.variant)
    path.parent.mkdir(parents=True, exist_ok=True)
    done = set(_load_existing(path))

    tasks = [(s, rep) for s in scenarios for rep in range(reps)
             if (s.scenario_id, rep) not in done]
    if not tasks:
        return path

    def generate(task):
        scenario, rep = task
        try:
            plan, info = planner.plan_for(scenario, rep)
            return scenario.scenario_id, rep, plan, info, None
        except PlannerError as e:
            return scenario.scenario_id, rep, Plan(), PlannerInfo(model="error"), str(e)

    finished = 0
    with path.open("a") as sink, ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(generate, t) for t in tasks]
        for future in as_completed(futures):
            sid, rep, plan, info, error = future.result()
            sink.write(json.dumps({
                "scenario_id": sid, "rep": rep, "variant": planner.variant,
                "model": planner.model, "prompt_sha": planner.prompt_sha,
                "plan": plan.model_dump(mode="json"),
                "planner": info.model_dump(mode="json"), "error": error,
            }) + "\n")
            sink.flush()
            finished += 1
            if finished % progress_every == 0:
                print(f"    plans {finished}/{len(tasks)} "
                      f"({planner.model}/{planner.variant})", flush=True)
    return path


def load_cached_planner(root: Path | str, model: str, variant: str) -> CachedPlanner:
    path = cache_path(root, model, variant)
    if not path.exists():
        raise FileNotFoundError(f"no plan cache at {path}")
    plans: dict[tuple[str, int], tuple[Plan, PlannerInfo]] = {}
    prompt_sha = ""
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("error") is not None:
                continue
            prompt_sha = rec.get("prompt_sha", prompt_sha)
            plans[(rec["scenario_id"], rec["rep"])] = (
                Plan.model_validate(rec["plan"]),
                PlannerInfo.model_validate(rec["planner"]),
            )
    return CachedPlanner(variant, prompt_sha, plans)


def cache_summary(root: Path | str, model: str, variant: str) -> dict:
    path = cache_path(root, model, variant)
    total = errors = refusals = tokens_in = tokens_out = 0
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            total += 1
            if rec.get("error"):
                errors += 1
                continue
            planner = rec.get("planner", {})
            refusals += bool(planner.get("refused"))
            tokens_in += planner.get("tokens_in", 0)
            tokens_out += planner.get("tokens_out", 0)
    return {"total": total, "errors": errors, "refusals": refusals,
            "tokens_in": tokens_in, "tokens_out": tokens_out}
