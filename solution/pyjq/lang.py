"""Lexer, parser and evaluator for the jq language.

Evaluation is generator based: every node has ev(inp, env), which yields the
node's outputs for one input. Path expressions (path(f), assignment, del,
paths, ...) use paths(inp, ps, env), which yields (value, ps) pairs where ps is
the (value_at_path, path) state jq keeps while tracking paths. Path operations
check that their input is identical to value_at_path, which is how jq detects
expressions like path(.a | tostring | .b).
"""
import re

from . import jv
from .jv import JQError, Lit, compare, identical, kind, trunc_dump, truthy


class CompileError(Exception):
    pass


# ====================================================================== lexer

KEYWORDS = {
    "def", "if", "then", "elif", "else", "end", "as", "reduce", "foreach",
    "try", "catch", "label", "import", "include", "and", "or", "__loc__",
    "module", "break",
}

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>[ \t\r\n]+|\#[^\n]*)
  | (?P<num>(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)
  | (?P<field>\.[a-zA-Z_][a-zA-Z_0-9]*)
  | (?P<var>\$(?:[a-zA-Z_][a-zA-Z_0-9]*::)*[a-zA-Z_][a-zA-Z_0-9]*)
  | (?P<fmt>@[a-zA-Z0-9_]+)
  | (?P<ident>(?:[a-zA-Z_][a-zA-Z_0-9]*::)*[a-zA-Z_][a-zA-Z_0-9]*)
  | (?P<op>\?//|//=|\|=|\+=|-=|\*=|/=|%=|==|!=|<=|>=|//|\.\.|[.\[\]{}()|,:;=<>+\-*/%?])
  | (?P<quote>")
    """,
    re.VERBOSE,
)


class Tok:
    __slots__ = ("type", "val", "pos")

    def __init__(self, type_, val, pos):
        self.type = type_
        self.val = val
        self.pos = pos

    def __repr__(self):
        return "Tok(%s,%r)" % (self.type, self.val)


def _lex_string(text, pos):
    """Lex a string literal starting just after the opening quote.

    Returns (parts, newpos). parts holds str chunks and token lists for
    interpolations. Literal chunks go through the JSON string parser, as jq
    does, so raw control characters are rejected.
    """
    parts = []
    chunk = []
    n = len(text)
    i = pos
    while True:
        if i >= n:
            raise CompileError("unterminated string")
        c = text[i]
        if c == '"':
            i += 1
            break
        if c == "\\":
            if i + 1 < n and text[i + 1] == "(":
                if chunk:
                    parts.append(_decode_chunk("".join(chunk)))
                    chunk = []
                toks, i = _lex(text, i + 2, stop_at_paren=True)
                parts.append(toks)
                continue
            if i + 1 >= n:
                raise CompileError("unterminated string")
            chunk.append(c)
            chunk.append(text[i + 1])
            i += 2
            continue
        chunk.append(c)
        i += 1
    if chunk:
        parts.append(_decode_chunk("".join(chunk)))
    return parts, i


def _decode_chunk(raw):
    try:
        return jv.parse_one('"' + raw + '"', allow_controls=True)
    except JQError as e:
        raise CompileError(str(e.value))


def _lex(text, pos=0, stop_at_paren=False):
    toks = []
    depth = 0
    n = len(text)
    while pos < n:
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise CompileError("syntax error, unexpected INVALID_CHARACTER")
        kind_ = m.lastgroup
        val = m.group(kind_)
        start = pos
        pos = m.end()
        if kind_ == "ws":
            continue
        if kind_ == "quote":
            parts, pos = _lex_string(text, pos)
            toks.append(Tok("str", parts, start))
            continue
        if kind_ == "num":
            toks.append(Tok("num", jv.Lit(jv.lit_from_text(val).dec), start))
            continue
        if kind_ == "ident" and val in KEYWORDS:
            toks.append(Tok("kw", val, start))
            continue
        if kind_ == "var" and val == "$__loc__":
            toks.append(Tok("loc", val, start))
            continue
        if kind_ == "op" and stop_at_paren:
            if val == "(":
                depth += 1
            elif val == ")":
                if depth == 0:
                    toks.append(Tok("eof", None, start))
                    return toks, pos
                depth -= 1
        toks.append(Tok(kind_, val, start))
    if stop_at_paren:
        raise CompileError("unterminated string interpolation")
    toks.append(Tok("eof", None, pos))
    return toks, pos


# ====================================================================== AST


class Env:
    __slots__ = ("parent", "key", "val")

    def __init__(self, parent, key, val):
        self.parent = parent
        self.key = key
        self.val = val

    def lookup(self, key):
        e = self
        while e is not None:
            if e.key == key:
                return e.val
            e = e.parent
        return None


def env_get(env, key):
    while env is not None:
        if env.key == key:
            return env.val
        env = env.parent
    return _MISSING


_MISSING = object()


class Break(Exception):
    def __init__(self, token):
        Exception.__init__(self)
        self.token = token


class Node:
    def ev(self, inp, env):
        raise NotImplementedError

    def paths(self, inp, ps, env):
        for v in self.ev(inp, env):
            yield v, ps


class Identity(Node):
    def ev(self, inp, env):
        yield inp

    def paths(self, inp, ps, env):
        yield inp, ps


class Literal(Node):
    def __init__(self, value):
        self.value = value

    def ev(self, inp, env):
        yield self.value


class Loc(Node):
    def __init__(self, line):
        self.line = line

    def ev(self, inp, env):
        yield {"file": "<top-level>", "line": float(self.line)}


class Var(Node):
    def __init__(self, name):
        self.name = name

    def ev(self, inp, env):
        v = env_get(env, ("v", self.name))
        if v is _MISSING:
            v = GLOBALS.vars.get(self.name)
        yield v


class Pipe(Node):
    def __init__(self, a, b):
        self.a = a
        self.b = b

    def ev(self, inp, env):
        b = self.b
        for v in self.a.ev(inp, env):
            yield from b.ev(v, env)

    def paths(self, inp, ps, env):
        b = self.b
        for v, ps1 in self.a.paths(inp, ps, env):
            yield from b.paths(v, ps1, env)


class Comma(Node):
    def __init__(self, a, b):
        self.a = a
        self.b = b

    def ev(self, inp, env):
        yield from self.a.ev(inp, env)
        yield from self.b.ev(inp, env)

    def paths(self, inp, ps, env):
        yield from self.a.paths(inp, ps, env)
        yield from self.b.paths(inp, ps, env)


def _check_intact(t, ps, what):
    if not identical(t, ps[0]):
        raise JQError(what)


def index_value(t, k):
    """jv_get."""
    tt = type(t)
    tk = type(k)
    if tt is dict and tk is str:
        return t.get(k)
    if tt is list and (tk is float or tk is Lit):
        if k != k or k in (float("inf"), float("-inf")):
            return None
        idx = int(k)
        if idx < 0:
            idx += len(t)
        if 0 <= idx < len(t):
            return t[idx]
        return None
    if tt is list and tk is dict:
        s, e = parse_slice(t, k)
        return t[s:e]
    if tt is str and tk is dict:
        s, e = parse_slice(t, k)
        return t[s:e]
    if tt is list and tk is list:
        return array_indexes(t, k)
    if t is None and (tk is str or tk is float or tk is Lit or tk is dict):
        return None
    if tk is str:
        if len(k.encode("utf-8")) < 30:
            raise JQError('Cannot index %s with string "%s"' % (kind(t), k))
        raise JQError("Cannot index %s with string" % kind(t))
    raise JQError("Cannot index %s with %s" % (kind(t), kind(k)))


def parse_slice(t, k):
    length = len(t)
    if "start" not in k or "end" not in k:
        raise JQError("Array/string slice indices must be integers")
    s = k["start"]
    e = k["end"]
    if s is None:
        s = 0.0
    if e is None:
        e = float(length)
    if not (jv.isnum(s) and jv.isnum(e)):
        raise JQError("Array/string slice indices must be integers")
    ds = float(s)
    de = float(e)
    if ds != ds:
        ds = 0.0
    if de != de:
        de = float(length)
    if ds < 0:
        ds += length
    if de < 0:
        de += length
    if ds < 0:
        ds = 0
    if ds > length:
        ds = length
    if de > length:
        de = length
    if de < ds:
        de = ds
    si = int(ds)
    ei = int(de)
    if de > ei:
        ei += 1
    if ei > length:
        ei = length
    return si, ei


def array_indexes(a, b):
    res = []
    if not b:
        return res
    for ai in range(len(a)):
        ok = True
        for bi, y in enumerate(b):
            if ai + bi >= len(a) or not jv.equal(a[ai + bi], y):
                ok = False
                break
        if ok:
            res.append(float(ai))
    return res


class Index(Node):
    def __init__(self, target, key, opt=False):
        self.target = target
        self.key = key
        self.opt = opt

    def ev(self, inp, env):
        target = self.target
        for k in self.key.ev(inp, env):
            for t in target.ev(inp, env):
                try:
                    v = index_value(t, k)
                except JQError:
                    if self.opt:
                        continue
                    raise
                yield v

    def paths(self, inp, ps, env):
        target = self.target
        for k in self.key.ev(inp, env):
            for t, ps1 in target.paths(inp, ps, env):
                if not identical(t, ps1[0]):
                    raise JQError("Invalid path expression near attempt to access element %s of %s"
                                  % (trunc_dump(k, 15), trunc_dump(t, 30)))
                try:
                    v = index_value(t, k)
                except JQError:
                    if self.opt:
                        continue
                    raise
                yield v, (v, ps1[1] + (k,))


class Slice(Node):
    def __init__(self, target, start, end, opt=False):
        self.target = target
        self.start = start
        self.end = end
        self.opt = opt

    def _keys(self, inp, env):
        starts = self.start.ev(inp, env) if self.start is not None else (None,)
        for s in starts:
            ends = self.end.ev(inp, env) if self.end is not None else (None,)
            for e in ends:
                yield {"start": s, "end": e}

    def ev(self, inp, env):
        for k in self._keys(inp, env):
            for t in self.target.ev(inp, env):
                try:
                    if t is None:
                        v = None
                    elif type(t) in (list, str):
                        v = index_value(t, k)
                    else:
                        raise JQError("Cannot index %s with object" % kind(t))
                except JQError:
                    if self.opt:
                        continue
                    raise
                yield v

    def paths(self, inp, ps, env):
        for k in self._keys(inp, env):
            for t, ps1 in self.target.paths(inp, ps, env):
                if not identical(t, ps1[0]):
                    raise JQError("Invalid path expression near attempt to access element %s of %s"
                                  % (trunc_dump(k, 15), trunc_dump(t, 30)))
                try:
                    if t is None:
                        v = None
                    elif type(t) in (list, str):
                        v = index_value(t, k)
                    else:
                        raise JQError("Cannot index %s with object" % kind(t))
                except JQError:
                    if self.opt:
                        continue
                    raise
                yield v, (v, ps1[1] + (k,))


def iterate_error(t):
    return JQError("Cannot iterate over %s (%s)" % (kind(t), trunc_dump(t)))


class Iterate(Node):
    def __init__(self, target, opt=False):
        self.target = target
        self.opt = opt

    def ev(self, inp, env):
        for t in self.target.ev(inp, env):
            tt = type(t)
            if tt is list:
                yield from t
            elif tt is dict:
                yield from list(t.values())
            elif not self.opt:
                raise iterate_error(t)

    def paths(self, inp, ps, env):
        for t, ps1 in self.target.paths(inp, ps, env):
            if not identical(t, ps1[0]):
                raise JQError("Invalid path expression near attempt to iterate through %s" % trunc_dump(t, 30))
            tt = type(t)
            base = ps1[1]
            if tt is list:
                for i, v in enumerate(t):
                    yield v, (v, base + (float(i),))
            elif tt is dict:
                for k, v in list(t.items()):
                    yield v, (v, base + (k,))
            elif not self.opt:
                raise iterate_error(t)


class Try(Node):
    def __init__(self, body, handler=None):
        self.body = body
        self.handler = handler

    def ev(self, inp, env):
        try:
            for v in self.body.ev(inp, env):
                yield v
        except JQError as e:
            if self.handler is not None:
                yield from self.handler.ev(e.value, env)

    def paths(self, inp, ps, env):
        try:
            for v in self.body.paths(inp, ps, env):
                yield v
        except JQError as e:
            if self.handler is not None:
                for v in self.handler.ev(e.value, env):
                    yield v, ps


class If(Node):
    def __init__(self, cond, then, else_):
        self.cond = cond
        self.then = then
        self.else_ = else_

    def ev(self, inp, env):
        for c in self.cond.ev(inp, env):
            if truthy(c):
                yield from self.then.ev(inp, env)
            elif self.else_ is None:
                yield inp
            else:
                yield from self.else_.ev(inp, env)

    def paths(self, inp, ps, env):
        for c in self.cond.ev(inp, env):
            if truthy(c):
                yield from self.then.paths(inp, ps, env)
            elif self.else_ is None:
                yield inp, ps
            else:
                yield from self.else_.paths(inp, ps, env)


class Alternative(Node):
    def __init__(self, a, b):
        self.a = a
        self.b = b

    def ev(self, inp, env):
        found = False
        for v in self.a.ev(inp, env):
            if truthy(v):
                found = True
                yield v
        if not found:
            yield from self.b.ev(inp, env)

    def paths(self, inp, ps, env):
        found = False
        for v, ps1 in self.a.paths(inp, ps, env):
            if truthy(v):
                found = True
                yield v, ps1
        if not found:
            yield from self.b.paths(inp, ps, env)


class And(Node):
    def __init__(self, a, b):
        self.a = a
        self.b = b

    def ev(self, inp, env):
        for l in self.a.ev(inp, env):
            if not truthy(l):
                yield False
                continue
            for r in self.b.ev(inp, env):
                yield truthy(r)


class Or(Node):
    def __init__(self, a, b):
        self.a = a
        self.b = b

    def ev(self, inp, env):
        for l in self.a.ev(inp, env):
            if truthy(l):
                yield True
                continue
            for r in self.b.ev(inp, env):
                yield truthy(r)


class BinOp(Node):
    def __init__(self, op, a, b):
        self.op = op
        self.a = a
        self.b = b
        self.fn = BINOPS[op]

    def ev(self, inp, env):
        fn = self.fn
        a = self.a
        for r in self.b.ev(inp, env):
            for l in a.ev(inp, env):
                yield fn(l, r)


class Neg(Node):
    def __init__(self, a):
        self.a = a

    def ev(self, inp, env):
        for v in self.a.ev(inp, env):
            if jv.isnum(v):
                yield -float(v)
            else:
                raise jv.type_error(v, "cannot be negated")


class ArrayCons(Node):
    def __init__(self, body):
        self.body = body

    def ev(self, inp, env):
        if self.body is None:
            yield []
        else:
            yield list(self.body.ev(inp, env))


class ObjectCons(Node):
    def __init__(self, entries):
        self.entries = entries  # list of (keynode, valnode)

    def ev(self, inp, env):
        entries = self.entries

        def rec(i, acc):
            if i == len(entries):
                yield dict(acc)
                return
            kn, vn = entries[i]
            for k in kn.ev(inp, env):
                for v in vn.ev(inp, env):
                    if type(k) is not str:
                        raise JQError("Cannot use %s (%s) as object key" % (kind(k), trunc_dump(k)))
                    acc2 = dict(acc)
                    acc2[k] = v
                    yield from rec(i + 1, acc2)

        yield from rec(0, {})


class StringNode(Node):
    def __init__(self, parts, fmt):
        self.parts = parts
        self.fmt = fmt

    def ev(self, inp, env):
        parts = self.parts
        fmt = self.fmt

        def rec(i):
            if i < 0:
                yield ""
                return
            p = parts[i]
            if type(p) is str:
                for s in rec(i - 1):
                    yield s + p
            else:
                for v in p.ev(inp, env):
                    if fmt is None:
                        fv = v if type(v) is str else jv.tojson(v)
                    else:
                        fv = FORMAT(v, fmt)
                    for s in rec(i - 1):
                        yield s + fv

        yield from rec(len(parts) - 1)


class FormatNode(Node):
    def __init__(self, name):
        self.name = name

    def ev(self, inp, env):
        yield FORMAT(inp, self.name)


class Label(Node):
    def __init__(self, name, body):
        self.name = name
        self.body = body

    def ev(self, inp, env):
        token = object()
        env2 = Env(env, ("l", self.name), token)
        try:
            for v in self.body.ev(inp, env2):
                yield v
        except Break as b:
            if b.token is not token:
                raise

    def paths(self, inp, ps, env):
        token = object()
        env2 = Env(env, ("l", self.name), token)
        try:
            for v in self.body.paths(inp, ps, env2):
                yield v
        except Break as b:
            if b.token is not token:
                raise


class BreakNode(Node):
    def __init__(self, name):
        self.name = name

    def ev(self, inp, env):
        raise Break(env_get(env, ("l", self.name)))
        yield  # pragma: no cover

    def paths(self, inp, ps, env):
        raise Break(env_get(env, ("l", self.name)))
        yield  # pragma: no cover


# ---------------------------------------------------------------- patterns


class PVar:
    def __init__(self, name):
        self.name = name


class PArray:
    def __init__(self, elems):
        self.elems = elems


class PObject:
    def __init__(self, entries):
        # entries: (keyvar or None, keynode or None, subpattern or None)
        self.entries = entries


def pattern_vars(p, out):
    if isinstance(p, PVar):
        out.append(p.name)
    elif isinstance(p, PArray):
        for e in p.elems:
            pattern_vars(e, out)
    else:
        for keyvar, keynode, sub in p.entries:
            if keyvar is not None:
                out.append(keyvar)
            if sub is not None:
                pattern_vars(sub, out)
    return out


def destructure(p, val, env, outer_inp):
    if isinstance(p, PVar):
        yield Env(env, ("v", p.name), val)
    elif isinstance(p, PArray):
        def rec(i, e):
            if i == len(p.elems):
                yield e
                return
            if val is not None and type(val) is not list:
                raise JQError("Cannot index %s with number" % kind(val))
            item = index_value(val, float(i))
            for e2 in destructure(p.elems[i], item, e, outer_inp):
                yield from rec(i + 1, e2)
        yield from rec(0, env)
    else:
        entries = p.entries

        def rec(i, e):
            if i == len(entries):
                yield e
                return
            keyvar, keynode, sub = entries[i]
            if keyvar is not None:
                keys = (keyvar,)
            else:
                keys = keynode.ev(val, e)
            for k in keys:
                if type(k) is not str:
                    raise JQError("Cannot index %s with %s" % (kind(val), kind(k)))
                item = index_value(val, k)
                e2 = e
                if keyvar is not None:
                    e2 = Env(e2, ("v", keyvar), item)
                if sub is None:
                    yield from rec(i + 1, e2)
                else:
                    for e3 in destructure(sub, item, e2, outer_inp):
                        yield from rec(i + 1, e3)
        yield from rec(0, env)


def bind_alternatives(patterns, allvars, val, env, inp, body_fn):
    """Run body_fn(env) for the first pattern whose destructuring and body succeed."""
    last = len(patterns) - 1
    for i, p in enumerate(patterns):
        e0 = env
        if last > 0:
            for name in allvars:
                e0 = Env(e0, ("v", name), None)
        try:
            for e in destructure(p, val, e0, inp):
                yield from body_fn(e)
            return
        except JQError:
            if i == last:
                raise


class Bind(Node):
    def __init__(self, source, patterns, body):
        self.source = source
        self.patterns = patterns
        self.body = body
        allv = []
        for p in patterns:
            pattern_vars(p, allv)
        self.allvars = list(dict.fromkeys(allv))

    def ev(self, inp, env):
        body = self.body
        for val in self.source.ev(inp, env):
            yield from bind_alternatives(self.patterns, self.allvars, val, env, inp,
                                         lambda e: body.ev(inp, e))

    def paths(self, inp, ps, env):
        body = self.body
        for val in self.source.ev(inp, env):
            yield from bind_alternatives(self.patterns, self.allvars, val, env, inp,
                                         lambda e: body.paths(inp, ps, e))


class Reduce(Node):
    def __init__(self, source, patterns, init, update):
        self.source = source
        self.patterns = patterns
        self.init = init
        self.update = update
        allv = []
        for p in patterns:
            pattern_vars(p, allv)
        self.allvars = list(dict.fromkeys(allv))

    def ev(self, inp, env):
        update = self.update
        for acc in self.init.ev(inp, env):
            state = [acc]
            for val in self.source.ev(inp, env):
                def body(e):
                    cur = state[0]
                    state[0] = None
                    for u in update.ev(cur, e):
                        state[0] = u
                    return ()
                for _ in bind_alternatives(self.patterns, self.allvars, val, env, inp, body):
                    pass
            yield state[0]


class Foreach(Node):
    def __init__(self, source, patterns, init, update, extract):
        self.source = source
        self.patterns = patterns
        self.init = init
        self.update = update
        self.extract = extract
        allv = []
        for p in patterns:
            pattern_vars(p, allv)
        self.allvars = list(dict.fromkeys(allv))

    def _run(self, inp, env, items):
        update = self.update
        extract = self.extract
        for acc in self.init.ev(inp, env):
            state = [acc]
            for val, extra in items():
                def body(e, extra=extra):
                    cur = state[0]
                    state[0] = None
                    for u in update.ev(cur, e):
                        state[0] = u
                        if extract is None:
                            yield u, extra
                        else:
                            for x in extract.ev(u, e):
                                yield x, extra
                yield from bind_alternatives(self.patterns, self.allvars, val, env, inp, body)

    def ev(self, inp, env):
        def items():
            for v in self.source.ev(inp, env):
                yield v, None
        for v, _ in self._run(inp, env, items):
            yield v

    def paths(self, inp, ps, env):
        def items():
            for v, ps1 in self.source.paths(inp, ps, env):
                yield v, ps1
        for v, ps1 in self._run(inp, env, items):
            yield v, ps1


# ---------------------------------------------------------------- functions


class Closure:
    __slots__ = ("fdef", "env")

    def __init__(self, fdef, env):
        self.fdef = fdef
        self.env = env


class ArgClosure:
    __slots__ = ("node", "env")

    def __init__(self, node, env):
        self.node = node
        self.env = env


class FuncDef(Node):
    def __init__(self, name, params, body, rest):
        self.name = name
        self.params = params  # list of (is_var, name)
        self.body = body
        self.rest = rest

    def define(self, env):
        env2 = Env(env, ("f", self.name, len(self.params)), None)
        env2.val = Closure(self, env2)
        return env2

    def ev(self, inp, env):
        yield from self.rest.ev(inp, self.define(env))

    def paths(self, inp, ps, env):
        yield from self.rest.paths(inp, ps, self.define(env))


def _bind_params(fd, args, callenv, fenv, inp):
    e = fenv
    varparams = []
    for (isvar, pname), argnode in zip(fd.params, args):
        e = Env(e, ("f", pname, 0), ArgClosure(argnode, callenv))
        if isvar:
            varparams.append((pname, argnode))
    if not varparams:
        yield e
        return

    def rec(i, e):
        if i == len(varparams):
            yield e
            return
        pname, argnode = varparams[i]
        for v in argnode.ev(inp, callenv):
            yield from rec(i + 1, Env(e, ("v", pname), v))

    yield from rec(0, e)


class Call(Node):
    def __init__(self, name, args):
        self.name = name
        self.args = args
        self.key = ("f", name, len(args))

    def _target(self, env):
        f = env_get(env, self.key)
        if f is _MISSING:
            f = GLOBALS.funcs.get(self.key)
        return f

    def ev(self, inp, env):
        f = self._target(env)
        if type(f) is ArgClosure:
            yield from f.node.ev(inp, f.env)
        elif type(f) is Closure:
            fd = f.fdef
            if not fd.params:
                yield from fd.body.ev(inp, f.env)
            else:
                for e in _bind_params(fd, self.args, env, f.env, inp):
                    yield from fd.body.ev(inp, e)
        else:
            yield from f.ev(inp, self.args, env)

    def paths(self, inp, ps, env):
        f = self._target(env)
        if type(f) is ArgClosure:
            yield from f.node.paths(inp, ps, f.env)
        elif type(f) is Closure:
            fd = f.fdef
            if not fd.params:
                yield from fd.body.paths(inp, ps, f.env)
            else:
                for e in _bind_params(fd, self.args, env, f.env, inp):
                    yield from fd.body.paths(inp, ps, e)
        else:
            yield from f.paths(inp, ps, self.args, env)


class Native:
    """A builtin implemented in Python.

    Value-style natives take their arguments as values (jq's C functions),
    with the last argument varying slowest. Generator natives receive the
    argument nodes and do their own evaluation.
    """

    def __init__(self, fn, nargs, gen=False, pathfn=None):
        self.fn = fn
        self.nargs = nargs
        self.gen = gen
        self.pathfn = pathfn

    def ev(self, inp, args, env):
        if self.gen:
            yield from self.fn(inp, args, env)
            return
        if not args:
            yield self.fn(inp)
            return
        fn = self.fn
        n = len(args)

        def rec(i, vals):
            if i < 0:
                yield fn(inp, *vals)
                return
            for v in args[i].ev(inp, env):
                vals[i] = v
                yield from rec(i - 1, vals)

        yield from rec(n - 1, [None] * n)

    def paths(self, inp, ps, args, env):
        if self.pathfn is not None:
            yield from self.pathfn(inp, ps, args, env)
            return
        for v in self.ev(inp, args, env):
            yield v, ps


class Assign(Node):
    def __init__(self, op, lhs, rhs):
        self.op = op
        self.lhs = lhs
        self.rhs = rhs

    def ev(self, inp, env):
        op = self.op
        lhs = self.lhs
        if op == "=":
            for v in self.rhs.ev(inp, env):
                out = inp
                for p in path_list(lhs, inp, env):
                    out = setpath(out, p, v)
                yield out
        elif op == "|=":
            rhs = self.rhs
            yield modify(inp, lhs, env, lambda x: rhs.ev(x, env))
        elif op == "//=":
            for v in self.rhs.ev(inp, env):
                out = inp
                for p in path_list(lhs, inp, env):
                    cur = getpath(out, p)
                    out = setpath(out, p, cur if truthy(cur) else v)
                yield out
        else:
            fn = BINOPS[op[0]]
            for v in self.rhs.ev(inp, env):
                yield modify(inp, lhs, env, lambda x, v=v: (fn(x, v),))


def path_list(node, inp, env):
    for v, ps in node.paths(inp, (inp, ()), env):
        if not identical(v, ps[0]):
            raise JQError("Invalid path expression with result %s" % trunc_dump(v, 30))
        yield list(ps[1])


def modify(inp, lhs, env, update):
    x = inp
    dels = []
    for p in path_list(lhs, inp, env):
        v = getpath(x, p)
        got = False
        for u in update(v):
            x = setpath(x, p, u)
            got = True
            break
        if not got:
            dels.append(p)
    return delpaths(x, dels)


# ====================================================================== paths


def getpath(t, path):
    for k in path:
        if t is None:
            return None
        t = index_value(t, k)
    return t


def setpath(t, path, v, i=0):
    if i == len(path):
        return v
    k = path[i]
    sub = index_value(t, k)
    newsub = setpath(sub, path, v, i + 1)
    return set_value(t, k, newsub)


def set_value(t, k, v):
    tk = type(k)
    tt = type(t)
    if tk is str and (tt is dict or t is None):
        d = dict(t) if t is not None else {}
        d[k] = v
        return d
    if (tk is float or tk is Lit) and (tt is list or t is None):
        arr = list(t) if t is not None else []
        idx = int(k)
        if idx < 0:
            idx += len(arr)
            if idx < 0:
                raise JQError("Out of bounds negative array index")
        if idx >= len(arr):
            arr.extend([None] * (idx + 1 - len(arr)))
        arr[idx] = v
        return arr
    if tk is dict and (tt is list or t is None):
        arr = list(t) if t is not None else []
        s, e = parse_slice(arr, k)
        if type(v) is not list:
            raise JQError("A slice of an array can only be assigned another array")
        return arr[:s] + list(v) + arr[e:]
    if tk is dict and tt is str:
        raise JQError("Cannot update string slices")
    if tk is str:
        raise JQError('Cannot index %s with string "%s"' % (kind(t), k))
    raise JQError("Cannot index %s with %s" % (kind(t), kind(k)))


def delpaths(t, paths):
    if not paths:
        return t
    for p in paths:
        if type(p) is not list:
            raise JQError("Path must be specified as array, not %s" % kind(p))
    paths = sorted(paths, key=jv.SortKey)
    if len(paths[0]) == 0:
        return None
    return _delpaths_sorted(t, paths, 0)


def _delpaths_sorted(obj, paths, start):
    delkeys = []
    i = 0
    n = len(paths)
    while i < n:
        j = i
        key = paths[i][start]
        delkey = len(paths[i]) == start + 1
        while j < n and jv.equal(key, paths[j][start]):
            j += 1
        if delkey:
            delkeys.append(key)
        else:
            sub = index_value(obj, key)
            if sub is not None:
                newsub = _delpaths_sorted(sub, paths[i:j], start + 1)
                obj = set_value(obj, key, newsub)
        i = j
    return _dels(obj, delkeys)


def _dels(t, keys):
    if t is None or not keys:
        return t
    tt = type(t)
    if tt is list:
        n = len(t)
        dels = set()
        for k in keys:
            tk = type(k)
            if tk is float or tk is Lit:
                idx = int(k)
                if idx < 0:
                    idx += n
                dels.add(idx)
            elif tk is dict:
                s, e = parse_slice(t, k)
                dels.update(range(s, e))
            else:
                raise JQError("Cannot delete %s element of array" % kind(k))
        return [x for i, x in enumerate(t) if i not in dels]
    if tt is dict:
        d = dict(t)
        for k in keys:
            if type(k) is not str:
                raise JQError("Cannot delete %s field of object" % kind(k))
            d.pop(k, None)
        return d
    raise JQError("Cannot delete fields from %s" % kind(t))


# ====================================================================== operators


def op_add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    ta, tb = type(a), type(b)
    if (ta is float or ta is Lit) and (tb is float or tb is Lit):
        return float(a) + float(b)
    if ta is str and tb is str:
        return a + b
    if ta is list and tb is list:
        return a + b
    if ta is dict and tb is dict:
        d = dict(a)
        d.update(b)
        return d
    raise jv.type_error2(a, b, "cannot be added")


def op_sub(a, b):
    ta, tb = type(a), type(b)
    if (ta is float or ta is Lit) and (tb is float or tb is Lit):
        return float(a) - float(b)
    if ta is list and tb is list:
        return [x for x in a if not any(jv.equal(x, y) for y in b)]
    raise jv.type_error2(a, b, "cannot be subtracted")


def deep_merge(a, b):
    d = dict(a)
    for k, v in b.items():
        if type(v) is dict and type(d.get(k)) is dict:
            d[k] = deep_merge(d[k], v)
        else:
            d[k] = v
    return d


def op_mul(a, b):
    ta, tb = type(a), type(b)
    an = ta is float or ta is Lit
    bn = tb is float or tb is Lit
    if an and bn:
        return float(a) * float(b)
    if (ta is str and bn) or (an and tb is str):
        s, n = (a, b) if ta is str else (b, a)
        d = float(n)
        if d != d or d < 0:
            return None
        count = int(d) if d < 2147483647 else 2147483647
        return s * count
    if ta is dict and tb is dict:
        return deep_merge(a, b)
    raise jv.type_error2(a, b, "cannot be multiplied")


def op_div(a, b):
    ta, tb = type(a), type(b)
    if (ta is float or ta is Lit) and (tb is float or tb is Lit):
        if float(b) == 0.0:
            raise jv.type_error2(a, b, "cannot be divided because the divisor is zero")
        return float(a) / float(b)
    if ta is str and tb is str:
        return string_split(a, b)
    raise jv.type_error2(a, b, "cannot be divided")


_IMAX = 9223372036854775807
_IMIN = -9223372036854775808


def _dtoi(x):
    if x < _IMIN:
        return _IMIN
    if x > _IMAX:
        return _IMAX
    return int(x)


def op_mod(a, b):
    ta, tb = type(a), type(b)
    if (ta is float or ta is Lit) and (tb is float or tb is Lit):
        na, nb = float(a), float(b)
        if na != na or nb != nb:
            return float("nan")
        bi = _dtoi(nb)
        if bi == 0:
            raise jv.type_error2(a, b, "cannot be divided (remainder) because the divisor is zero")
        if bi == -1:
            return 0.0
        ai = _dtoi(na)
        r = abs(ai) % abs(bi)
        if ai < 0:
            r = -r
        return float(r)
    raise jv.type_error2(a, b, "cannot be divided (remainder)")


def string_split(s, sep):
    if s == "":
        return []
    if sep == "":
        return list(s)
    return s.split(sep)


def _cmp_op(fn):
    return lambda a, b: fn(compare(a, b))


BINOPS = {
    "+": op_add,
    "-": op_sub,
    "*": op_mul,
    "/": op_div,
    "%": op_mod,
    "==": lambda a, b: jv.equal(a, b),
    "!=": lambda a, b: not jv.equal(a, b),
    "<": _cmp_op(lambda c: c < 0),
    "<=": _cmp_op(lambda c: c <= 0),
    ">": _cmp_op(lambda c: c > 0),
    ">=": _cmp_op(lambda c: c >= 0),
}


# ====================================================================== parser


class ParserState:
    def __init__(self, toks, text):
        self.toks = toks
        self.i = 0
        self.text = text

    @property
    def tok(self):
        return self.toks[self.i]

    def peek(self, k=1):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else self.toks[-1]

    def advance(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def is_op(self, val):
        t = self.toks[self.i]
        return t.type == "op" and t.val == val

    def is_kw(self, val):
        t = self.toks[self.i]
        return t.type == "kw" and t.val == val

    def expect_op(self, val):
        if not self.is_op(val):
            self.error()
        return self.advance()

    def expect_kw(self, val):
        if not self.is_kw(val):
            self.error()
        return self.advance()

    def error(self):
        t = self.tok
        if t.type == "eof":
            raise CompileError("syntax error, unexpected end of file")
        raise CompileError("syntax error, unexpected %s" % (t.val,))


ASSIGN_OPS = {"=", "|=", "+=", "-=", "*=", "/=", "%=", "//="}
CMP_OPS = {"==", "!=", "<", "<=", ">", ">="}


def parse_program(text):
    toks, _ = _lex(text)
    st = ParserState(toks, text)
    if st.tok.type == "eof":
        return Identity()
    node = parse_pipe(st)
    if st.tok.type != "eof":
        st.error()
    return node


def parse_pipe(st):
    if st.is_kw("def"):
        name, params, body = parse_funcdef(st)
        rest = parse_pipe(st)
        return FuncDef(name, params, body, rest)
    lhs = parse_comma(st)
    if st.is_op("|"):
        st.advance()
        rhs = parse_pipe(st)
        return Pipe(lhs, rhs)
    return lhs


def parse_funcdef(st):
    st.expect_kw("def")
    t = st.advance()
    if t.type not in ("ident", "kw"):
        st.i -= 1
        st.error()
    name = t.val
    params = []
    if st.is_op("("):
        st.advance()
        while True:
            p = st.advance()
            if p.type == "var":
                params.append((True, p.val[1:]))
            elif p.type in ("ident", "kw"):
                params.append((False, p.val))
            else:
                st.i -= 1
                st.error()
            if st.is_op(";"):
                st.advance()
                continue
            st.expect_op(")")
            break
    st.expect_op(":")
    body = parse_pipe(st)
    st.expect_op(";")
    return name, params, body


def parse_comma(st):
    lhs = parse_alt(st)
    while st.is_op(","):
        st.advance()
        rhs = parse_alt(st)
        lhs = Comma(lhs, rhs)
    return lhs


def parse_alt(st):
    lhs = parse_assign(st)
    if st.is_op("//"):
        st.advance()
        rhs = parse_alt(st)
        return Alternative(lhs, rhs)
    return lhs


def parse_assign(st):
    lhs = parse_or(st)
    t = st.tok
    if t.type == "op" and t.val in ASSIGN_OPS:
        st.advance()
        rhs = parse_or(st)
        t2 = st.tok
        if t2.type == "op" and t2.val in ASSIGN_OPS:
            st.error()
        return Assign(t.val, lhs, rhs)
    return lhs


def parse_or(st):
    lhs = parse_and(st)
    while st.is_kw("or"):
        st.advance()
        lhs = Or(lhs, parse_and(st))
    return lhs


def parse_and(st):
    lhs = parse_cmp(st)
    while st.is_kw("and"):
        st.advance()
        lhs = And(lhs, parse_cmp(st))
    return lhs


def parse_cmp(st):
    lhs = parse_additive(st)
    t = st.tok
    if t.type == "op" and t.val in CMP_OPS:
        st.advance()
        rhs = parse_additive(st)
        t2 = st.tok
        if t2.type == "op" and t2.val in CMP_OPS:
            st.error()
        return BinOp(t.val, lhs, rhs)
    return lhs


def parse_additive(st):
    lhs = parse_mult(st)
    while True:
        t = st.tok
        if t.type == "op" and t.val in ("+", "-"):
            st.advance()
            lhs = BinOp(t.val, lhs, parse_mult(st))
        else:
            return lhs


def parse_mult(st):
    lhs = parse_unary(st)
    while True:
        t = st.tok
        if t.type == "op" and t.val in ("*", "/", "%"):
            st.advance()
            lhs = BinOp(t.val, lhs, parse_unary(st))
        else:
            return lhs


def parse_unary(st):
    if st.is_op("-"):
        st.advance()
        return Neg(parse_mult(st))
    if st.is_kw("def"):
        name, params, body = parse_funcdef(st)
        rest = parse_pipe(st)
        return FuncDef(name, params, body, rest)
    if st.is_kw("label"):
        st.advance()
        v = st.advance()
        if v.type != "var":
            st.i -= 1
            st.error()
        st.expect_op("|")
        body = parse_pipe(st)
        return Label(v.val[1:], body)
    return parse_postfix(st)


_INDEXISH = (Index, Slice, Iterate)


def parse_postfix(st, allow_as=True):
    t = parse_primary(st)
    fresh_index = isinstance(t, _INDEXISH) and getattr(t, "_from_postfix", False)
    while True:
        tok = st.tok
        if tok.type == "field":
            st.advance()
            t = Index(t, Literal(tok.val[1:]))
            fresh_index = True
            continue
        if tok.type == "op" and tok.val == "." and st.peek().type == "str":
            st.advance()
            s = parse_string_tok(st, st.advance(), None)
            t = Index(t, s)
            fresh_index = True
            continue
        if tok.type == "op" and tok.val == "." and st.peek().type == "op" and st.peek().val == "[":
            st.advance()
            t = parse_bracket(st, t)
            fresh_index = True
            continue
        if tok.type == "op" and tok.val == "[":
            t = parse_bracket(st, t)
            fresh_index = True
            continue
        if tok.type == "op" and tok.val == "?":
            st.advance()
            if fresh_index and isinstance(t, _INDEXISH) and not t.opt:
                t.opt = True
            else:
                t = Try(t, None)
            fresh_index = False
            continue
        if allow_as and tok.type == "kw" and tok.val == "as":
            st.advance()
            patterns = parse_patterns(st)
            st.expect_op("|")
            body = parse_pipe(st)
            return Bind(t, patterns, body)
        return t


def parse_bracket(st, target):
    st.expect_op("[")
    if st.is_op("]"):
        st.advance()
        return Iterate(target)
    if st.is_op(":"):
        st.advance()
        end = parse_pipe(st)
        st.expect_op("]")
        return Slice(target, None, end)
    e = parse_pipe(st)
    if st.is_op(":"):
        st.advance()
        if st.is_op("]"):
            st.advance()
            return Slice(target, e, None)
        end = parse_pipe(st)
        st.expect_op("]")
        return Slice(target, e, end)
    st.expect_op("]")
    return Index(target, e)


def parse_string_tok(st, tok, fmt):
    parts = []
    for p in tok.val:
        if type(p) is str:
            parts.append(p)
        else:
            sub = ParserState(p, st.text)
            if sub.tok.type == "eof":
                sub.error()
            node = parse_pipe(sub)
            if sub.tok.type != "eof":
                sub.error()
            parts.append(node)
    if all(type(p) is str for p in parts):
        return Literal("".join(parts))
    return StringNode(parts, fmt)


_CONSTS = {"true": True, "false": False, "null": None}


def _line_of(st, pos):
    return st.text.count("\n", 0, pos) + 1


def parse_primary(st):
    tok = st.tok
    tt = tok.type
    if tt == "num":
        st.advance()
        return Literal(tok.val)
    if tt == "str":
        st.advance()
        return parse_string_tok(st, tok, None)
    if tt == "fmt":
        st.advance()
        if st.tok.type == "str":
            return parse_string_tok(st, st.advance(), tok.val[1:])
        return FormatNode(tok.val[1:])
    if tt == "field":
        st.advance()
        return Index(Identity(), Literal(tok.val[1:]))
    if tt == "var":
        st.advance()
        return Var(tok.val[1:])
    if tt == "loc":
        st.advance()
        return Loc(_line_of(st, tok.pos))
    if tt == "ident":
        st.advance()
        name = tok.val
        args = []
        if name in _CONSTS and not st.is_op("("):
            return Literal(_CONSTS[name])
        if st.is_op("("):
            st.advance()
            while True:
                args.append(parse_pipe(st))
                if st.is_op(";"):
                    st.advance()
                    continue
                st.expect_op(")")
                break
        return Call(name, args)
    if tt == "op":
        v = tok.val
        if v == ".":
            st.advance()
            if st.tok.type == "str":
                s = parse_string_tok(st, st.advance(), None)
                return Index(Identity(), s)
            return Identity()
        if v == "..":
            st.advance()
            return Call("recurse", [])
        if v == "(":
            st.advance()
            e = parse_pipe(st)
            st.expect_op(")")
            return e
        if v == "[":
            st.advance()
            if st.is_op("]"):
                st.advance()
                return ArrayCons(None)
            e = parse_pipe(st)
            st.expect_op("]")
            return ArrayCons(e)
        if v == "{":
            return parse_object(st)
        st.error()
    if tt == "kw":
        v = tok.val
        if v == "reduce":
            st.advance()
            src = parse_postfix(st, allow_as=False)
            st.expect_kw("as")
            pats = parse_patterns(st)
            st.expect_op("(")
            init = parse_pipe(st)
            st.expect_op(";")
            upd = parse_pipe(st)
            st.expect_op(")")
            return Reduce(src, pats, init, upd)
        if v == "foreach":
            st.advance()
            src = parse_postfix(st, allow_as=False)
            st.expect_kw("as")
            pats = parse_patterns(st)
            st.expect_op("(")
            init = parse_pipe(st)
            st.expect_op(";")
            upd = parse_pipe(st)
            ext = None
            if st.is_op(";"):
                st.advance()
                ext = parse_pipe(st)
            st.expect_op(")")
            return Foreach(src, pats, init, upd, ext)
        if v == "if":
            st.advance()
            return parse_if_rest(st)
        if v == "try":
            st.advance()
            body = parse_unary_noas(st)
            handler = None
            if st.is_kw("catch"):
                st.advance()
                handler = parse_unary_noas(st)
            return Try(body, handler)
        if v == "break":
            st.advance()
            t2 = st.advance()
            if t2.type != "var":
                st.i -= 1
                st.error()
            return BreakNode(t2.val[1:])
        if v in ("def", "label"):
            return parse_unary(st)
    st.error()


def parse_unary_noas(st):
    if st.is_op("-"):
        st.advance()
        return Neg(parse_unary_noas(st))
    return parse_postfix(st)


def parse_if_rest(st):
    cond = parse_pipe(st)
    st.expect_kw("then")
    then = parse_pipe(st)
    if st.is_kw("elif"):
        st.advance()
        else_ = parse_if_rest(st)
        return If(cond, then, else_)
    if st.is_kw("else"):
        st.advance()
        else_ = parse_pipe(st)
        st.expect_kw("end")
        return If(cond, then, else_)
    st.expect_kw("end")
    return If(cond, then, None)


def parse_object(st):
    st.expect_op("{")
    entries = []
    while not st.is_op("}"):
        tok = st.tok
        if tok.type == "var":
            st.advance()
            name = tok.val[1:]
            if st.is_op(":"):
                st.advance()
                entries.append((Var(name), parse_objval(st)))
            else:
                entries.append((Literal(name), Var(name)))
        elif tok.type == "loc":
            st.advance()
            entries.append((Literal("__loc__"), Loc(_line_of(st, tok.pos))))
        elif tok.type in ("ident", "kw"):
            st.advance()
            name = tok.val
            if st.is_op(":"):
                st.advance()
                entries.append((Literal(name), parse_objval(st)))
            else:
                entries.append((Literal(name), Index(Identity(), Literal(name))))
        elif tok.type == "str" or (tok.type == "fmt" and st.peek().type == "str"):
            st.advance()
            if tok.type == "fmt":
                key = parse_string_tok(st, st.advance(), tok.val[1:])
            else:
                key = parse_string_tok(st, tok, None)
            if st.is_op(":"):
                st.advance()
                entries.append((key, parse_objval(st)))
            else:
                entries.append((key, Index(Identity(), key)))
        elif tok.type == "op" and tok.val == "(":
            st.advance()
            key = parse_pipe(st)
            st.expect_op(")")
            st.expect_op(":")
            entries.append((key, parse_objval(st)))
        else:
            st.error()
        if st.is_op(","):
            st.advance()
            continue
        if not st.is_op("}"):
            st.error()
    st.expect_op("}")
    for key, _ in entries:
        if isinstance(key, Literal) and type(key.value) is not str:
            raise CompileError("Cannot use %s (%s) as object key" % (kind(key.value), trunc_dump(key.value)))
    return ObjectCons(entries)


def parse_objval(st):
    if st.is_op("-"):
        st.advance()
        lhs = Neg(parse_objval(st))
    else:
        lhs = parse_postfix(st, allow_as=False)
    while st.is_op("|"):
        st.advance()
        if st.is_op("-"):
            st.advance()
            rhs = Neg(parse_objval(st))
        else:
            rhs = parse_postfix(st, allow_as=False)
        lhs = Pipe(lhs, rhs)
    return lhs


def parse_patterns(st):
    pats = [parse_pattern(st)]
    while st.is_op("?//"):
        st.advance()
        pats.append(parse_pattern(st))
    return pats


def parse_pattern(st):
    tok = st.tok
    if tok.type == "var":
        st.advance()
        return PVar(tok.val[1:])
    if tok.type == "op" and tok.val == "[":
        st.advance()
        elems = [parse_pattern(st)]
        while st.is_op(","):
            st.advance()
            elems.append(parse_pattern(st))
        st.expect_op("]")
        return PArray(elems)
    if tok.type == "op" and tok.val == "{":
        st.advance()
        entries = []
        while True:
            t = st.tok
            if t.type == "var":
                st.advance()
                name = t.val[1:]
                if st.is_op(":"):
                    st.advance()
                    entries.append((name, None, parse_pattern(st)))
                else:
                    entries.append((name, None, None))
            elif t.type in ("ident", "kw"):
                st.advance()
                st.expect_op(":")
                entries.append((None, Literal(t.val), parse_pattern(st)))
            elif t.type == "str":
                st.advance()
                key = parse_string_tok(st, t, None)
                st.expect_op(":")
                entries.append((None, key, parse_pattern(st)))
            elif t.type == "op" and t.val == "(":
                st.advance()
                key = parse_pipe(st)
                st.expect_op(")")
                st.expect_op(":")
                entries.append((None, key, parse_pattern(st)))
            else:
                st.error()
            if st.is_op(","):
                st.advance()
                continue
            st.expect_op("}")
            break
        return PObject(entries)
    st.error()


# ====================================================================== resolver


class Scope:
    __slots__ = ("parent", "key")

    def __init__(self, parent, key):
        self.parent = parent
        self.key = key

    def has(self, key):
        s = self
        while s is not None:
            if s.key == key:
                return True
            s = s.parent
        return False


def _has(scope, key):
    return scope is not None and scope.has(key)


def resolve(node, scope, globals_):
    """Report undefined functions, variables and labels like jq's compiler."""
    errors = []

    def has_var(sc, name):
        return _has(sc, ("v", name)) or name in globals_.vars

    def has_func(sc, key):
        return _has(sc, key) or key in globals_.funcs

    def pat(p, sc):
        if isinstance(p, PArray):
            for e in p.elems:
                pat(e, sc)
        elif isinstance(p, PObject):
            for keyvar, keynode, sub in p.entries:
                if keynode is not None:
                    walk(keynode, sc)
                if sub is not None:
                    pat(sub, sc)

    def bind_scope(pats, sc):
        names = []
        for p in pats:
            pattern_vars(p, names)
        for n in names:
            sc = Scope(sc, ("v", n))
        return sc

    def walk(n, sc):
        c = n.__class__
        if c in (Identity, Literal, Loc, FormatNode):
            return
        if c is Var:
            if not has_var(sc, n.name):
                errors.append("$%s is not defined" % n.name)
            return
        if c in (Pipe, Comma, Alternative, And, Or, BinOp):
            walk(n.a, sc)
            walk(n.b, sc)
            return
        if c is Assign:
            walk(n.lhs, sc)
            walk(n.rhs, sc)
            return
        if c is Neg:
            walk(n.a, sc)
            return
        if c is Index:
            walk(n.target, sc)
            walk(n.key, sc)
            return
        if c is Slice:
            walk(n.target, sc)
            if n.start is not None:
                walk(n.start, sc)
            if n.end is not None:
                walk(n.end, sc)
            return
        if c is Iterate:
            walk(n.target, sc)
            return
        if c is Try:
            walk(n.body, sc)
            if n.handler is not None:
                walk(n.handler, sc)
            return
        if c is If:
            walk(n.cond, sc)
            walk(n.then, sc)
            if n.else_ is not None:
                walk(n.else_, sc)
            return
        if c is ArrayCons:
            if n.body is not None:
                walk(n.body, sc)
            return
        if c is ObjectCons:
            for k, v in n.entries:
                walk(k, sc)
                walk(v, sc)
            return
        if c is StringNode:
            for p in n.parts:
                if type(p) is not str:
                    walk(p, sc)
            return
        if c is Label:
            walk(n.body, Scope(sc, ("l", n.name)))
            return
        if c is BreakNode:
            if not _has(sc, ("l", n.name)):
                errors.append("$*label-%s is not defined" % n.name)
            return
        if c is Bind:
            walk(n.source, sc)
            for p in n.patterns:
                pat(p, sc)
            walk(n.body, bind_scope(n.patterns, sc))
            return
        if c is Reduce:
            walk(n.source, sc)
            walk(n.init, sc)
            for p in n.patterns:
                pat(p, sc)
            walk(n.update, bind_scope(n.patterns, sc))
            return
        if c is Foreach:
            walk(n.source, sc)
            walk(n.init, sc)
            for p in n.patterns:
                pat(p, sc)
            inner = bind_scope(n.patterns, sc)
            walk(n.update, inner)
            if n.extract is not None:
                walk(n.extract, inner)
            return
        if c is FuncDef:
            sc2 = Scope(sc, ("f", n.name, len(n.params)))
            body_sc = sc2
            for isvar, pname in n.params:
                body_sc = Scope(body_sc, ("f", pname, 0))
                if isvar:
                    body_sc = Scope(body_sc, ("v", pname))
            walk(n.body, body_sc)
            walk(n.rest, sc2)
            return
        if c is Call:
            if not has_func(sc, n.key):
                errors.append("%s/%d is not defined" % (n.name, len(n.args)))
            for a in n.args:
                walk(a, sc)
            return
        raise AssertionError(c)

    walk(node, scope)
    if errors:
        raise CompileError(errors[0])


# ====================================================================== globals


class Globals:
    def __init__(self):
        self.funcs = {}
        self.vars = {}


GLOBALS = Globals()
FORMAT = None  # set by builtins
