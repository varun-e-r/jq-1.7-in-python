"""Score degenerate programs and single-rule mutants of the reference on the hidden set.

    python3 calibrate.py REF_DIR HIDDEN.jsonl

Each mutant is the reference with one jq 1.7.1 behaviour replaced by the
reading a careful engineer could plausibly pick (Python's json module, jq 1.6
semantics, the manual's wording). The table shows how much each behaviour is
worth and that no shortcut scores well.
"""
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

PRELUDE = """import os, sys
sys.path.insert(0, %r)
from pyjq import jv, lang, builtins, cli
"""

MUTANTS = {
    "python-json-output": """
import json as _json
def _dumps(v, indent=0, sort_keys=False, ascii_only=False, use_tab=False):
    def conv(x):
        if type(x) is jv.Lit:
            return float(x)
        if type(x) is list:
            return [conv(e) for e in x]
        if type(x) is dict:
            return {k: conv(e) for k, e in x.items()}
        return x
    ind = "\\t" if use_tab else (indent or None)
    return _json.dumps(conv(v), indent=ind, sort_keys=sort_keys, ensure_ascii=ascii_only,
                       separators=(",", ": ") if ind else (",", ":"))
jv.dumps = _dumps
cli.jv.dumps = _dumps
""",
    "no-literal-preservation": """
_fmt = jv.fmt_double
jv.fmt_number = lambda x: _fmt(float(x))
_cmp = jv._numcmp
jv._numcmp = lambda a, b: _cmp(float(a), float(b))
""",
    "python-regex-iteration": """
builtins._iter_matches = lambda r, s: r.finditer(s)
""",
    "alternative-suppresses-errors": """
def _alt_ev(self, inp, env):
    found = False
    try:
        for v in self.a.ev(inp, env):
            if jv.truthy(v):
                found = True
                yield v
    except jv.JQError:
        pass
    if not found:
        yield from self.b.ev(inp, env)
lang.Alternative.ev = _alt_ev
""",
    "untruncated-error-values": """
jv.trunc_dump = lambda v, bufsize=15: jv.dumps(v)
lang.trunc_dump = jv.trunc_dump
""",
    "lhs-outer-binops": """
def _bin_ev(self, inp, env):
    for l in self.a.ev(inp, env):
        for r in self.b.ev(inp, env):
            yield self.fn(l, r)
lang.BinOp.ev = _bin_ev
""",
    "update-deletes-immediately": """
def _modify(inp, lhs, env, update):
    x = inp
    for p in list(lang.path_list(lhs, inp, env)):
        v = lang.getpath(x, p)
        got = False
        for u in update(v):
            x = lang.setpath(x, p, u)
            got = True
            break
        if not got:
            x = lang.delpaths(x, [p])
    return x
lang.modify = _modify
""",
    "manual-repeat": """
builtins.PRELUDE = builtins.PRELUDE.replace("def _repeat: exp, _repeat;", "def _repeat: ., (exp | _repeat);")
""",
    "codepoint-string-indices": """
def _idx(v, sub):
    if type(v) is not str or type(sub) is not str:
        raise jv.JQError("bad")
    out = []
    if not sub:
        return out
    i = v.find(sub)
    while i >= 0:
        out.append(float(i))
        i = v.find(sub, i + 1)
    return out
builtins.NATIVES[("f", "_strindices", 1)] = lang.Native(_idx, 1)
""",
    "exit-status-any-error": """
_orig_main = cli.main
def _main(argv=None):
    rc = _orig_main(argv)
    return 0 if rc == 4 or rc == 1 else rc
cli.main = _main
""",
}

DEGENERATE = {
    "print-nothing-exit-0": "import sys\nsys.stdin.read()\nsys.exit(0)\n",
    "print-nothing-exit-5": "import sys\nsys.stdin.read()\nsys.exit(5)\n",
    "echo-stdin": "import sys\nsys.stdout.write(sys.stdin.read())\n",
    "python-json-identity": (
        "import json, sys\n"
        "data = sys.stdin.read()\n"
        "dec = json.JSONDecoder()\n"
        "i = 0\n"
        "compact = any(a.startswith('-') and 'c' in a for a in sys.argv[1:])\n"
        "while True:\n"
        "    while i < len(data) and data[i] in ' \\t\\r\\n':\n"
        "        i += 1\n"
        "    if i >= len(data):\n"
        "        break\n"
        "    try:\n"
        "        v, i = dec.raw_decode(data, i)\n"
        "    except ValueError:\n"
        "        sys.exit(5)\n"
        "    print(json.dumps(v, ensure_ascii=False, indent=None if compact else 2, separators=(',', ':') if compact else None))\n"
    ),
}


def score(program, cases):
    per = {}

    def one(c):
        try:
            p = subprocess.run(["python3", program] + c["args"], input=c["stdin"].encode("utf-8"),
                               capture_output=True, timeout=10, cwd="/tmp")
            ok = p.returncode == c["exit_code"] and p.stdout == c["stdout"].encode("utf-8")
        except subprocess.TimeoutExpired:
            ok = False
        return c["category"], ok

    with ThreadPoolExecutor(10) as ex:
        for cat, ok in ex.map(one, cases):
            st = per.setdefault(cat, [0, 0])
            st[1] += 1
            st[0] += ok
    return sum(p / t for p, t in per.values()) / len(per), per


def main():
    ref, hidden = sys.argv[1], sys.argv[2]
    cases = [json.loads(l) for l in open(hidden, encoding="utf-8")]
    tmp = tempfile.mkdtemp()
    rows = []
    for name, body in DEGENERATE.items():
        path = os.path.join(tmp, name + ".py")
        open(path, "w").write(body)
        rows.append((name,) + score(path, cases))
    for name, patch in MUTANTS.items():
        path = os.path.join(tmp, name + ".py")
        open(path, "w").write(PRELUDE % ref + patch + "\nsys.exit(cli.run())\n")
        rows.append((name,) + score(path, cases))
    path = os.path.join(tmp, "reference.py")
    open(path, "w").write(PRELUDE % ref + "\nsys.exit(cli.run())\n")
    rows.append(("reference",) + score(path, cases))
    cats = sorted(rows[0][2])
    print("%-32s %7s  %s" % ("program", "score", "  ".join("%7s" % c[:7] for c in cats)))
    for name, s, per in rows:
        print("%-32s %7.4f  %s" % (name, s, "  ".join("%7.3f" % (per[c][0] / per[c][1]) for c in cats)))


if __name__ == "__main__":
    main()
