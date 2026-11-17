"""Terminal, Markdown and HTML reports. Every value is treated as untrusted and escaped."""
from __future__ import annotations

import html
import json
from typing import Any

from .sanitize import clean_text

_COLORS = {"OK": "32", "SILENT_FAILURE": "31", "REPORTED_FAILURE": "33", "UNVERIFIED": "35"}
_STATUS_MARK = {"pass": "PASS", "fail": "FAIL", "error": "ERR "}


def _c(text: str, code: str, color: bool) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if color else text


def render_terminal(d: dict[str, Any], color: bool = False) -> str:
    v, job, run = d["verdict"], d["job"], d["run"]
    lines = [
        _c(f"[{v['code']}] {job['name']}: {v['label']}", _COLORS.get(v["code"], "0") + ";1", color),
        f"  reason:   {v['reason']}",
        f"  schedule: {job['schedule'] or '-'}   exit code: {run['exit_code']}   "
        f"duration: {run['duration_seconds'] if run['duration_seconds'] is not None else '-'}s",
    ]
    if job["expected_outcome"]:
        lines.append(f"  expected: {job['expected_outcome']}")
    lines.append("  checks:")
    for c in d["checks"]:
        mark = _STATUS_MARK.get(c["status"], c["status"])
        col = {"pass": "32", "fail": "31", "error": "35"}.get(c["status"], "0")
        lines.append(f"    {_c(mark, col, color)} {c['name']}")
        if c["status"] != "pass":
            lines.append(f"         expected: {c['expected']}")
            lines.append(f"         actual:   {c['actual']}")
            if c.get("message"):
                lines.append(f"         note:     {c['message']}")
    dg = d.get("diagnosis")
    if dg:
        src = dg["provider"] + (f" ({dg['model']})" if dg.get("model") else "")
        lines += [f"  diagnosis [{src}, uncertainty: {dg['uncertainty']}]:",
                  f"    what:  {dg['diagnosis']}",
                  f"    cause: {dg['likely_cause']}",
                  f"    fix:   {dg['suggested_fix']}  (suggestion only, not applied)",
                  f"    next:  {dg['next_step']}"]
        if dg.get("fallback_reason"):
            lines.append(f"    note:  {dg['fallback_reason']}")
        for w in dg.get("warnings", []):
            lines.append(_c(f"    warning: {w}", "33", color))
    return "\n".join(lines)


def _md_cell(text: Any) -> str:
    return clean_text(text, 400, keep="head").replace("|", "\\|").replace("\n", " ")


def render_markdown(d: dict[str, Any]) -> str:
    v, job, run = d["verdict"], d["job"], d["run"]
    out = [f"# {v['code']}: {_md_cell(job['name'])}", "", f"**{v['label']}**: {_md_cell(v['reason'])}", "",
           f"- Schedule: {_md_cell(job['schedule'] or '-')}", f"- Command: `{_md_cell(job['command'])}`",
           f"- Expected outcome: {_md_cell(job['expected_outcome'] or '-')}",
           f"- Exit code: {run['exit_code']}, timed out: {run['timed_out']}, started: {run['started_at_iso'] or '-'}, "
           f"duration: {run['duration_seconds']}s", "", "## Checks", "", "| Status | Check | Expected | Actual |",
           "|---|---|---|---|"]
    for c in d["checks"]:
        out.append(f"| {c['status'].upper()} | {_md_cell(c['name'])} | {_md_cell(c['expected'])} | {_md_cell(c['actual'])} |")
    dg = d.get("diagnosis")
    if dg:
        out += ["", f"## Diagnosis ({_md_cell(dg['provider'])}, uncertainty: {dg['uncertainty']})", "",
                f"- **What happened:** {_md_cell(dg['diagnosis'])}", f"- **Likely cause:** {_md_cell(dg['likely_cause'])}",
                f"- **Suggested fix (not applied):** {_md_cell(dg['suggested_fix'])}",
                f"- **Next step:** {_md_cell(dg['next_step'])}"]
        if dg.get("fallback_reason"):
            out.append(f"- _Note: {_md_cell(dg['fallback_reason'])}_")
    return "\n".join(out) + "\n"


_CSS = """
:root{--bg:#f7f7f5;--fg:#1d1d1f;--muted:#6b6b70;--card:#fff;--line:#e3e3e0;--ok:#1f7a3f;--bad:#b42318;
--warn:#a15c00;--unv:#7a3fa1;--code:#f0f0ed}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececec;--muted:#9a9aa0;--card:#1d1d20;--line:#2e2e33;
--ok:#4cc27a;--bad:#ff6b5e;--warn:#f0a83a;--unv:#c08cf0;--code:#26262a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:28px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:8px 0}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:600;font-size:13px;color:#fff}
.OK{background:var(--ok)}.SILENT_FAILURE{background:var(--bad)}.REPORTED_FAILURE{background:var(--warn)}
.UNVERIFIED{background:var(--unv)}.muted{color:var(--muted)}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:8px;border-top:1px solid var(--line);
vertical-align:top}th{color:var(--muted);font-weight:500;font-size:13px}
.s-pass{color:var(--ok);font-weight:600}.s-fail{color:var(--bad);font-weight:600}.s-error{color:var(--unv);font-weight:600}
pre{background:var(--code);padding:10px;border-radius:8px;overflow:auto;white-space:pre-wrap;word-break:break-word;
font-size:12.5px;max-height:360px}dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:0}
dt{color:var(--muted)}dd{margin:0}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:700px){.grid2{grid-template-columns:1fr}dl{grid-template-columns:1fr}}
.warn{border-left:4px solid var(--warn)}a{color:inherit}
"""


def _e(value: Any) -> str:
    return html.escape(clean_text(value, 4000, keep="head"), quote=True)


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><title>{_e(title)}</title><style>{_CSS}</style></head>"
            f"<body><main>{body}</main></body></html>")


def render_html(d: dict[str, Any]) -> str:
    v, job, run = d["verdict"], d["job"], d["run"]
    parts = [f"<p class='muted'>Automation Doctor report · {_e(run['started_at_iso'])}</p>",
             f"<h1>{_e(job['name'])}</h1>",
             f"<p><span class='badge {_e(v['code'])}'>{_e(v['code'])}</span> <strong>{_e(v['label'])}</strong></p>",
             f"<p>{_e(v['reason'])}</p>",
             "<div class='grid2'><div class='card'><h2>Expected outcome</h2>"
             f"<p>{_e(job['expected_outcome'] or '-')}</p></div><div class='card'><h2>Reported by the job</h2><dl>"
             f"<dt>Exit code</dt><dd>{_e(run['exit_code'])}</dd><dt>Timed out</dt><dd>{_e(run['timed_out'])}</dd>"
             f"<dt>Started</dt><dd>{_e(run['started_at_iso'] or '-')}</dd>"
             f"<dt>Duration</dt><dd>{_e(run['duration_seconds'])}s</dd>"
             f"<dt>Schedule</dt><dd>{_e(job['schedule'] or '-')}</dd>"
             f"<dt>Command</dt><dd><code>{_e(job['command'])}</code></dd></dl></div></div>"]

    dg = d.get("diagnosis")
    if dg:
        src = dg["provider"] + (f" · {dg['model']}" if dg.get("model") else "")
        lat = f" · {dg['latency_ms']} ms" if dg.get("latency_ms") else ""
        parts.append(f"<h2>Diagnosis <span class='muted'>({_e(src)}{_e(lat)} · uncertainty: "
                     f"{_e(dg['uncertainty'])})</span></h2><div class='card'><dl>"
                     f"<dt>What happened</dt><dd>{_e(dg['diagnosis'])}</dd>"
                     f"<dt>Likely cause</dt><dd>{_e(dg['likely_cause'])}</dd>"
                     f"<dt>Suggested fix</dt><dd>{_e(dg['suggested_fix'])} <em class='muted'>(suggestion only, "
                     f"not applied)</em></dd><dt>Next step</dt><dd>{_e(dg['next_step'])}</dd>"
                     f"<dt>Uncertainty</dt><dd>{_e(dg['uncertainty'])}: {_e(dg['uncertainty_reason'])}</dd></dl></div>")
        notes = ([dg["fallback_reason"]] if dg.get("fallback_reason") else []) + list(dg.get("warnings", []))
        for n in notes:
            parts.append(f"<div class='card warn'>{_e(n)}</div>")

    rows = "".join(f"<tr><td class='s-{_e(c['status'])}'>{_e(c['status'].upper())}</td><td>{_e(c['name'])}"
                   f"<div class='muted'>{_e(c['type'])}</div></td><td>{_e(c['expected'])}</td><td>{_e(c['actual'])}"
                   + (f"<div class='muted'>{_e(c['message'])}</div>" if c.get("message") else "") + "</td></tr>"
                   for c in d["checks"])
    parts.append("<h2>Outcome checks</h2><div class='card'><table><tr><th>Status</th><th>Check</th>"
                 f"<th>Expected</th><th>Actual</th></tr>{rows}</table></div>")
    for c in d["checks"]:
        if c["status"] != "pass" and c.get("evidence"):
            parts.append(f"<h2>Evidence: {_e(c['name'])}</h2>"
                         f"<pre>{_e(json.dumps(c['evidence'], indent=2, default=str))}</pre>")
    parts.append(f"<h2>stdout (tail)</h2><pre>{_e(run['stdout'][-3000:] or '(empty)')}</pre>")
    parts.append(f"<h2>stderr (tail)</h2><pre>{_e(run['stderr'][-3000:] or '(empty)')}</pre>")
    parts.append(f"<p class='muted'>{_e(d['tool'])} · job spec: {_e(job['spec'])} · Automation Doctor never "
                 "changes jobs; suggestions are for a human to review.</p>")
    return _page(f"{v['code']} {job['name']}", "".join(parts))


def render_index_html(summary: dict[str, Any]) -> str:
    rows = []
    for r in summary["cases"]:
        match = "yes" if r["verdict_matches"] else "NO"
        rows.append(f"<tr><td><a href='{_e(r['report_html'])}'>{_e(r['job'])}</a><div class='muted'>"
                    f"{_e(r['description'])}</div></td><td>{_e(r['expected_verdict'])}</td>"
                    f"<td><span class='badge {_e(r['verdict'])}'>{_e(r['verdict'])}</span></td><td>{match}</td>"
                    f"<td>{_e(r['diagnosis_provider'] or '-')}</td><td>{_e(r['cause_keyword_hit'])}</td></tr>")
    m = summary["metrics"]
    body = (f"<h1>Automation Doctor demo</h1><p class='muted'>{_e(summary['generated_at'])} · provider setting: "
            f"{_e(summary['provider_setting'])} · model: {_e(summary['model'])}</p>"
            f"<div class='card'><strong>{m['verdicts_correct']}/{m['cases']}</strong> scenarios classified as labelled · "
            f"alerts on healthy runs: {m['false_alerts']} · missed silent failures: {m['missed_failures']}<br>"
            f"<span class='muted'>Measured on this repo's own synthetic scenarios only. This is not a real-world "
            f"detection rate.</span></div>"
            "<div class='card'><table><tr><th>Scenario</th><th>Expected</th><th>Actual</th><th>Match</th>"
            f"<th>Diagnosis by</th><th>Cause keyword hit</th></tr>{''.join(rows)}</table></div>")
    return _page("Automation Doctor demo", body)
