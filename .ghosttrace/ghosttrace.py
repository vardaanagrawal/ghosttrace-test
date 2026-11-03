#!/usr/bin/env python3
"""GhostTrace AI - change impact detective.
Usage:
  python ghosttrace.py demo                       # create a sample repo in ./demo_repo
  python ghosttrace.py analyze demo_repo --file config/settings.py --key PAYMENT_TIMEOUT
  python ghosttrace.py analyze . --git            # use your uncommitted git diff
  add --ai to get a Claude-written narrative (needs ANTHROPIC_API_KEY)
"""
import argparse, ast, json, os, re, subprocess, sys, urllib.request

SKIP = {'.ghosttrace', '.github', '.git', '__pycache__', 'node_modules', '.venv'}
TEXT = {'.py', '.yaml', '.yml', '.json', '.sql', '.sh', '.cfg', '.ini', '.toml', '.env', '.js', '.ts', '.md', '.txt', ''}
WEIGHT = {'schedule': 25, 'database': 25, 'job': 20, 'config': 15, 'code': 10, 'test': 0}


def kind(p):
    p = p.lower(); b = os.path.basename(p)
    if 'test' in b: return 'test'
    if 'cron' in p: return 'schedule'
    if p.endswith('.sql') or 'migration' in p: return 'database'
    if p.endswith(('.yaml', '.yml', '.toml', '.ini', '.cfg', '.env')) or 'settings' in p or 'config' in p: return 'config'
    if 'job' in p: return 'job'
    return 'code'


def load(root):
    files = {}
    for d, ds, fs in os.walk(root):
        ds[:] = [x for x in ds if x not in SKIP]
        for f in fs:
            full = os.path.join(d, f); rel = os.path.relpath(full, root).replace(os.sep, '/')
            if os.path.splitext(f)[1] in TEXT and rel != 'incidents.json':
                try: files[rel] = open(full, encoding='utf-8').read()
                except Exception: pass
    return files


# ---------- Module 1: Hidden Dependency Mapper ----------
def py_imports(src):
    out = set()
    try: tree = ast.parse(src)
    except SyntaxError: return out
    for n in ast.walk(tree):
        if isinstance(n, ast.Import): out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module); out |= {n.module + '.' + a.name for a in n.names}
    return out


def build_graph(files):
    """deps[a] = files that a depends on (imports, or mentions by path)."""
    mods = {f[:-3].replace('/', '.'): f for f in files if f.endswith('.py')}
    deps = {f: set() for f in files}
    for f, src in files.items():
        if f.endswith('.py'):
            for imp in py_imports(src):
                if imp in mods and mods[imp] != f: deps[f].add(mods[imp])
        for other in files:
            if other != f and other in src: deps[f].add(other)
    rev = {f: set() for f in files}
    for a, bs in deps.items():
        for b in bs: rev[b].add(a)
    return rev


def key_hits(files, key, skip):
    pat = re.compile(r'\b' + re.escape(key) + r'\b')
    return {f: [(i + 1, l.strip()) for i, l in enumerate(s.splitlines()) if pat.search(l)]
            for f, s in files.items() if f != skip and pat.search(s)}


# ---------- Module 4 helper / Module 2: Incident Memory ----------
def incident_memory(root, affected, keys):
    path = os.path.join(root, 'incidents.json')
    if not os.path.exists(path): return []
    hits = []
    for inc in json.load(open(path)):
        score = 2 * len(set(inc.get('files', [])) & set(affected)) + \
                sum(1 for k in inc.get('keywords', []) if any(k.lower() in x.lower() for x in keys))
        if score: hits.append((score, inc))
    return [i for _, i in sorted(hits, key=lambda x: -x[0])]


# ---------- Module 3: Counterfactual Change Simulator ----------
def simulate(f, k, key, lines):
    ev = lines[0][1] if lines else ''
    return {
        'schedule': f"'{key}' changes silently at the next scheduled run ({ev}); nobody is watching when it fires, so the failure surfaces hours later.",
        'job': f"Batch job reads '{key}' ({ev}); a larger/different value can change runtime, memory use or trigger its own limits overnight.",
        'database': f"Queries/migrations referencing '{key}' may fail or return different rows after the change.",
        'config': f"Values derived from '{key}' elsewhere in config may drift out of sync with it.",
        'code': f"Code path using '{key}' ({ev}) will behave differently; check default values, limits and retry logic.",
        'test': f"Test references '{key}'; it may pass for the wrong reason.",
    }[k]


def assumption(lines, key):
    out = []
    for n, l in lines:
        rest = l.replace(key, '')
        if re.search(r'[<>]=?|==|\*|/|\bassert\b|\d{2,}', rest) and 'import' not in l:
            out.append(f"line {n}: {l}")
    return out


# ---------- Module 5: Missing Test Discovery ----------
def missing_tests(files, affected):
    tests = {f: s for f, s in files.items() if kind(f) == 'test'}
    gaps = []
    for f, info in affected.items():
        if kind(f) == 'test': continue
        stem = os.path.splitext(os.path.basename(f))[0]
        if not any(stem in s or f in s or any(k in s for k in info['keys']) for s in tests.values()):
            gaps.append(f)
    return gaps


def analyze(root, change_file, keys, files):
    rev = build_graph(files)
    affected = {}

    def add(f, hops, via, key=None, lines=None, direct=True):
        a = affected.setdefault(f, {'hops': hops, 'via': via, 'keys': set(), 'lines': [], 'hidden': False})
        if hops < a['hops']: a['hops'], a['via'] = hops, via
        if key: a['keys'].add(key)
        if lines: a['lines'] += lines
        return a

    linked = rev.get(change_file, set())
    for key in keys:  # evidence-based discovery: who uses the changed identifier?
        for f, lines in key_hits(files, key, change_file).items():
            a = add(f, 1, f"references {key}", key, lines)
            if f not in linked: a['hidden'] = True  # no import/path link -> invisible to normal tools
    for f in linked: add(f, 1, 'imports/mentions changed file')
    frontier = list(affected)
    for hop in (2, 3):  # indirect ripple
        nxt = []
        for f in frontier:
            for g in rev.get(f, ()):
                if g != change_file and g not in affected:
                    add(g, hop, f"depends on {f}"); nxt.append(g)
        frontier = nxt
    return affected


def brief(root, change_file, keys, files, affected):
    incidents = incident_memory(root, list(affected), keys)
    gaps = missing_tests(files, affected)
    score = 0
    rows = []
    for f, a in sorted(affected.items(), key=lambda x: (x[1]['hops'], x[0])):
        k = kind(f)
        s = WEIGHT[k] / a['hops'] + (10 if f in gaps else 0)
        asm = [x for key in a['keys'] for x in assumption(a['lines'], key)]
        s += 10 if asm else 0
        score += s
        rows.append((f, k, a, asm))
    score = min(100, int(score + 15 * min(len(incidents), 2)))
    level = 'HIGH' if score >= 60 else 'MEDIUM' if score >= 30 else 'LOW'
    o = [f"## Change in `{change_file}`",
         f"**Change:** `{change_file}` (keys: {', '.join(keys) or 'n/a'})",
         f"**Risk: {level} ({score}/100)** - {len(affected)} files in blast radius\n",
         "## Hidden & indirect impact (Dependency Mapper + Simulator)"]
    for f, k, a, asm in rows:
        tag = ' **[HIDDEN - no import link]**' if a['hidden'] else ''
        o.append(f"- `{f}` [{k}, hop {a['hops']}]{tag} - {a['via']}")
        if a['keys']: o.append(f"  - Scenario: {simulate(f, k, sorted(a['keys'])[0], a['lines'])}")
        for x in asm: o.append(f"  - Undocumented assumption: {x}")
    o.append("\n## Similar past incidents (Incident Memory)")
    o += [f"- **{i['id']}** ({i.get('date','')}): {i['title']} - root cause: {i.get('root_cause','')}" for i in incidents] or ["- none matched"]
    o.append("\n## Missing tests (no existing test touches these)")
    o += [f"- `{g}` - add a test for the changed value in a {kind(g)} context" for g in gaps] or ["- coverage looks adequate"]
    return '\n'.join(o), score


def narrate(text):
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key: return "(set ANTHROPIC_API_KEY to enable the AI narrative)"
    body = json.dumps({"model": "claude-sonnet-5-5", "max_tokens": 600, "messages": [{"role": "user", "content":
        "You are a release-risk reviewer. Using ONLY this evidence, write a 5-sentence PR comment: the top risk, "
        "why it is non-obvious, and the 3 checks to do before merge.\n\n" + text}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", body,
                                 {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    return json.load(urllib.request.urlopen(req))['content'][0]['text']


IGNORE_EXT = ('.md', '.txt', '.lock', '.png', '.jpg', '.svg', '.gif')
STOP = {'None', 'True', 'False', 'String', 'Object', 'Number', 'Boolean', 'Error', 'Exception', 'Promise', 'Array'}


def git_changes(root, base=None):
    """Changed files + distinctive identifiers (snake_case/camelCase/CONSTANTS) from the diff."""
    ref = f"{base}...HEAD" if base else "HEAD"
    git = lambda *a: subprocess.check_output(['git', '-C', root, *a], text=True)
    names = [n for n in git('diff', ref, '--name-only').split() if not n.endswith(IGNORE_EXT)]
    out = []
    for f in names[:5]:
        diff = git('diff', ref, '-U0', '--', f)
        toks = {t for l in diff.splitlines() if l[:1] in '+-' and l[:3] not in ('+++', '---')
                for t in re.findall(r'[A-Za-z_][A-Za-z0-9_]{4,}', l)}
        toks = {t for t in toks if t not in STOP and ('_' in t or any(c.isupper() for c in t[1:]))}
        out.append((f, sorted(toks)[:8]))
    return out


DEMO = {
    'config/settings.py': "PAYMENT_TIMEOUT = 30\nRETRIES = 3\n",
    'services/payment.py': "from config import settings\n\ndef charge(o):\n    return call_gateway(o, timeout=settings.PAYMENT_TIMEOUT)\n",
    'services/orders.py': "from services import payment\n\ndef checkout(o):\n    return payment.charge(o)\n",
    'jobs/nightly_reconcile.py': "from config.settings import PAYMENT_TIMEOUT\n\n# processes 10k orders sequentially\nassert PAYMENT_TIMEOUT * 10000 < 21600, 'must finish in 6h window'\n",
    'deploy/crontab': "0 2 * * * python jobs/nightly_reconcile.py\n",
    'legacy/partner_sync.py': "import os\nTIMEOUT = int(os.environ['PAYMENT_TIMEOUT'])\n",
    'tests/test_payment.py': "from services import payment\n\ndef test_charge():\n    assert payment\n",
    'incidents.json': json.dumps([{"id": "INC-142", "date": "2025-03-11", "title": "Nightly reconcile overran its window and blocked morning settlement",
                                   "root_cause": "timeout increased without re-checking batch duration",
                                   "files": ["jobs/nightly_reconcile.py"], "keywords": ["timeout", "PAYMENT_TIMEOUT"]}], indent=2),
}


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('demo')
    a = sub.add_parser('analyze'); a.add_argument('repo'); a.add_argument('--file'); a.add_argument('--key', action='append', default=[])
    a.add_argument('--git', action='store_true'); a.add_argument('--ai', action='store_true')
    a.add_argument('--base'); a.add_argument('--out')
    args = ap.parse_args()
    if args.cmd == 'demo':
        for p, c in DEMO.items():
            os.makedirs(os.path.dirname(os.path.join('demo_repo', p)) or '.', exist_ok=True)
            open(os.path.join('demo_repo', p), 'w').write(c)
        print("Created demo_repo. Try:\n  python ghosttrace.py analyze demo_repo --file config/settings.py --key PAYMENT_TIMEOUT")
        return
    files = load(args.repo)
    if args.git or args.base:
        changes = git_changes(args.repo, args.base)
    elif args.file:
        keys = args.key or sorted(set(re.findall(r'\b[A-Z][A-Z0-9_]{3,}\b', files.get(args.file, ''))))
        changes = [(args.file, keys)]
    else:
        sys.exit("Provide --file, --git or --base")
    if not changes:
        report, top = "<!-- ghosttrace -->\n## GhostTrace AI\nNo analyzable code changes in this PR.", 0
    else:
        parts, top = [], 0
        for f, keys in changes:
            text, score = brief(args.repo, f, keys, files, analyze(args.repo, f, keys, files))
            parts.append(text); top = max(top, score)
        report = f"<!-- ghosttrace -->\n# GhostTrace AI: change risk (highest {top}/100)\n\n" + "\n\n---\n\n".join(parts)
        if args.ai:
            try: report += "\n\n## AI narrative\n" + narrate(report)
            except Exception as e: report += f"\n\n(AI narrative unavailable: {e})"
    print(report)
    if args.out: open(args.out, 'w').write(report)
    if os.environ.get('GITHUB_OUTPUT'): open(os.environ['GITHUB_OUTPUT'], 'a').write(f"score={top}\n")


if __name__ == '__main__':
    main()
