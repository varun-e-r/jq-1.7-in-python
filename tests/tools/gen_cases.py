"""Generate jq test cases (program, flags, stdin). Truth comes from jq 1.7.1 later.

usage: python3 gen_cases.py SEED OUT.jsonl

The same templates run against different seeded documents for each seed, and
the random fuzzer draws different programs, so two seeds give disjoint case
sets that cover the same surface.
"""
import json
import random
import sys

# ---------------------------------------------------------------- documents

FIRST = ["ana", "bo", "chen", "dmitri", "eve", "farah", "gus", "hana", "ivo", "jun", "kai", "lena", "mo", "nia"]
LAST = ["smith", "okafor", "nguyen", "garcia", "kowalski", "ito", "silva", "haddad"]
TAGS = ["vip", "new", "eu", "us", "beta", "churn-risk", "wholesale", "gift"]
SKUS = ["A-100", "A-200", "B-7", "C-33", "D-9", "E-1"]
STATUS = ["paid", "shipped", "pending", "refunded", "cancelled"]
WORDS = ["alpha", "beta", "gamma", "delta", "Epsilon", "zeta", "Eta", "theta", "iota", "kappa"]
UNI = ["caf\u00e9", "na\u00efve", "\u00fcber", "\u65e5\u672c", "\u00c5ngstr\u00f6m", "\U0001F600 smile", "r\u00e9sum\u00e9", "\u0436\u0443\u043a"]
ODD = ["a,b", 'say "hi"', "tab\there", "line\nbreak", "back\\slash", "<b>&amp;</b>", "it's", "100%", "a b c", "", " x "]
LITNUMS = ["1.0", "1.50", "1e3", "2E-3", "-0", "0.10", "100000000000000000001", "3.14159265358979323846",
           "12345678901234567890", "1.000", "5e-324", "1e1000", "-1.5e+300", "0.000001", "7"]


def jnum(rng):
    r = rng.random()
    if r < 0.45:
        return str(rng.randint(-50, 500))
    if r < 0.7:
        return repr(round(rng.uniform(-100, 1000), rng.choice([1, 2, 3])))
    if r < 0.85:
        return rng.choice(LITNUMS)
    return str(rng.choice([0, 1, 2, 10, 100, 1000]))


def jstr(rng):
    r = rng.random()
    if r < 0.5:
        return json.dumps(rng.choice(WORDS + FIRST))
    if r < 0.7:
        return json.dumps(rng.choice(UNI), ensure_ascii=rng.random() < 0.5)
    if r < 0.9:
        return json.dumps(rng.choice(ODD))
    return json.dumps(rng.choice(SKUS))


def obj(pairs):
    return "{" + ",".join(json.dumps(k) + ":" + v for k, v in pairs) + "}"


def arr(items):
    return "[" + ",".join(items) + "]"


def doc_store(rng):
    orders = []
    for i in range(rng.randint(2, 6)):
        items = [obj([("sku", json.dumps(rng.choice(SKUS))), ("qty", str(rng.randint(1, 5))),
                      ("price", rng.choice(["9.99", "19.5", "0.5", "100", "2.25", "1.10", "15"]))])
                 for _ in range(rng.randint(0, 3))]
        cust = obj([("name", json.dumps(rng.choice(FIRST) + " " + rng.choice(LAST))),
                    ("email", json.dumps(rng.choice(FIRST) + "@example.com") if rng.random() < 0.8 else "null"),
                    ("tags", arr([json.dumps(t) for t in rng.sample(TAGS, rng.randint(0, 3))]))])
        pairs = [("id", str(1000 + i * rng.randint(1, 9))), ("customer", cust), ("items", arr(items)),
                 ("status", json.dumps(rng.choice(STATUS))),
                 ("note", "null" if rng.random() < 0.6 else jstr(rng))]
        if rng.random() < 0.3:
            pairs.append(("discount", rng.choice(["0.1", "5", "0.25", "null"])))
        rng.shuffle(pairs) if rng.random() < 0.2 else None
        orders.append(obj(pairs))
    meta = obj([("region", json.dumps(rng.choice(["eu-west", "us-east", "ap-south"]))),
                ("version", rng.choice(["2", "2.0", "3.10"])), ("generated", json.dumps("2026-0%d-1%d" % (rng.randint(1, 9), rng.randint(0, 9))))])
    return obj([("orders", arr(orders)), ("meta", meta)])


def doc_events(rng):
    evs = []
    for i in range(rng.randint(3, 8)):
        payload = rng.choice([
            obj([("path", json.dumps("/" + rng.choice(WORDS))), ("ms", str(rng.randint(1, 900)))]),
            obj([("amount", jnum(rng)), ("currency", json.dumps(rng.choice(["EUR", "USD"])))]),
            obj([]), "null", arr([jnum(rng) for _ in range(rng.randint(0, 3))]),
        ])
        evs.append(obj([("ts", str(1700000000 + rng.randint(0, 99999))), ("type", json.dumps(rng.choice(["click", "view", "buy", "error"]))),
                        ("user", json.dumps(rng.choice(FIRST)) if rng.random() < 0.85 else "null"),
                        ("payload", payload), ("tags", arr([json.dumps(t) for t in rng.sample(TAGS, rng.randint(0, 2))]))]))
    return arr(evs)


def doc_config(rng):
    def val(depth):
        r = rng.random()
        if depth > 2 or r < 0.35:
            return rng.choice([jnum(rng), jstr(rng), "true", "false", "null"])
        if r < 0.65:
            return obj([(rng.choice(WORDS + ["a b", "x-y", "\u00e9t\u00e9", "$ref", "", "0"]), val(depth + 1)) for _ in range(rng.randint(0, 4))])
        return arr([val(depth + 1) for _ in range(rng.randint(0, 4))])
    return obj([("name", jstr(rng)), ("enabled", rng.choice(["true", "false"])), ("limits", obj([("cpu", jnum(rng)), ("mem", jnum(rng))])),
                 ("servers", arr([obj([("host", json.dumps(rng.choice(WORDS) + ".internal")), ("port", str(rng.choice([80, 443, 8080, 5432]))),
                                       ("weight", jnum(rng))]) for _ in range(rng.randint(1, 4))])),
                 ("extra", val(0))])


def doc_matrix(rng):
    rows = rng.randint(1, 4)
    cols = rng.randint(1, 4)
    jag = rng.random() < 0.3
    return arr([arr([jnum(rng) for _ in range(cols if not jag else rng.randint(0, cols))]) for _ in range(rows)])


def doc_text(rng):
    return arr([jstr(rng) for _ in range(rng.randint(2, 7))])


def doc_nums(rng):
    return arr([jnum(rng) for _ in range(rng.randint(3, 9))])


def doc_mixed(rng):
    pool = ["null", "true", "false", "0", "-1", "2.5", '""', '"0"', '"abc"', "[]", "[1,[2]]", "{}", '{"a":1}', '{"b":[null]}', "1.0", '"\u00e9"']
    return arr([rng.choice(pool) for _ in range(rng.randint(3, 8))])


def doc_tree(rng):
    def node(d):
        kids = [] if d >= 3 else [node(d + 1) for _ in range(rng.randint(0, 3 - d))]
        pairs = [("name", json.dumps(rng.choice(WORDS))), ("size", str(rng.randint(0, 99)))]
        if kids or rng.random() < 0.5:
            pairs.append(("children", arr(kids)))
        return obj(pairs)
    return node(0)


def doc_scalar(rng):
    return rng.choice(["null", "true", "false", "0", "-3", "12.5", '"text"', '"\u00e9t\u00e9"', '""', "[]", "{}", "1.0", "100000000000000000001"])


DOCS = {"store": doc_store, "events": doc_events, "config": doc_config, "matrix": doc_matrix, "text": doc_text,
        "nums": doc_nums, "mixed": doc_mixed, "tree": doc_tree, "scalar": doc_scalar}

# ---------------------------------------------------------------- templates
# (category, family, flags, program). family None means -n with no input.

T = []


def add(cat, fam, progs, flags=("-c",)):
    for p in progs:
        T.append((cat, fam, list(flags), p))


add("core", "store", [
    ".orders[0].customer.name",
    ".orders[].id",
    ".orders[-1].items[0].sku?",
    "[.orders[] | .customer.tags[]]",
    ".orders | length",
    ".orders[1:3] | map(.id)",
    ".orders[:-1][] | .status",
    "[.orders[].items[]?.qty] | add",
    ".orders[] | select(.status == \"paid\") | .id",
    "[.orders[] | .note // \"none\"]",
    ".meta | keys",
    ".meta | to_entries",
    "[paths(type == \"number\")] | length",
    "[..|numbers] | length",
    "[.. | strings] | sort | first",
    ".orders | map(.items | length)",
    ".orders[] | {id, n: (.items | length), s: .status}",
    "[.orders[] | .customer | {name, email}]",
    ".orders | map(select(.customer.email == null) | .id)",
    ".orders[0] | keys_unsorted",
    "[.orders[] | .discount?] ",
    ".orders | [.[] | .items[] | .price * .qty] | add",
    ".orders | first(.[] | select(.items | length > 1)) | .id",
    ".orders | [limit(2; .[] | .id)]",
    ".orders | to_entries | map(\"\\(.key):\\(.value.id)\") | join(\",\")",
    ".orders | group_by(.status) | map({status: .[0].status, n: length})",
    ".orders | sort_by(.id) | reverse | map(.id)",
    ".orders | min_by(.id) | .id, (max_by(.items | length) | .id)",
    ".orders | unique_by(.status) | map(.status)",
    "[.orders[].customer.tags | length] | max",
    ".meta.version | type",
    ".meta.version, (.meta.version | tojson), (.meta.version + 0)",
    ".orders[0].items | map(.price)",
    "[.orders[].items[] | .price] | sort",
    "[.orders[].items[] | .price] | unique",
    ".orders | INDEX(.id) | keys",
    ".orders | map(.customer.name | split(\" \") | .[0])",
    "[.orders[] | .customer.name | ascii_upcase]",
    ".orders | any(.status == \"refunded\"), all(.items | length >= 0)",
    ".orders | map(has(\"discount\"))",
    "del(.orders) | .meta",
    "del(.orders[].items) | .orders[0]",
    ".orders |= map(.id) | .orders",
    ".orders[].status |= ascii_upcase | [.orders[].status]",
    "[.orders[] | .items[] | .sku] | group_by(.) | map([.[0], length])",
    "reduce .orders[] as $o ({}; .[$o.status] += 1)",
    "[foreach .orders[] as $o (0; . + ($o.items | length); .)]",
    ".orders | [.[] | .id] | [.[] | . % 7]",
    "path(.orders[0].customer.name)",
    "[paths] | map(map(tostring) | join(\".\")) | .[0:5]",
    "getpath([\"orders\", 0, \"id\"])",
    "[leaf_paths?] | length",
    "tostream | select(length == 2) | .[0] | length",
    "[tostream] | fromstream(.[]) == .",
    ".orders | map(.items | map(.qty) | add // 0)",
    ".orders | map(.customer.tags | join(\"|\"))",
    "[.orders[] | select(.customer.tags | index(\"vip\"))] | length",
    ".orders | map(.customer.tags | contains([\"eu\"]))",
    "with_entries(.value |= type)",
    ".orders[0] | to_entries | map(.key) | sort",
    "[.orders[] | .note | strings]",
    ".orders | map(select(.note != null)) | length",
    "[.orders[] | .id] | @csv",
    ".orders | map([.id, .status]) | .[] | @tsv",
    "[.orders[].customer.name] | @sh",
    ".orders[0].customer | @json",
    "[.orders[] | \"\\(.id)-\\(.status)\"]",
    ".orders | map(.id) | . as $ids | $ids | length as $n | [$n, ($ids | add / $n | floor)]",
])

add("core", "tree", [
    "[.. | .name? // empty]",
    "[recurse(.children[]?) | .size] | add",
    "[recurse(.children[]?; .size > 10) | .name]",
    "def depth: if (.children // []) | length == 0 then 1 else 1 + ([.children[] | depth] | max) end; depth",
    "[paths(type == \"object\")] | length",
    "[.. | objects | select(has(\"children\")) | .children | length]",
    "walk(if type == \"object\" then del(.size) else . end)",
    "walk(if type == \"object\" and has(\"children\") then .children |= length else . end)",
    "[getpath(paths(type == \"string\"))]",
    "del(.. | .size?) | [.. | numbers]",
    "(.. | select(type == \"object\") | .size) |= . * 2 | [.. | .size? // empty]",
    "[.. | objects | .name] | unique",
    "[paths | length] | max",
    "pick(.children[0].name)?",
    "[.children[]?.name] | sort",
    "tojson | length > 10",
    "[tostream] | length",
    "reduce (.. | objects) as $n (0; . + $n.size)",
    "def f: {n: .name, c: [.children[]? | f]}; f",
    "[limit(3; .. | objects | .name)]",
])

add("core", "events", [
    "map(.type) | unique",
    "group_by(.type) | map({(.[0].type): length}) | add",
    "map(select(.user != null)) | map(.user) | unique | length",
    "[.[] | .payload | objects | keys[]] | unique",
    "[.[] | .payload.ms? // empty] | add",
    "map(.payload | type)",
    "[.[] | .payload | arrays | length]",
    "map(.tags | length) | add",
    "sort_by(.ts) | .[0].ts, .[-1].ts",
    "map(.ts) | (max - min)",
    "[.[] | select(.type == \"buy\") | .payload.amount?] ",
    "map(select(.payload | type == \"object\" and has(\"amount\"))) | map(.payload.amount) | add",
    "[.[] | {user, type}] | group_by(.user) | map({(.[0].user // \"anon\"): map(.type)}) | add",
    "map(.payload) | map(tojson) | .[0:3]",
    "[.[] | .payload | numbers]",
    ".[1:] | length",
    "first(.[] | select(.tags | length == 2) | .user)",
    "[.[] | .ts % 100] | sort",
    "map(.user // \"?\" | ascii_downcase) | join(\" \")",
    "to_entries | map(select(.value.type == \"error\") | .key)",
    "map(.payload | if type == \"array\" then add elif type == \"object\" then length else . end)",
    "[.[] | .payload | .. | numbers] | sort | .[0]",
    "map(.tags) | flatten | group_by(.) | map([.[0], length]) | sort_by(-.[1])",
    "INDEX(.ts | tostring) | keys | length",
    "[.[] | .type] | indices(\"view\")",
])

add("numbers", "nums", [
    ".", ".[]", "map(. + 0)", "map(. * 1)", "map(tostring)", "map(tojson)", "sort", "unique", "min, max", "add",
    "map(floor)", "map(. / 3)", "map(-.)", "map(abs)", "map(length)", "map(. == 1)", "map(. < 1)",
    "map(. % 3)?", "map(type)", "[.[] | select(. > 1)]", "map(sqrt)", "map(. * 100 | round / 100)",
    "group_by(. > 0)", "map(tostring | tonumber)", "map(tostring | length)", "[.[] | . * 2]",
    "map(fabs)", "map(trunc)", "map(ceil)", "@csv", "@sh", "map(@text)", "map(@json)",
    "[.[] | tostring] | join(\";\")", "(add / length)", "map(. == .)", "sort_by(-.)", "map({v: .})",
    "map([.] | tojson)", "to_entries | map(.value)", "[.[] | . as $x | $x]", "map(. + 0.5)",
    "map(isinfinite)", "map(isnormal)", "map(exp10? // 0 | tostring | length)",
    "map(log10? | floor?)", "map(pow(.; 2))", "map(significand)", "map(logb)", "map(frexp)", "map(modf)",
    "[.[] | ldexp(.; 3)]", "map(. / 7 | tostring)", "map(1 / (. + 1000))",
])

add("types", "mixed", [
    ".", "sort", "map(type)", "map(length?)", "map(tostring)", "map(tojson)", "unique", "group_by(type) | map(length)",
    "map(. // \"d\")", "map(not)", "map(if . then 1 else 0 end)", "map(select(. != null))", "[.[] | values]",
    "map(scalars)", "map(iterables)", "[.[] | booleans]", "map(. == false)", "min, max", "[.[] | arrays | length]",
    "map(try keys catch \"nokeys\")", "map(try length catch .)", "map(try (. + 1) catch .)", "map(try -(.) catch .)",
    "map(try (. * 2) catch .)", "map(try implode catch .)", "map(try explode catch .)", "map(try tonumber catch .)",
    "map(try ascii_downcase catch .)", "map(try .[0] catch .)", "map(try .a catch .)", "map(try .[] catch .)",
    "map(try has(\"a\") catch .)", "map(try has(0) catch .)", "map(try (. - 1) catch .)", "map(try (. / 2) catch .)",
    "map(try sort catch .)", "map(try join(\",\") catch .)", "map(try test(\"a\") catch .)", "map(try utf8bytelength catch .)",
    "map(try ltrimstr(\"a\") catch .)", "map(try startswith(\"a\") catch .)", "map(try split(\",\") catch .)",
    "map(try tostring catch .)", "map(try fromjson catch .)", "map(try @csv catch .)", "map(try @base64 catch .)",
    "map(try contains(\"a\") catch .)", "map(try add catch .)", "map(try flatten catch .)", "map(try reverse catch .)",
    "map(try to_entries catch .)", "map(try (.[1:]) catch .)", "map(try splits(\"x\") catch .)",
    "map(try (. % 2) catch .)", "map(try isnan catch .)", "map(try floor catch .)", "map(try keys_unsorted catch .)",
    "map(try (. as [$a] | $a) catch .)", "map(try (. as {a: $a} | $a) catch .)", "map(try first catch .)",
    "map(try min catch .)", "map(try unique catch .)", "map(try any catch .)", "map(try tojson catch .)",
    "map(try (. * \"x\") catch .)", "map(try ({} * .) catch .)", "map(try ([] - .) catch .)",
    "map(try (. < 1) catch .)", "map(try indices(1) catch .)", "map(try inside([1]) catch .)",
    "map(try getpath([\"a\"]) catch .)", "map(try setpath([0]; 1) catch .)", "map(try delpaths([[0]]) catch .)",
    "map(try paths catch .)", "map(try (.. | numbers) catch .)", "[.[] | try error catch .]",
    "map(try error(\"x\") catch .)", "map(try (error) catch type)", "map(try @sh catch .)", "map(try @uri catch .)",
])

add("strings", "text", [
    ".", ".[]", "map(length)", "map(utf8bytelength)", "map(ascii_downcase)", "map(ascii_upcase)", "map(explode)",
    "map(explode | implode)", "map(tojson)", "map(@json)", "map(@html)", "map(@uri)", "map(@sh)", "map(@base64)",
    "map(@base64 | @base64d)", "@csv", "@tsv", "map(@text)", "join(\"-\")", "map(split(\"a\"))", "map(split(\"\"))",
    "map(ltrimstr(\"a\"))", "map(rtrimstr(\"a\"))", "map(startswith(\"a\"))", "map(endswith(\"e\"))",
    "map(test(\"a\"))", "map(test(\"^[a-z]+$\"))", "map(test(\"A\"; \"i\"))", "map([match(\"[aeiou]\"; \"g\") | .offset])",
    "map(gsub(\"[aeiou]\"; \"_\"))", "map(sub(\"(?<x>[a-z])\"; \"<\\(.x)>\"))", "map([scan(\"[a-z]{2}\")])",
    "map(splits(\" \") )", "map([splits(\"[, ]+\")])", "map(indices(\"a\"))", "map(index(\"e\"))", "map(rindex(\"a\"))",
    "map(.[1:3])", "map(.[-2:])", "sort", "unique", "min, max", "map(length) | add", "map(tostring)",
    "map(. + \"!\")", "map(. * 2)", "map(contains(\"a\"))", "map(inside(\"alphabet\"))", "map(ascii_downcase | ltrimstr(\"e\"))",
    "map(capture(\"(?<first>.)(?<rest>.*)\"))", "map([match(\"(a)(x)?\") | .captures[] | .offset])",
    "map(test(\"\\\\s\"))", "map(gsub(\"\\\\s+\"; \" \"))", "map(gsub(\"(?<c>[A-Z])\"; \"_\\(.c | ascii_downcase)\"))",
    "map(sub(\"$\"; \".\"))", "map(gsub(\"\"; \"|\"))", "map(split(\", \"))", "map(@uri | length)",
    "map(\"<\\(.)>\")", "map(@html \"<p>\\(.)</p>\")", "map(@sh \"echo \\(.)\")", "map(@json \"v=\\(.)\")",
    "map(@base64 \"\\(.):\\(.)\")", "map(tojson | fromjson)", "map(try tonumber catch \"nan\")", "map(. as $s | [$s | length, ($s | utf8bytelength)])",
    "map(explode | map(. + 1) | implode)", "map(explode | reverse | implode)", "map(ascii_downcase | explode | unique | implode)",
    "group_by(length) | map(length)", "map(select(test(\"e\")))", "map(gsub(\"a\"; \"A\"; \"g\"))",
    "map([match(\"\"; \"g\")] | length)", "map(@base32? // \"x\")",
])

add("control", None, [
    "def f: def g: 3; g * 2; f",
    "def f(x): x * 2; f(3), f(1, 2)",
    "def f($a; $b): $a + $b; f(1, 2; 10, 20)",
    "def f(a; $b): [a, $b]; f(1, 2; 3, 4)",
    "def fac: if . <= 1 then 1 else . * (. - 1 | fac) end; [range(1; 11) | fac]",
    "def fib: if . < 2 then . else (. - 1 | fib) + (. - 2 | fib) end; [range(15) | fib]",
    "def f: 1; def g: f; def f: 2; [g, f]",
    "def f(x): def x: 3; x; f(1)",
    "def ack(m; n): if m == 0 then n + 1 elif n == 0 then ack(m - 1; 1) else ack(m - 1; ack(m; n - 1)) end; ack(2; 3)",
    "def compose(f; g): f | g; [range(5)] | compose(map(. * 2); add)",
    "def apply(f): [.[] | f]; [1, 2, 3] | apply(. * 10)",
    "def r: if . > 0 then ., (. - 1 | r) else . end; [5 | r]",
    "reduce range(10) as $i (0; . + $i)",
    "reduce range(5) as $i ([]; [$i] + .)",
    "reduce range(3) as $x (0; empty)",
    "reduce range(4) as $x (0; if $x == 2 then empty else . + 1 end)",
    "reduce (1, 2) as $x (0, 100; . + $x)",
    "reduce range(3) as $x (0; ., 10)",
    "[foreach range(5) as $x (0; . + $x)]",
    "[foreach range(5) as $x (0; . + $x; [$x, .])]",
    "[foreach range(6) as $x (0; . + $x; select(. % 2 == 0))]",
    "[foreach (1, 2) as $x (0, 100; . + $x)]",
    "[foreach range(4) as $x (0; if $x == 1 then empty else . + 1 end; .)]",
    "[foreach ([1, 2], [3, 4]) as [$a, $b] (0; . + $a; [$b, .])]",
    "[label $out | range(10) | ., (select(. == 3) | break $out)]",
    "[label $a | label $b | 1, break $b, 2], [label $a | (label $b | 1, break $a, 2), 3]",
    "[limit(3; range(100))], [limit(0; 1, 2)], [limit(-1; 1, 2)]",
    "[first(range(10; 0; -3))], [nth(2; range(10))], isempty(empty), isempty(1, error(\"x\"))",
    "[range(0; 10; 3)], [range(5; 0; -2)], [range(0; 1; 0)], [range(1; 2; 0.3)]",
    "[range(0, 1; 3, 4)]",
    "[limit(5; repeat(1))]",
    "1 | [limit(4; repeat(. * 10))]",
    "[1, 2] | [while(length > 0; .[1:])]",
    "[range(5)] | until(length < 2; .[2:])",
    "[1 | recurse(. * 3; . < 100)]",
    "[2 | recurse(if . < 50 then . * . else empty end)]",
    "if 1 then \"a\" elif 2 then \"b\" else \"c\" end, (if false then 1 elif null then 2 else 3 end)",
    "[if (true, false) then 1 else 2 end], [if empty then 1 else 2 end], [if null then 1 end]",
    "[true, false, null, 0] | map(if . then \"t\" end)",
    "try error(\"x\") catch . + \"!\"",
    "try error({\"a\": 1}) catch .a",
    "[.[]?], [1 | .[]?], [1 | .a?], [\"x\" | .[0]?]",
    "[try (1, 2, error(\"x\"), 3)], [(1, 2, error(\"x\"), 3)?]",
    "try error(\"a\") catch (try error(\"b\") catch .)",
    "[(1, 2) | try (if . == 1 then error(\"one\") else . end) catch \"caught \\(.)\"]",
    "try (try error(\"inner\") catch error(\"outer: \" + .)) catch .",
    "[.[]?] | try length catch 0 | . + 1",
    "try error catch ., try error(null) catch ., try error(false) catch .",
    "[1, 2] as [$a, $b] | {$a, $b}",
    "[[1, 2], [3]] | .[] as [$a, $b] | [$a, $b]",
    "{\"a\": {\"b\": [1, 2]}} as {a: {b: [$x, $y]}} | $x + $y",
    "{\"k\": \"a\", \"a\": 5} as {k: $k} | {\"a\": 5} as {($k): $v} | $v",
    "{\"a\": {\"b\": 1}} as {$a: {$b}} | [$a, $b]",
    "[[1, 2], \"x\", {\"a\": 3}] | .[] as [$a] ?// $a | $a",
    "[[1, 2], \"x\"] | .[] as [$a] ?// $a | if ($a | type) == \"number\" then error(\"n\") else $a end",
    "[[3], {\"v\": 4}, 5] | .[] as [$x] ?// {v: $x} ?// $x | $x * 10",
    "1 as $x | [$x, (2 as $x | $x), $x]",
    "[1, 2, 3] as [$a] | $a, ([] as [$a] | $a), (null as {a: $a} | $a)",
    ". as [$a, {b: $c}] | [$a, $c]",
    "[range(3)] | . as $arr | reduce range(length) as $i (0; . + $arr[$i] * $i)",
    "def f: reduce .[] as $x (0; . + $x); [[1, 2], [3]] | map(f)",
    "[splits(\"a\"; \"b\")]?",
    "{} | .a.b.c, (.a[0]?), ([.a[]?]), (try (.a | keys) catch .)",
    "[1, [2]] | .[1]?[0], (.[0]?[0])?, [.[]?[]?]",
    "\"\\(1, 2) \\(3, 4)\"",
    "{(\"a\", \"b\"): (1, 2)}",
    "{\"a\": (1, 2), \"b\": (3, 4)}",
    "[(1, 2) + (10, 20)], [(1, 2) * (3, 4)], [(1, 2) < (2, 1)]",
    "[(false, true) or (true, false)], [(true, false) and (true, false)]",
    "[1 // 2, null // 2, (null, false) // 3, (empty // 4), (1, null, 2) // 3]",
    "[(1, error(\"x\")) // 2]?",
    "$__loc__",
    "[.[]?] | {a: 1} | .a += 1 | .a",
    "[limit(3; range(1; 100) | select(. % 7 == 0))]",
    "[range(10)] | [.[] | select(. % 2 == 0)] | add",
    "[range(1; 6)] | combinations(2)? | select(.[0] < .[1])",
    "[[1, 2], [3, 4]] | [combinations]",
    "[1, 2] | [combinations(2)]",
    "def f(g): [g]; f(1, 2), f(empty)",
    "def f(g): g | g; 2 | f(. * 3)",
    "[.[]?, 1] | .[-1], .[5], .[-5]",
    "[range(10)] | .[2:4], .[:3], .[-3:], .[3:2], .[2.7:5.1], .[null:null]",
    "\"abcdef\" | .[1:], .[:-1], .[5:], .[-1:], .[1:1], length",
    "[range(5)] | .[1.5], .[-1.5], (try .[\"a\"] catch .), .[[2, 3]]",
    "def addvalue(f): f as $x | map(. + $x); [[1, 2], [10, 20]] | addvalue(.[0])",
    "def mymap(f): reduce .[] as $x ([]; . + [$x | f]); [1, 2, 3] | mymap(. * .)",
    "def f: if length > 2 then .[1:] | f else . end; [range(7)] | f",
    "[\"a\", \"b\"] | to_entries | map({(.value): .key}) | add",
    "input",
    "[inputs]",
    "{a: 1} | has(\"a\", \"b\")",
    "[1, 2, 3] | IN(2), IN(.[]; 5, 3)",
    "[3, 1, 2] | bsearch(2), (sort | bsearch(2), bsearch(2.5), bsearch(9), bsearch(0))",
    "[] | bsearch(1), ([1] | bsearch(1), bsearch(0), bsearch(2))",
    "{\"a\": [1, 2]} | getpath([\"a\", 1]), getpath([\"x\", \"y\"]), (try getpath([\"a\", \"b\"]) catch .)",
    "null | setpath([\"a\", 0, \"b\"]; 1), setpath([]; 2), delpaths([[]])",
    "[1, 2, 3, 4] | delpaths([[0], [0], [2]]), delpaths([[{\"start\": 1, \"end\": 3}]]), delpaths([])",
    "{\"a\": 1, \"b\": {\"c\": 2}} | delpaths([[\"b\", \"c\"], [\"a\"]]), (try delpaths([[\"a\", \"x\"]]) catch .)",
    "[[1, 2], [3]] | transpose, ([[1], [2, 3], []] | transpose), ([] | transpose)",
    "[1, [2, [3, [4]]]] | flatten, flatten(1), flatten(0), (try flatten(-1) catch .)",
    "[{\"a\": 1}, {\"a\": 2, \"b\": 0}] | INDEX(.a), (INDEX(.[]; .a | tostring) | keys)",
    "[\"a\", \"b\", \"a\"] | reduce .[] as $x ({}; .[$x] += 1)",
    "{\"user\": \"x\", \"titles\": [\"a\", \"b\"]} | {user, title: .titles[]}",
    "{\"a\": [{\"b\": 1}, {\"b\": 2}]} | [.a[].b], (.a | map(.b) | add), [.a[] | .b as $v | $v * 2]",
    "[1, 2, 3] | tojson | fromjson | add",
    "\"[1,\\\"a\\\",{\\\"b\\\":null}]\" | fromjson | length, (.[2] | keys)",
    "\"{\\\"a\\\":1.50,\\\"b\\\":1e2}\" | fromjson | ., .a + 0, tojson",
    "{\"a\": 1} | to_entries, with_entries(.value += 1), (to_entries | from_entries)",
    "[{\"name\": \"a\", \"value\": 1}, {\"key\": \"b\", \"Value\": 2}, {\"Name\": \"c\"}, {\"Key\": \"d\", \"value\": false}] | from_entries",
    "[{\"key\": \"a\", \"value\": 1}, {\"key\": \"a\", \"value\": 2}] | from_entries",
    "[] | from_entries, ({} | to_entries), ({} | with_entries(.))",
    "[{\"key\": 1, \"value\": 2}] | try from_entries catch .",
    "[{\"k\": \"b\", \"v\": 2}] | try from_entries catch .",
    "{\"b\": 2, \"a\": 1} | to_entries | from_entries | keys_unsorted",
    "[1, [1, 2]] | pick(.[1][0]), ({\"a\": {\"b\": 1, \"c\": 2}} | pick(.a.b)), ({\"x\": 1} | pick(.y.z))",
    "[3, 1, 2] | pick(.[2]), (try pick(.[-1]) catch .)",
    "[{\"a\": [1, 2]}] | tostream",
    "[{\"a\": 1, \"b\": [1, 2]} | tostream] | fromstream(.[])",
    "[1 | truncate_stream([[0], 1], [[1, 0], 2], [[1, 0]], [[1]])]",
    "fromstream(1 | truncate_stream([[0], 1], [[1, 0], 2], [[1, 0]], [[1]]))",
    "{\"a\": [1, {\"b\": 2}]} | [paths], [paths(type == \"number\")], [getpath(paths)]",
    "{\"a\": [1, 2, {\"b\": null}]} | [..], [.. | scalars], [paths(scalars)]",
    "{\"a\": [1]} | [paths(..)]",
    "[[3, 1], [2]] | walk(if type == \"array\" then sort else . end)",
    "{\"a\": 1, \"b\": [2, {\"c\": 3}]} | walk(if type == \"number\" then . * 10 else . end)",
    "[1, [2]] | walk(numbers |= -.)?",
    "{\"a\": {\"b\": {\"c\": 1}}} | [.. | objects | keys[]], [paths | join(\"/\")?]",
    "[\"a,b\", \"c\"] | map(split(\",\")) | add | join(\"+\")",
    "{} | .a.b |= 1, (.x[2] = 1), (try (.[0] = 1) catch .)",
    "[[1, 2], [3, 4]] | .[] |= first(.[]), map(last), (.[0] |= length)",
    "{\"a\": null} | .a //= 1, (.b //= 2), ([1, null] | .[] //= 5)",
    "[1, 2, 3] | .[1] = 9, .[1] += 9, .[1] -= 9, .[1] *= 9, .[1] /= 2, .[1] %= 2, .[5] //= 7",
    "{\"a\": [1, 2]} | .a += [3], .a -= [1], (.a |= map(. * 2)), (.a[0] |= tostring), (.b.c |= 1), (.a |= length)",
    "{\"a\": 1, \"b\": 2} | .[] += 1, (.[] = 0), map_values(. * 10), map_values(empty), (.[] |= select(. > 1))",
    "[1, 2, 3, 4] | (.[] | select(. % 2 == 0)) |= empty, map_values(empty), (.[1:3] = []), del(.[1, 2])",
    "[1, 2, 3] | .[] |= empty",
    "{\"a\": 1} | .a = (.a + 1, .a + 2), (.b = .a), (.a |= . + 1 | .a |= . * 2)",
    "[[1]] | .[0][0] = 2, (.[0] += [3]), (.[][] |= . + 1), (.. |= (numbers |= . + 1)?)",
    "[1, 2, 3] | .[1:] = [\"x\"], (.[5:7] = [\"y\"]), (.[-1:] = [\"z\"]), (.[1:2] |= [\"w\"] + .)",
    "[1, 2, 3] | .[1:] |= empty, (.[0] |= empty), (try (.[1:] = 3) catch .)",
    "\"abc\" | try (.[1:] = \"x\") catch .",
    "[1] | .[1.5] = 2, del(.[0.5]), (try (.[-2] = 5) catch .), (.[-1] = 5)",
    "{\"a\": [1, 2]} | del(.a[0], .a[1]), del(.a[]), del(.a), del(.b)",
    "[1, 2, 3, 4] | del(.[0], .[2]), del(.[1:3]), del(.[-1]), (to_entries | map(select(.key % 2 == 0) | .value))",
    "{\"a\": 1, \"b\": 2} | del(.a, .b), del(.[]), delpaths([[\"a\"]])",
    "null | del(.a), del(.[0])",
    "[1, 2] | to_entries, keys, ({} | keys), (try (\"ab\" | keys) catch .)",
    "{\"b\": 1, \"a\": 2, \"c\": {\"z\": 1, \"y\": 2}} | keys, [.[]], (to_entries | map(.key)), tojson, add?",
    "path(..)",
    "{\"a\": [1, 2]} | [path(..)], [path(.a[] | select(. > 1))], path(.a | first(.[]))",
    "[[1, 2]] | path(first(.[][])), path(limit(1; .[][])), (try path(last(.[][])) catch .)",
    "{\"a\": 1} | path(. as $x | $x), path(if .a then .a else .b end), path(getpath([\"x\", \"y\"]))",
    "{\"a\": null, \"b\": 1} | path(.a // .b), [paths]",
    "try path(1) catch ., ({} | try path(.a | 1) catch .), ([1] | try path(.[0] + 1) catch .)",
    "{\"a\": {\"b\": 1}} | try path(.a | tojson | .x) catch .",
    "[1, 2, 3] | path(.[1:2]), path(.[:2]), path(.[1:]), path(.[-1])",
])

add("formats", None, [
    "\"<&>'\\\"x\" | @html",
    "[1, \"a,b\", null, true, 1.5, \"q\\\"q\"] | @csv",
    "[1, \"a\\tb\", null, false, \"c\\\\d\", \"e\\nf\"] | @tsv",
    "try ([[1]] | @csv) catch ., try ([{}] | @tsv) catch ., try ([1, [2]] | @sh) catch ., try ({\"a\": 1} | @sh) catch .",
    "try ({\"a\": 1} | @csv) catch ., try (\"x\" | @tsv) catch .",
    "5 | [tostring, @text, @json, @html, @uri, @sh, @base64]",
    "[1, \"x\"] | @json, @text, @html, @uri, @sh, @base64",
    "\"\\u00e9x\" | @uri, @sh, @base64, (@base64 | @base64d), @html, @json",
    "{\"a\": \"<b>\"} | @html \"x\\(.a)y\", @uri \"q=\\(.a)\", @sh \"e \\(.a)\", @json \"j\\(.a)\", @base64 \"\\(.a)\", @text \"\\(.a)\"",
    "{\"a\": \"<b>\"} | @csv \"\\([.a])\", @tsv \"\\([.a, 1])\"",
    "\"YWJj\", \"YWJ\", \"YQ==YQ==\", \"=\", \"\", \"Zm9vYmFy\" | @base64d",
    "\"aGVsbG8gd29ybGQ=\" | @base64d | ascii_upcase | @base64",
    "try (\"a\" | @base64d) catch ., try (\"!!!!\" | @base64d) catch .",
    "\"hello\" | try @base32 catch .",
    "[\"it's\", \"a b\", \"\"] | @sh, (map(@sh) | join(\" \"))",
    "\"a/b?c=d&e f\" | @uri",
    "[1.0, 1.50, 100000000000000000001] | @csv, @tsv, @sh, @text",
    "\"\\t\\n\\r\\b\\f\\\\\\\"\\/\\u0001 \\u001f\\u007f\\u0080\"",
    "\"\\ud83d\\ude00\" | length, utf8bytelength, explode, @json",
    "\"\\ud83d\" | explode",
    "[65, 0, 1114111, 1114112, -1, 55296, 9.7] | implode | explode",
    "try ([\"a\"] | implode) catch ., try (null | implode) catch ., try ([.] | implode) catch .",
    "\"pre\\(1 + 2)mid\\(\"in\\(\"ner\")\")post\"",
    "\"\\([1, {\"a\": \"x\"}]) and \\(null) and \\(\"s\")\"",
    "@base64 \"\\(\"a\")\\(\"b\")\"",
    "{\"if\": 1, \"then\": 2, \"and\": 3, \"reduce\": 4, \"__loc__\": 5} | keys",
    "{a: 1, \"b c\": 2, \"\\(1 + 1)\": 3, (\"x\" + \"y\"): 4} | to_entries | map(.key)",
    "\"x\" as $x | {$x, \"y\": 2, $__loc__}",
    "{a: .x | .y, b: -1, \"c\": \"d\" | ascii_upcase}",
    "{a: 1, b: 2,}",
    "[.[]?] | {a: [.[]], b: {}}",
])

add("numbers", None, [
    "3.0, 3.0 + 0, 1.000, 100000000000000000000, 100000000000000000000 + 0",
    "1e1000, -1e1000, 1e-1000, (1e1000 | -.), (1e1000 | floor), (1e1000 | tostring)",
    "0.1 + 0.2, 1 / 3, 2 / 3, 10 / 4, 1e-7, 1e-7 * 1, 1e17, 1e17 * 1, 1e16 * 1, 1e15 * 1",
    "[1.10, 2e3, 1E2, .5, 00012, 1.0e0, 0.10, 100e-2, 1e-0]",
    "[1e-5, 1e-4, 1e-3, 123.456e5, 1.5e300, 2e-7, 1e15, 1e16, 123456789012 * 1000000] | map(. * 1)",
    "[5e-324 * 1, 1e308 * 10, 9007199254740993, 9007199254740993 + 0, 2 | pow(.; 64)]",
    "[2 | pow(.; 53) + 1, pow(.; 60), (pow(.; 70) | tostring)]",
    "[-0, -0.0, 0, 0.0] | map(tostring), sort, unique, (.[0] == .[2])",
    "-1.0, -(1.0), [-1.0], (-1.0 | tojson), (1.0 | -.), (-0 | tostring)",
    "1.0, 1.0 * 1, (1.0 | floor), ([1.0] | add), ([1.0, 2] | add), ({\"a\": 1.0} | .a), (1.0 | abs), (-1.0 | abs)",
    "1.10 | ., tostring, tojson, (. == 1.1), ([.] | unique), (. < 1.11)",
    "[1.0, 1, 1.00] | unique, sort, group_by(.), (map({(tostring): .}) | add)",
    "{\"a\": 1.0} == {\"a\": 1}, ([1.0] == [1]), (1.0 | contains(1)), ([1.0] | index(1))",
    "100000000000000000001 | . == 100000000000000000000, ([.] | contains([100000000000000000000])), (. < 100000000000000000002)",
    "[100000000000000000001, 100000000000000000000.0, 1e20] | unique, sort, (map(. + 0) | unique)",
    "[5 % 3, -5 % 3, 5 % -3, -5 % -3, 5.9 % 3.1, 1 % 1, 0 % 5, 2147483648 % 3, 7 % -1]",
    "try (7 % 0.5) catch ., try (1 / 0) catch ., try (1 % 0) catch .",
    "[1, 0.5, 0.1, 100, 1e10, 1.25e-8, 123456.789, 2.5e-3] | map(. * 3)",
    "[3.0, 2.5, -2.5, 0.5, -0.5, 1.5] | map(round), map(rint), map(nearbyint), map(trunc), map(ceil), map(floor)",
    "[4, 2.25, 0, -1] | map(sqrt)",
    "[1, 2, 3] | map(log), map(exp), map(log2), map(log10), map(exp2), map(exp10)",
    "[0.5, 1, 2] | map(sin), map(cos), map(tan), map(atan), map(asin), map(cbrt)",
    "[pow(2; 10), pow(2; -1), pow(0; 0), atan2(1; 1), fmin(1; 2), fmax(1; 2), fmod(7; 3), fmod(-7; 3), hypot(3; 4), copysign(1; -2), ldexp(3; 2)]",
    "5 | [significand, logb, gamma, lgamma, tgamma, frexp, modf, ldexp(.; 1), drem(.; 3), nearbyint, expm1, log1p, sinh, cosh, tanh, asinh, acosh]",
    "[.5, 8, 0.1] | map(frexp), map(modf), map(lgamma_r)",
    "infinite, -infinite, (infinite | tostring), ([infinite] | tojson), (nan | tostring), ([nan] | tojson), (nan < 1)",
    "[nan, 1, null] | sort | map(type)",
    "[1, \"1\", [1], null] | map(tostring | tonumber?)",
    "\"1.50\", \"1e3\", \" 5 \", \"-0\", \"1e400\", \"0.000\" | tonumber",
    "try (\"x\" | tonumber) catch ., try (\"[1]\" | tonumber) catch ., try (\"1 2\" | tonumber) catch ., try ([] | tonumber) catch .",
    "[\"1 2\", \"{\", \"{\\\"a\\\":1,}\", \"[1 2]\", \"tru\", \"\\\"abc\", \"nul\", \"1.2.3\", \"{1:2}\", \"]\", \"[1,]\", \"\", \"  \"] | .[] | try fromjson catch .",
    "1e17 | ., . + 1, tostring",
    "[0.1, 0.25, 1e100, 1e-100, 123e-20] | map(. + 0) | tojson",
    "[range(-3; 4)] | map(. / 2)",
    "1, 1.5, -2.25 | . * 1e20, . / 1e20",
    "[12345678901234567890, 0.1e1, 1.23e2, 1230e-1] | ., map(. + 0)",
    "$__loc__.line + 0.5",
    "[3, 3.0, 3.00] | map(length), map(-. | length)",
    "4 as $x | [range($x)] | map(. * $x) | add",
])

add("errors", None, [
    "1 + \"a\"", "{} | .[0]", "[] | .a", "1 | .[]", "\"a\" - 1", "1 / 0", "1 % 0", "1 | keys", "true | length",
    "null | implode", "error(null)", "error({a: 1})", "\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\" + 1",
    "{\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\": 1} + 1", "[1,2,3,4,5,6,7,8,9,10,11,12] - \"a\"", "\"\\u00e9\" * {}",
    "{} - {}", "\"abc\" | .[0]", "{} | .[[]]", "[1] | has(\"a\")", "{} | has(0)", "\"a\" | test(1)",
    "{} | sort", "[{}] | join(\",\")", "1 | splits(\"a\")", "[1, 2] | .[1:\"a\"]", "[1] | .[-2] = 5",
    "1 | ltrimstr(\"a\")", "1 | startswith(\"a\")", "\"a\" | endswith(1)", "[1] | .a = 1", "1 | .a = 1",
    "{\"a\": 1} | .[0] = 1", "[1, 2] | nth(-1; .[])", "- \"a\"", "-(1, 2), -\"a\"", "[1, [2]] | add",
    "[1, 2] | combinations", "\"abc\" | reverse", "{\"a\": 1} | reverse", "\"x\" | error", "[1, 2] | .[] | error",
    "try error(\"a\") catch error(\"b\")", "1, error(\"x\"), 2", "[1, error(\"x\")]", "{} | keys | .[0] | .x",
    "\"abc\" | sort", "{} | from_entries | .a.b.c = 1 | .a.b[0]", "[3] | .[\"x\"]", "null | .[\"a\"] | .[0] | .[1:2] | .[]",
    "{\"a\": \"x\"} | .a.b", "[[]] | .[0][0][0]", "[] | first", "[] | first(.[])", "{} | min_by(.a)",
    "\"\" | test(\"(\")?", "1 as [$a] | $a", "{} as [$a] | $a", "[] as {a: $a} | $a", "{(1): 2}",
    "{(null): 2}", "{(\"a\", 1): 2}", "[range(\"a\")]", "range(1; \"b\")", "[1] | implode", "[1] | getpath([0, 0])",
    "{\"a\": 1} | getpath([1])", "[1] | getpath([\"a\"])", "{} | setpath(1; 2)", "{} | delpaths(1)", "{} | delpaths([1])",
    "[1, 2] | to_entries | from_entries", "[1] | inside(1)", "true | contains(false)", "\"a\" | contains([\"a\"])",
    "{} | tojson | fromjson | .x | length", "null | utf8bytelength", "1 | ascii_downcase", "1 | explode",
    "\"x\" | @base64d | explode | implode | @base64", "[] | @sh", "{} | @uri | length", "1 | halt_error(\"x\")",
    "halt_error", "\"bye\\n\" | halt_error", "{\"a\": 1} | halt_error(3)", "1, halt, 2", "\"x\" | halt_error(1)",
    "[1, 2] | halt_error", "1 | halt_error(0)", "null | halt_error(2)", "1 | isnan | not | not | type | tonumber",
    "[1, 2] | min_by(error(\"k\"))", "[1, 2] | sort_by(error(\"k\"))", "{} | sort_by(.)", "{\"a\": 2, \"b\": 1} | sort_by(.)",
    "{\"a\": 2, \"b\": 1} | group_by(.)", "{\"a\": 2} | min", "\"ab\" | split(1)", "[1] | split(\",\")",
    "\"a\" | _strindices(1)?", "try (\"abc\" | test(\"b\"; \"q\")) catch .", "try (\"abc\" | test(\"b\"; 1)) catch .",
    "try (\"abc\" | test([\"b\", \"gx\", 1])) catch .", "try ({} | test(\"a\")) catch .", "try ([] | match(\"a\")) catch .",
    "try (1 | isinfinite) catch ., try (\"a\" | floor) catch ., try (null | sqrt) catch ., try (pow(\"a\"; 2)) catch .",
    "try ([] | flatten(\"a\")) catch .", "try (\"a\" | ltrimstr(1) | rtrimstr(1) | startswith(\"a\") | not) catch .",
    "try ({\"a\": [1]} | .a.b) catch ., try ({\"a\": [1]} | .a[\"b\"]) catch .",
    "try ([1, 2] | .[\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"]) catch .",
    "try (1 | .[\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"]) catch .",
    "try ([1, 2] | .[{}]) catch ., try ([1, 2] | .[{\"start\": 1}]) catch ., try (\"ab\" | .[{\"start\": \"x\", \"end\": 1}]) catch .",
    "try (\"\\u00e9\\u00e9\\u00e9\\u00e9\\u00e9\\u00e9\\u00e9\" + 1) catch .",
    "try ([\"\\u00e9\\u00e9\\u00e9\\u00e9\\u00e9\\u00e9\\u00e9\"] | .[0] - 1) catch .",
    "try ({\"a\": {\"b\": {\"c\": {\"d\": [1, 2, 3, 4, 5, 6]}}}} | .[]) catch ., try ({\"x\": \"yyyyyyyyyyyyyy\"} | keys | .[0] | .[] ) catch .",
    "try (path(1)) catch ., try ([1] | path(.[0] | . + 1 | .x)) catch ., try ({\"a\": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]} | path(.a | sort | .[0])) catch .",
    "try ({\"a\": [1, 2]} | path(.a | map(.) | .[])) catch .",
    "try ([[1]] | .[0] |= (. as [$x] | error(\"inner\"))) catch .",
    "try (reduce range(3) as $x (0; if $x == 2 then error(\"stop at \\($x)\") else . + 1 end)) catch .",
    "try ([foreach range(5) as $x (0; . + $x; if . > 3 then error(\"big\") else . end)]) catch .",
    "try (label $f | error(\"in label\")) catch .", "try ({} | .a.b.c |= error(\"upd\")) catch .",
    "try ([1, 2, 3] | .[] += error(\"rhs\")) catch .", "try ({} | .[error(\"key\")] = 1) catch .",
    "try (\"\\(error(\"interp\"))\") catch .", "try ({a: error(\"val\")}) catch .", "try ({(error(\"k\")): 1}) catch .",
    "try ([1, 2] | map(if . == 2 then error(\"two\") else . end)) catch .",
    "try (def f: error(\"in f\"); f) catch .", "try error([1, {\"a\": 2}]) catch .[1].a",
    "try (\"a\" | error) catch (. + \"b\")", "try (null | error) catch (. == null)",
    "[.[] | try . catch .]?", "try ([1] | .[\"a\"] = 1) catch ., try (\"x\" | .[0] = 1) catch .",
])

add("errors", None, [
    "1 +", "[1, 2", "foo", "$undefined", "break $x", "{a: 1 + 2}", "[1, 2,]", "1 < 2 < 3", "1 == 2 == false",
    "def f: 1; f(1)", ". as [$a | $a", "if 1 then 2", "try", "reduce . as $x (0)", "{(1): }", ".a.[", "@", "\"unterminated",
    "leaf_paths", "splits", "ltrimstr", "ascii", "toarray", "trim", "getpath([\"a\"]; 1)", "\"\\x\"", "\"a\\(\"",
    ".[]?//1", "[.[]?//1]", "1 as $x | $y", "label $a | break $b", "def f(a): a; f", ".a = 1 = 2",
    "{\"a\" 1}", "(1", ")", "]", "1 2", ". .", "$__loc__ | $__loc__.x", "\"ok\\u00zz\"",
], flags=("-c", "-n"))

FAMS_ERRORS_NULL = None

# ---------------------------------------------------------------- CLI cases

CLI = [
    (["-c", ".a"], "1 {}"), (["-c", ".a"], "{} 1"), (["-e", ".a"], '{"a":1} {"a":null}'), (["-e", ".a"], '{"a":null} {"a":1}'),
    (["-e", ".a"], '1 {"a":2}'), (["-e", ".a"], '{"a":2} 1'), (["-e", "empty"], "1"), (["-e", ".[]"], "[]"), (["-e", "."], "false"),
    (["-e", "."], "null"), (["-e", "."], "0"), (["-ne", "null"], ""), (["-ne", "empty"], ""), (["-ne", "1"], ""), (["-e", "-r", "."], '""'),
    (["."], "1 2 [3,"), (["."], "1 2 x"), (["."], "[1]x"), (["."], "1x 2"), (["."], '{"a":1,}'), (["."], '"abc'), (["."], "]"),
    (["."], "[1 2]"), (["."], '{"a" 1}'), (["."], "tru"), (["."], "nul"), (["."], '"a\\qb"'), (["."], '"\\u12"'), (["-c", "."], '"\\ud800x"'),
    (["-c", "."], '{"a":1} [1,{"b":[]}] "x"'), (["."], '{"a":[1,{"b":[]},{}],"c":"d"}'), (["--tab", "."], '{"a":[1,2]}'),
    (["--indent", "1", "."], '{"a":[1,2]}'), (["--indent", "0", "."], '{"a":[1,2]}'), (["--indent", "7", "."], '{"a":[1]}'),
    (["--indent", "8", "."], '{"a":[1]}'), (["--indent", "3", "."], '[[],{},[{}]]'), (["-S", "."], '{"b":1,"a":{"d":1,"c":2}}'),
    (["-S", "-c", "tojson"], '{"b":1,"a":2}'), (["-S", "-c", "keys_unsorted"], '{"b":1,"a":2}'), (["-S", "-c", "."], '[{"z":1,"y":[{"b":1,"a":2}]}]'),
    (["-r", "."], '"a\\nb" [1] "c" {"d":"e"}'), (["-j", "."], '"a" 1 [2] "b"'), (["-a", "-c", "."], '"\u00e9\U0001F600\\u007f"'),
    (["-rj", "."], '"x" "y"'), (["-nr", "--arg", "x", "1", "--argjson", "y", '{"z":2}', "[$x,$y,$ARGS]"], ""),
    (["-nc", "$ARGS", "--args", "a", "--arg", "q", "r", "b"], ""), (["-nc", "--jsonargs", "$ARGS.positional", "1", '{"a":2}', "null"], ""),
    (["-nc", "--args", "[$ARGS.positional[] | length]", "abc", "", "\u00e9"], ""), (["-nc", "--arg", "v", "5", "$v + $v"], ""),
    (["-nc", "--argjson", "v", "5", "$v + $v"], ""), (["-nc", "--argjson", "v", "{bad", "$v"], ""), (["-nc", "--arg", "a", "1", "--arg", "a", "2", "$a, $ARGS.named"], ""),
    (["-s", "-c", "."], "1 2 3"), (["-s", "-c", "."], ""), (["-s", "-c", "map(. * 2)"], "[1] [2]"), (["-R", "-c", "."], "a\nb\n"),
    (["-R", "-c", "."], "a\nb"), (["-R", "-s", "-c", "."], "a\nb\n"), (["-nR", "-c", "[inputs]"], "a\nb"), (["-R", "-c", "length"], "\u00e9\n\n x \n"),
    (["-n", "-c", "[inputs]"], "1 2"), (["-n", "-c", "input"], "1 2"), (["-c", "[., input]"], "1 2 3 4"), (["-c", "[., input]"], "1 2 3"),
    (["-n", "-c", "input, input"], "7"), (["-n", "-c", "[inputs] | add"], '{"a":1} {"b":2}'), (["-c", "[inputs]"], "1 2 3"),
    (["-c", ".[] | (if . == 2 then error(\"two\") else . end)"], "[1,2,3]"), (["-c", ".a"], '1 {"a":5} "s" {"a":6}'),
    (["-c", "., halt"], "1 2 3"), (["-c", "if . == 2 then halt_error else . end"], "1 2 3"),
    (["-c", "if . == 2 then \"two\\n\" | halt_error(4) else . end"], "1 2 3"), (["-n", "\"x\" | halt_error(1)"], ""),
    (["-n", "[1,2] | halt_error"], ""), (["-n", "1 | halt_error(0)"], ""), (["-n", "{} | halt_error"], ""), (["-n", "halt"], ""),
    (["-n", "-c", "1, error(\"x\"), 2"], ""), (["-c", "error"], '"a" "b"'), (["-c", "try error catch ."], '"a" {"b":1}'),
    (["-nc", "-5 | abs"], ""), (["-nc", "-(1, 2)"], ""), (["-x", "."], "1"), (["-nx", "."], ""), (["--foo", "."], "1"),
    (["-n", "--", "-1"], ""), (["-c", ".", "-n"], ""), (["-n", "1", "-c"], ""), (["-nc"], ""), (["-c", ".[0]"], ""),
    ([".", "-c"], "[1, 2]"), (["-n", "\"\\u00e9\""], ""), (["-n", "-a", "\"\\u00e9\\ud83d\\ude00\""], ""),
    (["-n", "-r", "\"a\\tb\\u0000c\""], ""), (["-cn", "[$__loc__]"], ""), (["-nc", "1\n|\n$__loc__"], ""),
    (["-nc", "# comment\n[1, # another\n2]"], ""), (["-nc", "\"multi\nline\""], ""), (["-nc", "\"tab\there\""], ""),
    (["-n", "."], ""), (["."], ""), (["-e", "."], ""), (["-c", "."], "   \n  "), (["-c", "."], "[1,2]\n\n[3]  "),
    (["-c", ".x"], '{"x":1.000} {"x":1e2} {"x":-0} {"x":100000000000000000001}'), (["-c", "map(. * 1)"], "[1.000, 1e2, -0]"),
    (["-S", "."], '{"b":{"y":1,"x":[{"d":0,"c":1}]},"a":null}'), (["--tab", "-S", "."], '{"b":1,"a":[{}]}'),
    (["-c", "-r", ".[]"], '["a", 1, null, "b\\nc", {"x":"y"}]'), (["-j", ".[]"], '["a", 1, null, "b"]'),
    (["-r", "@csv"], '[1,"a"] [2,"b"]'), (["-r", "@tsv"], '["a\\tb","c"]'), (["-r", ".[] | @sh"], '["it\'s", "x y"]'),
    (["-c", "tostream"], '{"a":[1,{"b":2}]}'), (["-c", "keys"], '{"b":1,"a":2,"A":3,"\u00e9":4,"":5}'),
    (["-c", "."], '{"a":1,"a":2}'), (["-c", "."], '{"b":1,"a":2,"b":3}'), (["-c", "."], '[1e1000, -1e1000, 1E-1000, 0e0, -0.0]'),
    (["-c", "."], '"\\u0000\\u001f\\u007f\\u00ff\\uffff"'), (["-ac", "."], '"\\u0000\\u001f\\u007f\\u00ff\\uffff"'),
    (["-c", "."], '"\\ud83d\\ude00\\ud83d"'), (["-c", "."], "nan"), (["-c", "[., 1]"], "[nan]"), (["-c", "."], "NaN"),
    (["-c", "."], "-"), (["-c", "."], "+1"), (["-c", "."], ".5"), (["-c", "."], "01"), (["-c", "."], "1."), (["-c", "."], "1e5"),
    (["-c", "."], "[1,,2]"), (["-c", "."], "{,}"), (["-c", "."], '{"a":}'), (["-c", "."], '{"a"}'), (["-c", "."], "[1:2]"),
    (["-c", "."], '{1:2}'), (["-c", "."], "'a'"), (["-c", "."], "[1]]"), (["-c", "."], "}"), (["-c", "."], "[\"a\" \"b\"]"),
    (["-c", "."], "truefalse"), (["-c", "."], "true false"), (["-c", "."], "null[1]"), (["-c", "."], "1 2\n3\n[4,\n5]"),
]


# ---------------------------------------------------------------- random fuzzer

def fuzz_program(rng, depth=0):
    """Type-blind random filter built from common constructs."""
    leaf = [".", ".a", ".b", ".[0]", ".[-1]", ".[]?", "..", "1", "\"s\"", "null", "true", "[]", "{}", "$x", ".a.b", ".[1:]", "length",
            "keys?", "type", "tostring", "tojson", "add?", "first?", "not", "empty", "0.5", "-2", "\"\\u00e9\"", "1.0", "100000000000000000001"]
    if depth > 3 or rng.random() < 0.25:
        return rng.choice(leaf)
    a = lambda: fuzz_program(rng, depth + 1)
    forms = [
        lambda: "%s | %s" % (a(), a()),
        lambda: "%s, %s" % (a(), a()),
        lambda: "(%s) %s (%s)" % (a(), rng.choice(["+", "-", "*", "/", "%", "==", "!=", "<", ">=", "and", "or", "//"]), a()),
        lambda: "[%s]" % a(),
        lambda: "{a: (%s), b: (%s)}" % (a(), a()),
        lambda: "if %s then %s else %s end" % (a(), a(), a()),
        lambda: "try (%s) catch ." % a(),
        lambda: "(%s)?" % a(),
        lambda: "select(%s)" % a(),
        lambda: "map(%s)?" % a(),
        lambda: "reduce (%s) as $x (%s; %s)" % (a(), a(), a()),
        lambda: "[foreach (%s) as $x (%s; %s)]" % (a(), a(), a()),
        lambda: "[limit(%d; %s)]" % (rng.randint(0, 3), a()),
        lambda: "first(%s)" % a(),
        lambda: "(%s) as $x | %s" % (a(), a()),
        lambda: "\"<\\(%s)>\"" % a(),
        lambda: "[paths]",
        lambda: "path(%s)?" % rng.choice([".a", ".[0]", "..", ".a[]?", ".[1:]", "first(.[]?)", "select(.a?)"]),
        lambda: "(%s) |= (%s)" % (rng.choice([".a", ".[0]", ".[]?", ".a.b", ".[1:]"]), a()),
        lambda: "(%s) += (%s)" % (rng.choice([".a", ".[0]", ".[]?"]), a()),
        lambda: "del(%s)" % rng.choice([".a", ".[0]", ".[]?", ".a.b", ".[1:]"]),
        lambda: "to_entries?",
        lambda: "sort_by(%s)?" % a(),
        lambda: "group_by(%s)?" % a(),
        lambda: "unique?",
        lambda: "min_by(%s)?" % a(),
        lambda: "has(%s)?" % rng.choice(["\"a\"", "0", "1"]),
        lambda: "contains(%s)?" % a(),
        lambda: "join(\",\")?",
        lambda: "split(\"a\")?",
        lambda: "test(\"a\")?",
        lambda: "ascii_downcase?",
        lambda: "tostream",
        lambda: "getpath([\"a\", 0])?",
        lambda: "with_entries(.value |= tojson)?",
        lambda: "flatten?",
        lambda: "@base64?",
        lambda: "@csv?",
        lambda: "indices(%s)?" % rng.choice(["1", "\"a\"", "[1]"]),
        lambda: "splits(\"a\")?",
        lambda: "tojson | fromjson",
        lambda: "walk(%s)?" % a(),
        lambda: "[.[]?] | length",
        lambda: "any(%s; %s)" % (a(), a()),
        lambda: "isempty(%s)" % a(),
        lambda: "label $f | %s | ., break $f" % a(),
        lambda: "def g(f): f | f; g(%s)" % a(),
        lambda: "def h($v): [$v, .]; h(%s)" % a(),
    ]
    return rng.choice(forms)()


FUZZ_INPUTS = ['{"a":{"b":[1,2]},"b":"x"}', '[1,"a",null,[2,3],{"a":4}]', '{"a":[{"a":1},{"b":2}],"b":null}', '"abc"', "3",
               "null", '[[1,2],[3]]', '{"a":"\u00e9t\u00e9","b":[true,false]}', '[3,1,2]', '{"a":1.0,"b":1e2}', "[]", "{}"]


# ---------------------------------------------------------------- assembly


def _bucket(text):
    import hashlib
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest(), 16) % 10


def build(seed):
    rng = random.Random(seed)
    cases = []
    n = 0

    def emit(cat, args, stdin, tkey=None, fixed=False):
        nonlocal n
        cases.append({"id": "%d-%05d" % (seed, n), "category": cat, "args": args, "stdin": stdin,
                      "tkey": tkey, "fixed": fixed})
        n += 1

    for cat, fam, flags, prog in T:
        if cat == "strings" and "@" in prog:
            cat = "formats"
        if cat == "errors" and "-n" not in flags and not prog.startswith("try") and _bucket(prog) >= 3:
            prog = "try (%s) catch ." % prog
        if fam is None:
            if "-n" in flags:
                emit(cat, flags + [prog], "", prog, True)
            elif "input" in prog:
                emit(cat, flags + [prog], " ".join(DOCS[rng.choice(["scalar", "nums"])](rng) for _ in range(rng.randint(1, 3))), prog, True)
            else:
                emit(cat, flags + ["-n", prog], "", prog, True)
            continue
        for _ in range(2):
            doc = DOCS[fam](rng)
            emit(cat, flags + [prog], doc, prog, False)
    for args, stdin in CLI:
        emit("cli", list(args), stdin, json.dumps([args, stdin]), True)
    for _ in range(900):
        prog = fuzz_program(rng)
        mode = rng.random()
        flags = ["-c"]
        if "$x" in prog and "as $x" not in prog:
            flags += ["--argjson", "x", rng.choice(["1", '"q"', "[1,2]", '{"a":null}', "null"])]
        emit("fuzz", flags + [prog], rng.choice(FUZZ_INPUTS) + (" " + rng.choice(FUZZ_INPUTS) if mode < 0.2 else ""))
    return cases


def main():
    seed = int(sys.argv[1])
    out = open(sys.argv[2], "w")
    for c in build(seed):
        out.write(json.dumps(c, ensure_ascii=True) + "\n")


if __name__ == "__main__":
    main()
