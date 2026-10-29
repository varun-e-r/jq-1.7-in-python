"""Command line front end matching jq 1.7.1's option handling and exit codes."""
import sys

from . import builtins, jv, lang
from .builtins import CTX, Halt
from .jv import JQError

OK, OK_NULL_KIND, ERROR_SYSTEM, ERROR_COMPILE, OK_NO_OUTPUT, ERROR_UNKNOWN = 0, -1, 2, 3, -4, 5

_SHORT = {
    "n": "null_input", "r": "raw_output", "j": "join_output", "a": "ascii",
    "c": "compact", "s": "slurp", "R": "raw_input", "e": "exit_status",
    "S": "sort_keys", "C": "color", "M": "mono",
}
_LONG = {
    "--null-input": "null_input", "--raw-output": "raw_output", "--join-output": "join_output",
    "--ascii-output": "ascii", "--compact-output": "compact", "--slurp": "slurp",
    "--raw-input": "raw_input", "--exit-status": "exit_status", "--sort-keys": "sort_keys",
    "--color-output": "color", "--monochrome-output": "mono", "--tab": "tab",
}


class UsageError(Exception):
    pass


def parse_args(argv):
    opts = {"indent": 2, "named": {}, "positional": []}
    program = None
    rest_mode = None  # "args" or "jsonargs"
    i = 0
    further_opts = True
    while i < len(argv):
        a = argv[i]
        i += 1
        if further_opts and a == "--":
            further_opts = False
            continue
        if further_opts and len(a) > 1 and a[0] == "-" and (a[1].isalpha() or a[1] == "-"):
            if a.startswith("--"):
                if a in _LONG:
                    opts[_LONG[a]] = True
                elif a == "--indent":
                    if i >= len(argv):
                        raise UsageError("--indent takes one parameter")
                    try:
                        n = int(argv[i])
                    except ValueError:
                        raise UsageError("--indent takes a number")
                    i += 1
                    if n < -1 or n > 7:
                        raise UsageError("--indent takes a number between -1 and 7")
                    opts["indent"] = n
                elif a in ("--arg", "--argjson"):
                    if i + 1 >= len(argv):
                        raise UsageError("%s takes two parameters" % a)
                    name, val = argv[i], argv[i + 1]
                    i += 2
                    if a == "--argjson":
                        try:
                            val = jv.parse_one(val)
                        except JQError:
                            raise UsageError("invalid JSON text passed to --argjson")
                    opts["named"].setdefault(name, val)
                elif a == "--args":
                    rest_mode = "args"
                elif a == "--jsonargs":
                    rest_mode = "jsonargs"
                else:
                    raise UsageError("Unknown option: %s" % a)
                continue
            for ch in a[1:]:
                if ch in _SHORT:
                    opts[_SHORT[ch]] = True
                else:
                    raise UsageError("Unknown option: %s" % a)
            continue
        if program is None:
            program = a
        elif rest_mode == "args":
            opts["positional"].append(a)
        elif rest_mode == "jsonargs":
            try:
                opts["positional"].append(jv.parse_one(a))
            except JQError:
                raise UsageError("invalid JSON text passed to --jsonargs")
        else:
            opts.setdefault("files", []).append(a)
    if program is None:
        program = "."
    return program, opts


def input_values(data, opts):
    """Yield input values; a parse error is raised as jv.ParseError after earlier values."""
    text = jv.decode_utf8(data)
    if opts.get("raw_input"):
        if opts.get("slurp"):
            yield text
            return
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        for line in lines:
            yield line
        return
    p = jv.Parser(text)
    if opts.get("slurp"):
        vals = []
        while True:
            v = p.next_value()
            if v is jv.Parser.END:
                break
            vals.append(v)
        yield vals
        return
    while True:
        v = p.next_value()
        if v is jv.Parser.END:
            return
        yield v


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    out = sys.stdout
    try:
        program, opts = parse_args(argv)
    except UsageError as e:
        sys.stderr.write("jq: %s\n" % e)
        return 2

    builtins.install()
    g = lang.GLOBALS
    named = opts["named"]
    g.vars["ENV"] = builtins.f_env(None)
    g.vars["ARGS"] = {"positional": opts["positional"], "named": dict(named)}
    for k, v in named.items():
        g.vars[k] = v
    try:
        node = lang.parse_program(program)
        lang.resolve(node, None, g)
    except lang.CompileError as e:
        sys.stderr.write("jq: error: %s\njq: 1 compile error\n" % e)
        return 3
    except RecursionError:
        sys.stderr.write("jq: error: program too deeply nested\n")
        return 3

    if opts.get("files"):
        try:
            data = b"".join(open(f, "rb").read() for f in opts["files"])
        except OSError as e:
            sys.stderr.write("jq: error: Could not open %s\n" % e)
            return 2
    else:
        data = sys.stdin.buffer.read()

    indent = opts["indent"]
    if opts.get("compact"):
        indent = 0
    use_tab = bool(opts.get("tab"))
    sort_keys = bool(opts.get("sort_keys"))
    ascii_only = bool(opts.get("ascii"))
    raw = bool(opts.get("raw_output") or opts.get("join_output"))
    no_lf = bool(opts.get("join_output"))

    inputs = input_values(data, opts)
    CTX.next_input = lambda: _next_or_break(inputs)
    write = out.write

    def process(value):
        ret = OK_NO_OUTPUT
        try:
            for r in node.ev(value, None):
                if raw and type(r) is str:
                    if ascii_only:
                        write(jv.dump_string(r, True))
                    else:
                        write(r)
                    ret = OK
                else:
                    ret = OK_NULL_KIND if (r is None or r is False) else OK
                    write(jv.dumps(r, indent=0 if (indent == 0 and not use_tab) else indent,
                                   sort_keys=sort_keys, ascii_only=ascii_only, use_tab=use_tab))
                if not no_lf:
                    write("\n")
        except JQError as e:
            msg = e.value
            if type(msg) is str:
                sys.stderr.write("jq: error (at <unknown>): %s\n" % msg)
            else:
                sys.stderr.write("jq: error (at <unknown>) (not a string): %s\n" % jv.dumps(msg))
            return ERROR_UNKNOWN, False
        except lang.Break:
            sys.stderr.write("jq: error (at <unknown>): break\n")
            return ERROR_UNKNOWN, False
        except Halt as h:
            if h.code is None:
                return OK, True
            code = h.code
            if type(h.message) is str:
                sys.stderr.write(h.message)
            elif h.message is not None:
                sys.stderr.write(jv.dumps(h.message) + "\n")
            if code != code:
                return ERROR_UNKNOWN, True
            return int(code), True
        except RecursionError:
            sys.stderr.write("jq: error: stack overflow\n")
            return ERROR_UNKNOWN, False
        return ret, False

    ret = OK_NO_OUTPUT
    last_result = -1
    if opts.get("null_input"):
        ret, _ = process(None)
    else:
        while True:
            try:
                value = _next_raw(inputs)
            except StopIteration:
                break
            except jv.ParseError as e:
                sys.stderr.write("jq: parse error: %s\n" % e.args[0])
                ret = ERROR_UNKNOWN
                break
            ret, halted = process(value)
            if ret <= 0 and ret != OK_NO_OUTPUT:
                last_result = 1 if ret != OK_NULL_KIND else 0
            if halted:
                break
    out.flush()
    if opts.get("exit_status"):
        if ret != OK_NO_OUTPUT:
            return abs(ret)
        if last_result == -1:
            return 4
        if last_result == 0:
            return 1
        return 0
    return ret if ret > 0 else 0


def _next_raw(it):
    return next(it)


def _next_or_break(it):
    try:
        return next(it)
    except StopIteration:
        raise JQError("break")
    except jv.ParseError as e:
        raise JQError(e.args[0])


def run():
    import threading
    sys.setrecursionlimit(1000000)
    threading.stack_size(512 * 1024 * 1024)
    result = [0]

    def target():
        try:
            result[0] = main()
        except BrokenPipeError:
            result[0] = 0

    t = threading.Thread(target=target)
    t.start()
    t.join()
    sys.stdout.flush()
    return result[0]
