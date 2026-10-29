#!/usr/bin/env python3
"""Score /app/jq against the recorded jq 1.7.1 runs in /app/examples/cases.jsonl.

A case passes when stdout matches byte for byte and the exit status matches.
The score is the mean over categories of the fraction passed, the same rule the
grader applies to its own cases. Programs run under launch.py, the same audit
hook the grader uses (ctypes limited to libm, no child processes).

    python3 /app/run_examples.py                 # summary
    python3 /app/run_examples.py --show 20       # also print the first 20 failures
    python3 /app/run_examples.py --category cli  # only one category
    python3 /app/run_examples.py --id 1-00042    # one case, verbose
"""
import argparse
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))


def run(case, program):
    try:
        p = subprocess.run(["python3", "-I", os.path.join(HERE, "launch.py"), program] + case["args"],
                           input=case["stdin"].encode("utf-8"),
                           capture_output=True, timeout=10, cwd="/tmp",
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "HOME": "/tmp"})
        return case, p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return case, "timeout", b"", b""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(HERE, "examples", "cases.jsonl"))
    ap.add_argument("--program", default=os.path.join(HERE, "jq"))
    ap.add_argument("--category")
    ap.add_argument("--id")
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    a = ap.parse_args()
    cases = [json.loads(l) for l in open(a.cases, encoding="utf-8")]
    if a.category:
        cases = [c for c in cases if c["category"] == a.category]
    if a.id:
        cases = [c for c in cases if c["id"] == a.id]
        a.show = max(a.show, 1)
    per_cat = {}
    fails = []
    with ThreadPoolExecutor(a.jobs) as ex:
        for case, rc, out, err in ex.map(lambda c: run(c, a.program), cases):
            st = per_cat.setdefault(case["category"], [0, 0])
            st[1] += 1
            if rc == case["exit_code"] and out == case["stdout"].encode("utf-8"):
                st[0] += 1
            else:
                fails.append((case, rc, out, err))
    for case, rc, out, err in fails[:a.show]:
        print("-" * 72)
        print("id %s  category %s" % (case["id"], case["category"]))
        print("args   %s" % json.dumps(case["args"], ensure_ascii=False))
        print("stdin  %s" % json.dumps(case["stdin"], ensure_ascii=False)[:400])
        print("want   exit %s  %r" % (case["exit_code"], case["stdout"][:400]))
        print("got    exit %s  %r" % (rc, out.decode("utf-8", "replace")[:400]))
        if err:
            print("stderr %s" % err.decode("utf-8", "replace")[-400:])
    if not per_cat:
        print("no cases")
        return
    print("-" * 72)
    for c, (p, t) in sorted(per_cat.items()):
        print("%-10s %5d / %-5d %6.1f%%" % (c, p, t, 100.0 * p / t))
    score = sum(p / t for p, t in per_cat.values()) / len(per_cat)
    print("score %.4f  (%d of %d cases)" % (score, sum(p for p, _ in per_cat.values()), len(cases)))


if __name__ == "__main__":
    main()
