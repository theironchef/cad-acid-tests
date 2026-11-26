"""Write docs/index.html (the GitHub Pages site) and SCOREBOARD.md from TESTS.md and results/*.jsonl.

A pipeline's score on a test is the number of stages that pass before the first stage that does not.
Pictures: each test's sketch (docs/img/<test>-sketch.png and docs/img/sketches.json, from tools/reference.py)
and, where a program passed every stage, its render (docs/img/<test>-<program version>.png, written by the
harness; tools/render.py names it). This script only links to them, so it needs no numpy.
"""
import collections
import glob
import html
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "expected"))
import values  # noqa: E402

IMG = os.path.join(ROOT, "docs", "img")


def slug(version):
    """As tools/render.py names a program's render (kept here so this script needs no numpy)."""
    return re.sub(r"[^a-z0-9.]+", "-", version.lower()).strip("-.")


def sketches():
    path = os.path.join(IMG, "sketches.json")
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}


def sketch_image(test):
    name = "%s-sketch.png" % test
    return name if os.path.exists(os.path.join(IMG, name)) else None


def render_image(test, ver):
    """The program's render of a test it passed in full, if the harness made one."""
    name = "%s-%s.png" % (test, slug(ver))
    return name if os.path.exists(os.path.join(IMG, name)) else None


def volume_line(info, final):
    """How the sketch's sampled volume compares with values.py."""
    if "volume" not in info:
        return "values.py gives this test no volume to compare it with"
    lo, hi = info["expected"]
    mid, n = (lo + hi) / 2, info["check_stage"]
    at = "" if n == final else " at stage %d, the last whose volume values.py pins down," % n
    num = lambda v: "{:,.0f}".format(v)
    if (hi - lo) / mid < 0.01:
        err = 100 * info["rel_err"]
        return "volume%s within %s%% of the analytic value" % (at, "%.2f" % err if err >= 0.01 else "0.01")
    if info["outside_rel"] == 0:
        return "volume%s %s mm&sup3;, inside the window values.py allows (%s to %s mm&sup3;)" % (
            at, num(info["volume"]), num(lo), num(hi))
    return "volume%s %s mm&sup3;, %.1f%% outside the window values.py allows (%s to %s mm&sup3;)" % (
        at, num(info["volume"]), 100 * info["outside_rel"], num(lo), num(hi))

WHY = {"error": "refused", "fail": "wrong result", "hang": "timeout"}


def parse_tests():
    """Each test's title, why, what it stresses and stages, from TESTS.md."""
    text = open(os.path.join(ROOT, "TESTS.md"), encoding="utf-8").read()
    out = {}
    for block in re.split(r"\n## ", text)[1:]:
        head, _, body = block.partition("\n")
        m = re.match(r"(acid-\d+) (.+)", head.strip())
        if not m:
            continue
        key, title = m.group(1), m.group(2)
        why = re.search(r"\*\*Why\.\*\* (.+?)\n\n", body, re.S)
        stress = re.search(r"\*\*What it stresses:\*\*\n((?:- .+\n)+)", body)
        stages = re.findall(r"^\d+\. (.+)$", body, re.M)
        notes = [p.strip() for p in re.split(r"\n\n", body)
                 if p.strip() and not p.startswith(("**", "-", "1.")) and not re.match(r"\d+\.", p.strip())]
        out[key] = {"title": title, "why": why.group(1).strip() if why else "",
                    "stresses": [l[2:].strip() for l in stress.group(1).splitlines()] if stress else [],
                    "stages": stages, "notes": notes}
    return out


# The kernel-only harnesses' pipelines: their results stay in results/, but the scoreboard and the
# site show programs only.
KERNEL_PIPELINES = ("solidcore", "solidcore-kernel", "fusion-kernel")


def load_runs():
    runs = collections.OrderedDict()
    for path in sorted(glob.glob(os.path.join(ROOT, "results", "*.jsonl"))):
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            if r["pipeline"] in KERNEL_PIPELINES:
                continue
            runs.setdefault((r["pipeline"], r.get("version", "?")), {}).setdefault(r["test"], {})[r["stage"]] = r
    return collections.OrderedDict(sorted(runs.items(), key=lambda kv: row_order(kv[0], runs)))


def release(ver):
    return tuple(int(x) if x.isdigit() else 0 for x in re.findall(r"\d+", ver))


def row_order(key, runs):
    """The latest SolidCraft release first, then the latest kernel-only runs (stage-faithful, then the
    neutral kernel-ops spec), then the other programs
    by name, then older SolidCraft releases and kernel-only runs, newest first."""
    pipe, ver = key
    if pipe in ("solidcraft", "solidcore", "solidcore-kernel"):
        latest = max((v for p, v in runs if p == pipe), key=release)
        first = ("solidcraft", "solidcore", "solidcore-kernel").index(pipe)
        if ver == latest:
            return (0, first, "", ())
        return (2, first, "", tuple(-x for x in release(ver)))
    return (1, 0, ver.lower(), ())


KERNEL_ONLY = "kernel only, no app"


def label(pipe, ver):
    """A results row's name: the kernel-only harness's rows say so."""
    return "%s (%s)" % (ver, KERNEL_ONLY) if pipe in ("solidcore", "solidcore-kernel") else ver


def score(per_test, test):
    n = len(values.TESTS[test])
    got = per_test.get(test, {})
    s = 0
    for k in range(1, n + 1):
        r = got.get(k)
        if r and r["outcome"] == "pass":
            s += 1
            continue
        return s, n, k, r
    return s, n, None, None


def md_inline(t):
    t = html.escape(t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"(?<![\w/])(solidcraft|stress-corpus)#(\d+)",
               lambda m: '<a href="https://github.com/%s/issues/%s">%s#%s</a>' % (
                   "tangency-labs/solidcraft" if m.group(1) == "solidcraft" else "dutchman-cad/stress-corpus",
                   m.group(2), m.group(1), m.group(2)), t)
    return t


def scoreboard_md(tests, runs):
    keys = list(values.TESTS)
    lines = ["# Scoreboard", "", "Generated by `tools/site.py`. The same table, with each test explained, is at "
             "https://theironchef.github.io/cad-acid-tests/.", "",
             "Each cell is the number of stages passed before the first one that does not, out of the test's stages.", "",
             "| Program | Total | " + " | ".join("[%s](docs/img/%s)" % (k, sketch_image(k)) if sketch_image(k) else k
                                               for k in keys) + " |", "|---|" + "---|" * (len(keys) + 1)]
    for (pipe, ver), per in runs.items():
        cells, tot, of = [], 0, 0
        for k in keys:
            s, n, stop, r = score(per, k)
            tot, of = tot + s, of + n
            if stop is None:
                img = render_image(k, ver)
                cells.append("[**%d/%d**](docs/img/%s)" % (s, n, img) if img else "**%d/%d**" % (s, n))
            else:
                cells.append("%d/%d %s" % (s, n, WHY.get(r["outcome"], "not run") if r else "not run"))
        lines.append("| %s | **%d/%d** | %s |" % (label(pipe, ver), tot, of, " | ".join(cells)))
    return "\n".join(lines) + "\n"


CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e4e1d8;--card:#ffffff;--accent:#2f5d8a;
--pass:#2e7d4f;--pass-bg:#e5f2ea;--part:#9a6a00;--part-bg:#fbf0d6;--fail:#a8322d;--fail-bg:#f8e3e1;--code:#f1efe8}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151514;--fg:#ecebe6;--muted:#a3a19a;
--line:#2f2e2b;--card:#1d1d1b;--accent:#8db6e0;--pass:#7fcf9c;--pass-bg:#183323;--part:#e7c36b;--part-bg:#3a2f12;
--fail:#f09a93;--fail-bg:#3d1c1a;--code:#262522}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1080px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:2rem;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:1.35rem;margin:40px 0 8px}
h3{font-size:1.15rem;margin:0 0 6px}
p{margin:8px 0}
a{color:var(--accent)}
.lede{color:var(--muted);max-width:760px}
code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:.9em}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card);margin-top:12px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:9px 10px;text-align:center;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left;position:sticky;left:0;background:var(--card)}
thead th{font-size:.8rem;color:var(--muted);font-weight:600}
thead th a{color:inherit;text-decoration:none}
tr:last-child td{border-bottom:0}
.cell{display:inline-block;min-width:54px;padding:3px 8px;border-radius:6px;font-weight:600}
.full{background:var(--pass-bg);color:var(--pass)}
.part{background:var(--part-bg);color:var(--part)}
.none{background:var(--fail-bg);color:var(--fail)}
.why{display:block;font-size:.72rem;font-weight:500;color:var(--muted);margin-top:2px}
.legend{color:var(--muted);font-size:.9rem;margin-top:8px}
.test{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:20px;margin:16px 0}
.test .kicker{color:var(--muted);font-size:.85rem;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:10px}
@media (max-width:760px){.grid{grid-template-columns:1fr}}
ul,ol{margin:6px 0;padding-left:22px}
li{margin:3px 0}
.stopped{font-size:.9rem;margin-top:12px;border-top:1px solid var(--line);padding-top:10px}
.stopped div{margin:4px 0}
.badge{font-weight:600}
footer{color:var(--muted);font-size:.85rem;margin-top:48px}
.thumb{display:block;text-decoration:none}
.thumb img{display:block;height:48px;width:64px;margin:5px auto 0;border-radius:4px;border:1px solid var(--line)}
thead .thumb img{height:40px;width:53px}
.thumb .icon{display:none;font-size:.95rem;line-height:1}
@media (max-width:760px){.thumb img{display:none}.thumb .icon{display:inline}}
.shape{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:14px 0}
.shape h3 a{color:inherit;text-decoration:none}
.shape .desc{color:var(--muted);margin:0 0 8px}
.pics{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.pics figure{margin:0}
.pics img{display:block;width:100%;height:auto;border-radius:6px;border:1px solid var(--line)}
.pics figcaption{font-size:.82rem;color:var(--muted);margin-top:4px}
.pics figcaption strong{color:var(--fg);font-weight:600}
.shape .note{font-size:.88rem;color:var(--muted);margin:8px 0 0}
"""


def thumb(name, alt):
    """A small clickable picture for the table; a link icon in its place on narrow screens."""
    return ('<a class="thumb" href="img/%s" title="%s"><img src="img/%s" alt="%s" width="64" height="48" loading="lazy">'
            '<span class="icon" aria-hidden="true">&#9635;</span></a>' % (name, html.escape(alt), name, html.escape(alt)))


def looks(tests, runs):
    """The section with each test's sketch and the renders of the programs that completed it."""
    info, cards = sketches(), []
    for k in values.TESTS:
        n = len(values.TESTS[k])
        title = tests.get(k, {}).get("title", values.NAMES[k])
        desc = title.split(": ", 1)[1] if ": " in title else ""
        figs = []
        if sketch_image(k):
            figs.append('<figure><img src="img/%s" alt="Sketch of %s, stage %d" loading="lazy"><figcaption>'
                        '<strong>Sketch</strong>: what stage %d should look like</figcaption></figure>'
                        % (sketch_image(k), k, n, n))
        for (pipe, ver), per in runs.items():
            _, _, stop, _ = score(per, k)
            img = render_image(k, ver) if stop is None else None
            if img:
                figs.append('<figure><img src="img/%s" alt="%s, %s" loading="lazy"><figcaption><strong>%s</strong>: '
                            'completed all %d stages</figcaption></figure>'
                            % (img, html.escape(label(pipe, ver)), k, html.escape(label(pipe, ver)), n))
        i = info.get(k)
        note = ("Sketch of the final stage, built from TESTS.md without a CAD kernel; %s. Approximate: %s."
                % (volume_line(i, n), html.escape(i["note"])) if i else "No sketch yet.")
        cards.append('<section class="shape" id="look-%s"><h3><a href="#%s">%s %s</a></h3><p class="desc">%s</p>'
                     '<div class="pics">%s</div><p class="note">%s</p></section>'
                     % (k, k, k, html.escape(values.NAMES[k]), md_inline(desc[:1].upper() + desc[1:]) + "." if desc else "",
                        "".join(figs), note))
    return "".join(cards)


def page(tests, runs):
    keys = list(values.TESTS)
    head = "".join('<th><a href="#%s">%s</a><br>%s%s</th>' % (
        k, k, html.escape(values.NAMES[k]),
        thumb(sketch_image(k), "Sketch of %s: what it should look like" % k) if sketch_image(k) else "") for k in keys)
    rows = []
    for (pipe, ver), per in runs.items():
        cells, tot, of = [], 0, 0
        for k in keys:
            s, n, stop, r = score(per, k)
            tot, of = tot + s, of + n
            cls = "full" if stop is None else ("none" if s == 0 else "part")
            why = "" if stop is None else '<span class="why">%s at %d</span>' % (
                WHY.get(r["outcome"], "not run") if r else "not run", stop)
            img = render_image(k, ver) if stop is None else None
            if img:
                why = thumb(img, "%s, %s: what it built, all %d stages complete" % (label(pipe, ver), k, n))
            cells.append('<td><span class="cell %s">%d/%d</span>%s</td>' % (cls, s, n, why))
        rows.append("<tr><td><strong>%s</strong></td><td><strong>%d/%d</strong></td>%s</tr>"
                    % (html.escape(label(pipe, ver)), tot, of, "".join(cells)))
    cards = []
    for k in keys:
        t = tests.get(k, {})
        stops = []
        for (pipe, ver), per in runs.items():
            s, n, stop, r = score(per, k)
            if stop is None:
                stops.append("<div><span class=\"badge\">%s</span>: all %d stages pass.</div>" % (html.escape(label(pipe, ver)), n))
            else:
                detail = (r.get("detail") or "").strip() if r else "not run"
                stops.append("<div><span class=\"badge\">%s</span>: %d/%d. Stopped at stage %d, %s: <code>%s</code></div>"
                             % (html.escape(label(pipe, ver)), s, n, stop, WHY.get(r["outcome"], "not run") if r else "not run",
                                html.escape(detail[:260])))
        cards.append("""<section class="test" id="%s"><div class="kicker">%s</div><h3>%s</h3>
<p>%s</p><div class="grid"><div><strong>What it stresses in a kernel</strong><ul>%s</ul></div>
<div><strong>Stages</strong><ol>%s</ol></div></div>%s<div class="stopped">%s</div></section>""" % (
            k, k, md_inline(t.get("title", "")), md_inline(t.get("why", "")),
            "".join("<li>%s</li>" % md_inline(x) for x in t.get("stresses", [])),
            "".join("<li>%s</li>" % md_inline(x) for x in t.get("stages", [])),
            "".join("<p>%s</p>" % md_inline(x) for x in t.get("notes", [])),
            "".join(stops)))
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CAD Acid Tests</title>
<meta name="description" content="Eleven deliberately hard parts and assemblies, scored stage by stage across CAD programs and releases.">
<style>%s</style></head><body><main>
<h1>CAD acid tests</h1>
<p class="lede">Eleven deliberately hard parts and assemblies, each built in stages and checked against values worked out by hand, never
taken from another CAD program. A score is the number of stages passed before the first one that fails.
Each test is built from the kinds of geometry that broke most often in a 10,000-item
<a href="https://github.com/dutchman-cad/stress-corpus">stress corpus</a>.</p>
<h2>Results</h2>
<div class="tablewrap"><table><thead><tr><th>Program</th><th>Total</th>%s</tr></thead><tbody>%s</tbody></table></div>
<p class="legend">Green: every stage passes. Amber: some stages pass. Red: none pass. Under each score: what stopped
it, and at which stage. "Refused" means the program raised an error. "Wrong result" means it built something that
fails a check (body count, validity, volume, or a point that should be inside or outside). "Timeout" means it took
longer than 300 s. Under each test's name, a sketch of what it should look like; in a cell where a program
completed every stage, a picture of what it built.</p>
<h2 id="looks">What each test should look like</h2>
<p class="lede">Each sketch is the test's final stage, built from TESTS.md as an implicit solid (exact primitives,
booleans, offsets and patterns, in numpy) with no CAD kernel, and checked against the hand-derived volumes in
<code>expected/values.py</code>, so it owes nothing to any program it is compared with. Rounds, drafts and the spline
outline are approximated, as each note says. Beside it is what each program built where it passed every stage, and
so every check. The harnesses render that on the run, in the same view and style: SolidCraft's and FreeCAD's are
here; Fusion's and SOLIDWORKS's appear with their next run. A split part is drawn in one piece, and a part built far
from the origin is drawn in its own frame.</p>
%s
<h2>How a stage is checked</h2>
<p>Each stage must build within 300 s, leave valid closed solids and the stated number of bodies, and give the right
answer at a few <em>probe points</em> that must be inside or outside the part. That is the exact check that catches a
feature which silently goes missing. Where a closed form exists, the volume must match too. Programs integrate
volume numerically, so a volume gets 1e-5 relative tolerance. Spline solids get a &plusmn;0.5%% window instead,
because some programs integrate their volume coarsely: FreeCAD 1.1.4's is 0.15%% low on the acid-2 outline.</p>
<p>Every test uses only built-in operations: solid modelling for acid-1 to acid-10, and the program's own assembly tools for acid-11 (in FreeCAD, its built-in Assembly workbench; in Fusion, joints and a motion link; in SOLIDWORKS, mates and Interference Detection). There is no sheet metal, and nothing from a plug-in or an add-on.
The construction, the picks and the expected values are in the
<a href="https://github.com/theironchef/cad-acid-tests">repository</a>: <code>TESTS.md</code>, <code>PLAN.md</code> and
<code>expected/values.py</code>.</p>
<h2>The tests</h2>
%s
<footer>Generated by <code>tools/site.py</code> from <code>results/</code>. Programs: SolidCraft releases (the built
<code>solidcraft-cli</code>), FreeCAD (its public Python API, run as a separate program), Fusion (its public API, through
its local MCP server) and SOLIDWORKS (its COM API, run as a separate program).</footer>
</main></body></html>
""" % (CSS, head, "".join(rows), looks(tests, runs), "".join(cards))


def main():
    tests, runs = parse_tests(), load_runs()
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    open(os.path.join(ROOT, "docs", "index.html"), "w", encoding="utf-8", newline="\n").write(page(tests, runs))
    open(os.path.join(ROOT, "docs", ".nojekyll"), "w").close()
    md = scoreboard_md(tests, runs)
    open(os.path.join(ROOT, "SCOREBOARD.md"), "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main()
