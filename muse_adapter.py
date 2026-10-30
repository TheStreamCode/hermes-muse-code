"""Muse Code request adapter: make subscription traffic look like Muse Code's own tool declarations.

Meta's ``api.meta.ai`` refuses a Muse Code subscription request when it declares a
flat function tool whose name is exactly one of Muse Code's builtin tool names
(``read_file``, ``bash``, ``web_search``, ...). The refusal is HTTP 403 with
``code: client_attestation_denied``. Names that do not collide are accepted, and a
colliding tool declared under a different name is accepted too.

This module runs a loopback reverse proxy in front of ``api.meta.ai``. It aliases
only the colliding tool declarations on the way out and restores them on the way
back, so Hermes keeps its own tool names. Nothing else about the request is
rewritten except the parameter normalization the Meta Responses wire also needs.

The file is standard-library only and deliberately host-agnostic: the sibling
Muse plugins vendor the same rules. Keep it in sync with ``ADAPTER_CONTRACT.md``
and bump ``ADAPTER_VERSION`` with that file.

No credential, request body or response body is ever logged or persisted: the
optional diagnostic line carries only the method, the path, the upstream status
and the names of the rules that fired.
"""

from __future__ import annotations

import atexit
import codecs
import http.server
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

# Version of the wire rules below; bump with ADAPTER_CONTRACT.md.
ADAPTER_VERSION = 3

# Default upstream origin; tests override it with a local fake.
DEFAULT_UPSTREAM = "https://api.meta.ai"

# Muse Code builtin tool names. Reconstructed from the 1.4.14 binary's tool
# catalog and from CLI captures, then probed one by one against api.meta.ai:
# a flat client tool with one of these exact names is refused with 403
# client_attestation_denied. The catalog includes goal, cron, session and patch
# tools as well as the file, shell and web families. tool_search stays in the
# set defensively: one probe returned 200, field reports see a 403.
COLLIDING_TOOL_NAMES = frozenset(
    {
        "workflow",
        "read_file",
        "write_file",
        "edit_file",
        "bash",
        "bash_input",
        "search",
        "web_search",
        "web_fetch",
        "powershell",
        "powershell_input",
        "read_memory",
        "add_memory",
        "edit_memory",
        "read_skill",
        "write_todos",
        "monitor",
        "work_status",
        "work_list",
        "work_stop",
        "snooze_reminder",
        "apply_patch",
        "shell",
        "create_goal",
        "get_goal",
        "update_goal",
        "report_progress",
        "request_user_input",
        "cron_create",
        "cron_delete",
        "cron_list",
        "send_session_message",
        "list_peer_sessions",
        "subagent_send_message",
        "subagent_cancel",
        "subagent_spawn",
        "subagent_status",
        "subagent_wait",
        "subagent_read_result",
        "tool_search",
    }
)

# Prefix used for aliases; kept outside the builtin set.
ALIAS_PREFIX = "client_"

# Environment variable that turns the wire rules off while keeping the proxy.
PASSTHROUGH_ENV_VAR = "MUSE_CODE_ADAPTER"

# Requests larger than this are refused (images, huge prompts).
MAX_BODY_BYTES = 32 * 1024 * 1024

# Ports tried in order, so a persisted base URL stays stable across restarts.
DEFAULT_PORTS = (43219, 43220, 43221, 43222)

_JsonRecord = dict[str, Any]


def is_adapted_path(pathname: str) -> bool:
    """Return True for the paths whose JSON body and response are adapted."""
    return pathname.endswith("/responses") or pathname.endswith("/chat/completions")


def _is_record(value: object) -> bool:
    return isinstance(value, dict)


@dataclass(frozen=True)
class AliasPlan:
    """One alias plan: original name to wire name, plus the reverse map."""

    aliases: Mapping[str, str]
    reverse: Mapping[str, str]


def plan_tool_aliases(tools: object, all_tools: bool = False) -> AliasPlan:
    """Decide which declared tool names must be aliased.

    Only flat ``function`` tools are considered: those are the declarations Meta
    compares against its builtin set. A namespace container is left untouched.
    With ``all_tools``, every flat function tool is aliased; the proxy uses that
    as a one-shot retry when the gate refuses names this release does not know.
    """
    aliases: dict[str, str] = {}
    if not isinstance(tools, list):
        return AliasPlan(aliases=aliases, reverse={})
    taken = {
        tool["name"]
        for tool in tools
        if _is_record(tool) and isinstance(tool.get("name"), str)
    }
    for tool in tools:
        if not _is_record(tool) or tool.get("type") != "function":
            continue
        name = tool.get("name")
        if not isinstance(name, str):
            continue
        if not all_tools and name not in COLLIDING_TOOL_NAMES:
            continue
        alias = f"{ALIAS_PREFIX}{name}"
        suffix = 2
        while alias in taken:
            alias = f"{ALIAS_PREFIX}{name}_{suffix}"
            suffix += 1
        taken.add(alias)
        aliases[name] = alias
    return AliasPlan(aliases=aliases, reverse={value: key for key, value in aliases.items()})


def _alias_tool(tool: object, plan: AliasPlan) -> object:
    if not _is_record(tool) or tool.get("type") != "function":
        return tool
    name = tool.get("name")
    if not isinstance(name, str):
        return tool
    alias = plan.aliases.get(name)
    return tool if alias is None else {**tool, "name": alias}


def _alias_item(item: object, plan: AliasPlan) -> object:
    if not _is_record(item) or item.get("type") != "function_call":
        return item
    name = item.get("name")
    if not isinstance(name, str):
        return item
    alias = plan.aliases.get(name)
    return item if alias is None else {**item, "name": alias}


def _apply_aliases(body: _JsonRecord, plan: AliasPlan) -> None:
    """Rewrite the names of the tools, history calls and forced tool choice."""
    if not plan.aliases:
        return
    tools = body.get("tools")
    if isinstance(tools, list):
        body["tools"] = [_alias_tool(tool, plan) for tool in tools]
    history = body.get("input")
    if isinstance(history, list):
        body["input"] = [_alias_item(item, plan) for item in history]
    choice = body.get("tool_choice")
    if _is_record(choice) and choice.get("type") == "function":
        name = choice.get("name")
        if isinstance(name, str):
            alias = plan.aliases.get(name)
            if alias is not None:
                body["tool_choice"] = {**choice, "name": alias}


@dataclass(frozen=True)
class AdaptedRequest:
    """Result of adapting a request body: the body, its plan and a log line."""

    body: _JsonRecord
    plan: AliasPlan
    notes: tuple[str, ...]


def transform_request_body(body: _JsonRecord, plan: AliasPlan | None = None) -> AdaptedRequest:
    """Apply the wire rules to one Responses/Chat request body.

    The input object is not mutated: a shallow copy is returned. Unknown fields
    are preserved untouched. Pass a plan from :func:`plan_tool_aliases` to reuse
    a mapping, for example the full-aliasing retry.
    """
    next_body: _JsonRecord = dict(body)
    notes: list[str] = []

    chosen = plan if plan is not None else plan_tool_aliases(next_body.get("tools"))
    if chosen.aliases:
        _apply_aliases(next_body, chosen)
        notes.append(f"aliased {len(chosen.aliases)} tool name(s)")

    if "tool_choice" in next_body and next_body["tool_choice"] != "auto":
        # Meta accepts only "auto"; named, "none" and "required" are rejected.
        next_body["tool_choice"] = "auto"
        notes.append("tool_choice=auto")

    effort = next_body.get("reasoning_effort")
    if isinstance(effort, str):
        reasoning = next_body.get("reasoning")
        reasoning = dict(reasoning) if _is_record(reasoning) else {}
        existing = reasoning.get("effort")
        reasoning["effort"] = effort if existing is None else existing
        next_body["reasoning"] = reasoning
        del next_body["reasoning_effort"]
        notes.append("reasoning_effort->reasoning.effort")

    max_output = next_body.get("max_output_tokens")
    if (
        isinstance(max_output, (int, float))
        and not isinstance(max_output, bool)
        and max_output < 16
    ):
        next_body["max_output_tokens"] = 16
        notes.append("max_output_tokens>=16")

    include = next_body.get("include")
    include = list(include) if isinstance(include, list) else []
    if "reasoning.encrypted_content" not in include:
        next_body["include"] = [*include, "reasoning.encrypted_content"]
        notes.append("include+reasoning.encrypted_content")

    return AdaptedRequest(body=next_body, plan=chosen, notes=tuple(notes))


def _restore_item(item: object, reverse: Mapping[str, str]) -> None:
    """Restore an aliased name on one wire object, in place."""
    if not _is_record(item):
        return
    if item.get("type") not in ("function_call", "function"):
        return
    name = item.get("name")
    if not isinstance(name, str):
        return
    original = reverse.get(name)
    if original is not None:
        item["name"] = original


class SseRestorer:
    """Streaming name restorer.

    Feed complete SSE lines and get the line to relay. It restores aliased
    function-call names and repairs empty ``arguments`` from the streamed
    deltas: Meta's authoritative ``response.output_item.done`` sometimes ships
    "" even though the deltas carried the JSON.
    """

    def __init__(self, reverse: Mapping[str, str]) -> None:
        self._reverse = dict(reverse)
        self._arguments: dict[str, str] = {}

    def feed(self, line: str) -> str:
        """Adapt one complete SSE line (without the trailing newline)."""
        trimmed = line[:-1] if line.endswith("\r") else line
        if not trimmed.startswith("data:"):
            return line
        payload = trimmed[5:].strip()
        if payload == "" or payload == "[DONE]":
            return line
        try:
            parsed = json.loads(payload)
        except ValueError:
            return line
        if not isinstance(parsed, dict):
            return line
        self._adapt(parsed)
        suffix = "\r" if line.endswith("\r") else ""
        encoded = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
        return f"data: {encoded}{suffix}"

    def _adapt(self, event: _JsonRecord) -> None:
        # `response.function_call_arguments.done` echoes the tool name at the
        # top level.
        top_name = event.get("name")
        if isinstance(top_name, str):
            original = self._reverse.get(top_name)
            if original is not None:
                event["name"] = original
        event_type = event.get("type")
        if event_type == "response.function_call_arguments.delta":
            item_id = event.get("item_id")
            delta = event.get("delta")
            if isinstance(item_id, str) and isinstance(delta, str):
                self._arguments[item_id] = self._arguments.get(item_id, "") + delta
        if event_type in ("response.output_item.added", "response.output_item.done"):
            item = event.get("item")
            self._collect_arguments(item)
            _restore_item(item, self._reverse)
            self._repair_arguments(item)
        response = event.get("response")
        if _is_record(response):
            self._adapt_response(response)

    def _adapt_response(self, response: _JsonRecord) -> None:
        """Restore names in an emitted response object (tools echo and output)."""
        tools = response.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                _restore_item(tool, self._reverse)
        output = response.get("output")
        if not isinstance(output, list):
            return
        for item in output:
            self._collect_arguments(item)
            _restore_item(item, self._reverse)
            self._repair_arguments(item)

    def _collect_arguments(self, item: object) -> None:
        if not _is_record(item) or item.get("type") != "function_call":
            return
        item_id = item.get("id")
        arguments = item.get("arguments")
        if isinstance(item_id, str) and isinstance(arguments, str) and arguments != "":
            self._arguments[item_id] = arguments

    def _repair_arguments(self, item: object) -> None:
        if not _is_record(item) or item.get("type") != "function_call":
            return
        item_id = item.get("id")
        if not isinstance(item_id, str):
            return
        arguments = item.get("arguments")
        if arguments is not None and arguments != "":
            return
        known = self._arguments.get(item_id)
        if known is not None and known != "":
            item["arguments"] = known


def transform_response_body(body: _JsonRecord, reverse: Mapping[str, str]) -> _JsonRecord:
    """Restore aliased names in a complete JSON response, in place."""
    output = body.get("output")
    if isinstance(output, list):
        for item in output:
            _restore_item(item, reverse)
    tools = body.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            _restore_item(tool, reverse)
    return body


_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

# Headers not relayed back to the host: hop-by-hop, recomputed framing, and the
# server/date pair the stdlib response already writes.
_RESPONSE_SKIP = _HOP_BY_HOP | {
    "content-length",
    "content-encoding",
    "server",
    "date",
}

_ADAPTER_ERROR = {
    "error": {
        "code": "muse_code_adapter_error",
        "message": "The local Muse Code adapter could not relay this request.",
        "type": "server_error",
    }
}


class AdapterError(RuntimeError):
    """The adapter could not bind a loopback port."""


@dataclass(frozen=True)
class _AdapterConfig:
    upstream: str
    passthrough: bool
    log: Callable[[str], None] | None


class _AdapterServer(http.server.ThreadingHTTPServer):
    """Loopback HTTP server carrying one adapter configuration."""

    # http.server enables SO_REUSEADDR, which on Windows lets a second socket
    # bind an already-listening port (and silently steal traffic). Disable it
    # there so the "first free port" order really detects a busy port.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True
    request_queue_size = 128

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[http.server.BaseHTTPRequestHandler],
        config: _AdapterConfig,
    ) -> None:
        self.config = config
        super().__init__(server_address, handler_class)


def _log(config: _AdapterConfig, message: str) -> None:
    if config.log is None:
        return
    try:
        config.log(message)
    except Exception:  # noqa: BLE001 - diagnostics must never break a request
        pass


class _AdapterHandler(http.server.BaseHTTPRequestHandler):
    """One proxied request: adapt the request, relay the answer, restore names."""

    protocol_version = "HTTP/1.0"
    server_version = "muse-code-adapter"
    sys_version = ""

    # stdlib request logging would print one line per request; the adapter logs
    # only adapted requests, through the host logger.
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def handle_expect_100(self) -> bool:
        """Accept the body even though responses are HTTP/1.0-framed."""
        self.send_response_only(100)
        self.end_headers()
        return True

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming
        self._handle()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler naming
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802 - stdlib handler naming
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib handler naming
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler naming
        self._handle()

    def _handle(self) -> None:
        self._headers_sent = False
        try:
            self._relay()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as error:  # noqa: BLE001 - one bad request must not kill the server
            config: _AdapterConfig = self.server.config  # type: ignore[attr-defined]
            _log(config, f"muse-code adapter error: {type(error).__name__}: {error}")
            if not self._headers_sent:
                payload = json.dumps(_ADAPTER_ERROR).encode("utf-8")
                try:
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self._headers_sent = True
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            self.close_connection = True

    # -- request side -----------------------------------------------------

    def _read_chunked_body(self) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            line = self.rfile.readline(65537)
            if not line:
                raise ValueError("truncated chunked request body")
            size_text = line.split(b";", 1)[0].strip()
            try:
                size = int(size_text, 16)
            except ValueError as error:
                raise ValueError("invalid chunk size") from error
            if size == 0:
                while True:
                    trailer = self.rfile.readline(65537)
                    if trailer in (b"", b"\r\n", b"\n"):
                        return b"".join(chunks)
            total += size
            if total > MAX_BODY_BYTES:
                raise ValueError("request body too large")
            chunk = self.rfile.read(size)
            if len(chunk) != size:
                raise ValueError("truncated chunked request body")
            chunks.append(chunk)
            self.rfile.read(2)  # trailing CRLF

    def _read_request_body(self) -> bytes:
        transfer = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in transfer:
            return self._read_chunked_body()
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b""
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError("invalid Content-Length") from error
        if length < 0:
            raise ValueError("invalid Content-Length")
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        return self.rfile.read(length)

    def _upstream_headers(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for name, value in self.headers.items():
            lower = name.lower()
            if lower in ("host", "content-length", "accept-encoding"):
                continue
            if lower in _HOP_BY_HOP:
                continue
            if lower in merged:
                merged[lower] = f"{merged[lower]}, {value}"
            else:
                merged[lower] = value
        merged["accept-encoding"] = "identity"
        if "x-api-version" not in merged:
            merged["x-api-version"] = "1.0.0"
        return merged

    # -- relay ------------------------------------------------------------

    def _relay(self) -> None:
        config: _AdapterConfig = self.server.config  # type: ignore[attr-defined]
        pathname = urllib.parse.urlsplit(self.path).path or "/"
        method = self.command or "GET"
        raw_body = self._read_request_body() if method == "POST" else b""
        body = raw_body
        reverse: Mapping[str, str] = {}
        notes: list[str] = []
        parsed_body: _JsonRecord | None = None

        if (
            not config.passthrough
            and method == "POST"
            and is_adapted_path(pathname)
            and raw_body
        ):
            try:
                parsed = json.loads(raw_body.decode("utf-8"))
            except ValueError:
                notes.append("body not JSON; relayed as-is")
            else:
                if _is_record(parsed):
                    parsed_body = parsed
                    adapted = transform_request_body(parsed)
                    body = json.dumps(
                        adapted.body, separators=(",", ":"), ensure_ascii=False
                    ).encode("utf-8")
                    reverse = adapted.plan.reverse
                    notes.extend(adapted.notes)

        target = config.upstream.rstrip("/") + self.path

        def send(payload: bytes) -> Any:
            request = urllib.request.Request(
                target,
                data=payload if method == "POST" else None,
                headers=self._upstream_headers(),
                method=method,
            )
            try:
                return urllib.request.urlopen(request)
            except urllib.error.HTTPError as error:
                # Error bodies are relayed like any other response (the provider
                # classifies the 403 itself), so keep the HTTPError object.
                return error

        upstream = send(body)
        refusal_probe: bytes | None = None
        if not config.passthrough and parsed_body is not None:
            refusal = getattr(upstream, "code", None) or getattr(upstream, "status", None)
            if refusal == 403:
                # A refusal can mean a builtin name this release does not know
                # yet: retry once with every flat function tool aliased.
                hint = str(
                    getattr(upstream, "headers", {}).get("content-type") or ""
                ).lower()
                if "json" in hint:
                    refusal_probe = upstream.read()
                    if b"client_attestation_denied" in refusal_probe:
                        all_plan = plan_tool_aliases(
                            parsed_body.get("tools"), all_tools=True
                        )
                        if len(all_plan.aliases) > len(reverse):
                            retry = transform_request_body(parsed_body, all_plan)
                            reverse = all_plan.reverse
                            notes.append(
                                f"gate refused; retried with all {len(all_plan.aliases)} tools aliased"
                            )
                            try:
                                upstream.close()
                            except Exception:  # noqa: BLE001 - closing the probe must not fail
                                pass
                            upstream = send(
                                json.dumps(
                                    retry.body,
                                    separators=(",", ":"),
                                    ensure_ascii=False,
                                ).encode("utf-8")
                            )
                            refusal_probe = None

        try:
            status = getattr(upstream, "status", None)
            if status is None:
                status = getattr(upstream, "code", 502)
            raw_headers = getattr(upstream, "headers", None)
            headers = raw_headers if raw_headers is not None else {}
            content_type = str(headers.get("content-type") or "").lower()
            if notes:
                _log(
                    config,
                    f"muse-code adapter: {method} {pathname} {status} | {', '.join(notes)}",
                )
            if "text/event-stream" in content_type:
                self._relay_event_stream(upstream, int(status), headers, reverse)
            else:
                self._relay_payload(
                    upstream, int(status), headers, reverse, content_type, refusal_probe
                )
        finally:
            try:
                upstream.close()
            except Exception:  # noqa: BLE001 - closing the relay must not fail the reply
                pass

    def _relay_event_stream(
        self,
        upstream: Any,
        status: int,
        headers: Any,
        reverse: Mapping[str, str],
    ) -> None:
        self._send_upstream_headers(status, headers)
        restorer = SseRestorer(reverse)
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        buffer = ""
        while True:
            chunk = upstream.read(65536)
            if not chunk:
                break
            buffer += decoder.decode(chunk)
            while True:
                index = buffer.find("\n")
                if index < 0:
                    break
                line, buffer = buffer[:index], buffer[index + 1 :]
                self.wfile.write((restorer.feed(line) + "\n").encode("utf-8"))
                self.wfile.flush()
        buffer += decoder.decode(b"", True)
        if buffer:
            self.wfile.write(restorer.feed(buffer).encode("utf-8"))
            self.wfile.flush()

    def _relay_payload(
        self,
        upstream: Any,
        status: int,
        headers: Any,
        reverse: Mapping[str, str],
        content_type: str,
        preloaded: bytes | None = None,
    ) -> None:
        payload = preloaded if preloaded is not None else upstream.read()
        if reverse and "application/json" in content_type:
            try:
                parsed = json.loads(payload.decode("utf-8"))
            except ValueError:
                pass
            else:
                if _is_record(parsed):
                    restored = transform_response_body(parsed, reverse)
                    payload = json.dumps(
                        restored, separators=(",", ":"), ensure_ascii=False
                    ).encode("utf-8")
        self.send_response(status)
        self._write_upstream_headers(headers)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self._headers_sent = True
        self.wfile.write(payload)

    def _send_upstream_headers(self, status: int, headers: Any) -> None:
        self.send_response(status)
        self._write_upstream_headers(headers)
        self.end_headers()
        self._headers_sent = True

    def _write_upstream_headers(self, headers: Any) -> None:
        for name, value in headers.items():
            if name.lower() in _RESPONSE_SKIP:
                continue
            self.send_header(name, value)


class _AdapterState:
    """Process-wide adapter registry.

    Kept on the ``sys`` module rather than in module globals so a reloaded
    plugin module (the test suite loads the plugin repeatedly) still finds and
    reuses the server that is already running instead of starting a duplicate.
    """

    __slots__ = ("lock", "active")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.active = None


_STATE_ATTR = "_muse_code_adapter_state_v1"


def _adapter_state() -> _AdapterState:
    state = getattr(sys, _STATE_ATTR, None)
    if state is None:
        state = sys.__dict__.setdefault(_STATE_ATTR, _AdapterState())
    return state


class RunningAdapter:
    """A running adapter."""

    def __init__(
        self,
        server: _AdapterServer,
        thread: threading.Thread,
        base_url: str,
        port: int,
        key: tuple[str, tuple[int, ...], bool],
    ) -> None:
        self._server = server
        self._thread = thread
        self._base_url = base_url
        self._port = port
        self._key = key
        self._closed = False
        self._close_lock = threading.Lock()
        self._atexit_callback: Callable[[], None] | None = self.close

    @property
    def base_url(self) -> str:
        """Loopback origin to use as the provider base URL, e.g. ``http://127.0.0.1:43219/v1``."""
        return self._base_url

    @property
    def port(self) -> int:
        """Actual loopback port."""
        return self._port

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Stop accepting connections and close the server. Safe to call twice."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        callback = self._atexit_callback
        self._atexit_callback = None
        if callback is not None:
            try:
                atexit.unregister(callback)
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass
        try:
            # Hosts keep their sockets alive; do not wait for them to time out.
            self._server.shutdown()
        finally:
            self._server.server_close()
        self._thread.join(timeout=10.0)
        state = _adapter_state()
        with state.lock:
            if state.active is self:
                state.active = None


def _resolve_passthrough(passthrough: bool | None) -> bool:
    if passthrough is not None:
        return bool(passthrough)
    return os.getenv(PASSTHROUGH_ENV_VAR, "").strip().lower() == "off"


def start_adapter(
    *,
    upstream: str | None = None,
    ports: Iterable[int] | None = None,
    log: Callable[[str], None] | None = None,
    passthrough: bool | None = None,
) -> RunningAdapter:
    """Start the loopback adapter, or return the one already running.

    The server binds ``127.0.0.1`` only, on the first free port of ``ports``
    (default 43219-43222) and then an ephemeral port. A second call with the same
    options returns the same running adapter, so repeated imports and plugin
    registrations never start a duplicate server; a call with different options
    stops the previous adapter and replaces it.

    ``passthrough`` defaults to ``MUSE_CODE_ADAPTER=off``: the proxy stays in
    place, the wire rules are skipped.
    """
    resolved_upstream = str(upstream or DEFAULT_UPSTREAM).rstrip("/") or DEFAULT_UPSTREAM
    resolved_ports = tuple(ports) if ports is not None else DEFAULT_PORTS
    resolved_passthrough = _resolve_passthrough(passthrough)
    key = (resolved_upstream, resolved_ports, resolved_passthrough)

    state = _adapter_state()
    with state.lock:
        active = state.active
        if active is not None and not active.closed:
            if active._key == key:
                return active
            active.close()

        config = _AdapterConfig(
            upstream=resolved_upstream,
            passthrough=resolved_passthrough,
            log=log,
        )
        server: _AdapterServer | None = None
        last_error: OSError | None = None
        for candidate in (*resolved_ports, 0):
            try:
                server = _AdapterServer(("127.0.0.1", candidate), _AdapterHandler, config)
            except OSError as error:
                last_error = error
                continue
            break
        if server is None:
            raise AdapterError(
                "muse-code adapter could not bind a loopback port: "
                f"{last_error if last_error is not None else 'no candidate port'}"
            )

        port = int(server.server_address[1])
        base_url = f"http://127.0.0.1:{port}/v1"
        thread = threading.Thread(
            target=server.serve_forever,
            args=(0.05,),
            name=f"muse-code-adapter-{port}",
            daemon=True,
        )
        running = RunningAdapter(server, thread, base_url, port, key)
        try:
            thread.start()
        except Exception:
            server.server_close()
            raise
        atexit.register(running.close)
        state.active = running
        return running


def stop_adapter() -> None:
    """Stop the process-wide adapter, if one is running."""
    state = _adapter_state()
    with state.lock:
        active = state.active
    if active is not None:
        active.close()
