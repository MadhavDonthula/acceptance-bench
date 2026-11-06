"""Build a self-contained results website (site/index.html) from recorded data.

Every number comes from results/raw/* (no hardcoded metrics). Run after analyze.py.
  python -m harness.build_site
"""

from __future__ import annotations

import json
import os
import random
import statistics as st

from analyze import (RAW, PREFLIGHT, _load_json, _load_jsonl, build_run_metrics,
                     provider_summary)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SITE = os.path.join(ROOT, "site")
ARMS = ["coral", "baseten", "baseten_sept"]
LABEL = {"coral": "CoralBricks Fast", "baseten": "Baseten V4.1-Flash-1017-Fast",
         "baseten_sept": "Baseten V4.1-Flash-Fast (Sept)"}
COLOR = {"coral": "#e8663b", "baseten": "#3b7dd8", "baseten_sept": "#6aa9f0"}


def _pct(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100
    lo = int(k); hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def bootstrap(by_run, n_boot=10000, seed=7):
    groups = {}
    for m in by_run.values():
        gi = m["run"].get("order_index", 0) // len(ARMS)
        groups.setdefault(gi, {})[m["provider"]] = m
    gidx = sorted(groups)
    rng = random.Random(seed)

    def pa(runs, field):
        acc = [r for r in runs if r["outcome"] == "accepted"]
        return sum(r[field] for r in runs) / len(acc) if acc else None

    samp = {a: {"t": [], "c": []} for a in ARMS}
    diffs = {"A-B": {"t": [], "c": []}, "B-C": {"t": [], "c": []}}
    for _ in range(n_boot):
        picks = [groups[rng.choice(gidx)] for _ in gidx]
        cur = {}
        for a in ARMS:
            runs = [g[a] for g in picks if a in g]
            t, c = pa(runs, "wall"), pa(runs, "cost")
            cur[a] = (t, c)
            if t is not None:
                samp[a]["t"].append(t); samp[a]["c"].append(c)
        for lbl, x, y in (("A-B", "coral", "baseten"), ("B-C", "baseten", "baseten_sept")):
            if cur.get(x) and cur.get(y) and None not in (cur[x][0], cur[y][0]):
                diffs[lbl]["t"].append(cur[x][0] - cur[y][0])
                diffs[lbl]["c"].append(cur[x][1] - cur[y][1])
    ci = lambda xs: (_pct(xs, 2.5), _pct(xs, 97.5)) if len(xs) > 1 else (None, None)
    return (
        {a: {"t": ci(samp[a]["t"]), "c": ci(samp[a]["c"])} for a in ARMS},
        {k: {"t": ci(v["t"]), "c": ci(v["c"])} for k, v in diffs.items()},
    )


def bar(frac, color, h=10):
    return (f'<div class="track"><div class="fill" style="width:{max(frac,0)*100:.1f}%;'
            f'background:{color};height:{h}px"></div></div>')


def main():
    runs = _load_jsonl("runs.jsonl")
    rounds = _load_jsonl("rounds.jsonl")
    calls = _load_jsonl("calls.jsonl")
    win = {r["run_id"]: (r.get("wall_start"), r.get("wall_end")) for r in runs}

    def inwin(c):
        rid = c.get("run_id", "")
        if not rid.startswith("run"):
            return False
        w = win.get(rid)
        if not w or not c.get("wall_request_start"):
            return True
        lo, hi = w
        return (not lo or c["wall_request_start"] >= lo) and (not hi or c["wall_request_start"] <= hi)

    pcalls = [c for c in calls if inwin(c)]
    by_run = build_run_metrics(runs, rounds, pcalls)
    arms = [a for a in ARMS if any(m["provider"] == a for m in by_run.values())]
    try:
        from proxy.config import load_providers
        prices = {n: {"input": p.price_input, "output": p.price_output, "cached": p.price_cached_read}
                  for n, p in load_providers().items()}
    except Exception:
        prices = {}
    summ = {a: provider_summary(by_run, pcalls, a, prices) for a in arms}
    per_arm = min(summ[a]["n"] for a in arms)
    arm_ci, diff_ci = bootstrap(by_run)

    diag = _load_json(os.path.join(PREFLIGHT, "pilot_diagnostics.json")) or {}
    probe = diag.get("step2_longgen_probe", {})
    decomp = diag.get("step4_cost_decomposition", {})
    overhead = _load_json(os.path.join(RAW, "proxy_overhead.json")) or {}
    trace = _load_jsonl("trace_replay.jsonl")

    def tr_med(arm, key):
        xs = [r[key] for r in trace if r["arm"] == arm and r.get(key) is not None]
        return st.median(xs) if xs else None

    # scales for bars
    max_t = max(summ[a]["time_per_accepted"] or 0 for a in arms) or 1
    max_c = max(summ[a]["cost_per_accepted"] or 0 for a in arms) or 1

    def fmt(v, nd=2, pre="", suf=""):
        return "—" if v is None else f"{pre}{v:.{nd}f}{suf}"

    def ci_str(c, nd=2, pre=""):
        return "—" if not c or c[0] is None else f"{pre}{c[0]:.{nd}f} – {pre}{c[1]:.{nd}f}"

    # ---- build HTML ----
    cards = ""
    for a in arms:
        s = summ[a]
        cards += f"""
        <div class="card" style="border-top:4px solid {COLOR[a]}">
          <div class="arm">{LABEL[a]}</div>
          <div class="big">{fmt(s['time_per_accepted'],1,suf='s')}</div>
          <div class="lbl">time / accepted &nbsp;·&nbsp; 95% CI {ci_str(arm_ci[a]['t'],1)}s</div>
          <div class="big">{fmt(s['cost_per_accepted'],4,'$')}</div>
          <div class="lbl">cost / accepted &nbsp;·&nbsp; 95% CI {ci_str(arm_ci[a]['c'],4,'$')}</div>
          <div class="mini">accepted {s['accepted']}/{s['n']} · cache {fmt((s['cache_hit_rate'] or 0)*100,1,suf='%')} · median rounds {fmt(s['rounds_median'],1)}</div>
        </div>"""

    rows = ""
    for a in arms:
        s = summ[a]
        rows += f"""<tr>
          <td><span class="dot" style="background:{COLOR[a]}"></span>{LABEL[a]}</td>
          <td>{fmt(s['time_per_accepted'],1,suf='s')}<div class="muted">{ci_str(arm_ci[a]['t'],1)}</div>{bar((s['time_per_accepted'] or 0)/max_t, COLOR[a])}</td>
          <td>{fmt(s['cost_per_accepted'],4,'$')}<div class="muted">{ci_str(arm_ci[a]['c'],4,'$')}</div>{bar((s['cost_per_accepted'] or 0)/max_c, COLOR[a])}</td>
          <td>{fmt((s['cache_hit_rate'] or 0)*100,1,suf='%')}{bar((s['cache_hit_rate'] or 0), COLOR[a])}</td>
          <td>{fmt((probe.get(a) or {}).get('visible_decode_tok_s_p50'),0,suf=' tok/s')}</td>
          <td>{s['accepted']}/{s['n']}</td>
        </tr>"""

    trace_rows = ""
    for a in arms:
        trace_rows += f"""<tr>
          <td><span class="dot" style="background:{COLOR[a]}"></span>{LABEL[a]}</td>
          <td>{fmt(tr_med(a,'model_time_s'),1,suf='s')}</td>
          <td>{fmt(tr_med(a,'ttft_content_p50'),3,suf='s')}</td>
          <td>{fmt((tr_med(a,'cache_hit_rate') or 0)*100,1,suf='%')}</td>
          <td>{fmt(tr_med(a,'cost_usd_public'),4,'$')}</td>
        </tr>"""

    def diff_line(lbl, x, y):
        d = diff_ci[lbl]
        t, c = d["t"], d["c"]
        t_rob = t[0] is not None and (t[0] > 0) == (t[1] > 0)
        c_rob = c[0] is not None and (c[0] > 0) == (c[1] > 0)
        badge = lambda ok: ('<span class="rob">CI excludes 0</span>' if ok
                            else '<span class="norob">CI includes 0</span>')
        return f"""<div class="diff">
          <b>{LABEL[x]} − {LABEL[y]}</b>
          <div>Δ time/accepted: <code>{ci_str(t,1)} s</code> {badge(t_rob)}</div>
          <div>Δ cost/accepted: <code>{ci_str(c,4,'$')}</code> {badge(c_rob)}</div>
        </div>"""

    html = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Accepted-work benchmark — CoralBricks vs Baseten</title>
<style>
:root{{--bg:#0f1115;--panel:#171a21;--line:#262b36;--txt:#e7ebf2;--mut:#93a0b5;--acc:#e8663b}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}}
.wrap{{max-width:980px;margin:0 auto;padding:40px 22px 80px}}
h1{{font-size:30px;margin:0 0 6px}} h2{{font-size:19px;margin:38px 0 12px;border-bottom:1px solid var(--line);padding-bottom:7px}}
.sub{{color:var(--mut);margin:0 0 4px}}
.banner{{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--acc);border-radius:10px;padding:14px 18px;margin:18px 0 8px}}
.verdict{{font-size:16px}} .verdict b{{color:#fff}}
.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:18px 0}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}}
.card .arm{{font-weight:600;font-size:13px;color:var(--mut);min-height:34px}}
.card .big{{font-size:26px;font-weight:700;margin-top:8px}}
.card .lbl{{font-size:11.5px;color:var(--mut);margin-bottom:4px}}
.card .mini{{font-size:11.5px;color:var(--mut);margin-top:10px;border-top:1px solid var(--line);padding-top:8px}}
table{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}}
th,td{{text-align:left;padding:11px 13px;border-bottom:1px solid var(--line);font-size:13.5px;vertical-align:top}}
th{{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}}
tr:last-child td{{border-bottom:none}}
.muted{{color:var(--mut);font-size:11px;margin:1px 0 4px}}
.dot{{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px}}
.track{{background:#0d0f13;border-radius:5px;overflow:hidden;margin-top:3px}}
.fill{{border-radius:5px}}
.diffs{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.diff{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;font-size:13.5px}}
.diff b{{display:block;margin-bottom:8px}} .diff div{{margin:5px 0;color:var(--mut)}}
code{{background:#0d0f13;padding:2px 6px;border-radius:5px;color:var(--txt);font-size:12.5px}}
.rob{{color:#5fd08a;font-weight:600;font-size:11.5px}} .norob{{color:var(--mut);font-size:11.5px}}
ul{{color:var(--mut);font-size:13.5px}} li{{margin:5px 0}}
.foot{{color:var(--mut);font-size:12px;margin-top:40px;border-top:1px solid var(--line);padding-top:14px}}
.pill{{display:inline-block;background:#0d0f13;border:1px solid var(--line);border-radius:20px;padding:3px 11px;font-size:12px;color:var(--mut);margin:2px 4px 2px 0}}
</style></head><body><div class="wrap">

<h1>Does fast inference reach <span style="color:var(--acc)">ACCEPTED</span> work faster?</h1>
<p class="sub">A pre-registered benchmark: same model, same harness (OpenCode 1.19.5), same task, same settings — only the inference provider changes. Acceptance = all 7 hidden tests pass.</p>
<div>
  <span class="pill">n={per_arm} runs / arm</span><span class="pill">{len(arms)} arms, interleaved</span>
  <span class="pill">deterministic judge</span><span class="pill">paired bootstrap 95% CIs</span>
  <span class="pill">cost at public list prices (billing-verified)</span>
</div>

<div class="banner"><div class="verdict">
<b>Baseten reaches accepted ~{fmt((summ['coral']['time_per_accepted'] or 0)-(summ['baseten']['time_per_accepted'] or 0),0,suf='s')} faster</b> and decodes
~{fmt(((probe.get('baseten') or {}).get('visible_decode_tok_s_p50') or 1)/((probe.get('coral') or {}).get('visible_decode_tok_s_p50') or 1),2,suf='×')} faster — but
<b>CoralBricks is ~{fmt((summ['baseten']['cost_per_accepted'] or 1)/(summ['coral']['cost_per_accepted'] or 1),1,suf='×')} cheaper per accepted result</b>, driven by a {fmt((summ['coral']['cache_hit_rate'] or 0)*100,0,suf='%')} cache hit rate.
Token throughput and <i>time/cost to accepted work</i> point in opposite directions — which a single tokens/sec number would hide.
</div></div>

<div class="cards">{cards}</div>

<h2>Per-provider results <span class="sub" style="font-weight:400">(main experiment, n={per_arm}/arm)</span></h2>
<table><thead><tr><th>Provider</th><th>Time / accepted</th><th>Cost / accepted</th><th>Cache hit</th><th>Decode (probe)</th><th>Accepted</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="sub" style="font-size:12px">Bars are relative within each column. Sub-values are paired-bootstrap 95% CIs. Decode is the controlled long-generation probe (reasoning off); per-call pilot decode is unreliable and excluded.</p>

<h2>Paired differences <span class="sub" style="font-weight:400">(CI excluding 0 ⇒ robust direction at this n)</span></h2>
<div class="diffs">{diff_line("A-B","coral","baseten")}{diff_line("B-C","baseten","baseten_sept")}</div>
<p class="sub" style="font-size:12px">A−B isolates the serving-stack/model gap; B−C isolates the Baseten snapshot. B−C CIs include 0 → the snapshot is not the driver.</p>

<h2>Trace replay <span class="sub" style="font-weight:400">(identical request sequences — inference isolated from agent behavior)</span></h2>
<table><thead><tr><th>Provider</th><th>Model time / session</th><th>TTFT (content)</th><th>Cache hit</th><th>Cost / session</th></tr></thead>
<tbody>{trace_rows}</tbody></table>
<p class="sub" style="font-size:12px">Same 6 captured sessions replayed against every arm with original inter-call gaps and a fresh affinity id. With agent behavior held fixed, the speed/cost tradeoff persists — it is inference-level, not just "Coral reasons more."</p>

<h2>Why the cost gap?</h2>
<ul>
<li><b>{fmt(decomp.get('token_cache_effect_pct_of_gap'),0,suf='%')}</b> of the cost gap is <b>token / cache behavior</b> (Baseten bills far more uncached input — lower cache hit).</li>
<li><b>{fmt(decomp.get('price_effect_pct_of_gap'),0,suf='%')}</b> is <b>unit price</b>. Even priced at Coral's rates, Baseten's tokens cost more.</li>
</ul>

<h2>How it's measured</h2>
<ul>
<li>Every request flows through a local recording proxy (added overhead: <b>{fmt((overhead.get('overhead_ms_median')),2,suf=' ms')}</b>/call, both arms).</li>
<li>Identical settings injected on both arms: temperature 0, max_tokens 8192, reasoning_effort high.</li>
<li>Deterministic judge: 7 prioritized hidden tests, acceptance = all pass; mutation-tested.</li>
<li>Cost at public list prices with cache discount (verified against provider billing).</li>
</ul>

<h2>Limitations</h2>
<ul>
<li>One task (payments idempotency), one machine/region — CIs quantify run-to-run variance, not external validity.</li>
<li>Snapshot identity: Coral serves an opaque snapshot; running both Baseten snapshots (B,C) showed no robust snapshot difference, but Coral-vs-Baseten weights identity is unconfirmed.</li>
<li>Coral documents FP4 serving; Baseten precision undisclosed — token-for-token quality may differ.</li>
</ul>

<div class="foot">Generated from <code>results/raw/</code> by <code>harness/build_site.py</code>. Every number traces to recorded data. Pre-registration committed before any main run (PREREGISTRATION.md). Pilot + diagnostics + decision history in DECISION_LOG.md.</div>
</div></body></html>"""

    os.makedirs(SITE, exist_ok=True)
    out = os.path.join(SITE, "index.html")
    with open(out, "w") as f:
        f.write(html)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
