"""JSON values as jq 1.7.1 sees them.

Values map onto plain Python objects: None, True/False, str, list, dict
(insertion ordered, like jq objects) and float. Every number is a float.
Numbers that came from a literal (program text, JSON input, tonumber,
fromjson) are Lit instances: they keep the canonical decimal text so that
printing them unchanged reproduces jq's literal preservation (1.000 stays
1.000, 1e1000 prints as 1E+1000). Any arithmetic produces a plain float.
"""
import math
import re
import struct
from decimal import Decimal


class JQError(Exception):
    """A jq runtime error carrying a jq value (usually the message string)."""

    def __init__(self, value):
        Exception.__init__(self)
        self.value = value


class Lit(float):
    __slots__ = ("dec", "text")

    def __new__(cls, dec):
        try:
            f = float(dec)
        except OverflowError:
            f = math.inf if dec > 0 else -math.inf
        except ValueError:
            f = math.nan
        obj = float.__new__(cls, f)
        obj.dec = dec
        obj.text = str(dec)
        return obj


_NUM_RE = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_SPECIAL_RE = re.compile(r"[+-]?(?:inf|infinity|nan|snan)\Z", re.IGNORECASE)


def lit_from_text(text):
    """decNumber-style parse of a numeric token; None if it is not a number."""
    if not (_NUM_RE.match(text) or _SPECIAL_RE.match(text)):
        return None
    return Lit(Decimal(text))


def kind(v):
    if v is None:
        return "null"
    if v is True or v is False:
        return "boolean"
    t = type(v)
    if t is float or t is Lit:
        return "number"
    if t is str:
        return "string"
    if t is list:
        return "array"
    return "object"


def isnum(v):
    t = type(v)
    return t is float or t is Lit


def truthy(v):
    return not (v is None or v is False)


# ---------------------------------------------------------------- ordering

_KIND_ORDER = {"null": 0, "false": 1, "true": 2, "number": 3, "string": 4, "array": 5, "object": 6}


def _korder(v):
    if v is None:
        return 0
    if v is False:
        return 1
    if v is True:
        return 2
    t = type(v)
    if t is float or t is Lit:
        return 3
    if t is str:
        return 4
    if t is list:
        return 5
    return 6


def _numcmp(a, b):
    if a != a:
        return -1 if b == b else -1
    if b != b:
        return 1
    if type(a) is Lit and type(b) is Lit:
        if a.dec < b.dec:
            return -1
        if a.dec == b.dec:
            return 0
        return 1
    if a < b:
        return -1
    if a == b:
        return 0
    return 1


def compare(a, b):
    ka, kb = _korder(a), _korder(b)
    if ka != kb:
        return -1 if ka < kb else 1
    if ka == 3:
        return _numcmp(a, b)
    if ka == 4:
        if a == b:
            return 0
        return -1 if a < b else 1
    if ka == 5:
        for x, y in zip(a, b):
            r = compare(x, y)
            if r:
                return r
        if len(a) == len(b):
            return 0
        return -1 if len(a) < len(b) else 1
    if ka == 6:
        ka_ = sorted(a)
        kb_ = sorted(b)
        if ka_ != kb_:
            return -1 if ka_ < kb_ else 1
        for k in ka_:
            r = compare(a[k], b[k])
            if r:
                return r
        return 0
    return 0


def equal(a, b):
    if a is b:
        return True
    return compare(a, b) == 0


def identical(a, b):
    """jq's jv_identical: pointer identity for heap values, bits for numbers."""
    if a is b:
        return True
    ta, tb = type(a), type(b)
    if ta is float and tb is float:
        return struct.pack("<d", a) == struct.pack("<d", b)
    if a is None or a is True or a is False:
        return a is b
    return False


class SortKey:
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v

    def __lt__(self, other):
        return compare(self.v, other.v) < 0


# ---------------------------------------------------------------- printing


def fmt_double(x):
    if x != x:
        return "null"
    if x > 1.7976931348623157e308:
        x = 1.7976931348623157e308
    elif x < -1.7976931348623157e308:
        x = -1.7976931348623157e308
    if x == 0:
        return "-0" if math.copysign(1.0, x) < 0 else "0"
    neg = x < 0
    s = repr(abs(float(x)))
    if "e" in s:
        mant, exp = s.split("e")
        exp = int(exp)
    else:
        mant, exp = s, 0
    if "." in mant:
        ip, fp = mant.split(".")
    else:
        ip, fp = mant, ""
    allds = ip + fp
    lead = len(allds) - len(allds.lstrip("0"))
    digits = allds.lstrip("0").rstrip("0") or "0"
    decpt = len(ip) + exp - lead
    nd = len(digits)
    out = []
    if decpt <= -4 or decpt > nd + 15:
        out.append(digits[0])
        if nd > 1:
            out.append(".")
            out.append(digits[1:])
        out.append("e")
        e = decpt - 1
        if e < 0:
            out.append("-")
            e = -e
        else:
            out.append("+")
        out.append("%02d" % e)
    elif decpt <= 0:
        out.append("0.")
        out.append("0" * (-decpt))
        out.append(digits)
    else:
        if nd <= decpt:
            out.append(digits)
            out.append("0" * (decpt - nd))
        else:
            out.append(digits[:decpt])
            out.append(".")
            out.append(digits[decpt:])
    return ("-" if neg else "") + "".join(out)


def fmt_number(x):
    if type(x) is Lit:
        if not x.dec.is_finite():
            return fmt_double(float(x))
        return x.text
    return fmt_double(x)


_ESC = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r", "\b": "\\b", "\f": "\\f"}


def dump_string(s, ascii_only=False):
    out = ['"']
    for ch in s:
        c = ord(ch)
        if 0x20 <= c <= 0x7E:
            if ch == '"' or ch == "\\":
                out.append("\\")
            out.append(ch)
        elif c < 0x20 or c == 0x7F:
            e = _ESC.get(ch)
            out.append(e if e else "\\u%04x" % c)
        elif ascii_only:
            if c <= 0xFFFF:
                out.append("\\u%04x" % c)
            else:
                c -= 0x10000
                out.append("\\u%04x\\u%04x" % (0xD800 | ((c & 0xFFC00) >> 10), 0xDC00 | (c & 0x3FF)))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def dumps(v, indent=0, sort_keys=False, ascii_only=False, use_tab=False):
    """Serialize like jv_dump_term. indent 0 means compact."""
    parts = []
    unit = "\t" if use_tab else " " * indent
    pretty = use_tab or indent > 0

    def rec(x, level):
        if x is None:
            parts.append("null")
        elif x is True:
            parts.append("true")
        elif x is False:
            parts.append("false")
        else:
            t = type(x)
            if t is float or t is Lit:
                parts.append(fmt_number(x))
            elif t is str:
                parts.append(dump_string(x, ascii_only))
            elif t is list:
                if not x:
                    parts.append("[]")
                    return
                parts.append("[")
                for i, e in enumerate(x):
                    if i:
                        parts.append(",")
                    if pretty:
                        parts.append("\n")
                        parts.append(unit * (level + 1))
                    rec(e, level + 1)
                if pretty:
                    parts.append("\n")
                    parts.append(unit * level)
                parts.append("]")
            else:
                if not x:
                    parts.append("{}")
                    return
                parts.append("{")
                keys = sorted(x) if sort_keys else list(x)
                for i, k in enumerate(keys):
                    if i:
                        parts.append(",")
                    if pretty:
                        parts.append("\n")
                        parts.append(unit * (level + 1))
                    parts.append(dump_string(k, ascii_only))
                    parts.append(": " if pretty else ":")
                    rec(x[k], level + 1)
                if pretty:
                    parts.append("\n")
                    parts.append(unit * level)
                parts.append("}")

    rec(v, 0)
    return "".join(parts)


def tojson(v):
    return dumps(v)


def trunc_dump(v, bufsize=15):
    """jv_dump_string_trunc: compact dump cut to bufsize-1 bytes with '...'."""
    s = dumps(v).encode("utf-8")
    if len(s) > bufsize - 1:
        s = s[: bufsize - 4] + b"..."
    return decode_utf8(s)


def type_error(v, msg):
    return JQError("%s (%s) %s" % (kind(v), trunc_dump(v), msg))


def type_error2(a, b, msg):
    return JQError("%s (%s) and %s (%s) %s" % (kind(a), trunc_dump(a), kind(b), trunc_dump(b), msg))


# ---------------------------------------------------------------- utf-8

_FIRST_MIN = {1: 0, 2: 0x80, 3: 0x800, 4: 0x10000}


def _coding_length(b):
    if b < 0x80:
        return 1
    if b < 0xC0:
        return -1  # continuation byte
    if b < 0xE0:
        return 2
    if b < 0xF0:
        return 3
    if b < 0xF8:
        return 4
    return 0


def decode_utf8(data):
    """Decode bytes the way jvp_utf8_next does, replacing bad input with U+FFFD."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    out = []
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        length = _coding_length(b)
        if length == 1:
            out.append(chr(b))
            i += 1
            continue
        if length <= 0:
            out.append("\ufffd")
            i += 1
            continue
        if i + length > n:
            out.append("\ufffd")
            i = n
            continue
        cp = b & {2: 0x1F, 3: 0x0F, 4: 0x07}[length]
        bad = False
        for k in range(1, length):
            c = data[i + k]
            if _coding_length(c) != -1:
                bad = True
                i += k
                break
            cp = (cp << 6) | (c & 0x3F)
        if bad:
            out.append("\ufffd")
            continue
        i += length
        if cp < _FIRST_MIN[length] or 0xD800 <= cp <= 0xDFFF or cp > 0x10FFFF:
            out.append("\ufffd")
        else:
            out.append(chr(cp))
    return "".join(out)


# ---------------------------------------------------------------- parsing


class ParseError(Exception):
    pass


_WS = " \t\r\n"
_STRUCT = "[{:,]}"


class Parser:
    """Port of the jv_parse.c state machine (non-streaming mode).

    Feed the whole text, then call next_value() repeatedly. It returns a value,
    raises ParseError with jq's message, or returns _END when input is done.
    """

    END = object()

    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.line = 1
        self.column = 0
        self.stack = []
        self.next = _INVALID
        self.last_seen = "none"
        self.token = []
        self.in_string = False
        self.escape = False
        self.done = False
        self.allow_controls = False

    def _value(self, v):
        if self.next is not _INVALID:
            raise ParseError("Expected separator between values")
        self.next = v
        self.last_seen = "value"

    def _check_literal(self):
        if not self.token:
            return
        tok = "".join(self.token)
        first = tok[0]
        pattern = None
        if first == "t":
            pattern, v = "true", True
        elif first == "f":
            pattern, v = "false", False
        elif first == "'":
            raise ParseError("Invalid string literal; expected \", but got '")
        elif first == "n":
            if len(tok) == 3:
                pattern, v = "nan", math.nan
            else:
                pattern, v = "null", None
        if pattern is not None:
            if tok != pattern:
                raise ParseError("Invalid literal")
            self._value(v)
        else:
            num = lit_from_text(tok)
            if num is None:
                raise ParseError("Invalid numeric literal")
            self._value(num)
        self.token = []

    def _token(self, ch):
        st = self.stack
        if ch == "[":
            if self.next is not _INVALID:
                raise ParseError("Expected separator between values")
            st.append([])
            self.last_seen = "open_array"
        elif ch == "{":
            if self.next is not _INVALID:
                raise ParseError("Expected separator between values")
            st.append({})
            self.last_seen = "open_object"
        elif ch == ":":
            if not st or type(st[-1]) is not dict:
                raise ParseError("':' not as part of an object")
            if self.next is _INVALID or self.last_seen == "none":
                raise ParseError("Expected string key before ':'")
            if type(self.next) is not str:
                raise ParseError("Object keys must be strings")
            if self.last_seen != "value":
                raise ParseError("':' should follow a key")
            self.last_seen = "colon"
            st.append(_Key(self.next))
            self.next = _INVALID
        elif ch == ",":
            if self.next is _INVALID:
                raise ParseError("Expected value before ','")
            if not st:
                raise ParseError("',' not as part of an object or array")
            top = st[-1]
            if type(top) is list:
                top.append(self.next)
                self.next = _INVALID
            elif type(top) is _Key:
                st.pop()
                st[-1][top.k] = self.next
                self.next = _INVALID
            else:
                raise ParseError("Objects must consist of key:value pairs")
            self.last_seen = "comma"
        elif ch == "]":
            if not st or type(st[-1]) is not list:
                raise ParseError("Unmatched ']'")
            if self.last_seen == "comma":
                raise ParseError("Expected another array element")
            if self.next is not _INVALID:
                st[-1].append(self.next)
                self.next = _INVALID
            elif st[-1]:
                raise ParseError("Expected another array element")
            self.next = st.pop()
            self.last_seen = "value"
        elif ch == "}":
            if not st:
                raise ParseError("Unmatched '}'")
            if self.last_seen == "comma":
                raise ParseError("Expected another key-value pair")
            if self.next is not _INVALID:
                if type(st[-1]) is not _Key:
                    raise ParseError("Objects must consist of key:value pairs")
                key = st.pop()
                st[-1][key.k] = self.next
                self.next = _INVALID
            else:
                if type(st[-1]) is not dict:
                    raise ParseError("Unmatched '}'")
                if st[-1]:
                    raise ParseError("Expected another key-value pair")
            self.next = st.pop()
            self.last_seen = "value"

    def _found_string(self):
        raw = self.token
        out = []
        i, n = 0, len(raw)
        while i < n:
            c = raw[i]
            i += 1
            if c == "\\":
                if i >= n:
                    raise ParseError("Expected escape character at end of string")
                c = raw[i]
                i += 1
                if c in '\\"/':
                    out.append(c)
                elif c == "b":
                    out.append("\b")
                elif c == "f":
                    out.append("\f")
                elif c == "t":
                    out.append("\t")
                elif c == "n":
                    out.append("\n")
                elif c == "r":
                    out.append("\r")
                elif c == "u":
                    if i + 4 > n:
                        raise ParseError("Invalid \\uXXXX escape")
                    h = "".join(raw[i:i + 4])
                    if not re.match(r"[0-9a-fA-F]{4}\Z", h):
                        raise ParseError("Invalid characters in \\uXXXX escape")
                    cp = int(h, 16)
                    i += 4
                    if 0xD800 <= cp <= 0xDBFF:
                        bad = "Invalid \\uXXXX\\uXXXX surrogate pair escape"
                        if not (i + 6 <= n and raw[i] == "\\" and raw[i + 1] == "u"):
                            raise ParseError(bad)
                        h2 = "".join(raw[i + 2:i + 6])
                        if not re.match(r"[0-9a-fA-F]{4}\Z", h2):
                            raise ParseError(bad)
                        lo = int(h2, 16)
                        if not 0xDC00 <= lo <= 0xDFFF:
                            raise ParseError(bad)
                        cp = 0x10000 + (((cp - 0xD800) << 10) | (lo - 0xDC00))
                        i += 6
                    elif 0xDC00 <= cp <= 0xDFFF:
                        cp = 0xFFFD
                    out.append(chr(cp))
                else:
                    raise ParseError("Invalid escape")
            else:
                if 0 < ord(c) < 0x1F and not self.allow_controls:
                    raise ParseError("Invalid string: control characters from U+0000 through U+001F must be escaped")
                out.append(c)
        self.token = []
        self._value("".join(out))

    def _check_done(self):
        if not self.stack and self.next is not _INVALID:
            v = self.next
            self.next = _INVALID
            return v
        return _INVALID

    def _fail(self, msg, at_eof=False):
        if at_eof:
            return ParseError("%s at EOF at line %d, column %d" % (msg, self.line, self.column))
        return ParseError("%s at line %d, column %d" % (msg, self.line, self.column))

    def next_value(self):
        text = self.text
        n = len(text)
        while self.pos < n:
            ch = text[self.pos]
            self.pos += 1
            prev_line, prev_col = self.line, self.column
            self.column += 1 if ch < "\x80" else len(ch.encode("utf-8"))
            if ch == "\n":
                self.line += 1
                self.column = 0
            try:
                if not self.in_string:
                    if ch in _WS or ch in _STRUCT or ch == '"':
                        if self.token:
                            self._check_literal()
                            v = self._check_done()
                            if v is not _INVALID:
                                self.pos -= 1
                                self.line, self.column = prev_line, prev_col
                                return v
                        if ch in _STRUCT:
                            self._token(ch)
                        elif ch == '"':
                            self.in_string = True
                            self.escape = False
                        v = self._check_done()
                        if v is not _INVALID:
                            return v
                    else:
                        self.token.append(ch)
                else:
                    if ch == '"' and not self.escape:
                        self._found_string()
                        self.in_string = False
                        v = self._check_done()
                        if v is not _INVALID:
                            return v
                    else:
                        self.token.append(ch)
                        self.escape = (ch == "\\" and not self.escape)
            except ParseError as e:
                self.done = True
                raise self._fail(e.args[0])
        if self.done:
            return Parser.END
        self.done = True
        if self.in_string:
            raise ParseError("Unfinished string at EOF at line %d, column %d" % (self.line, self.column))
        try:
            self._check_literal()
        except ParseError as e:
            raise self._fail(e.args[0], at_eof=True)
        if self.stack:
            raise ParseError("Unfinished JSON term at EOF at line %d, column %d" % (self.line, self.column))
        if self.next is not _INVALID:
            v = self.next
            self.next = _INVALID
            return v
        return Parser.END


class _Key:
    __slots__ = ("k",)

    def __init__(self, k):
        self.k = k


class _Invalid:
    pass


_INVALID = _Invalid()


def parse_one(text, allow_controls=False):
    """jv_parse_sized: exactly one JSON value or a JQError with jq's message."""
    p = Parser(text)
    p.allow_controls = allow_controls
    try:
        v = p.next_value()
        if v is Parser.END:
            raise ParseError("Expected JSON value")
        try:
            nxt = p.next_value()
        except ParseError:
            raise
        if nxt is not Parser.END:
            raise ParseError("Unexpected extra JSON values")
    except ParseError as e:
        raise JQError("%s (while parsing '%s')" % (e.args[0], text))
    return v
