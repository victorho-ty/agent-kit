"""The bundle as an MCP client.

The desk's news, sentiment and macro all come from MCP servers. The question is
only *who holds the client end of the pipe*, and the answer here is Python
rather than the agent -- which is the single decision that makes MCP-sourced
news affordable at all.

Measured on this watchlist: one ``get_news`` call returns about 6,400 tokens of
JSON for ten stories, of which title, url, date and provider -- everything the
desk actually stores -- is roughly 15%. One ``news_sentiment`` call returns
28,000. Ten symbols twice a day is ~128k tokens of intake before a single word
is written. Routed through the agent that lands in context every run; routed
through here it lands in SQLite once, and the agent reads the same ~2k report it
always did.

So nothing in this module returns a payload to a caller who will show it to a
model. Callers parse, filter and store; :mod:`stock_desk.report` decides what
little of it is worth saying.

## Sessions, and why calls are batched

Cold-starting a server through ``uvx`` costs ten to sixty seconds -- resolving
the environment, not running the tool. Per-call spawning would dominate the
runtime of a poll. :func:`call_batch` therefore spawns once and issues every
call for that server inside one session, which is exactly the shape the pollers
want: all tickers, one server, one process.

## Why the working directory is pinned to the bundle root

``alphavantage-mcp`` reads its own version with a bare ``open("pyproject.toml")``
(``server.py:2154``) -- a relative path, resolved against whatever directory the
*launching* process happens to be in. Spawned from anywhere without one it dies
at import, before the protocol handshake, with a ``FileNotFoundError`` that
looks nothing like the packaging bug it is. The bundle root has a
``pyproject.toml``, so that is where servers are started from.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Iterable, Sequence

from .. import settings
from ..errors import ConfigError, FetchError

# ``${VAR}`` inside a .mcp.json env value. Claude Code expands these; so must we,
# or the bundle and the interactive agent disagree about which key is in use.
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

BUNDLE_ROOT = Path(__file__).resolve().parents[2]

# The one server with a request-rate limit as well as a daily cap.
METERED = "alphavantage"


def _expand(value: str) -> str:
    return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)


def server_specs(path: Path | None = None) -> dict[str, dict]:
    """Read ``.mcp.json`` and return one spec per configured server.

    One source of truth, deliberately: the interactive agent and this bundle
    read the same file, so a pin or a workaround applied for one is applied for
    both. A spec whose ``env`` references an unset variable is *kept*, not
    dropped -- the server itself gives a far better error than a missing-key
    guess made here.
    """
    location = settings.mcp_config_path() if path is None else Path(path)
    if not location.exists():
        raise ConfigError(
            f"no MCP config at {location}",
            path=str(location),
            remedy="copy .mcp.json.example to .mcp.json and fill in the keys",
        )
    try:
        raw = json.loads(location.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{location} is not valid JSON: {exc.msg} at line {exc.lineno}",
            path=str(location),
        ) from exc

    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        raise ConfigError(f"{location} has no mcpServers object", path=str(location))

    specs: dict[str, dict] = {}
    for name, entry in servers.items():
        command = entry.get("command")
        if not command:
            continue
        specs[name] = {
            "command": command,
            "args": [str(a) for a in entry.get("args", [])],
            "env": {k: _expand(str(v)) for k, v in (entry.get("env") or {}).items()},
        }
    return specs


def available(path: Path | None = None) -> set[str]:
    """Servers configured *and* holding every credential they declare.

    Used by the callers to degrade rather than fail: with no Alpha Vantage key
    the desk still reports setups and Yahoo news, and says which reading is
    missing instead of inventing one.
    """
    try:
        specs = server_specs(path)
    except ConfigError:
        return set()
    return {
        name
        for name, spec in specs.items()
        if all(value for value in spec["env"].values())
    }


_SECRET_CACHE: list[str] | None = None


def secrets(path: Path | None = None) -> tuple[str, ...]:
    """Every credential value configured for any server, for scrubbing.

    Read once and cached: this is called on every error path, and re-reading
    `.mcp.json` to format an error message would be its own small disaster.
    """
    global _SECRET_CACHE
    if _SECRET_CACHE is None:
        found: set[str] = set()
        try:
            for spec in server_specs(path).values():
                for value in spec["env"].values():
                    # Short values are not credentials and would scrub real
                    # words out of legitimate error text.
                    if value and len(value) >= 8:
                        found.add(value)
        except ConfigError:
            pass
        _SECRET_CACHE = sorted(found, key=len, reverse=True)
    return tuple(_SECRET_CACHE)


def redact(text: str, path: Path | None = None) -> str:
    """Scrub credentials out of text that is about to be shown to somebody.

    Not paranoia. Alpha Vantage's own quota message quotes the key back at you
    -- "We have detected your API key as XXXXXXXXXXXXXXXX and our standard API
    rate limit is..." -- and that string travels from the failure list into the
    report payload, into the model's context, and out to a Telegram chat. The
    vendor decided to put the secret in the error; this decides it stops here.
    """
    if not text:
        return text
    for secret in secrets(path):
        text = text.replace(secret, "***REDACTED***")
    return text


def _content_text(result: Any) -> str:
    """The text of a tool result, or a FetchError naming what the server said."""
    blocks = getattr(result, "content", None) or []
    text = "\n".join(
        getattr(block, "text", "") for block in blocks if getattr(block, "text", None)
    )
    if getattr(result, "isError", False):
        safe = redact(text)[:300]
        raise FetchError(f"mcp tool reported an error: {safe}", detail=safe)
    return text


def _decode(text: str) -> Any:
    """Parse JSON when the server sent JSON, otherwise hand back the string.

    Servers vary: ``mcp-yahoo-finance`` returns ``json.dumps(...)`` inside a text
    block, ``alphavantage-mcp`` returns the vendor's JSON the same way, and some
    tools genuinely return prose. Guessing wrong in either direction is worse
    than letting the caller see what arrived.
    """
    stripped = text.strip()
    if not stripped:
        return None
    if stripped[0] in "[{":
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
    return stripped


@asynccontextmanager
async def _session(spec: dict, timeout: float):
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise FetchError(
            "the mcp client library is not installed; run uv sync",
        ) from exc

    params = StdioServerParameters(
        command=spec["command"],
        args=spec["args"],
        # Merged, not replaced: uvx needs PATH, HOME and its own cache variables,
        # and a spec's env carries only the few keys the server itself declares.
        env={**os.environ, **spec["env"]},
        cwd=str(BUNDLE_ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=timeout)
            yield session


async def _run_batch(
    spec: dict, calls: Sequence[tuple[str, dict]], timeout: float, delay: float
) -> list[Any]:
    results: list[Any] = []
    async with _session(spec, timeout) as session:
        for index, (tool, arguments) in enumerate(calls):
            # Alpha Vantage's free tier answers a burst with an HTTP 200 whose
            # body is "please spread out your free API requests more sparingly
            # (1 request per second)" -- no error status, no retry header, and
            # the call still counts against the daily 25. Observed live: three
            # of six back-to-back news calls came back as that message. A delay
            # here is cheaper than the wasted quota.
            if index and delay:
                await asyncio.sleep(delay)
            raw = await asyncio.wait_for(
                session.call_tool(tool, arguments), timeout=timeout
            )
            results.append(_decode(_content_text(raw)))
    return results


def call_batch(
    server: str,
    calls: Sequence[tuple[str, dict]],
    path: Path | None = None,
    timeout: float | None = None,
    delay: float | None = None,
) -> list[Any]:
    """Run every call against one server in a single session, in order.

    Returns one entry per call. Raises :class:`FetchError` for the whole batch
    rather than returning partial results: a half-finished poll that looks
    complete is how a ticker silently stops being watched. Callers that want
    per-ticker tolerance batch per ticker and catch around each.
    """
    if not calls:
        return []
    specs = server_specs(path)
    if server not in specs:
        raise ConfigError(
            f"no MCP server named {server!r} is configured",
            server=server,
            configured=sorted(specs),
        )
    limit = settings.mcp_timeout() if timeout is None else timeout
    # Metered servers get spaced out; free ones do not pay for the wait.
    pause = (settings.request_delay() if server == METERED else 0.0) if delay is None else delay
    try:
        return asyncio.run(_run_batch(specs[server], list(calls), limit, pause))
    except FetchError:
        raise
    except asyncio.TimeoutError as exc:
        raise FetchError(
            f"{server} did not answer within {limit:.0f}s",
            server=server,
            timeout=limit,
        ) from exc
    except Exception as exc:  # noqa: BLE001 - the transport raises many shapes
        raise FetchError(
            redact(f"{server} could not be reached: {type(exc).__name__}: {exc}"),
            server=server,
        ) from exc


def call(server: str, tool: str, arguments: dict, **kwargs) -> Any:
    """One call. Prefer :func:`call_batch` in a loop -- the spawn dominates."""
    return call_batch(server, [(tool, arguments)], **kwargs)[0]


def list_tools(server: str, path: Path | None = None) -> list[str]:
    """Tool names a server exposes. For diagnostics, not for the hot path."""

    async def _go() -> list[str]:
        async with _session(server_specs(path)[server], settings.mcp_timeout()) as s:
            return [t.name for t in (await s.list_tools()).tools]

    try:
        return asyncio.run(_go())
    except Exception as exc:  # noqa: BLE001
        raise FetchError(
            f"{server} could not be reached: {type(exc).__name__}: {exc}", server=server
        ) from exc
