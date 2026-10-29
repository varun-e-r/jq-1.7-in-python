"""Build the visible examples and the hidden test set from jq 1.7.1 output.

Runs inside the authoring image (python 3.12 + the official jq 1.7.1 binary):

    python3 build_truth.py REF_DIR OUT_EXAMPLES OUT_HIDDEN

A case is kept only when real jq and the reference implementation produce the
same stdout bytes and exit code. jq crashes, timeouts and oversized outputs are
dropped. Every drop is listed in the report.
"""
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_cases  # noqa: E402

MAX_OUT = 64 * 1024


def bucket(key):
    return int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16) % 10


def run(cmd, stdin):
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/tmp"}
    try:
        p = subprocess.run(cmd, input=stdin.encode("utf-8"), capture_output=True, timeout=20, env=env, cwd="/tmp")
    except subprocess.TimeoutExpired:
        return None
    return p.returncode, p.stdout


def select(cases, visible):
    out = []
    for c in cases:
        if c["category"] == "fuzz" or c["tkey"] is None:
            out.append(c)
            continue
        b = bucket(c["tkey"])
        if c["fixed"]:
            if (b >= 6) == visible:
                out.append(c)
        else:
            if visible and b < 3:
                continue
            out.append(c)
    return out


def main():
    ref, out_examples, out_hidden = sys.argv[1:4]
    visible = select(gen_cases.build(1), True)
    hidden = select([c for c in gen_cases.build(2)], False)
    hidden += [c for c in select(gen_cases.build(3), False) if not c["fixed"]]
    seen = set()
    pool = []
    for split, cs in (("visible", visible), ("hidden", hidden)):
        for c in cs:
            key = json.dumps([c["args"], c["stdin"]])
            if key in seen:
                continue
            seen.add(key)
            c["split"] = split
            pool.append(c)

    def check(c):
        a = run(["jq"] + c["args"], c["stdin"])
        if a is None:
            return c, "jq-timeout", None
        if a[0] < 0 or a[0] > 5:
            return c, "jq-crash", None
        if len(a[1]) > MAX_OUT:
            return c, "too-big", None
        b = run(["python3", ref + "/jq"] + c["args"], c["stdin"])
        if b is None:
            return c, "ref-timeout", None
        if a != b:
            return c, "disagree", (a, b)
        return c, "ok", a

    kept = {"visible": [], "hidden": []}
    drops = {}
    with ThreadPoolExecutor(10) as ex:
        for c, status, res in ex.map(check, pool):
            if status != "ok":
                drops.setdefault(status, []).append((c, res))
                continue
            rc, stdout = res
            if c["category"] == "fuzz" and not stdout and bucket(c["id"]) != 0:
                drops.setdefault("fuzz-empty", []).append((c, None))
                continue
            rec = {"id": c["id"], "category": c["category"], "args": c["args"], "stdin": c["stdin"],
                   "stdout": stdout.decode("utf-8"), "exit_code": rc}
            kept[c["split"]].append(rec)
    with open(out_examples, "w") as f:
        for r in kept["visible"]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out_hidden, "w") as f:
        for r in kept["hidden"]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for split in ("visible", "hidden"):
        cats = {}
        for r in kept[split]:
            cats[r["category"]] = cats.get(r["category"], 0) + 1
        print(split, len(kept[split]), json.dumps(cats, sort_keys=True))
    for status, items in sorted(drops.items()):
        print("dropped", status, len(items))
        for c, res in items[:40]:
            line = "   %s %s" % (c["id"], json.dumps(c["args"])[:160])
            if res:
                line += "\n      jq  %r\n      ref %r" % (res[0][0], res[1][0])
                line += "\n      jq  %r\n      ref %r" % (res[0][1][:160], res[1][1][:160])
            print(line)


if __name__ == "__main__":
    main()
