"""Run the candidate /app/jq under an audit hook.

The grader starts every case as `python3 -I /opt/jqlaunch/launch.py
/opt/candidate/jq ARGS...`. Before any candidate code runs, a permanent audit
hook is installed that

  * allows ctypes to open only libm, look up only libm's math functions and
    call only those function pointers,
  * forbids raw memory access through ctypes (from_address, from_buffer,
    string_at, memmove, memset, cast are removed or blocked) and mmap,
  * forbids starting processes (fork, exec, spawn, system, subprocess),
  * forbids opening another process's or this process's memory.

So the program can use the C math library the way jq does, but cannot load a
jq library or run a jq binary smuggled into /app.
"""
import ctypes
import ctypes.util
import os
import runpy
import sys

MATH = [
    "acos", "acosh", "asin", "asinh", "atan", "atanh", "cbrt", "cos", "cosh", "exp", "exp10",
    "exp2", "expm1", "fabs", "floor", "gamma", "j0", "j1", "lgamma", "log", "log10", "log1p",
    "log2", "logb", "nearbyint", "rint", "round", "significand", "sin", "sinh", "sqrt", "tan",
    "tanh", "tgamma", "trunc", "y0", "y1", "ceil", "atan2", "copysign", "drem", "fdim", "fmax",
    "fmin", "fmod", "hypot", "nextafter", "nexttoward", "pow", "scalb", "fma", "ldexp", "scalbln",
    "frexp", "modf", "lgamma_r", "ilogb", "remainder", "erf", "erfc", "exp2f", "pow10",
]
LIBM_NAMES = {"libm.so.6", "libm.so", "m"}


def _allowed_pointers():
    allowed = set()
    try:
        libm = ctypes.CDLL("libm.so.6")
    except OSError:
        return allowed
    for name in MATH:
        try:
            f = getattr(libm, name)
        except AttributeError:
            continue
        allowed.add(ctypes.cast(f, ctypes.c_void_p).value)
    return allowed


ALLOWED_PTRS = _allowed_pointers()
BLOCKED = {"os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty",
           "subprocess.Popen", "pty.spawn", "os.startfile", "mmap.__new__", "ctypes.cdata",
           "ctypes.cdata/buffer", "ctypes.string_at", "ctypes.wstring_at", "ctypes.dlsym/handle"}
RAW_HELPERS = ["memmove", "memset", "cast", "string_at", "wstring_at", "_memmove_addr", "_memset_addr",
               "_cast_addr", "_string_at_addr", "_wstring_at_addr", "_string_at", "_wstring_at", "_cast"]


def hook(event, args):
    if event in BLOCKED:
        raise RuntimeError("not allowed in the graded program (%s)" % event)
    if event == "ctypes.dlopen":
        name = args[0]
        base = os.path.basename(name) if isinstance(name, str) else name
        if base not in LIBM_NAMES:
            raise RuntimeError("ctypes may only load libm (got %r)" % (name,))
    elif event == "ctypes.dlsym":
        name = args[1] if len(args) > 1 else None
        if isinstance(name, bytes):
            name = name.decode("ascii", "replace")
        if name not in MATH:
            raise RuntimeError("ctypes may only use libm math functions (got %r)" % (name,))
    elif event == "ctypes.call_function":
        if args[0] not in ALLOWED_PTRS:
            raise RuntimeError("ctypes may only call libm math functions")
    elif event == "open":
        path = args[0]
        if isinstance(path, int):
            return
        if isinstance(path, bytes):
            path = path.decode("utf-8", "replace")
        if isinstance(path, str):
            real = os.path.realpath(path)
            if (real.startswith("/proc/") and real.rstrip("/").endswith("mem")) or real in ("/dev/mem", "/dev/kmem"):
                raise RuntimeError("process memory is off limits")


def strip_raw_helpers():
    import _ctypes
    for mod in (ctypes, _ctypes):
        for name in RAW_HELPERS:
            if hasattr(mod, name):
                delattr(mod, name)


def main():
    program = sys.argv[1]
    sys.argv = [program] + sys.argv[2:]
    sys.path.insert(0, os.path.dirname(os.path.abspath(program)))
    strip_raw_helpers()
    sys.addaudithook(hook)
    runpy.run_path(program, run_name="__main__")


if __name__ == "__main__":
    main()
