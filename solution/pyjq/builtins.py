"""Builtins: the natives jq implements in C, and a prelude written in jq."""
import base64
import ctypes
import ctypes.util
import math
import os
import re
import sys

from . import jv, lang
from .jv import JQError, Lit, isnum, kind, type_error, type_error2
from .lang import Native


class Halt(Exception):
    def __init__(self, code, message):
        Exception.__init__(self)
        self.code = code
        self.message = message


class Context:
    """Process-wide state the builtins need: the input stream and stderr."""

    def __init__(self):
        self.next_input = None  # callable returning a value or raising StopIteration

    def read_input(self):
        if self.next_input is None:
            raise JQError("break")
        return self.next_input()


CTX = Context()
NATIVES = {}


def native(name, nargs=0, gen=False, pathfn=None):
    def deco(fn):
        NATIVES[("f", name, nargs)] = Native(fn, nargs, gen, pathfn)
        return fn
    return deco


# ---------------------------------------------------------------- core


@native("empty", gen=True, pathfn=lambda inp, ps, args, env: iter(()))
def f_empty(inp, args, env):
    return iter(())


@native("not")
def f_not(inp):
    return not jv.truthy(inp)


def _error_paths(inp, ps, args, env):
    raise JQError(inp)
    yield  # pragma: no cover


@native("error", pathfn=_error_paths)
def f_error(inp):
    raise JQError(inp)


@native("path", 1, gen=True)
def f_path(inp, args, env):
    for p in lang.path_list(args[0], inp, env):
        yield p


def _getpath_value(inp, p):
    if p is None:
        return inp
    if type(p) is not list:
        raise JQError("Path must be specified as an array")
    t = inp
    for k in p:
        if t is None:
            t = lang.index_value(t, k) if not (k is None or k is True or k is False or type(k) is list) else _null_index_error(k)
            continue
        t = lang.index_value(t, k)
    return t


def _null_index_error(k):
    raise JQError("Cannot index null with %s" % kind(k))


def _getpath_paths(inp, ps, args, env):
    for p in args[0].ev(inp, env):
        v = _getpath_value(inp, p)
        if not jv.identical(inp, ps[0]):
            raise JQError("Invalid path expression with result %s" % jv.trunc_dump(inp, 30))
        newpath = ps[1] + tuple(p) if type(p) is list else ps[1]
        yield v, (v, newpath)


@native("getpath", 1, pathfn=_getpath_paths)
def f_getpath(inp, p):
    return _getpath_value(inp, p)


@native("setpath", 2)
def f_setpath(inp, p, v):
    if type(p) is not list:
        raise JQError("Path must be specified as an array")
    return lang.setpath(inp, p, v)


@native("delpaths", 1)
def f_delpaths(inp, ps):
    if type(ps) is not list:
        raise JQError("Paths must be specified as an array")
    return lang.delpaths(inp, ps)


@native("range", 2, gen=True)
def f_range(inp, args, env):
    for frm in args[0].ev(inp, env):
        for upto in args[1].ev(inp, env):
            if not (isnum(frm) and isnum(upto)):
                raise JQError("Range bounds must be numeric")
            cur = frm
            u = float(upto)
            while float(cur) < u:
                yield cur
                cur = float(cur) + 1.0


@native("length")
def f_length(v):
    if v is None:
        return 0.0
    if v is True or v is False:
        raise type_error(v, "has no length")
    t = type(v)
    if t is float or t is Lit:
        return abs(float(v))
    return float(len(v))


@native("utf8bytelength")
def f_utf8bytelength(v):
    if type(v) is not str:
        raise type_error(v, "only strings have UTF-8 byte length")
    return float(len(v.encode("utf-8")))


@native("type")
def f_type(v):
    return kind(v)


@native("keys")
def f_keys(v):
    if type(v) is dict:
        return sorted(v)
    if type(v) is list:
        return [float(i) for i in range(len(v))]
    raise type_error(v, "has no keys")


@native("keys_unsorted")
def f_keys_unsorted(v):
    if type(v) is dict:
        return list(v)
    if type(v) is list:
        return [float(i) for i in range(len(v))]
    raise type_error(v, "has no keys")


@native("has", 1)
def f_has(v, k):
    if v is None:
        return False
    if type(v) is dict and type(k) is str:
        return k in v
    if type(v) is list and isnum(k):
        if k != k:
            return False
        idx = int(k) if abs(k) < 1e18 else -1
        return 0 <= idx < len(v)
    raise JQError("Cannot check whether %s has a %s key" % (kind(v), kind(k)))


def _kind2(v):
    if v is True:
        return "true"
    if v is False:
        return "false"
    return kind(v)


def contains(a, b):
    if _kind2(a) != _kind2(b):
        return False
    ta = type(a)
    if ta is dict:
        for k, bv in b.items():
            if k not in a or not contains(a[k], bv):
                return False
        return True
    if ta is list:
        return all(any(contains(av, bv) for av in a) for bv in b)
    if ta is str:
        return b.encode("utf-8") in a.encode("utf-8")
    return jv.equal(a, b)


@native("contains", 1)
def f_contains(a, b):
    if _kind2(a) == _kind2(b):
        return contains(a, b)
    raise type_error2(a, b, "cannot have their containment checked")


@native("tostring")
def f_tostring(v):
    if type(v) is str:
        return v
    return jv.tojson(v)


@native("tojson")
def f_tojson(v):
    return jv.tojson(v)


@native("fromjson")
def f_fromjson(v):
    if type(v) is not str:
        raise type_error(v, "only strings can be parsed")
    return jv.parse_one(v)


@native("tonumber")
def f_tonumber(v):
    if isnum(v):
        return v
    if type(v) is str:
        r = jv.parse_one(v)
        if not isnum(r):
            raise type_error(v, "cannot be parsed as a number")
        return r
    raise type_error(v, "cannot be parsed as a number")


@native("infinite")
def f_infinite(v):
    return math.inf


@native("nan")
def f_nan(v):
    return math.nan


@native("isinfinite")
def f_isinfinite(v):
    if not isnum(v):
        return False
    return math.isinf(v)


@native("isnan")
def f_isnan(v):
    if not isnum(v):
        return False
    return v != v


@native("isnormal")
def f_isnormal(v):
    if not isnum(v):
        return False
    x = float(v)
    return x == x and not math.isinf(x) and x != 0 and abs(x) >= 2.2250738585072014e-308


def _sorted(values, keys):
    order = sorted(range(len(values)), key=lambda i: jv.SortKey(keys[i]))
    return order


@native("sort")
def f_sort(v):
    if type(v) is not list:
        raise type_error(v, "cannot be sorted, as it is not an array")
    return sorted(v, key=jv.SortKey)


@native("_sort_by_impl", 1)
def f_sort_by_impl(v, keys):
    if type(v) is list and type(keys) is list and len(v) == len(keys):
        return [v[i] for i in _sorted(v, keys)]
    raise type_error2(v, keys, "cannot be sorted, as they are not both arrays")


@native("_group_by_impl", 1)
def f_group_by_impl(v, keys):
    if type(v) is list and type(keys) is list and len(v) == len(keys):
        order = _sorted(v, keys)
        groups = []
        lastkey = None
        for i in order:
            if groups and jv.equal(keys[i], lastkey):
                groups[-1].append(v[i])
            else:
                groups.append([v[i]])
                lastkey = keys[i]
        return groups
    raise type_error2(v, keys, "cannot be sorted, as they are not both arrays")


def _minmax_by(values, keys, is_min):
    if type(values) is not list:
        raise type_error2(values, keys, "cannot be iterated over")
    if type(keys) is not list:
        raise type_error2(values, keys, "cannot be iterated over")
    if len(values) != len(keys):
        raise type_error2(values, keys, "have wrong length")
    if not values:
        return None
    ret = values[0]
    retkey = keys[0]
    for i in range(1, len(values)):
        c = jv.compare(keys[i], retkey)
        if (c < 0) == is_min:
            ret = values[i]
            retkey = keys[i]
    return ret


@native("min")
def f_min(v):
    return _minmax_by(v, v, True)


@native("max")
def f_max(v):
    return _minmax_by(v, v, False)


@native("_min_by_impl", 1)
def f_min_by_impl(v, keys):
    return _minmax_by(v, keys, True)


@native("_max_by_impl", 1)
def f_max_by_impl(v, keys):
    return _minmax_by(v, keys, False)


@native("explode")
def f_explode(v):
    if type(v) is not str:
        raise JQError("explode input must be a string")
    return [float(ord(c)) for c in v]


@native("implode")
def f_implode(v):
    if type(v) is not list:
        raise JQError("implode input must be an array")
    out = []
    for n in v:
        if not isnum(n) or n != n:
            raise type_error(n, "can't be imploded, unicode codepoint needs to be numeric")
        cp = int(n) if abs(n) < 1e18 else -1
        if cp < 0 or cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
            cp = 0xFFFD
        out.append(chr(cp))
    return "".join(out)


@native("ltrimstr", 1)
def f_ltrimstr(v, pre):
    if type(v) is str and type(pre) is str and v.startswith(pre):
        return v[len(pre):]
    return v


@native("rtrimstr", 1)
def f_rtrimstr(v, suf):
    if type(v) is str and type(suf) is str and v.endswith(suf) and suf:
        return v[: len(v) - len(suf)]
    return v


@native("startswith", 1)
def f_startswith(v, pre):
    if type(v) is not str or type(pre) is not str:
        raise JQError("startswith() requires string inputs")
    return v.startswith(pre)


@native("endswith", 1)
def f_endswith(v, suf):
    if type(v) is not str or type(suf) is not str:
        raise JQError("endswith() requires string inputs")
    return v.endswith(suf)


@native("split", 1)
def f_split(v, sep):
    if type(v) is not str or type(sep) is not str:
        raise JQError("split input and separator must be strings")
    return lang.string_split(v, sep)


@native("_strindices", 1)
def f_strindices(v, sub):
    if type(v) is not str or type(sub) is not str:
        raise JQError("_strindices input and argument must be strings")
    hay = v.encode("utf-8")
    needle = sub.encode("utf-8")
    out = []
    if not needle:
        return out
    i = hay.find(needle)
    while i >= 0:
        out.append(float(i))
        i = hay.find(needle, i + 1)
    return out


@native("input", gen=True)
def f_input(inp, args, env):
    yield CTX.read_input()


@native("debug")
def f_debug(v):
    sys.stderr.write(jv.dumps(["DEBUG:", v]) + "\n")
    return v


@native("stderr")
def f_stderr(v):
    sys.stderr.write(jv.dumps(v))
    return v


@native("input_filename")
def f_input_filename(v):
    return None


@native("input_line_number")
def f_input_line_number(v):
    return 0.0


@native("halt")
def f_halt(v):
    raise Halt(None, None)


@native("halt_error", 1)
def f_halt_error(v, code):
    if not isnum(code):
        raise JQError("halt_error/1: number required")
    raise Halt(code, v)


@native("env")
def f_env(v):
    return dict(os.environ)


@native("format", 1)
def f_format(v, name):
    if type(name) is not str:
        raise type_error(name, "is not a valid format")
    return do_format(v, name)


# ---------------------------------------------------------------- formats


def _csv_cell(x, tsv):
    if x is None:
        return ""
    if x is True or x is False:
        return jv.dumps(x)
    if isnum(x):
        if x != x:
            return ""
        return jv.dumps(x)
    if type(x) is str:
        if tsv:
            return x.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
        return '"' + x.replace('"', '""') + '"'
    raise type_error(x, "is not valid in a csv row")


def _b64decode(s, orig):
    table = {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")}
    data = s.encode("utf-8")
    out = bytearray()
    code = 0
    nread = 0
    for b in data:
        if b == 0x3D:
            break
        c = chr(b)
        if c not in table:
            raise type_error(orig, "is not valid base64 data")
        code = (code << 6) | table[c]
        nread += 1
        if nread == 4:
            out += bytes([(code >> 16) & 0xFF, (code >> 8) & 0xFF, code & 0xFF])
            nread = 0
            code = 0
    if nread == 3:
        out += bytes([(code >> 10) & 0xFF, (code >> 2) & 0xFF])
    elif nread == 2:
        out += bytes([(code >> 4) & 0xFF])
    elif nread == 1:
        raise type_error(orig, "trailing base64 byte found")
    return jv.decode_utf8(bytes(out))


_URI_SAFE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~")


def do_format(v, name):
    if name == "text":
        return f_tostring(v)
    if name == "json":
        return jv.tojson(v)
    if name in ("csv", "tsv"):
        tsv = name == "tsv"
        if type(v) is not list:
            raise type_error(v, "cannot be %s-formatted, only array" % name)
        return ("\t" if tsv else ",").join(_csv_cell(x, tsv) for x in v)
    if name == "html":
        s = f_tostring(v)
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("'", "&apos;").replace('"', "&quot;"))
    if name == "uri":
        s = f_tostring(v)
        out = []
        for ch in s:
            if ch in _URI_SAFE:
                out.append(ch)
            else:
                out.extend("%%%02X" % b for b in ch.encode("utf-8"))
        return "".join(out)
    if name == "sh":
        items = v if type(v) is list else [v]
        out = []
        for x in items:
            if x is None or x is True or x is False or isnum(x):
                out.append(jv.dumps(x))
            elif type(x) is str:
                out.append("'" + x.replace("'", "'\\''") + "'")
            else:
                raise type_error(x, "can not be escaped for shell")
        return " ".join(out)
    if name == "base64":
        s = f_tostring(v)
        return base64.b64encode(s.encode("utf-8")).decode("ascii")
    if name == "base64d":
        s = f_tostring(v)
        return _b64decode(s, v)
    raise JQError("%s is not a valid format" % name)


lang.FORMAT = do_format


# ---------------------------------------------------------------- math


def _load_libm():
    try:
        return ctypes.CDLL("libm.so.6")
    except OSError:
        pass
    name = ctypes.util.find_library("m")
    if name:
        try:
            return ctypes.CDLL(name)
        except OSError:
            pass
    return None


_LIBM = _load_libm()


def _cfn(name, argtypes, restype=ctypes.c_double):
    if _LIBM is None:
        return None
    try:
        f = getattr(_LIBM, name)
    except AttributeError:
        return None
    f.argtypes = argtypes
    f.restype = restype
    return f


_D = ctypes.c_double


def _need_num(x):
    if not isnum(x):
        raise type_error(x, "number required")
    return float(x)


_MATH1 = [
    "acos", "acosh", "asin", "asinh", "atan", "atanh", "cbrt", "cos", "cosh",
    "exp", "exp10", "exp2", "expm1", "fabs", "floor", "gamma", "j0", "j1",
    "lgamma", "log", "log10", "log1p", "log2", "logb", "nearbyint", "rint",
    "round", "significand", "sin", "sinh", "sqrt", "tan", "tanh", "tgamma",
    "trunc", "y0", "y1", "ceil",
]
_MATH2 = ["atan2", "copysign", "drem", "fdim", "fmax", "fmin", "fmod", "hypot",
          "nextafter", "nexttoward", "pow", "scalb"]


def _register_math():
    for name in _MATH1:
        cf = _cfn(name, [_D])
        if cf is None:
            continue

        def f1(v, cf=cf):
            return float(cf(_need_num(v)))
        NATIVES[("f", name, 0)] = Native(f1, 0)
    for name in _MATH2:
        if name == "nexttoward":
            cf = _cfn(name, [_D, ctypes.c_longdouble])
        else:
            cf = _cfn(name, [_D, _D])
        if cf is None:
            continue

        def f2(inp, a, b, cf=cf):
            return float(cf(_need_num(a), _need_num(b)))
        NATIVES[("f", name, 2)] = Native(f2, 2)
    fma = _cfn("fma", [_D, _D, _D])
    if fma is not None:
        NATIVES[("f", "fma", 3)] = Native(
            lambda inp, a, b, c: float(fma(_need_num(a), _need_num(b), _need_num(c))), 3)
    ldexp = _cfn("ldexp", [_D, ctypes.c_int])
    if ldexp is not None:
        NATIVES[("f", "ldexp", 2)] = Native(
            lambda inp, a, b: float(ldexp(_need_num(a), int(_need_num(b)))), 2)
    scalbln = _cfn("scalbln", [_D, ctypes.c_long])
    if scalbln is not None:
        NATIVES[("f", "scalbln", 2)] = Native(
            lambda inp, a, b: float(scalbln(_need_num(a), int(_need_num(b)))), 2)
    frexp = _cfn("frexp", [_D, ctypes.POINTER(ctypes.c_int)])
    if frexp is not None:
        def f_frexp(v):
            e = ctypes.c_int(0)
            m = frexp(_need_num(v), ctypes.byref(e))
            return [float(m), float(e.value)]
        NATIVES[("f", "frexp", 0)] = Native(f_frexp, 0)
    modf = _cfn("modf", [_D, ctypes.POINTER(_D)])
    if modf is not None:
        def f_modf(v):
            ip = _D(0.0)
            fr = modf(_need_num(v), ctypes.byref(ip))
            return [float(fr), float(ip.value)]
        NATIVES[("f", "modf", 0)] = Native(f_modf, 0)
    lgr = _cfn("lgamma_r", [_D, ctypes.POINTER(ctypes.c_int)])
    if lgr is not None:
        def f_lgamma_r(v):
            s = ctypes.c_int(0)
            r = lgr(_need_num(v), ctypes.byref(s))
            return [float(r), float(s.value)]
        NATIVES[("f", "lgamma_r", 0)] = Native(f_lgamma_r, 0)


_register_math()


# ---------------------------------------------------------------- regex

_FLAG_CHARS = set("gixnspl")


def _translate_regex(pat):
    """Rewrite Oniguruma (Perl_NT syntax) constructs Python's re spells differently."""
    out = []
    i = 0
    n = len(pat)
    in_class = False
    while i < n:
        c = pat[i]
        if c == "\\" and i + 1 < n:
            d = pat[i + 1]
            if d == "h" and not in_class:
                out.append("[0-9a-fA-F]")
            elif d == "h":
                out.append("0-9a-fA-F")
            elif d == "H":
                out.append("[^0-9a-fA-F]")
            elif d == "z":
                out.append("\\Z")
            elif d == "Z":
                out.append("(?=\\n?\\Z)")
            elif d == "k" and i + 2 < n and pat[i + 2] == "<":
                j = pat.index(">", i)
                out.append("(?P=%s)" % pat[i + 3:j])
                i = j + 1
                continue
            else:
                out.append(c + d)
            i += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
            out.append(c)
            i += 1
            continue
        if c == "[":
            in_class = True
            out.append(c)
            i += 1
            if i < n and pat[i] == "^":
                out.append("^")
                i += 1
            if i < n and pat[i] == "]":
                out.append("\\]")
                i += 1
            continue
        if c == "(" and pat.startswith("(?<", i) and i + 3 < n and pat[i + 3] not in "=!":
            out.append("(?P<")
            i += 3
            continue
        if c == "$":
            out.append("(?=\\n?\\Z)")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


_RE_CACHE = {}


def _compile(regex, flags):
    key = (regex, flags)
    r = _RE_CACHE.get(key)
    if r is None:
        f = 0
        if "i" in flags:
            f |= re.IGNORECASE
        if "x" in flags:
            f |= re.VERBOSE
        if "p" in flags:
            f |= re.DOTALL
        try:
            r = re.compile(_translate_regex(regex), f)
        except (re.error, ValueError, IndexError) as e:
            raise JQError("%s (at offset 0) is not a valid regex: %s" % (regex, e))
        _RE_CACHE[key] = r
    return r


class _EmptyMatch:
    """An empty match jq reports after stepping into the middle of a UTF-8 character."""

    def __init__(self, pos, ngroups):
        self.pos = pos
        self.ngroups = ngroups

    def start(self, g=0):
        return self.pos if g == 0 else -1

    def end(self, g=0):
        return self.pos if g == 0 else -1

    def group(self, g=0):
        return "" if g == 0 else None


def _iter_matches(r, s):
    """Global search the way jq drives Oniguruma.

    After an empty match jq advances the start by one byte, so inside a
    multi-byte character it searches from the middle of the character; a
    pattern that can match the empty string then matches there again, at the
    offset of the next character.
    """
    pos = 0
    n = len(s)
    empty_ok = None
    while pos <= n:
        m = r.search(s, pos)
        if m is None:
            return
        yield m
        if m.end() == m.start():
            p = m.start()
            if p < n:
                extra = len(s[p].encode("utf-8")) - 1
                if extra:
                    if empty_ok is None:
                        empty_ok = r.fullmatch("") is not None
                    if empty_ok:
                        for _ in range(extra):
                            yield _EmptyMatch(p + 1, r.groups)
            pos = p + 1
        else:
            pos = m.end()


@native("_match_impl", 3)
def f_match_impl(inp, regex, modifiers, test):
    if type(inp) is not str:
        raise type_error(inp, "cannot be matched, as it is not a string")
    if type(regex) is not str:
        raise type_error(regex, "is not a string")
    if modifiers is None:
        flags = ""
    elif type(modifiers) is str:
        flags = modifiers
        for ch in flags:
            if ch not in _FLAG_CHARS:
                raise JQError("%s is not a valid modifier string" % flags)
    else:
        raise type_error(modifiers, "is not a string")
    glob = "g" in flags
    noempty = "n" in flags
    r = _compile(regex, flags)
    if test is True:
        for m in _iter_matches(r, inp):
            if noempty and m.end() == m.start():
                continue
            return True
        return False
    names = {idx: name for name, idx in r.groupindex.items()}
    results = []
    for m in _iter_matches(r, inp):
        if noempty and m.end() == m.start():
            continue
        caps = []
        for gi in range(1, (r.groups or 0) + 1):
            s = m.start(gi)
            if s < 0:
                caps.append({"offset": -1.0, "string": None, "length": 0.0, "name": names.get(gi)})
            else:
                txt = m.group(gi)
                caps.append({"offset": float(s), "length": float(len(txt)), "string": txt, "name": names.get(gi)})
        results.append({"offset": float(m.start()), "length": float(m.end() - m.start()),
                        "string": m.group(0), "captures": caps})
        if not glob:
            break
    return results


# ---------------------------------------------------------------- prelude

PRELUDE = r"""
def halt_error: halt_error(5);
def error(msg): msg | error;
def map(f): [.[] | f];
def select(f): if f then . else empty end;
def sort_by(f): _sort_by_impl(map([f]));
def group_by(f): _group_by_impl(map([f]));
def unique: group_by(.) | map(.[0]);
def unique_by(f): group_by(f) | map(.[0]);
def max_by(f): _max_by_impl(map([f]));
def min_by(f): _min_by_impl(map([f]));
def add: reduce .[] as $x (null; . + $x);
def del(f): delpaths([path(f)]);
def abs: if . < 0 then - . else . end;
def map_values(f): .[] |= f;
def recurse(f): def r: ., (f | r); r;
def recurse(f; cond): def r: ., (f | select(cond) | r); r;
def recurse: recurse(.[]?);
def to_entries: [keys_unsorted[] as $k | {key: $k, value: .[$k]}];
def from_entries: map({(.key // .Key // .name // .Name): (if has("value") then .value else .Value end)}) | add | . //= {};
def with_entries(f): to_entries | map(f) | from_entries;
def reverse: [.[length - 1 - range(0; length)]];
def indices($i):
  if type == "array" and ($i | type) == "array" then .[$i]
  elif type == "array" then .[[$i]]
  elif type == "string" and ($i | type) == "string" then _strindices($i)
  else .[$i] end;
def index($i): indices($i) | .[0];
def rindex($i): indices($i) | .[-1:][0];
def paths: path(recurse) | select(length > 0);
def paths(node_filter): path(recurse | select(node_filter)) | select(length > 0);
def isfinite: type == "number" and (isinfinite | not);
def arrays: select(type == "array");
def objects: select(type == "object");
def iterables: select(type | . == "array" or . == "object");
def booleans: select(type == "boolean");
def numbers: select(type == "number");
def normals: select(isnormal);
def finites: select(isfinite);
def strings: select(type == "string");
def nulls: select(. == null);
def values: select(. != null);
def scalars: select(type | . != "array" and . != "object");
def join($x): reduce .[] as $i (null;
    (if . == null then "" else . + $x end) +
    ($i | if type == "boolean" or type == "number" then tostring else . // "" end)
  ) // "";
def _flatten($x): reduce .[] as $i ([]; if $i | type == "array" and $x != 0 then . + ($i | _flatten($x - 1)) else . + [$i] end);
def flatten($x): if $x < 0 then error("flatten depth must not be negative") else _flatten($x) end;
def flatten: _flatten(-1);
def range($x): range(0; $x);
def match(re; mode): _match_impl(re; mode; false) | .[];
def match($val): ($val | type) as $vt
  | if $vt == "string" then match($val; null)
    elif $vt == "array" and ($val | length) > 1 then match($val[0]; $val[1])
    elif $vt == "array" and ($val | length) > 0 then match($val[0]; null)
    else error($vt + " not a string or array") end;
def test(re; mode): _match_impl(re; mode; true);
def test($val): ($val | type) as $vt
  | if $vt == "string" then test($val; null)
    elif $vt == "array" and ($val | length) > 1 then test($val[0]; $val[1])
    elif $vt == "array" and ($val | length) > 0 then test($val[0]; null)
    else error($vt + " not a string or array") end;
def capture(re; mods): match(re; mods) | reduce (.captures | .[] | select(.name != null) | {(.name): .string}) as $pair ({}; . + $pair);
def capture($val): ($val | type) as $vt
  | if $vt == "string" then capture($val; null)
    elif $vt == "array" and ($val | length) > 1 then capture($val[0]; $val[1])
    elif $vt == "array" and ($val | length) > 0 then capture($val[0]; null)
    else error($vt + " not a string or array") end;
def scan($re; $flags):
  match($re; "g" + $flags)
  | if (.captures | length > 0) then [.captures | .[] | .string] else .string end;
def scan($re): scan($re; null);
def _nwise($n):
  def n: if length <= $n then . else .[0:$n], (.[$n:] | n) end;
  n;
def _nwise(a; $n): a | _nwise($n);
def splits($re; flags): . as $s
  | [match($re; "g" + flags) | (.offset, .offset + .length)]
  | [0] + . + [$s | length]
  | _nwise(2)
  | $s[.[0]:.[1]];
def splits($re): splits($re; null);
def split($re; flags): [splits($re; flags)];
def sub($re; s; $flags): . as $in
  | (reduce match($re; $flags) as $edit
      ({result: [], previous: 0};
        $in[.previous: ($edit | .offset)] as $gap
        | [reduce ($edit | .captures | .[] | select(.name != null) | {(.name): .string}) as $pair
            ({}; . + $pair) | s] as $inserts
        | reduce range(0; $inserts | length) as $ix (.; .result[$ix] += $gap + $inserts[$ix])
        | .previous = ($edit | .offset + .length))
    | .result[] + $in[.previous:])
  // $in;
def sub($re; s): sub($re; s; "");
def gsub($re; s; flags): sub($re; s; flags + "g");
def gsub($re; s): sub($re; s; "g");
def while(cond; update):
  def _while: if cond then ., (update | _while) else empty end;
  _while;
def until(cond; next):
  def _until: if cond then . else (next | _until) end;
  _until;
def limit($n; exp):
  if $n > 0 then label $out | foreach exp as $item ($n; . - 1; $item, if . <= 0 then break $out else empty end)
  elif $n == 0 then empty
  else exp end;
def range($init; $upto; $by):
  if $by > 0 then $init | while(. < $upto; . + $by)
  elif $by < 0 then $init | while(. > $upto; . + $by)
  else empty end;
def first(g): label $out | g | ., break $out;
def isempty(g): first((g | false), true);
def all(generator; condition): isempty(generator | condition and empty);
def any(generator; condition): isempty(generator | condition or empty) | not;
def all(condition): all(.[]; condition);
def any(condition): any(.[]; condition);
def all: all(.[]; .);
def any: any(.[]; .);
def last(g): reduce g as $item (null; $item);
def nth($n; g):
  if $n < 0 then error("nth doesn't support negative indices")
  else label $out | foreach g as $item ($n + 1; . - 1; if . <= 0 then $item, break $out else empty end) end;
def first: .[0];
def last: .[-1];
def nth($n): .[$n];
def combinations:
  if length == 0 then [] else
    .[0][] as $x | (.[1:] | combinations) as $y | [$x] + $y
  end;
def combinations(n): . as $dot | [range(n) | $dot] | combinations;
def transpose: [range(0; map(length) | max // 0) as $i | [.[][$i]]];
def in(xs): . as $x | xs | has($x);
def inside(xs): . as $x | xs | contains($x);
def repeat(exp):
  def _repeat: exp, _repeat;
  _repeat;
def inputs: try repeat(input) catch if . == "break" then empty else error end;
def ascii_downcase: explode | map(if 65 <= . and . <= 90 then . + 32 else . end) | implode;
def ascii_upcase: explode | map(if 97 <= . and . <= 122 then . - 32 else . end) | implode;
def truncate_stream(stream): . as $n | null | stream | . as $input | if (.[0] | length) > $n then setpath([0]; $input[0][$n:]) else empty end;
def fromstream(i): {x: null, e: false} as $init
  | foreach i as $i ($init;
      if .e then $init else . end
      | if $i | length == 2
        then setpath(["e"]; $i[0] | length == 0) | setpath(["x"] + $i[0]; $i[1])
        else setpath(["e"]; $i[0] | length == 1) end;
      if .e then .x else empty end);
def tostream:
  path(def r: (.[]? | r), .; r) as $p
  | getpath($p)
  | reduce path(.[]?) as $q ([$p, .]; [$p + $q]);
def bsearch($target):
  if length == 0 then -1
  elif length == 1 then (if $target == .[0] then 0 elif $target < .[0] then -1 else -2 end)
  else . as $in
    | [0, length - 1, null]
    | until(.[0] > .[1];
        if .[2] != null then (.[1] = -1)
        else
          (((.[1] + .[0]) / 2) | floor) as $mid
          | $in[$mid] as $monkey
          | if $monkey == $target then (.[2] = $mid)
            elif .[0] == .[1] then (.[1] = -1)
            elif $monkey < $target then (.[0] = ($mid + 1))
            else (.[1] = ($mid - 1)) end
        end)
    | if .[2] == null then
        (if $in[.[0]] < $target then (-2 - .[0]) else (-1 - .[0]) end)
      else .[2] end
  end;
def walk(f):
  def w: if type == "object" then map_values(w) elif type == "array" then map(w) else . end | f;
  w;
def pick(pathexps): . as $in | reduce path(pathexps) as $a (null; setpath($a; $in | getpath($a)));
def debug(msgs): (msgs | debug | empty), .;
def INDEX(stream; idx_expr): reduce stream as $row ({}; .[$row | idx_expr | tostring] = $row);
def INDEX(idx_expr): INDEX(.[]; idx_expr);
def JOIN($idx; idx_expr): [.[] | [., $idx[idx_expr]]];
def JOIN($idx; stream; idx_expr): stream | [., $idx[idx_expr]];
def JOIN($idx; stream; idx_expr; join_expr): stream | [., $idx[idx_expr]] | join_expr;
def IN(s): any(s == .; .);
def IN(src; s): any(src == s; .);
"""


def install():
    g = lang.GLOBALS
    g.funcs.update(NATIVES)
    prog = lang.parse_program(PRELUDE + " .")
    node = prog
    env = None
    while isinstance(node, lang.FuncDef):
        env = lang.Env(env, ("f", node.name, len(node.params)), None)
        clo = lang.Closure(node, None)
        g.funcs[("f", node.name, len(node.params))] = clo
        node = node.rest
    # Prelude closures resolve through GLOBALS, so their env stays empty.
