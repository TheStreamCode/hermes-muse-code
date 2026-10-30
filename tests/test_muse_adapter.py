"""Hermetic tests for the Muse Code loopback wire adapter.

No network: the proxy tests point the adapter at a local ``http.server`` fake
upstream, and the unit tests never touch a socket.
"""

import http.server
import importlib.util
import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ADAPTER_FILE = REPO / "muse_adapter.py"


def _load():
    name = "_hermes_test_muse_adapter"
    spec = importlib.util.spec_from_file_location(name, ADAPTER_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


adapter = _load()


@pytest.fixture(autouse=True)
def _isolated_adapter():
    """Every test starts and leaves no process-wide adapter behind."""
    adapter.stop_adapter()
    yield
    adapter.stop_adapter()


def tool(name, **extra):
    return {
        "type": "function",
        "name": name,
        "description": f"Tool {name}.",
        "parameters": {"type": "object", "properties": {}},
        **extra,
    }


# -- wire rules -----------------------------------------------------------


def test_contract_version_upstream_and_collision_set_are_pinned():
    assert adapter.ADAPTER_VERSION == 3
    assert adapter.DEFAULT_UPSTREAM == "https://api.meta.ai"
    assert adapter.DEFAULT_PORTS == (43219, 43220, 43221, 43222)
    for name in (
        "read_file",
        "write_file",
        "edit_file",
        "bash",
        "web_search",
        "web_fetch",
        "workflow",
        "search",
        "write_todos",
        "tool_search",
        "subagent_send_message",
        "subagent_cancel",
    ):
        assert name in adapter.COLLIDING_TOOL_NAMES
    assert "get_weather" not in adapter.COLLIDING_TOOL_NAMES
    assert len(adapter.COLLIDING_TOOL_NAMES) == 40
    assert adapter.is_adapted_path("/v1/responses")
    assert adapter.is_adapted_path("/v1/chat/completions")
    assert not adapter.is_adapted_path("/v1/models")


def test_only_colliding_flat_function_tools_are_aliased():
    plan = adapter.plan_tool_aliases(
        [
            tool("bash"),
            tool("get_weather"),
            tool("read_file"),
            {"type": "namespace", "name": "muse", "tools": [tool("bash")]},
            {"type": "web_search"},
        ]
    )
    assert list(plan.aliases.items()) == [
        ("bash", "client_bash"),
        ("read_file", "client_read_file"),
    ]
    assert plan.reverse["client_bash"] == "bash"
    assert "read_file" not in plan.aliases.values()


def test_the_all_mode_aliases_every_flat_function_tool():
    plan = adapter.plan_tool_aliases(
        [tool("read"), tool("bash"), {"type": "web_search"}, tool("get_weather")],
        all_tools=True,
    )
    assert list(plan.aliases) == ["read", "bash", "get_weather"]


def test_an_alias_that_already_exists_gets_a_stable_suffix():
    plan = adapter.plan_tool_aliases([tool("bash"), tool("client_bash")])
    assert plan.aliases["bash"] == "client_bash_2"
    assert plan.reverse["client_bash_2"] == "bash"


def test_the_request_rewrite_aliases_tools_history_and_forced_choice():
    original = {
        "model": "muse-spark-1.3",
        "tools": [tool("bash"), tool("get_weather")],
        "input": [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "function_call", "call_id": "call_1", "name": "bash", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
        ],
        "tool_choice": {"type": "function", "name": "bash"},
        "reasoning_effort": "low",
        "max_output_tokens": 8,
        "unknown_field": {"kept": True},
    }
    adapted = adapter.transform_request_body(original)
    body = adapted.body
    assert [entry["name"] for entry in body["tools"]] == ["client_bash", "get_weather"]
    assert body["input"][1]["name"] == "client_bash"
    assert "name" not in body["input"][2]
    assert body["tool_choice"] == "auto"
    assert body["reasoning"] == {"effort": "low"}
    assert "reasoning_effort" not in body
    assert body["max_output_tokens"] == 16
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["unknown_field"] == {"kept": True}
    assert original["tools"][0]["name"] == "bash"
    assert len(adapted.notes) == 5


def test_a_request_without_collisions_only_gets_the_parameter_normalisation():
    adapted = adapter.transform_request_body(
        {
            "model": "muse-spark-1.3",
            "tools": [tool("get_weather")],
            "tool_choice": "auto",
            "max_output_tokens": 64,
            "include": ["reasoning.encrypted_content"],
        }
    )
    assert [entry["name"] for entry in adapted.body["tools"]] == ["get_weather"]
    assert adapted.body["tool_choice"] == "auto"
    assert adapted.body["max_output_tokens"] == 64
    assert adapted.body["include"] == ["reasoning.encrypted_content"]
    assert adapted.notes == ()


def test_an_explicit_reasoning_effort_wins_and_is_not_overwritten():
    body = adapter.transform_request_body(
        {"reasoning_effort": "low", "reasoning": {"effort": "high", "summary": "auto"}}
    ).body
    assert body["reasoning"] == {"effort": "high", "summary": "auto"}
    assert "reasoning_effort" not in body


def test_the_json_response_transform_restores_names_and_leaves_other_items_alone():
    body = {
        "tools": [tool("client_bash"), tool("get_weather")],
        "output": [
            {"type": "reasoning", "id": "rs_1", "encrypted_content": "x"},
            {
                "type": "function_call",
                "id": "fc_1",
                "name": "client_bash",
                "arguments": '{"command":"ls"}',
            },
            {"type": "message", "role": "assistant", "content": []},
        ],
    }
    out = adapter.transform_response_body(body, {"client_bash": "bash"})
    assert out is body
    assert body["output"][1]["name"] == "bash"
    assert body["output"][0]["encrypted_content"] == "x"
    assert body["tools"][0]["name"] == "bash"
    assert body["tools"][1]["name"] == "get_weather"


def _sse_line(event):
    return f"data: {json.dumps(event)}"


def test_sse_restores_names_everywhere_and_repairs_empty_arguments():
    restorer = adapter.SseRestorer({"client_bash": "bash"})
    events = [
        {
            "type": "response.created",
            "response": {
                "tools": [tool("client_bash", description="Runs a command.")],
                "output": [],
            },
        },
        {
            "type": "response.output_item.added",
            "item": {"type": "function_call", "id": "fc_1", "name": "client_bash", "arguments": ""},
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "delta": '{"command":"ls"}',
        },
        {
            "type": "response.function_call_arguments.done",
            "item_id": "fc_1",
            "name": "client_bash",
            "arguments": '{"command":"ls"}',
        },
        {
            "type": "response.output_item.done",
            "item": {"type": "function_call", "id": "fc_1", "name": "client_bash", "arguments": ""},
        },
        {
            "type": "response.completed",
            "response": {
                "tools": [tool("client_bash", description="Runs a command.")],
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "name": "client_bash",
                        "arguments": "",
                    }
                ],
            },
        },
    ]
    output = "\n".join(restorer.feed(_sse_line(event)) for event in events)
    assert "client_bash" not in output
    assert '"name":"bash"' in output
    assert '"arguments":"{\\"command\\":\\"ls\\"}"' in output


def test_sse_leaves_other_lines_and_crlf_untouched():
    restorer = adapter.SseRestorer({})
    assert restorer.feed("event: response.created") == "event: response.created"
    assert restorer.feed("data: [DONE]") == "data: [DONE]"
    assert restorer.feed("data: not-json") == "data: not-json"
    restored = restorer.feed(_sse_line({"type": "response.created", "response": {}}) + "\r")
    assert restored.endswith("\r")
    assert restored.startswith("data: ")


# -- proxy ----------------------------------------------------------------


class _FakeUpstream:
    """Local stdlib HTTP server standing in for api.meta.ai."""

    def __init__(self, respond):
        self.requests = []
        upstream = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, format, *args):  # noqa: A002 - stdlib signature
                return

            def do_GET(self):  # noqa: N802 - stdlib handler naming
                upstream._record(self, b"")

            def do_POST(self):  # noqa: N802 - stdlib handler naming
                length = int(self.headers.get("Content-Length") or 0)
                upstream._record(self, self.rfile.read(length))

        self._respond = respond
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _record(self, handler, body):
        record = {
            "method": handler.command,
            "path": handler.path,
            "headers": {name.lower(): value for name, value in handler.headers.items()},
            "body": body,
        }
        self.requests.append(record)
        self._respond(record, handler)

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _send_json(handler, payload, status=200):
    data = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _send_sse(handler, events):
    data = "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
    ).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.end_headers()
    handler.wfile.write(data)


def _post(url, payload, headers=None):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer LLM|sentinel",
            **(headers or {}),
        },
    )
    return urllib.request.urlopen(request, timeout=10)


def test_proxy_rewrites_the_request_and_restores_the_streamed_answer():
    events = [
        {"type": "response.created", "response": {"tools": [], "output": []}},
        {
            "type": "response.output_item.added",
            "item": {"type": "function_call", "id": "fc_1", "name": "client_bash", "arguments": ""},
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "delta": '{"command":"ls"}',
        },
        {
            "type": "response.output_item.done",
            "item": {"type": "function_call", "id": "fc_1", "name": "client_bash", "arguments": ""},
        },
        {"type": "response.completed", "response": {"tools": [], "output": []}},
    ]
    upstream = _FakeUpstream(lambda record, handler: _send_sse(handler, events))
    log_lines = []
    running = adapter.start_adapter(
        upstream=upstream.origin, ports=(0,), log=log_lines.append
    )
    try:
        with _post(
            f"{running.base_url}/responses",
            {
                "model": "muse-spark-1.3",
                "tools": [tool("bash")],
                "tool_choice": "required",
                "stream": True,
            },
        ) as response:
            assert response.status == 200
            assert "text/event-stream" in response.headers.get("Content-Type", "")
            text = response.read().decode()
    finally:
        running.close()
        upstream.close()

    assert "client_bash" not in text
    assert '"name":"bash"' in text
    assert '"arguments":"{\\"command\\":\\"ls\\"}"' in text

    assert len(upstream.requests) == 1
    forwarded = upstream.requests[0]
    assert forwarded["path"] == "/v1/responses"
    body = json.loads(forwarded["body"])
    assert body["tools"][0]["name"] == "client_bash"
    assert body["tool_choice"] == "auto"
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["stream"] is True
    assert forwarded["headers"]["authorization"] == "Bearer LLM|sentinel"
    assert forwarded["headers"]["x-api-version"] == "1.0.0"
    assert forwarded["headers"]["accept-encoding"] == "identity"
    # Diagnostics never carry credential material or payloads.
    assert all("sentinel" not in line for line in log_lines)
    assert all("command" not in line for line in log_lines)


def test_proxy_restores_json_answers_and_relays_other_paths():
    catalog = {"object": "list", "data": [{"id": "muse-spark-1.3"}]}

    def respond(record, handler):
        if record["method"] == "GET":
            _send_json(handler, catalog)
            return
        _send_json(
            handler,
            {
                "tools": [tool("client_read_file")],
                "output": [
                    {"type": "reasoning", "id": "rs_1", "encrypted_content": "x"},
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "name": "client_read_file",
                        "arguments": '{"path":"a"}',
                    },
                ],
            },
        )

    upstream = _FakeUpstream(respond)
    running = adapter.start_adapter(upstream=upstream.origin, ports=(0,))
    try:
        with _post(
            f"{running.base_url}/responses",
            {"model": "muse-spark-1.3", "tools": [tool("read_file")]},
        ) as response:
            body = json.load(response)
        assert body["output"][1]["name"] == "read_file"
        assert body["tools"][0]["name"] == "read_file"
        assert body["output"][0]["encrypted_content"] == "x"

        request = urllib.request.Request(
            f"{running.base_url}/models",
            headers={"Authorization": "Bearer LLM|sentinel"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
            assert json.load(response) == catalog
    finally:
        running.close()
        upstream.close()

    assert [request["path"] for request in upstream.requests] == [
        "/v1/responses",
        "/v1/models",
    ]
    assert upstream.requests[1]["headers"]["authorization"] == "Bearer LLM|sentinel"


def test_proxy_relays_error_status_and_body_for_the_provider_classifier():
    refusal = {
        "error": {
            "code": "client_attestation_denied",
            "message": "Muse Code subscriptions cover requests from the Muse Code client only.",
        }
    }
    upstream = _FakeUpstream(lambda record, handler: _send_json(handler, refusal, status=403))
    running = adapter.start_adapter(upstream=upstream.origin, ports=(0,))
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(
                f"{running.base_url}/responses",
                {"model": "muse-spark-1.3", "tools": [tool("bash")]},
            )
        assert caught.value.code == 403
        assert json.load(caught.value) == refusal
    finally:
        running.close()
        upstream.close()

    forwarded = json.loads(upstream.requests[0]["body"])
    assert forwarded["tools"][0]["name"] == "client_bash"


def test_a_gate_refusal_retries_once_with_every_tool_aliased():
    bodies = []

    def respond(record, handler):
        payload = json.loads(record["body"])
        bodies.append(payload)
        unaliased = [
            entry
            for entry in payload.get("tools", [])
            if entry.get("type") == "function"
            and not str(entry.get("name", "")).startswith("client_")
        ]
        if unaliased:
            _send_json(
                handler, {"error": {"code": "client_attestation_denied"}}, status=403
            )
            return
        _send_json(
            handler,
            {
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "name": payload["tools"][0]["name"],
                        "arguments": "{}",
                    }
                ]
            },
        )

    upstream = _FakeUpstream(respond)
    running = adapter.start_adapter(upstream=upstream.origin, ports=(0,))
    try:
        with _post(
            f"{running.base_url}/responses",
            {
                "model": "muse-spark-1.3",
                "tools": [tool("future_muse_tool"), tool("get_weather")],
            },
        ) as response:
            body = json.load(response)
        assert body["output"][0]["name"] == "future_muse_tool"
    finally:
        running.close()
        upstream.close()

    assert len(upstream.requests) == 2
    retried = json.loads(upstream.requests[1]["body"])
    assert [entry["name"] for entry in retried["tools"]] == [
        "client_future_muse_tool",
        "client_get_weather",
    ]


def test_passthrough_mode_relays_the_original_body_and_response():
    payload = {
        "model": "muse-spark-1.3",
        "tools": [tool("bash")],
        "tool_choice": "required",
        "reasoning_effort": "low",
        "max_output_tokens": 8,
    }
    upstream = _FakeUpstream(
        lambda record, handler: _send_json(
            handler,
            {"output": [{"type": "function_call", "id": "fc_1", "name": "client_bash"}]},
        )
    )
    running = adapter.start_adapter(
        upstream=upstream.origin, ports=(0,), passthrough=True
    )
    try:
        with _post(f"{running.base_url}/responses", payload) as response:
            body = json.load(response)
    finally:
        running.close()
        upstream.close()

    assert json.loads(upstream.requests[0]["body"]) == payload
    assert body["output"][0]["name"] == "client_bash"
    assert "x-api-version" in upstream.requests[0]["headers"]


def test_passthrough_mode_can_be_enabled_by_the_environment(monkeypatch):
    payload = {"model": "muse-spark-1.3", "tools": [tool("read_file")]}
    upstream = _FakeUpstream(lambda record, handler: _send_json(handler, {"output": []}))
    monkeypatch.setenv("MUSE_CODE_ADAPTER", "off")
    running = adapter.start_adapter(upstream=upstream.origin, ports=(0,))
    try:
        with _post(f"{running.base_url}/responses", payload):
            pass
    finally:
        running.close()
        upstream.close()

    forwarded = json.loads(upstream.requests[0]["body"])
    assert forwarded == payload
    assert "include" not in forwarded


# -- lifecycle ------------------------------------------------------------


def test_start_is_idempotent_and_close_is_safe():
    first = adapter.start_adapter(ports=(0,))
    second = adapter.start_adapter(ports=(0,))
    assert second is first
    assert second.base_url == first.base_url
    assert second.port == first.port

    first.close()
    first.close()
    assert first.closed

    third = adapter.start_adapter(ports=(0,))
    try:
        assert third is not first
        assert not third.closed
        assert third.base_url.startswith("http://127.0.0.1:")
    finally:
        third.close()
    adapter.stop_adapter()
    adapter.stop_adapter()


def test_a_busy_candidate_port_falls_through_to_the_next_one():
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    busy = blocker.getsockname()[1]
    try:
        running = adapter.start_adapter(ports=(busy, 0))
        try:
            assert running.port != busy
            assert running.base_url == f"http://127.0.0.1:{running.port}/v1"
        finally:
            running.close()
    finally:
        blocker.close()
