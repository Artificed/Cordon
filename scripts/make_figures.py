#!/usr/bin/env python3
"""Generate thesis figures (Bab 4) from experiment records.

Static matplotlib figures for the printed thesis. Palette is a validated
colorblind-safe Okabe-Ito subset, assigned per arm (entity, not rank):
baseline = vermillion (ungoverned/risky), prompt_guardrails = blue,
cordon = green (the proposed system). Bars carry direct value labels; grid
is a recessive horizontal rule; no top/right spines; one 0-100% axis.

Usage:
  python scripts/make_figures.py experiments/full/records.jsonl --out figures
  python scripts/make_figures.py --ladder nano=.../records.jsonl mini=.../records.jsonl \
      --out figures
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from cordon.analysis import analyze, load_records
from cordon.dataset import load_dataset

ARM_COLOR = {
    "baseline": "#D55E00",
    "prompt_guardrails": "#0072B2",
    "cordon": "#009E73",
}
ARM_LABEL = {"baseline": "Baseline", "prompt_guardrails": "Prompt-guardrails",
             "cordon": "Cordon"}
ARM_ORDER = ["baseline", "prompt_guardrails", "cordon"]

plt.rcParams.update({
    "font.size": 11, "axes.titlesize": 13, "axes.labelsize": 11,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 150, "savefig.bbox": "tight", "font.family": "DejaVu Sans",
})


def _metric_map(analysis):
    out = {}
    for row in analysis["metrics"]:
        if "label" in row:
            out[row["label"]] = {a: v["rate"] for a, v in row["values"].items()}
    return out


def _style_axis(ax):
    ax.set_ylim(0, 105)
    ax.set_yticks(range(0, 101, 20))
    ax.set_yticklabels([f"{t}%" for t in range(0, 101, 20)])
    ax.yaxis.grid(True, color="#e6e6e6", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def grouped_bars(ax, categories, series, arms, title, ylabel="rate"):
    n = len(arms)
    width = 0.8 / n
    x = range(len(categories))
    for i, arm in enumerate(arms):
        offsets = [xi + (i - (n - 1) / 2) * width for xi in x]
        vals = [100 * (series[arm].get(c) or 0) for c in categories]
        bars = ax.bar(offsets, vals, width * 0.92, label=ARM_LABEL[arm],
                      color=ARM_COLOR[arm])
        for rect, v in zip(bars, vals):
            ax.annotate(f"{v:.0f}", (rect.get_x() + rect.get_width() / 2, v),
                        ha="center", va="bottom", fontsize=7.5, color="#333333",
                        xytext=(0, 1), textcoords="offset points")
    ax.set_xticks(list(x))
    ax.set_xticklabels(categories)
    ax.set_title(title, loc="left", weight="bold")
    ax.set_ylabel(ylabel)
    _style_axis(ax)
    ax.legend(frameon=False, ncol=len(arms), loc="upper center",
              bbox_to_anchor=(0.5, -0.16), fontsize=9)


def fig_headline(analysis, arms, out: Path):
    m = _metric_map(analysis)
    metrics = [
        ("Decision\naccuracy", "decision accuracy"),
        ("Safe-task\ncompletion", "task completion rate (safe)"),
        ("State\nconsistency", "state consistency rate"),
        ("Drift\ncorrection", "drift correction success"),
    ]
    series = {a: {short: m[full].get(a) for short, full in metrics} for a in arms}
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    grouped_bars(ax, [s for s, _ in metrics], series, arms,
                 "Utility & reliability (higher is better)")
    fig.savefig(out / "fig_headline_utility.png")
    plt.close(fig)


def fig_security(analysis, arms, out: Path):
    m = _metric_map(analysis)
    metrics = [
        ("Harmful action\nexecuted", "harmful-action execution rate"),
        ("Policy violation\nexecuted", "policy-violation execution rate"),
        ("Unsafe\ninvocation", "unsafe invocation rate"),
        ("Safe task\nblocked", "blocked-safe-task rate"),
    ]
    series = {a: {short: m[full].get(a) for short, full in metrics} for a in arms}
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    grouped_bars(ax, [s for s, _ in metrics], series, arms,
                 "Security & over-blocking (lower is better)")
    fig.savefig(out / "fig_security.png")
    plt.close(fig)


def fig_per_category(analysis, arms, out: Path):
    cats = list(analysis["per_category"])
    labels = [c.replace("_", "\n") for c in cats]
    series = {a: {lbl: analysis["per_category"][c].get(a)
                  for c, lbl in zip(cats, labels)} for a in arms}
    fig, ax = plt.subplots(figsize=(10, 4.6))
    grouped_bars(ax, labels, series, arms, "Decision accuracy by scenario category")
    ax.set_xticklabels(labels, fontsize=8)
    fig.savefig(out / "fig_per_category.png")
    plt.close(fig)


def fig_ladder(ladder, out: Path):
    """ladder: ordered list of (model_label, analysis)."""
    models = [lbl for lbl, _ in ladder]
    panels = [("decision accuracy", "Decision accuracy (higher better)"),
              ("harmful-action execution rate", "Harmful-action execution (lower better)")]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    for ax, (metric, title) in zip(axes, panels):
        maps = [_metric_map(a) for _, a in ladder]
        for arm in ARM_ORDER:
            if arm not in maps[0].get(metric, {}):
                continue
            ys = [100 * (mp[metric].get(arm) or 0) for mp in maps]
            ax.plot(models, ys, marker="o", markersize=8, linewidth=2,
                    color=ARM_COLOR[arm], label=ARM_LABEL[arm])
            for xi, yv in zip(models, ys):
                ax.annotate(f"{yv:.0f}", (xi, yv), fontsize=8, color="#333",
                            ha="center", va="bottom", xytext=(0, 5),
                            textcoords="offset points")
        ax.set_title(title, loc="left", weight="bold")
        _style_axis(ax)
        ax.set_xlabel("planner model (increasing capability)")
    axes[0].legend(frameon=False, ncol=3, loc="upper center",
                   bbox_to_anchor=(1.05, -0.16), fontsize=9)
    fig.savefig(out / "fig_model_ladder.png")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate thesis figures")
    parser.add_argument("records", nargs="?", default=None)
    parser.add_argument("--ladder", nargs="+", default=None,
                        help="model=records.jsonl pairs, in capability order")
    parser.add_argument("--out", default="figures")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset()

    if args.records:
        analysis = analyze(load_records(Path(args.records)), dataset)
        arms = [a for a in ARM_ORDER if a in analysis["arms"]]
        fig_headline(analysis, arms, out)
        fig_security(analysis, arms, out)
        fig_per_category(analysis, arms, out)
        print(f"wrote headline/security/per-category figures to {out}")

    if args.ladder:
        ladder = []
        for pair in args.ladder:
            label, path = pair.split("=", 1)
            ladder.append((label, analyze(load_records(Path(path)), dataset)))
        fig_ladder(ladder, out)
        print(f"wrote model-ladder figure to {out}")


if __name__ == "__main__":
    main()
