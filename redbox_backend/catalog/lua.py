"""The Lua table literals that profile.data stores as values, e.g.

    { product_id = 1000 , starring = { "Tony Jaa" , "Wu Jing" } , genres = { [1000] = true } , banner_id = nil }

`decode` reads what the engine's `ParseLuaValue` (software/src/Rental/Redbox.Rental.Model/ParseLuaValue.cs) reads,
`encode` writes Redbox's own layout back, so `encode(decode(v)) == v` for every value in a real profile.data
(tests/test_catalog.py). The Python form is JSON-ready:

  * a table with keys -> dict in file order; `[1000] = x` becomes key "1000", `name = x` key "name";
  * a table of bare items -> list; items keep their type (`{ 1878 }` -> [1878], `{ "a" }` -> ["a"]);
  * `nil` -> None, numbers -> int / Decimal (keeps `0.00` as written; `plain` turns them into floats);
  * strings: like the engine, only `\\"` (a quote) and `\\r\\n` (a line break) are escapes; any other
    backslash is text (descriptions hold `\\t` and `\\\\` that the kiosk shows as written);
  * empty tables: Redbox writes `{  }` for empty lists (directors, starring) and `{}` for an empty map
    (a ProductFamily's genres), so `{}` -> {} and `{ }`/`{  }` -> [], and back.
"""
import decimal
import math
import re

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_NUMBER = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


class LuaError(ValueError):
    pass


class _Reader:
    def __init__(self, text):
        self.s = text
        self.i = 0

    def ws(self):
        s, n = self.s, len(self.s)
        while self.i < n:
            c = s[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif s.startswith("--", self.i):
                j = s.find("\n", self.i)
                self.i = n if j < 0 else j + 1
            else:
                break

    def peek(self):
        self.ws()
        return self.s[self.i] if self.i < len(self.s) else ""

    def expect(self, ch):
        if self.peek() != ch:
            raise LuaError(f"expected {ch!r} at {self.i}: {self.s[self.i:self.i + 30]!r}")
        self.i += 1

    def value(self):
        c = self.peek()
        if c == "{":
            return self.table()
        if c in "\"'":
            return self.string()
        m = _NUMBER.match(self.s, self.i)
        if m:
            self.i = m.end()
            t = m.group()
            return decimal.Decimal(t) if any(x in t for x in ".eE") else int(t)
        m = re.compile(r"[A-Za-z_][A-Za-z0-9_]*").match(self.s, self.i)
        if m:
            word = m.group()
            self.i = m.end()
            low = word.lower()
            if low in ("nil", "null"):
                return None
            if low in ("true", "false"):
                return low == "true"
            raise LuaError(f"unexpected word {word!r}")
        raise LuaError(f"unexpected {c!r} at {self.i}")

    def string(self):
        q = self.s[self.i]
        self.i += 1
        out = []
        s = self.s
        while True:
            j = self.i
            while j < len(s) and s[j] != q and s[j] != "\\":
                j += 1
            out.append(s[self.i:j])
            if j >= len(s):
                raise LuaError("unterminated string")
            if s[j] == q:
                self.i = j + 1
                return "".join(out)
            if s.startswith('\\"', j):
                out.append('"')
                self.i = j + 2
            elif s.startswith("\\r\\n", j):
                out.append("\r\n")
                self.i = j + 4
            else:
                out.append("\\")
                self.i = j + 1

    def table(self):
        self.expect("{")
        if self.s.startswith("}", self.i):
            self.i += 1
            return {}           # `{}`: an empty map (ProductFamily genres)
        keyed, items = {}, []
        while self.peek() != "}":
            c = self.peek()
            if c == "[":
                self.i += 1
                k = self.value()
                self.expect("]")
                self.expect("=")
                keyed[str(k)] = self.value()
            else:
                m = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)").match(self.s, self.i)
                if m and m.group(1).lower() not in ("nil", "true", "false"):
                    self.i = m.end()
                    keyed[m.group(1)] = self.value()
                else:
                    items.append(self.value())
            if self.peek() in ",;":
                self.i += 1
            elif self.peek() != "}":
                raise LuaError(f"expected ',' or '}}' at {self.i}: {self.s[self.i:self.i + 30]!r}")
        self.i += 1
        if items and keyed:
            raise LuaError("mixed list and keyed table")
        return keyed if keyed else items   # `{  }`: an empty list (directors, starring)


def decode(text):
    """Parse one value (normally a table). Raises LuaError."""
    r = _Reader(text)
    v = r.value()
    if r.peek():
        raise LuaError(f"trailing text at {r.i}")
    return v


def _string(s):
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\r\\n")
    return '"' + s.replace('"', '\\"') + '"'


def _number(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        raise LuaError("NaN/inf cannot be written")
    if isinstance(v, decimal.Decimal):
        if not v.is_finite():
            raise LuaError("NaN/inf cannot be written")
        return str(v) if "E" not in str(v) else format(v, "f")
    return repr(v) if isinstance(v, float) else str(v)


def _key(k):
    if re.fullmatch(r"\d+", k):
        return f"[{k}]"
    if _IDENT.match(k) and k.lower() not in ("nil", "true", "false", "null"):
        return k
    return "[" + _string(k) + "]"


def encode(v):
    """Write a value in Redbox's layout: `{ a = 1 , b = { "x" , "y" } }`, `{ }` for an empty table."""
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float, decimal.Decimal)):
        return _number(v)
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, dict):
        parts = [f"{_key(str(k))} = {encode(x)}" for k, x in v.items()]
    elif isinstance(v, (list, tuple)):
        parts = [encode(x) for x in v]
    else:
        raise LuaError(f"cannot write {type(v).__name__}")
    if not parts:
        return "{}" if isinstance(v, dict) else "{  }"
    return "{ " + " , ".join(parts) + " }"


def plain(v):
    """`v` with Decimals as floats (ints when whole), for JSON and the API."""
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() and "." not in str(v) else float(v)
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [plain(x) for x in v]
    return v