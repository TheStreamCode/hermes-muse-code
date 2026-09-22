"""Local equivalent of the upstream `plugin-validate` action.

Why this exists (no upstream dependency): the official
`NousResearch/hermes-agent/.github/actions/plugin-validate` action
pip-installs `hermes-agent` from git (`pip install
"git+https://github.com/NousResearch/hermes-agent@<ref>"`), but
hermes-agent's own `setup.py` build guard rejects wheel/sdist builds
outside a sealed Nix derivation ("Building wheels or sdists for
hermes-agent is not supported ... distributed via the shell installer,
Docker image, or Nix"). The action has a single revision in history, so
there is no last-good action version to pin — every ref fails the same
way. This script therefore performs the same admission checks locally,
stdlib-only, without installing hermes-agent:

  1. plugin.yaml manifest schema (required fields, requires_hermes spec,
     config_schema / requires_env shapes),
  2. stdlib-only imports (hermes-provided `agent` / `providers` exempt),
  3. capability probe: import registers a provider and `register(ctx)`
     runs probe-safe against a recording stub context.

Usage: python .github/scripts/validate-plugin.py [--path .]
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import re
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs pyyaml explicitly
    yaml = None

_VERSION_CLAUSE_RE = re.compile(
    r"^(>=|<=|==|!=|>|<|~=)?\s*\d+(\.\d+)*(\.\*)?\s*$"
)
_UPPER_SNAKE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_CONFIG_TYPES = {
    "str", "string", "int", "integer", "float", "number",
    "bool", "boolean", "list", "array", "dict", "mapping", "map",
}
# Top-level modules provided by the Hermes runtime at load time (not pip
# dependencies), plus everything in the stdlib.
_RUNTIME_PROVIDED = {"agent", "providers"}

_failures: list[str] = []
_warnings: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        _failures.append(f"{name}: {detail}" if detail else name)


def warn(message: str) -> None:
    print(f"[WARN] {message}")
    _warnings.append(message)


def _requires_hermes_spec_valid(spec: str) -> bool:
    return all(
        _VERSION_CLAUSE_RE.match(clause.strip())
        for clause in spec.split(",")
        if clause.strip()
    )


def _check_manifest(manifest: dict) -> None:
    missing = [f for f in ("name", "version", "description") if not manifest.get(f)]
    check(
        "manifest fields",
        not missing,
        "name, version, description present"
        if not missing
        else f"plugin.yaml missing required field(s): {', '.join(missing)}",
    )
    spec = str(manifest.get("requires_hermes") or "").strip()
    if not spec:
        check("requires_hermes", True, "not declared")
    else:
        check(
            "requires_hermes",
            _requires_hermes_spec_valid(spec),
            f"spec {spec!r} parses"
            if _requires_hermes_spec_valid(spec)
            else f"spec {spec!r} does not parse "
            '(expected e.g. ">=0.21.3" or ">=0.21, <1.0")',
        )
    raw = manifest.get("config_schema")
    if raw in (None, [], {}):
        check("config schema", True, "not declared")
    elif not isinstance(raw, dict):
        check("config schema", False, "config_schema: must be a mapping")
    else:
        problems = []
        for skey, entry in raw.items():
            if not isinstance(entry, dict):
                problems.append(f"config_schema.{skey}: must be a mapping")
                continue
            typ = entry.get("type")
            if typ is not None and str(typ).lower() not in _CONFIG_TYPES:
                problems.append(f"config_schema.{skey}: bad type {typ!r}")
            if "required" in entry and not isinstance(entry["required"], bool):
                problems.append(f"config_schema.{skey}: required must be bool")
        check(
            "config schema",
            not problems,
            "shape valid" if not problems else "; ".join(problems),
        )
    raw_env = manifest.get("requires_env") or []
    if not isinstance(raw_env, list):
        check("requires_env", False, "requires_env: must be a list")
    else:
        problems = []
        for i, entry in enumerate(raw_env):
            name = entry if isinstance(entry, str) else str((entry or {}).get("name") or "")
            if not _UPPER_SNAKE_RE.match(name):
                problems.append(f"requires_env[{i}]: {name!r} not UPPER_SNAKE_CASE")
        check(
            "requires_env",
            not problems,
            "all entries UPPER_SNAKE" if not problems else "; ".join(problems),
        )


def _check_stdlib_only(plugin_dir: Path) -> None:
    stdlib = set(sys.stdlib_module_names)
    problems = []
    for path in sorted(plugin_dir.glob("*.py")):
        if path.name == "validate-plugin.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            problems.append(f"{path.name}: syntax error: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue  # relative import within the plugin
                mods = [node.module.split(".")[0]] if node.module else []
            else:
                continue
            for mod in mods:
                if mod not in stdlib and mod not in _RUNTIME_PROVIDED:
                    problems.append(f"{path.name}: third-party import {mod!r}")
    check(
        "stdlib-only",
        not problems,
        "no third-party runtime imports"
        if not problems
        else "; ".join(sorted(set(problems))),
    )


class _RecordingContext:
    plugin_config = {}
    profile_name = "default"
    plugin_id = "local_validate_probe"

    def __init__(self) -> None:
        self.tools: list[str] = []
        self.hooks: list[str] = []
        self.middleware: list[str] = []

    def register_tool(self, name, *args, **kwargs) -> None:
        self.tools.append(str(name))

    def register_hook(self, hook_name, callback) -> None:
        self.hooks.append(str(hook_name))

    def register_middleware(self, kind, callback) -> None:
        self.middleware.append(str(kind))

    def register_command(self, name, *args, **kwargs) -> None:
        self.tools.append(str(name))

    register_cli_command = register_command

    def get_config(self, key, default=None):
        return default


def _check_capability_probe(plugin_dir: Path, manifest: dict) -> None:
    init = plugin_dir / "__init__.py"
    if not init.is_file():
        warn("no __init__.py — capability probe skipped (manifest-only plugin)")
        check("capability probe", True, "skipped (no __init__.py)")
        return
    import types

    scratch = tempfile.mkdtemp(prefix="hermes-local-validate-")
    os.environ["MUSE_CODE_SUB_CREDENTIALS"] = os.path.join(scratch, "absent.json")
    os.environ.pop("MUSE_CODE_SUB_TOKEN", None)

    recorded_providers: list[str] = []

    providers_mod = types.ModuleType("providers")

    def _record_provider(profile) -> None:
        recorded_providers.append(str(getattr(profile, "name", profile)))

    providers_mod.register_provider = _record_provider  # type: ignore[attr-defined]

    base_mod = types.ModuleType("providers.base")

    class ProviderProfile:  # minimal stub of the runtime base
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

        def fetch_models(self, **kwargs):
            return None

    base_mod.ProviderProfile = ProviderProfile  # type: ignore[attr-defined]

    agent_mod = types.ModuleType("agent")
    reasoning_mod = types.ModuleType("agent.reasoning_effort")
    reasoning_mod.META_AI_EFFORTS = (  # type: ignore[attr-defined]
        "minimal", "low", "medium", "high", "xhigh", "max",
    )
    reasoning_mod.clamp_effort = lambda effort, efforts: (  # type: ignore[attr-defined]
        effort if effort in efforts else "medium"
    )

    saved = {k: sys.modules.get(k) for k in ("providers", "providers.base", "agent", "agent.reasoning_effort")}
    sys.modules["providers"] = providers_mod
    sys.modules["providers.base"] = base_mod
    sys.modules["agent"] = agent_mod
    sys.modules["agent.reasoning_effort"] = reasoning_mod
    try:
        spec = importlib.util.spec_from_file_location(
            "local_validate_probe_plugin", str(init),
            submodule_search_locations=[str(plugin_dir)],
        )
        module = importlib.util.module_from_spec(spec)
        module.__path__ = [str(plugin_dir)]
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - report, don't crash
            check("capability probe", False, f"import failed: {exc}")
            return
        if manifest.get("kind") == "model-provider":
            check(
                "capability probe",
                bool(recorded_providers),
                "import registered provider(s): " + ", ".join(recorded_providers)
                if recorded_providers
                else "model-provider plugin registered no ProviderProfile at import",
            )
        else:
            check("capability probe", True, "import ok")
        register = getattr(module, "register", None)
        if register is None:
            check("capability probe", False, "no register() function")
            return
        try:
            ctx = _RecordingContext()
            register(ctx)
        except Exception as exc:  # noqa: BLE001 - report, don't crash
            check("capability probe", False, f"register() raised: {exc}")
            return
        for kind, attr, manifest_key in (
            ("tools", "tools", "provides_tools"),
            ("hooks", "hooks", "provides_hooks"),
            ("middleware", "middleware", "provides_middleware"),
        ):
            declared = set(manifest.get(manifest_key) or [])
            actual = set(getattr(ctx, attr))
            undeclared = sorted(actual - declared)
            if undeclared:
                check(
                    f"declared {kind}",
                    False,
                    f"undeclared {kind} registered (not in {manifest_key}): "
                    + ", ".join(undeclared),
                )
            else:
                check(f"declared {kind}", True, "matches registrations")
            unregistered = sorted(declared - actual)
            if unregistered:
                warn(
                    f"{manifest_key} declares {', '.join(unregistered)} "
                    "but register() did not register them"
                )
    finally:
        sys.modules.pop("local_validate_probe_plugin", None)
        for key, mod in saved.items():
            if mod is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = mod


def main() -> int:
    parser = argparse.ArgumentParser(description="Local plugin validation")
    parser.add_argument("--path", default=".", help="plugin directory")
    args = parser.parse_args()
    plugin_dir = Path(args.path).resolve()
    if not plugin_dir.is_dir():
        check("plugin directory", False, f"{plugin_dir} is not a directory")
        return 1
    manifest_file = plugin_dir / "plugin.yaml"
    if not manifest_file.is_file():
        manifest_file = plugin_dir / "plugin.yml"
    if not manifest_file.is_file():
        check("manifest", False, "no plugin.yaml in the plugin directory")
        return 1
    if yaml is None:
        check("manifest", False, "pyyaml not installed (CI must pip install pyyaml)")
        return 1
    try:
        manifest = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - report, don't crash
        check("manifest", False, f"plugin.yaml does not parse: {exc}")
        return 1
    if not isinstance(manifest, dict):
        check("manifest", False, "plugin.yaml top level must be a mapping")
        return 1
    check("manifest", True, "plugin.yaml parses")
    _check_manifest(manifest)
    _check_stdlib_only(plugin_dir)
    for path in sorted(plugin_dir.glob("*.py")):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            check("compile", False, f"{path.name}: {exc}")
            return 1
    check("compile", True, "all modules compile")
    _check_capability_probe(plugin_dir, manifest)
    if _failures:
        print(f"\nFAIL: {len(_failures)} check(s) failed")
        return 1
    print("\nPASS: plugin validated cleanly (local validator)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
