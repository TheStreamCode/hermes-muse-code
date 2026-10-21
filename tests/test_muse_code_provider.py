"""Hermetic unit tests: plugin and login modules loaded from file, Hermes
imports stubbed, network mocked. No hermes install, Meta account, or
network needed."""

import importlib.util
import io
import json
import os
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN_FILE = REPO / "__init__.py"
LOGIN_FILE = REPO / "muse_code_login.py"


@pytest.fixture(autouse=True)
def _clean_secret_env():
    """Do not leak provider env between tests or into the developer shell."""
    for var in ("MUSE_CODE_SUB_TOKEN", "MUSE_CODE_SUB_CREDENTIALS"):
        os.environ.pop(var, None)
    yield
    for var in ("MUSE_CODE_SUB_TOKEN", "MUSE_CODE_SUB_CREDENTIALS"):
        os.environ.pop(var, None)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_plugin(env=None):
    """Import the plugin with a stubbed `providers` package."""
    os.environ.pop("MUSE_CODE_SUB_TOKEN", None)
    for var in ("MUSE_CODE_SUB_TOKEN",):
        env_value = (env or {}).get(var)
        if env_value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = env_value

    registered = {}

    base_mod = types.ModuleType("providers.base")

    class ProviderProfile:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def fetch_models(self, **kwargs):
            return kwargs.get("_live")

    base_mod.ProviderProfile = ProviderProfile

    providers = types.ModuleType("providers")
    providers.base = base_mod
    providers.register_provider = lambda p: registered.setdefault(p.name, p)

    with patch.dict(
        sys.modules,
        {
            "providers": providers,
            "providers.base": base_mod,
        },
    ):
        sys.modules.pop("_hermes_test_muse_code", None)
        module = _load("_hermes_test_muse_code", PLUGIN_FILE)
    return module, registered


def _load_login():
    sys.modules.pop("_hermes_test_login", None)
    return _load("_hermes_test_login", LOGIN_FILE)


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


def _urlopen_script(*steps):
    """Build a fake urlopen playing canned responses (dict) or errors."""
    calls = {"n": 0}

    def fake(request, timeout=None):
        step = steps[min(calls["n"], len(steps) - 1)]
        calls["n"] += 1
        if isinstance(step, Exception):
            raise step
        return _FakeResponse(step)

    fake.calls = calls
    return fake


def test_provider_registered_with_expected_wiring(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(tmp_path / "cache.json"))
    module, registered = _load_plugin()
    profile = registered["muse-code"]
    assert profile.base_url == "https://api.meta.ai/v1"
    assert profile.api_mode == "codex_responses"
    assert profile.auth_type == "api_key"
    assert profile.env_vars == ("MUSE_CODE_SUB_TOKEN",)
    assert profile.fallback_models == ("muse-spark-1.3",)


def test_cached_key_resolution(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"oauthAccessToken": "dca-x", "apiKey": "LLM|k"}))
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(cache))
    module, _ = _load_plugin()
    assert module.read_cached_key() == "LLM|k"
    module._ensure_subscription_key()
    assert os.environ["MUSE_CODE_SUB_TOKEN"] == "LLM|k"


def test_base_url_is_fixed(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(tmp_path / "c.json"))
    monkeypatch.setenv("META_BASE_URL", "https://attacker.test/v1")
    _, registered = _load_plugin()
    assert registered["muse-code"].base_url == "https://api.meta.ai/v1"


def test_cache_change_refreshes_owned_env(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"apiKey": "LLM|first"}))
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(cache))
    module, _ = _load_plugin()
    assert os.environ["MUSE_CODE_SUB_TOKEN"] == "LLM|first"
    cache.write_text(json.dumps({"apiKey": "LLM|second"}))
    module._ensure_subscription_key()
    assert os.environ["MUSE_CODE_SUB_TOKEN"] == "LLM|second"


def test_removed_cache_clears_owned_env(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"apiKey": "LLM|gone"}))
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(cache))
    module, _ = _load_plugin()
    assert os.environ["MUSE_CODE_SUB_TOKEN"] == "LLM|gone"
    cache.unlink()
    module._ensure_subscription_key()
    assert "MUSE_CODE_SUB_TOKEN" not in os.environ


def test_explicit_env_is_never_overwritten(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"apiKey": "LLM|cache"}))
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(cache))
    module, _ = _load_plugin(env={"MUSE_CODE_SUB_TOKEN": "pinned"})
    module._ensure_subscription_key()
    assert os.environ["MUSE_CODE_SUB_TOKEN"] == "pinned"
    assert module._ENV_KEY_OWNED is None


def test_bare_import_does_not_export_the_key(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"apiKey": "LLM|bare"}))
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(cache))
    spec = importlib.util.spec_from_file_location(
        "_hermes_test_muse_code_bare_env", PLUGIN_FILE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._HERMES_AVAILABLE is False
    assert "MUSE_CODE_SUB_TOKEN" not in os.environ


def test_missing_cache_is_silent_miss(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(tmp_path / "absent.json"))
    module, _ = _load_plugin()
    assert module.read_cached_key() == ""
    assert "MUSE_CODE_SUB_TOKEN" not in os.environ


def test_explicit_env_wins_over_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"apiKey": "LLM|cache"}))
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(cache))
    module, _ = _load_plugin(env={"MUSE_CODE_SUB_TOKEN": "pinned"})
    module._ensure_subscription_key()
    assert os.environ["MUSE_CODE_SUB_TOKEN"] == "pinned"


def test_no_secret_material_in_logs(tmp_path, monkeypatch, caplog):
    import logging

    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"apiKey": "LLM|sentinel-secret"}))
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(cache))
    module, _ = _load_plugin()
    with caplog.at_level(logging.DEBUG, logger=module.logger.name):
        module._ensure_subscription_key()
    assert "LLM|sentinel-secret" not in caplog.text


def test_reasoning_effort_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(tmp_path / "c.json"))
    _, registered = _load_plugin()
    profile = registered["muse-code"]
    assert profile.build_api_kwargs_extras(
        reasoning_config={"enabled": False}
    ) == ({}, {"reasoning_effort": "minimal"})
    assert profile.build_api_kwargs_extras(
        reasoning_config={"effort": "high"}
    ) == ({}, {"reasoning_effort": "high"})
    assert profile.build_api_kwargs_extras(reasoning_config={}) == (
        {},
        {"reasoning_effort": "medium"},
    )
    assert profile.build_api_kwargs_extras(reasoning_config=None) == (
        {},
        {"reasoning_effort": "medium"},
    )
    assert profile.build_api_kwargs_extras(
        reasoning_config={"effort": "none"}
    ) == ({}, {"reasoning_effort": "minimal"})
    assert profile.build_api_kwargs_extras(
        reasoning_config={"effort": "minimal"}
    ) == ({}, {"reasoning_effort": "minimal"})
    assert profile.build_api_kwargs_extras(
        reasoning_config={"effort": "max"}
    ) == ({}, {"reasoning_effort": "xhigh"})
    assert profile.build_api_kwargs_extras(
        reasoning_config={"effort": "ultra"}
    ) == ({}, {"reasoning_effort": "xhigh"})
    assert profile.build_api_kwargs_extras(
        reasoning_config={"effort": "bespoke-level"}
    ) == ({}, {"reasoning_effort": "medium"})


def test_meta_wire_vocabulary_pinned(tmp_path, monkeypatch):
    """The vendored Meta wire set must match Hermes core META_AI_EFFORTS."""
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(tmp_path / "c.json"))
    module, _ = _load_plugin()
    assert module._META_WIRE_EFFORTS == ("minimal", "low", "medium", "high", "xhigh")


def test_clamp_effort_semantics(tmp_path, monkeypatch):
    """Vendored clamp keeps the core contract: verbatim/nearest-weaker/weakest."""
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(tmp_path / "c.json"))
    module, _ = _load_plugin()
    clamp = module._clamp_effort
    supported = module._META_WIRE_EFFORTS
    assert clamp("high", supported) == "high"
    assert clamp("max", supported) == "xhigh"
    assert clamp("ultra", supported) == "xhigh"
    assert clamp("bespoke", supported) == "bespoke"
    assert clamp("", supported) == ""
    assert clamp("none", supported) == "minimal"


def test_fetch_models_admits_live_catalog_dynamically(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(tmp_path / "c.json"))
    _, registered = _load_plugin()
    profile = registered["muse-code"]
    live = [
        "muse-spark-1.3",
        "muse-spark-1.2-contributor",
        "muse-wonder-1.0",
        "muse-wonder-full-1.0",
        "muse-nova-9.9",
        "sam-3.1",
        "muse-image-1.0",
        "muse-voice-transcribe-1.0",
    ]
    with patch.object(
        type(profile).__mro__[1], "fetch_models", return_value=live
    ):
        assert profile.fetch_models(api_key="k") == [
            "muse-spark-1.3",
            "muse-spark-1.2-contributor",
            "muse-wonder-1.0",
            "muse-wonder-full-1.0",
            "muse-nova-9.9",
        ]
    with patch.object(
        type(profile).__mro__[1], "fetch_models", return_value=None
    ):
        assert profile.fetch_models(api_key="k") is None


def test_register_entry_point_is_probe_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(tmp_path / "c.json"))
    module, _ = _load_plugin(env={"MUSE_CODE_SUB_TOKEN": "pinned"})
    assert callable(getattr(module, "register", None))
    module.register(object())
    assert os.environ["MUSE_CODE_SUB_TOKEN"] == "pinned"


def test_importable_without_hermes_runtime(tmp_path, monkeypatch):
    """Plain import registers nothing but keeps helpers usable."""
    monkeypatch.setenv("MUSE_CODE_SUB_CREDENTIALS", str(tmp_path / "c.json"))
    for mod in ("providers", "providers.base"):
        assert mod not in sys.modules
    spec = importlib.util.spec_from_file_location(
        "_hermes_test_muse_code_bare", PLUGIN_FILE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._HERMES_AVAILABLE is False
    assert module.read_cached_key() == ""


DEVICE_OK = {
    "device_code": "dc",
    "user_code": "ABCD-EFGH",
    "verification_uri": "https://auth.meta.com/device",
    "verification_uri_complete": "https://auth.meta.com/device?user_code=ABCD-EFGH",
    "interval": 5,
    "expires_in": 600,
}
KEY_OK = {
    "api_key": "LLM|fresh",
    "user_email": "User@Example.com",
    "user_id": "uid-1",
    "is_subs_active": True,
}


def test_full_login_flow_writes_cache(tmp_path):
    login = _load_login()
    cache = str(tmp_path / "creds.json")
    fake = _urlopen_script(DEVICE_OK, {"error": "authorization_pending"}, {"access_token": "dca-new"}, KEY_OK)
    with patch.object(login.urllib.request, "urlopen", fake):
        with patch.object(login.time, "sleep", lambda s: None):
            out = io.StringIO()
            with redirect_stdout(out):
                assert login.main(["--cache", cache]) == 0
    assert "ABCD-EFGH" in out.getvalue()
    assert "LLM|fresh" not in out.getvalue()  # secrets never printed
    raw = open(cache).read()
    assert "oauthAccessToken" not in raw  # never persisted
    assert "dca-new" not in raw
    saved = json.loads(raw)
    assert saved == {
        "apiKey": "LLM|fresh",
        "accountId": "uid-1",
        "email": "user@example.com",
    }


def test_login_rejects_inactive_subscription():
    login = _load_login()
    bad = dict(KEY_OK, is_subs_active=False)
    fake = _urlopen_script(bad)
    with patch.object(login.urllib.request, "urlopen", fake):
        with patch.object(login.time, "sleep", lambda s: None):
            try:
                login.mint_key("dca-x")
            except login.LoginError as exc:
                assert "inactive" in str(exc)
            else:
                raise AssertionError("expected LoginError")


def test_login_requires_payment_action():
    login = _load_login()
    nopay = {"require_payment": True, "action_url": "https://example.com/pay"}
    fake = _urlopen_script(nopay)
    with patch.object(login.urllib.request, "urlopen", fake):
        with patch.object(login.time, "sleep", lambda s: None):
            try:
                login.mint_key("dca-x")
            except login.LoginError as exc:
                assert "https://example.com/pay" in str(exc)
            else:
                raise AssertionError("expected LoginError")


def test_login_poll_timeout():
    login = _load_login()
    fake = _urlopen_script({"error": "authorization_pending"})
    with patch.object(login.urllib.request, "urlopen", fake):
        with patch.object(login.time, "sleep", lambda s: None):
            with patch.object(login.time, "time", side_effect=[0.0, 0.0, 9999.0]):
                try:
                    login.poll_token("dc", 5, 10)
                except login.LoginError as exc:
                    assert "timed out" in str(exc)
                else:
                    raise AssertionError("expected LoginError")


def test_login_auth_contract_is_pinned():
    login = _load_login()
    assert login.AUTH_CONTRACT_VERSION == 1
    assert login.CLIENT_ID == "1031625952748946"
    assert login.DEVICE_URL == "https://auth.meta.com/oidc/device/authorization/"
    assert login.TOKEN_URL == "https://auth.meta.com/oidc/device/token/"
    assert login.KEY_URL == "https://api.meta.ai/muse-code/key"
    assert login.API_VERSION == "1.0.0"
