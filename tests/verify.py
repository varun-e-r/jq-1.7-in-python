#!/usr/bin/env python3
"""Score the candidate /app/jq against the hidden jq 1.7.1 cases.

Runs as root inside the verifier image. The candidate tree is copied to a
root-owned staging directory and every case runs as `nobody` in its own
session with a 10 second limit, so the program cannot see /tests, cannot touch
the reward files and cannot outlive its case.

A case passes when stdout matches jq's bytes exactly and the exit status
matches. The reward is the mean over categories of the fraction of cases
passed, written to /logs/verifier/reward.txt and reward.json.
"""
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

TESTS = os.path.dirname(os.path.abspath(__file__))
OUT = "/logs/verifier"
APP = "/app"
STAGE = "/opt/candidate"
LAUNCH_DIR = "/opt/jqlaunch"
WORK = "/tmp/jqwork"
CASE_TIMEOUT = 10.0
MAX_TREE_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
EXPECTED_CASES = int(open(os.path.join(TESTS, "expected_count.txt")).read().split()[0])


def write_reward(reward, detail):
    os.makedirs(OUT, exist_ok=True)
    reward = round(max(0.0, min(1.0, reward)), 6)
    with open(os.path.join(OUT, "reward.json"), "w") as f:
        json.dump({"reward": reward}, f)
    with open(os.path.join(OUT, "details.json"), "w") as f:
        json.dump({"reward": reward, **detail}, f, indent=2, ensure_ascii=False)
    with open(os.path.join(OUT, "reward.txt"), "w") as f:
        f.write("%s\n" % reward)
    print("reward %s" % reward)


def stage_candidate():
    """Copy /app to a root-owned tree and reject anything that is not source text."""
    if not os.path.isfile(os.path.join(APP, "jq")):
        return "missing /app/jq"
    if os.path.exists(STAGE):
        shutil.rmtree(STAGE)
    shutil.copytree(APP, STAGE, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    total = 0
    for root, dirs, files in os.walk(STAGE):
        for name in files:
            p = os.path.join(root, name)
            if os.path.islink(p):
                target = os.path.realpath(p)
                if not target.startswith(STAGE + os.sep):
                    return "symlink leaves /app: %s" % os.path.relpath(p, STAGE)
                continue
            size = os.path.getsize(p)
            total += size
            if size > MAX_FILE_BYTES:
                return "file too large: %s" % os.path.relpath(p, STAGE)
            with open(p, "rb") as fh:
                data = fh.read()
            if b"\0" in data or data[:4] == b"\x7fELF" or data[:2] == b"PK":
                return "binary file not allowed: %s" % os.path.relpath(p, STAGE)
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                return "non-text file not allowed: %s" % os.path.relpath(p, STAGE)
    if total > MAX_TREE_BYTES:
        return "/app is larger than %d bytes" % MAX_TREE_BYTES
    subprocess.run(["chown", "-R", "root:root", STAGE], check=True)
    subprocess.run(["chmod", "-R", "u=rwX,go=rX", STAGE], check=True)
    return None


def tree_digest(root):
    h = hashlib.sha256()
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", ".git", "examples"))
        for name in sorted(files):
            if name.endswith(".pyc") or name in ("run_examples.py", "launch.py"):
                continue
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                continue
            h.update(os.path.relpath(p, root).encode() + b"\0" + open(p, "rb").read() + b"\0")
    return h.hexdigest()


def kill_nobody():
    """Kill anything still running as nobody (uid 65534)."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/status" % pid) as f:
                uid = next(l for l in f if l.startswith("Uid:")).split()[1]
            if uid == "65534":
                os.kill(int(pid), signal.SIGKILL)
        except (OSError, StopIteration, ValueError):
            pass


def run_case(case):
    cmd = ["setpriv", "--reuid=nobody", "--regid=nogroup", "--clear-groups", "--no-new-privs",
           "python3", "-I", os.path.join(LAUNCH_DIR, "launch.py"), os.path.join(STAGE, "jq")] + case["args"]
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": WORK,
           "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         cwd=WORK, env=env, start_new_session=True)
    try:
        out, _ = p.communicate(case["stdin"].encode("utf-8"), timeout=CASE_TIMEOUT)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
        return case, False, "timeout"
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
    want = case["stdout"].encode("utf-8")
    ok = out == want and rc == case["exit_code"]
    reason = None
    if not ok:
        reason = "exit %s (want %s), stdout %r (want %r)" % (rc, case["exit_code"], out[:200], want[:200])
    return case, ok, reason


def main():
    cases = [json.loads(l) for l in open(os.path.join(TESTS, "hidden_cases.jsonl"), encoding="utf-8")]
    if len(cases) != EXPECTED_CASES:
        write_reward(0.0, {"reason": "hidden case file is damaged", "valid": 0})
        return
    problem = stage_candidate()
    if problem:
        write_reward(0.0, {"reason": problem, "valid": 1})
        return
    starter = open(os.path.join(TESTS, "starter_digest.txt")).read().split()[0]
    if tree_digest(STAGE) == starter:
        write_reward(0.0, {"reason": "unmodified starter", "valid": 1})
        return
    os.makedirs(LAUNCH_DIR, exist_ok=True)
    shutil.copy(os.path.join(TESTS, "launch.py"), os.path.join(LAUNCH_DIR, "launch.py"))
    os.chmod(LAUNCH_DIR, 0o755)
    os.chmod(os.path.join(LAUNCH_DIR, "launch.py"), 0o644)
    if os.path.exists(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)
    subprocess.run(["chown", "nobody:nogroup", WORK], check=True)

    per_cat = {}
    failures = []
    workers = max(2, min(8, os.cpu_count() or 2))
    with ThreadPoolExecutor(workers) as ex:
        for case, ok, reason in ex.map(run_case, cases):
            st = per_cat.setdefault(case["category"], [0, 0])
            st[1] += 1
            if ok:
                st[0] += 1
            elif len(failures) < 200:
                failures.append({"id": case["id"], "category": case["category"], "args": case["args"], "why": reason})
    kill_nobody()
    fractions = {c: p / t for c, (p, t) in per_cat.items()}
    reward = sum(fractions.values()) / len(fractions)
    passed = sum(p for p, _ in per_cat.values())
    detail = {"valid": 1, "passed": passed, "total": len(cases),
              "categories": {c: {"passed": p, "total": t, "fraction": round(p / t, 6)} for c, (p, t) in sorted(per_cat.items())},
              "failures": failures}
    write_reward(reward, detail)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # any verifier fault scores 0 rather than crashing the run
        kill_nobody()
        write_reward(0.0, {"reason": "verifier error: %s" % e, "valid": 0})
