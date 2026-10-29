# jq 1.7.1 in Python

The agent reimplements the jq 1.7.1 command-line JSON processor in pure Python (3.12, standard library only) at `/app/jq`. It is graded differentially: 2417 hidden runs were recorded from the official jq 1.7.1 release binary, and a case passes only when the candidate writes the same stdout bytes and exits with the same status. The reward is the mean over nine categories of the fraction of cases passed, so it is continuous, as in FrontierSWE.

## Layout

```
instruction.md                 what the agent is told
task.toml                      20 h agent budget, separate verifier image
environment/Dockerfile         python:3.12-slim-bookworm (pinned digest), no jq anywhere
environment/workspace/         becomes /app: starter jq (exits 99), examples, runner, launcher
solution/solve.sh              installs the reference over the starter
solution/jq, solution/pyjq/    the reference implementation (about 3,000 lines)
tests/Dockerfile               verifier image: same Python, hidden cases, scorer
tests/test.sh, verify.py       scoring
tests/launch.py                audit-hook launcher every case runs under
tests/hidden_cases.jsonl       the 2417 graded runs (never in the agent image)
tests/tools/                   generator, truth builder, calibration (author tooling, removed from the image)
```

## How the cases were made

`tests/tools/gen_cases.py` produces candidate runs from four sources: about 440 hand-written program templates run against seeded documents (store orders, event logs, nested config, matrices, text with unicode and awkward characters, number arrays full of literals such as `1.000`, `1e1000` and `100000000000000000001`, mixed-type arrays, trees); a fixed list of about 180 command-line cases (exit status under `-e`, malformed input part way through a stream, `--args`, `--indent`, `-S`, `-a`, `halt_error` codes and so on); a list of compile errors; and a random program fuzzer over common constructs. `build_truth.py` runs every candidate through jq 1.7.1 and through the reference and keeps a case only when both agree on stdout and exit status.

Seed 1 becomes the 1197 visible examples. Seeds 2 and 3 become the hidden set. Templates are split by a hash of their text: 30% of the document templates never appear in the examples, and the fixed cases (CLI, compile errors, no-input programs) are split 40/60 so none of them appears on both sides. Fuzz cases that expect empty output were thinned to 10%, so a program that prints nothing gains almost nothing.

Out of about 3600 candidate runs only five were dropped: one where jq 1.7.1 hits an assertion and aborts, two where jq's own backtracking misbehaves when `?` sits inside a `reduce`/`foreach` initialiser (a jq bug, not behaviour worth asking for), plus duplicates. Every other disagreement found during development was a reference bug and was fixed.

## What the agent has to get right

The agent needs a whole interpreter, and the score depends on fidelity to jq 1.7.1, much of which is not in the manual. Examples the hidden set exercises:

- Numbers: literals keep their decNumber text until arithmetic (`1.000`, `1E+1000`, `-0`), comparisons between two literals are decimal, and computed doubles print with jq's dtoa rules (`1e+17`, `1e-05`, `12345678901234567000`).
- Error messages that reach stdout through `try ... catch .`: exact wording, 11-byte value truncation with `...`, `Cannot index array with string "a"`, `cannot be divided (remainder)`, `cannot be csv-formatted, only array`, parse errors with byte-based columns.
- Grammar: `try` binds tighter than any binary operator, comparisons do not chain, `?//` is a single token so `.[]?//1` is a syntax error, object values are terms joined by pipes, constant non-string object keys fail at compile time.
- Evaluation order: binary operators vary the right operand slowest, C builtins vary their last argument slowest, `$` parameters vary their first argument slowest.
- Path tracking: legality of a path expression follows jq's value-at-path identity check (`path(. as $x | $x)` is fine, `path(last(.[]))` is not), and `|=` with an empty update collects deletions until the end.
- jq 1.7.1 specifics: `//` does not swallow errors on its left, `repeat(f)` does not pipe, `nth` past the end is empty, `indices` on strings returns byte offsets, `isnan` on a non-number is false, `has` on null is false, `input` exhausts with the message `break`.
- Regex: Oniguruma Perl syntax semantics, and after an empty match jq steps one byte, so empty-matching patterns report duplicate offsets inside multi-byte characters.
- CLI: the exit status comes from the last input processed, `-e` has its own codes, a parse error exits 5 after the values before it were printed, and an argument like `-5` or `-(1)` is a program, not an option.

## Calibration

`tests/tools/calibrate.py` scores degenerate programs and single-rule mutants of the reference on the hidden set:

| program | score |
|---|---|
| prints nothing, exits 0 | 0.013 |
| prints nothing, exits 5 | 0.049 |
| echoes stdin | 0.005 |
| Python `json` identity filter | 0.096 |
| reference with Python's `json` serializer | 0.541 |
| reference without literal preservation | 0.939 |
| reference with Python's regex iteration | 0.998 |
| reference where `//` suppresses errors (jq 1.6) | 0.996 |
| reference with untruncated error values | 0.999 |
| reference with left-operand-outer binary ops | 0.999 |
| reference where `|=` deletes immediately (jq 1.6) | 0.998 |
| reference with the manual's `repeat` | 0.995 |
| reference with codepoint `indices` on strings | 0.9995 |
| reference with naive exit status | 0.989 |
| reference | 1.000 |

No shortcut gets past 10%. Each jq-specific rule is worth a small, real amount, and the score scales with how much of the language is exact.

Harbor runs: `harbor run -p . -a oracle` gives Mean 1.000 (53 s), `harbor run -p . -a nop` gives Mean 0.000.

## Verifier

`test.sh` seals `/logs/verifier` and `/tests`, then runs `verify.py` as root. The candidate tree is copied to a root-owned staging directory; binary files, archives, files over 2 MB, trees over 8 MB, symlinks leaving `/app` and an untouched starter are rejected. Each case runs as `nobody`, in its own session, from an empty directory, with a 10 second limit, under `launch.py`. The launcher installs a Python audit hook before candidate code runs: ctypes may open only libm and use only its math functions (jq's math builtins come from libm, and bit-identical results need it), and the program may not start processes or reach raw memory. That rules out loading libjq or running a jq binary, even one embedded in a source file. Leftover `nobody` processes are killed before the reward is written. The agent's `run_examples.py` uses the same launcher and the same scoring rule.

The environment is meant to run offline. Local Docker cannot enforce `no-network` on macOS, so the task does not depend on it; the launcher is what makes downloaded native code useless at grading time.

## Reference solution

`solution/pyjq` is a full implementation, written for this task: `jv.py` (value model, ordering, printer, port of jq's JSON parser state machine), `lang.py` (lexer, precedence parser, compile-time resolver, generator evaluator with path mode and assignment), `builtins.py` (C builtins, formats, libm math, regex) and `cli.py` (options and exit status). Builtins that jq defines in jq are written as a jq prelude that follows jq 1.7.1's builtin library definitions so their edge cases match; jq is MIT licensed.

## Regenerating

```
docker build -t jqauth -f tests/tools/Dockerfile.auth tests/tools     # python:3.12-slim-bookworm plus the jq-1.7.1 release binary
docker run --rm -v "$PWD":/task jqauth python3 /task/tests/tools/build_truth.py \
    /task/solution /task/environment/workspace/examples/cases.jsonl /task/tests/hidden_cases.jsonl
```

The jq binary comes from the GitHub release `jq-1.7.1` (`jq-linux-arm64` sha256 `4dd2d8a0…b4a5`, `jq-linux-amd64` sha256 `5942c9b0…8ff5`). Generation is seeded and deterministic.
