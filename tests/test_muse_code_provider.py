"""Hermetic unit tests: the plugin module is loaded with stubbed Hermes
imports, so no hermes install, omp binary, or network is needed."""

import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

PLUGIN_FILE = Path(__file__).resolve().parent.parent / "__init__.py"

EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")


def _load_plugin(env=None):
    """Import the plugin with stubbed `agent`/`providers` packages."""
    os.environ["HERMES_MUSE_CODE_SUB_AUTO"] = "0"  # never shell out on import
    for var in ("MUSE_CODE_SUB_TOKEN",):
        env_value = (env or {}).get(var)
        if env_value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = env_value

    agent = types.ModuleType("agent")
    reasoning = types.ModuleType("agent.reasoning_effort")
    reasoning.META_AI_EFFORTS = EFFORTS
    reasoning.clamp_effort = (
        lambda effort, efforts: effort if effort in efforts else "medium"
    )
    agent.reasoning_effort = reasoning

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
            "agent": agent,
            "agent.reasoning_effort": reasoning,
            "providers": providers,
            "providers.base": base_mod,
        },
    ):
        for mod in ("_hermes_test_muse_code",):
            sys.modules.pop(mod, None)
        spec = importlib.util.spec_from_file_location(
            "_hermes_test_muse_code", PLUGIN_FILE
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["_hermes_test_muse_code"] = module
        spec.loader.exec_module(module)
    return module, registered


class _Proc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_provider_registered_with_expected_wiring():
    module, registered = _load_plugin()
    profile = registered["muse-code"]
    assert profile.base_url == "https://api.meta.ai/v1"
    assert profile.api_mode == "codex_responses"
    assert profile.auth_type == "api_key"
    assert profile.env_vars == ("MUSE_CODE_SUB_TOKEN",)
    assert profile.fallback_models == ("muse-spark-1.3",)


def test_fetch_extracts_apikey_from_omp_blob():
    module, _ = _load_plugin()
    blob = json.dumps({"apiKey": "LLM|test-key", "oauthAccessToken": "dca-x"})
    with (
        patch.object(module.shutil, "which", return_value="omp"),
        patch.object(
            module.subprocess, "run", return_value=_Proc(0, blob + "\n")
        ) as run,
    ):
        assert module._fetch_omp_subscription_key() == "LLM|test-key"
        assert run.call_args.args[0][:3] == ["omp", "token", "muse-code"]


def test_fetch_misses_silently():
    module, _ = _load_plugin()
    with patch.object(module.shutil, "which", return_value=None):
        assert module._fetch_omp_subscription_key() == ""
    with (
        patch.object(module.shutil, "which", return_value="omp"),
        patch.object(
            module.subprocess, "run", return_value=_Proc(1, "boom")
        ),
    ):
        assert module._fetch_omp_subscription_key() == ""
    with (
        patch.object(module.shutil, "which", return_value="omp"),
        patch.object(
            module.subprocess, "run", return_value=_Proc(0, "not-json")
        ),
    ):
        assert module._fetch_omp_subscription_key() == ""
    with (
        patch.object(module.shutil, "which", return_value="omp"),
        patch.object(
            module.subprocess,
            "run",
            return_value=_Proc(0, json.dumps({"nope": 1})),
        ),
    ):
        assert module._fetch_omp_subscription_key() == ""


def test_explicit_env_wins_over_fetch():
    module, _ = _load_plugin(env={"MUSE_CODE_SUB_TOKEN": "pinned"})
    with patch.object(
        module.subprocess,
        "run",
        side_effect=AssertionError("must not shell out"),
    ):
        module._ensure_subscription_key()
        assert os.environ["MUSE_CODE_SUB_TOKEN"] == "pinned"


def test_reasoning_effort_mapping():
    module, registered = _load_plugin()
    profile = registered["muse-code"]
    assert profile.build_api_kwargs_extras(
        reasoning_config={"enabled": False}
    ) == ({}, {"reasoning_effort": "minimal"})
    assert profile.build_api_kwargs_extras(
        reasoning_config={"effort": "none"}
    ) == ({}, {"reasoning_effort": "minimal"})
    assert profile.build_api_kwargs_extras(
        reasoning_config={"effort": "high"}
    ) == ({}, {"reasoning_effort": "high"})
    assert profile.build_api_kwargs_extras(reasoning_config={}) == (
        {},
        {"reasoning_effort": "medium"},
    )

def test_importable_without_hermes_runtime():
    """Plain import (no stubbed Hermes modules) registers nothing but keeps
    the key helpers usable — this is what pytest collection relies on."""
    os.environ["HERMES_MUSE_CODE_SUB_AUTO"] = "0"
    os.environ.pop("MUSE_CODE_SUB_TOKEN", None)
    for mod in ("agent", "agent.reasoning_effort", "providers", "providers.base"):
        assert mod not in sys.modules
    spec = importlib.util.spec_from_file_location(
        "_hermes_test_muse_code_bare", PLUGIN_FILE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._HERMES_AVAILABLE is False
    assert module._fetch_omp_subscription_key() == "" or isinstance(
        module._fetch_omp_subscription_key(), str
    )

def test_register_entry_point_is_probe_safe():
    """`hermes plugins validate` requires register() and runs it in a bare
    interpreter: it must exist, take any ctx, and never raise."""
    module, _ = _load_plugin(env={"MUSE_CODE_SUB_TOKEN": "pinned"})
    assert callable(getattr(module, "register", None))
    module.register(object())
    assert os.environ["MUSE_CODE_SUB_TOKEN"] == "pinned"

def _stub_base(profile):
    """The stubbed Hermes base class (super() target of the profile)."""
    return type(profile).__mro__[1]


def test_fetch_models_filters_non_chat():
    module, registered = _load_plugin()
    profile = registered["muse-code"]
    live = [
        "muse-spark-1.3",
        "muse-spark-1.2-contributor",
        "muse-image-1.0",
        "muse-voice-transcribe-1.0",
    ]
    with patch.object(_stub_base(profile), "fetch_models", return_value=live):
        assert profile.fetch_models(api_key="k") == [
            "muse-spark-1.3",
            "muse-spark-1.2-contributor",
        ]
    with patch.object(_stub_base(profile), "fetch_models", return_value=None):
        assert profile.fetch_models(api_key="k") is None


def test_no_secret_material_in_logs(caplog):
    import logging

    module, _ = _load_plugin()
    blob = json.dumps(
        {"apiKey": "LLM|sentinel-secret", "oauthAccessToken": "dca-x"}
    )
    with (
        caplog.at_level(logging.DEBUG, logger=module.logger.name),
        patch.object(module.shutil, "which", return_value="omp"),
        patch.object(
            module.subprocess, "run", return_value=_Proc(0, blob)
        ),
    ):
        assert module._fetch_omp_subscription_key() == "LLM|sentinel-secret"
    assert "LLM|sentinel-secret" not in caplog.text
    assert "dca-x" not in caplog.text
