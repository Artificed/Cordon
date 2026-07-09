#!/usr/bin/env python3
"""Assemble the self-contained results dashboard (embeds figures as data URIs)."""
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "figures"
OUT = ROOT.parent / ".claude-research" / "results" / "dashboard.html"


def data_uri(name: str) -> str:
    b = (FIG / name).read_bytes()
    return "data:image/png;base64," + base64.b64encode(b).decode()


CSS = """
:root{
  --bg:#f6f7f8; --surface:#ffffff; --ink:#171b1f; --ink-2:#4a5560; --ink-3:#78838d;
  --line:#e2e6ea; --accent:#009E73;
  --baseline:#D55E00; --prompt:#0072B2; --cordon:#009E73;
  --plate:#ffffff; --shadow:0 1px 2px rgba(20,30,40,.06),0 8px 24px rgba(20,30,40,.05);
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0f1317; --surface:#161b20; --ink:#e7ecef; --ink-2:#a7b2bb; --ink-3:#6f7b84;
    --line:#252c33; --accent:#2bb98f; --plate:#f4f5f6;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.35);
  }
}
:root[data-theme="light"]{
  --bg:#f6f7f8; --surface:#ffffff; --ink:#171b1f; --ink-2:#4a5560; --ink-3:#78838d;
  --line:#e2e6ea; --accent:#009E73; --plate:#ffffff;
  --shadow:0 1px 2px rgba(20,30,40,.06),0 8px 24px rgba(20,30,40,.05);
}
:root[data-theme="dark"]{
  --bg:#0f1317; --surface:#161b20; --ink:#e7ecef; --ink-2:#a7b2bb; --ink-3:#6f7b84;
  --line:#252c33; --accent:#2bb98f; --plate:#f4f5f6;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.35);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  line-height:1.6;-webkit-font-smoothing:antialiased}
.mono{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace}
.wrap{max-width:1000px;margin:0 auto;padding:clamp(1.5rem,4vw,3.5rem) clamp(1.1rem,4vw,2rem)}
.eyebrow{font-family:ui-monospace,monospace;font-size:.72rem;letter-spacing:.22em;
  text-transform:uppercase;color:var(--accent);font-weight:600}
h1{font-size:clamp(1.9rem,5vw,3rem);line-height:1.08;letter-spacing:-.02em;margin:.5rem 0 .3rem;
  text-wrap:balance;font-weight:800}
h2{font-size:clamp(1.25rem,3vw,1.6rem);letter-spacing:-.01em;margin:0 0 .2rem;text-wrap:balance;font-weight:750}
.lede{font-size:clamp(1.05rem,2vw,1.2rem);color:var(--ink-2);max-width:60ch;margin:.4rem 0 0}
section{margin-top:clamp(2.6rem,6vw,4rem)}
.sec-head{border-top:1px solid var(--line);padding-top:1.4rem;margin-bottom:1.3rem}
.sec-head .eyebrow{color:var(--ink-3)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem;margin-top:2rem}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:1.15rem 1.2rem;
  box-shadow:var(--shadow)}
.tile .k{font-family:ui-monospace,monospace;font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;
  color:var(--ink-3)}
.tile .v{font-size:2.1rem;font-weight:800;letter-spacing:-.02em;margin:.25rem 0 .1rem;
  font-variant-numeric:tabular-nums;color:var(--cordon)}
.tile .d{font-size:.85rem;color:var(--ink-2)}
.tile .d b{color:var(--baseline);font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;margin-top:.4rem}
.tbl-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:14px;background:var(--surface);
  box-shadow:var(--shadow)}
th,td{padding:.7rem .9rem;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
thead th{font-family:ui-monospace,monospace;font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;
  color:var(--ink-3);font-weight:600}
tbody tr:last-child td{border-bottom:none}
td.metric{color:var(--ink-2);text-align:left}
.dot{display:inline-block;width:.6rem;height:.6rem;border-radius:50%;margin-right:.45rem;vertical-align:baseline}
.b-baseline{color:var(--baseline)} .b-prompt{color:var(--prompt)} .b-cordon{color:var(--cordon);font-weight:700}
.best{font-weight:750}
.plate{background:var(--plate);border:1px solid var(--line);border-radius:14px;padding:1rem;margin-top:1.3rem;
  box-shadow:var(--shadow)}
.plate img{width:100%;height:auto;display:block;border-radius:6px}
.plate figcaption{font-size:.82rem;color:#5a6570;margin-top:.7rem;padding:0 .3rem;font-family:ui-monospace,monospace}
.callout{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:10px;padding:1.1rem 1.3rem;margin-top:1.3rem}
.callout .q{font-weight:700;color:var(--ink)} .callout p{margin:.4rem 0 0;color:var(--ink-2)}
p.note{color:var(--ink-2);max-width:64ch}
ul.clean{list-style:none;padding:0;margin:1rem 0 0;display:grid;gap:.6rem}
ul.clean li{padding-left:1.3rem;position:relative;color:var(--ink-2);max-width:66ch}
ul.clean li::before{content:"";position:absolute;left:0;top:.62em;width:.5rem;height:.5rem;border-radius:2px;
  background:var(--accent)}
ul.clean li b{color:var(--ink)}
footer{margin-top:clamp(3rem,7vw,4.5rem);border-top:1px solid var(--line);padding-top:1.4rem;
  color:var(--ink-3);font-size:.85rem;display:flex;flex-wrap:wrap;gap:1.4rem;justify-content:space-between}
footer .mono{color:var(--ink-2)}
"""


def tile(k, v, d):
    return f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div><div class="d">{d}</div></div>'


def row(metric, b, p, c, best="c", note=""):
    cls = {"b": ("best", "", ""), "p": ("", "best", ""), "c": ("", "", "best")}[best]
    return (f'<tr><td class="metric">{metric}{note}</td>'
            f'<td class="b-baseline {cls[0]}">{b}</td>'
            f'<td class="b-prompt {cls[1]}">{p}</td>'
            f'<td class="b-cordon {cls[2]}">{c}</td></tr>')


HTML = f"""<div class="wrap">
<header>
  <div class="eyebrow">Cordon &middot; Experiment Results</div>
  <h1>A guardrailed control plane makes an LLM cloud agent safe &mdash; prompting alone does not.</h1>
  <p class="lede">240 infrastructure scenarios &times; 3 configurations &times; 3 repetitions, run with real
  LLM planners against a LocalStack cloud. The ungoverned agent executes <b>94%</b> of harmful actions;
  Cordon executes <b>0%</b> &mdash; while completing every safe task.</p>
  <div class="tiles">
    {tile("Decision accuracy", "98.6%", "Cordon vs baseline <b>41.7%</b>")}
    {tile("Harmful execution", "0.0%", "baseline <b>94.4%</b>")}
    {tile("Drift correction", "100%", "baseline <b>0%</b>")}
    {tile("Guardrail overhead", "9&nbsp;ms", "0.2% of planner latency")}
  </div>
</header>

<section>
  <div class="sec-head"><div class="eyebrow">01 &middot; Three-arm comparison</div>
    <h2>The same LLM, three levels of control</h2></div>
  <p class="note">Every arm shares the same planner, action schema, execution interface and environment.
  <span class="b-baseline">Baseline</span> executes any structurally valid plan;
  <span class="b-prompt">Prompt-guardrails</span> puts the policies in the planner's prompt;
  <span class="b-cordon">Cordon</span> enforces them in a deterministic control plane. Planner: gpt-5-mini.</p>
  <div class="tbl-wrap"><table>
    <thead><tr><th>Metric</th>
      <th><span class="dot" style="background:var(--baseline)"></span>Baseline</th>
      <th><span class="dot" style="background:var(--prompt)"></span>Prompt-guard</th>
      <th><span class="dot" style="background:var(--cordon)"></span>Cordon</th></tr></thead>
    <tbody>
      {row("Decision accuracy", "41.7%", "75.1%", "98.6%")}
      {row("Harmful-action execution", "94.4%", "0.0%", "0.0%", best="c")}
      {row("Policy-violation execution", "100%", "0.0%", "0.0%")}
      {row("Safe-task completion", "77.8%", "78.1%", "100%")}
      {row("Drift correction", "0%", "1.7%", "100%")}
      {row("Task refusal (over-blocking)", "4.2%", "50.1%", "4.2%")}
    </tbody>
  </table></div>
  <div class="callout"><span class="q">Prompting is not enforcement.</span>
    <p>Given the same policies, prompt-guardrails buys safety by refusing <b>half of all tasks</b> &mdash; its
    safe-task completion (78%) barely beats the baseline. It cannot escalate high-risk actions for approval
    or reconcile drift. Only the control plane gives graduated, stateful decisions at full utility.</p></div>
  <figure class="plate"><img alt="Decision accuracy by scenario category" src="{data_uri('fig_per_category.png')}">
    <figcaption>Decision accuracy by category. The gaps are drift (only Cordon reconciles), high-risk
    escalation (only Cordon), and policy / network / adversarial enforcement.</figcaption></figure>
</section>

<section>
  <div class="sec-head"><div class="eyebrow">02 &middot; Planner-model ladder</div>
    <h2>&ldquo;Won&rsquo;t better models just be safe on their own?&rdquo;</h2></div>
  <p class="note">The same experiment across three planner scales &mdash; gpt-5-nano, gpt-5-mini, gpt-5.5 &mdash;
  measuring whether the ungoverned agent gets safer as the model improves.</p>
  <figure class="plate"><img alt="Model ladder: decision accuracy and harmful execution" src="{data_uri('fig_model_ladder.png')}">
    <figcaption>Left: decision accuracy. Right: harmful-action execution. The gaps hold across model capability.</figcaption></figure>
  <div class="callout"><span class="q">No &mdash; capability is not safety.</span>
    <p>The ungoverned baseline still executes <b>83%</b> of harmful actions even with the frontier gpt-5.5.
    Prompt-guardrails <b>leaks with the weaker model</b> (1.1% harmful on nano) and only reaches zero once the
    planner is strong enough. Cordon holds harmful execution at <b>0% on all three models</b> &mdash; enforcement
    is deterministic and model-independent.</p></div>
</section>

<section>
  <div class="sec-head"><div class="eyebrow">03 &middot; Ablation</div>
    <h2>Does every layer earn its place?</h2></div>
  <p class="note">Each bar removes one layer and replays the same plans (5,040 runs, no new API calls),
  isolating that layer's contribution to decision accuracy.</p>
  <figure class="plate"><img alt="Decision accuracy when one layer is removed" src="{data_uri('fig_ablation.png')}">
    <figcaption>Full Cordon (green) vs each single-layer ablation.</figcaption></figure>
  <ul class="clean">
    <li><b>Policy</b> is load-bearing &mdash; removing it drops accuracy to 70.8% and lets 100% of policy violations execute.</li>
    <li><b>Risk</b> owns escalation &mdash; without it, 50% of harmful production deletes execute instead of waiting for approval.</li>
    <li><b>Reconcile</b> solely owns drift correction (100% &rarr; 0%).</li>
    <li><b>Adversarial</b> is defense-in-depth &mdash; removing it keeps harmful execution at 0% because policy and risk still catch the payloads.</li>
    <li><b>Capability &amp; state</b> show no change here &mdash; the strict output schema already constrains actions. Honest, not overclaimed.</li>
  </ul>
</section>

<section>
  <div class="sec-head"><div class="eyebrow">04 &middot; Method &amp; integrity</div>
    <h2>How the numbers were produced</h2></div>
  <ul class="clean">
    <li>Metrics computed by an <b>offline judge</b> against a machine-checkable oracle &mdash; the system never grades itself; the baseline's violations are judged post-hoc against the same policies.</li>
    <li>Baseline and Cordon share the sampled plan per scenario &mdash; a <b>paired design</b> isolating the control plane. McNemar exact test: <b>p = 7.6&times;10<sup>&minus;124</sup></b>.</li>
    <li>Running the experiment surfaced <b>three real defects</b> a paper design would have missed (an async-delete race, a state-model aliasing bug across repetitions, a policy rule fooled by a malformed encryption action) &mdash; all fixed with regression tests.</li>
    <li>Rates carry Wilson 95% intervals; guardrail cost is measured <b>per layer</b>, not end-to-end.</li>
  </ul>
</section>

<footer>
  <span class="mono">240 scenarios &middot; 3 arms &middot; 3 models &middot; 123 tests &middot; ~$5.27 API spend</span>
  <span class="mono">Cordon &mdash; Guardrailed Intent-Driven Control Plane</span>
</footer>
</div>"""

OUT.write_text(f"<style>{CSS}</style>\n{HTML}")
print(f"wrote {OUT} ({OUT.stat().st_size//1024} KB)")
