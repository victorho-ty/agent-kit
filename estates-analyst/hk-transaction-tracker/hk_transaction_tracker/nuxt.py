"""Reading the data Centanet already sent us.

The 成交 list is a Nuxt page, and Nuxt is server-rendered: by the time the HTML
arrives, every transaction on the page is already in it, inside a
``window.__NUXT__`` assignment near the end of the document. So this package
needs no browser, no JavaScript engine and no scraping of the rendered DOM --
one plain HTTP GET and this decoder produce structured records with the
saleable area, the unit price and the sale/rental flag already separated.

That is worth stating plainly because the obvious approach is the wrong one
here: the visible table is built by Vue from this payload, so CSS selectors
against the served HTML match nothing at all, and a headless browser would be
paying Chromium to hand back data that was already in the response.

The payload is minified into a function of a few hundred single-letter
parameters, called with the literal values::

    window.__NUXT__=(function(a,b,c,...){return {state:{...,count:c,...}}}(false,true,0,...))

so ``count:c`` means ``count:0``. Decoding is therefore: read the parameter
names, read the argument literals, and evaluate the returned object literal
against that symbol table. It is a JSON parser with three extra cases --
identifiers, ``void 0`` and ``Array(n)`` -- and no evaluation of anything else.
Nothing from the page is ever passed to ``eval``.

The fragile part is the minifier, not the shape of the data: if Centanet changes
build tooling the symbol table stops resolving, and that surfaces here as an
unknown identifier rather than as silence. Every failure in this module is
ERR_PARSE, which is the one the agent is told to report.
"""

from __future__ import annotations

import json

from .errors import ParseError

MARKER = "window.__NUXT__="
BACKSLASH = chr(92)
_LITERALS = {"true": True, "false": False, "null": None, "undefined": None, "NaN": None}
_WHITESPACE = " \t\r\n"
# Enough of the surrounding text to see what changed, without pasting a
# 200KB page into an error payload.
_CONTEXT = 80


def _skip_string(text: str, index: int) -> int:
    """Index just past the string literal starting at ``index``."""
    quote = text[index]
    index += 1
    while index < len(text):
        char = text[index]
        if char == BACKSLASH:
            index += 2
            continue
        if char == quote:
            return index + 1
        index += 1
    raise ParseError("unterminated string in the __NUXT__ payload", offset=index)


def _match_bracket(text: str, start: int, opener: str, closer: str) -> int:
    """Index of the bracket matching the one at ``start``, string-aware.

    Counting brackets without skipping string literals is the classic way to
    mis-parse this payload: estate names and addresses contain parentheses, and
    one of them would close the function early and take the argument list with
    it.
    """
    if text[start] != opener:
        raise ParseError(f"expected {opener!r} in the __NUXT__ payload", offset=start)
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char in "\"'":
            index = _skip_string(text, index)
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ParseError(f"unbalanced {opener!r} in the __NUXT__ payload", offset=start)


class _Reader:
    """A JSON reader that also resolves the minifier's identifiers."""

    def __init__(self, text: str, symbols: dict):
        self.text = text
        self.index = 0
        self.symbols = symbols

    # -- plumbing ---------------------------------------------------------

    def _fail(self, message: str):
        start = max(0, self.index - _CONTEXT)
        raise ParseError(
            message,
            offset=self.index,
            context=self.text[start:self.index + _CONTEXT],
        )

    def _peek(self) -> str:
        if self.index >= len(self.text):
            self._fail("the __NUXT__ payload ended mid-value")
        return self.text[self.index]

    def _space(self) -> None:
        while self.index < len(self.text) and self.text[self.index] in _WHITESPACE:
            self.index += 1

    # -- values -----------------------------------------------------------

    def value(self):
        self._space()
        char = self._peek()
        if char == "{":
            return self.mapping()
        if char == "[":
            return self.sequence()
        if char in "\"'":
            return self.string()
        if char == "-" or char.isdigit() or char == ".":
            return self.number()
        return self.identifier()

    def mapping(self) -> dict:
        self.index += 1
        out: dict = {}
        self._space()
        if self._peek() == "}":
            self.index += 1
            return out
        while True:
            self._space()
            key = self.string() if self._peek() in "\"'" else self.bare_key()
            self._space()
            if self._peek() != ":":
                self._fail(f"expected ':' after key {key!r}")
            self.index += 1
            out[key] = self.value()
            self._space()
            char = self._peek()
            self.index += 1
            if char == ",":
                continue
            if char == "}":
                return out
            self._fail("expected ',' or '}' in an object")

    def sequence(self) -> list:
        self.index += 1
        out: list = []
        self._space()
        if self._peek() == "]":
            self.index += 1
            return out
        while True:
            out.append(self.value())
            self._space()
            char = self._peek()
            self.index += 1
            if char == ",":
                continue
            if char == "]":
                return out
            self._fail("expected ',' or ']' in an array")

    def bare_key(self) -> str:
        start = self.index
        while self.index < len(self.text) and self.text[self.index] not in ":" + _WHITESPACE:
            self.index += 1
        if self.index == start:
            self._fail("expected an object key")
        return self.text[start:self.index]

    def string(self) -> str:
        start = self.index
        quote = self.text[start]
        self.index = _skip_string(self.text, start)
        raw = self.text[start:self.index]
        try:
            if quote == "'":
                # Single-quoted: re-quote before handing to the JSON decoder,
                # which is what actually understands the / escapes the
                # minifier uses for every forward slash in a URL.
                return json.loads('"' + raw[1:-1].replace('"', BACKSLASH + '"') + '"')
            return json.loads(raw)
        except json.JSONDecodeError:
            self._fail("a string literal in the __NUXT__ payload is not decodable")

    def number(self):
        start = self.index
        while self.index < len(self.text) and self.text[self.index] in "-+.0123456789eE":
            self.index += 1
        raw = self.text[start:self.index]
        try:
            return float(raw) if ("." in raw or "e" in raw.lower()) else int(raw)
        except ValueError:
            self._fail(f"not a number: {raw!r}")

    # -- statements -------------------------------------------------------

    def program(self):
        """The function body: a prelude of assignments, then one return.

        The prelude is how the minifier expresses a value that appears in more
        than one place. ``ci[0]=E;`` patches an argument that was passed in as a
        hole, and because the arguments are already Python objects the patch is
        applied by reference, exactly as the browser would. Skipping the prelude
        would leave those holes empty -- a silent, partial payload, which is the
        worst of the available failures.
        """
        while True:
            self._space()
            if self.text.startswith("return", self.index):
                self.index += len("return")
                return self.value()
            self.assignment()

    def _name(self) -> str:
        start = self.index
        while self.index < len(self.text) and (
            self.text[self.index].isalnum() or self.text[self.index] in "_$"
        ):
            self.index += 1
        return self.text[start:self.index]

    def assignment(self) -> None:
        name = self._name()
        if not name:
            self._fail("expected an assignment or 'return' in the __NUXT__ function body")
        if name not in self.symbols:
            self._fail(f"assignment to unknown symbol {name!r}")

        keys: list = []
        while True:
            self._space()
            char = self._peek()
            if char == "[":
                self.index += 1
                keys.append(self.value())
                self._space()
                if self._peek() != "]":
                    self._fail("expected ']' in an assignment target")
                self.index += 1
            elif char == ".":
                self.index += 1
                key = self._name()
                if not key:
                    self._fail("expected a property name after '.'")
                keys.append(key)
            else:
                break

        if not keys:
            self._fail(
                f"the __NUXT__ prelude rebinds {name!r} wholesale, which this reader "
                "does not model"
            )

        self._space()
        if self._peek() != "=":
            self._fail(f"expected '=' after the assignment target {name!r}")
        self.index += 1
        value = self.value()

        target = self.symbols[name]
        try:
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
        except (TypeError, IndexError, KeyError) as exc:
            self._fail(f"could not apply the __NUXT__ prelude assignment to {name!r}: {exc}")

        self._space()
        if self._peek() == ";":
            self.index += 1

    def identifier(self):
        start = self.index
        while self.index < len(self.text) and (
            self.text[self.index].isalnum() or self.text[self.index] in "_$"
        ):
            self.index += 1
        name = self.text[start:self.index]
        if not name:
            self._fail("expected a value")
        if name == "void":
            self._space()
            self.number()  # void 0
            return None
        if name == "Array":
            # Array(0) / Array(1): a hole the minifier could not spell.
            self._space()
            if self._peek() != "(":
                self._fail("expected '(' after Array")
            self.index += 1
            length = self.number()
            self._space()
            if self._peek() != ")":
                self._fail("expected ')' after Array(n")
            self.index += 1
            return [None] * int(length)
        if name in _LITERALS:
            return _LITERALS[name]
        if name in self.symbols:
            return self.symbols[name]
        self._fail(
            f"unknown identifier {name!r} in the __NUXT__ payload -- "
            "the page's build tooling has probably changed"
        )


def decode(html: str) -> dict:
    """The ``window.__NUXT__`` object, as plain Python.

    Raises :class:`ParseError` for every way this can go wrong, because every
    one of them means the page no longer looks the way this code expects and
    the operator needs to hear about it.
    """
    marker = html.find(MARKER)
    if marker < 0:
        raise ParseError(
            "no window.__NUXT__ payload in the page -- "
            "this may not be a Centanet transaction list URL",
            bytes_received=len(html),
        )

    opener = html.find("function(", marker)
    if opener < 0:
        raise ParseError("the __NUXT__ payload is not the expected minified function")

    params_open = opener + len("function(") - 1
    params_close = _match_bracket(html, params_open, "(", ")")
    params = [name.strip() for name in html[params_open + 1:params_close].split(",")]

    body_open = html.find("{", params_close)
    if body_open < 0:
        raise ParseError("the __NUXT__ function has no body")
    body_close = _match_bracket(html, body_open, "{", "}")

    args_open = html.find("(", body_close)
    if args_open < 0:
        raise ParseError("the __NUXT__ function is never called")
    args_close = _match_bracket(html, args_open, "(", ")")
    arguments = _Reader("[" + html[args_open + 1:args_close] + "]", {}).sequence()

    if len(params) != len(arguments):
        raise ParseError(
            "the __NUXT__ symbol table does not line up",
            parameters=len(params), arguments=len(arguments),
        )

    reader = _Reader(html[body_open + 1:body_close], dict(zip(params, arguments)))
    payload = reader.program()
    if not isinstance(payload, dict):
        raise ParseError("the __NUXT__ payload is not an object", got=type(payload).__name__)
    return payload
