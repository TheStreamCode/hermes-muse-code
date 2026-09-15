"""Muse Code subscription provider — Muse Spark billed to the monthly sub.

Reuses the local ``omp`` Muse Code login instead of a pay-as-you-go API
key. On import it shells out to ``omp token muse-code --raw`` (omp owns
OAuth + refresh), extracts the subscription-bound ``apiKey`` field, and
exposes it through ``MUSE_CODE_SUB_TOKEN`` as a regular ``api_key``
provider against https://api.meta.ai/v1.

Resolution order: an explicitly configured ``MUSE_CODE_SUB_TOKEN`` wins;
otherwise a fresh per-process fetch from omp. Any failure is a silent
miss (provider simply shows as unconfigured). Secret material is never
logged. Set ``HERMES_MUSE_CODE_SUB_AUTO=0`` to disable the auto-fetch.

The Hermes imports are guarded: outside the Hermes runtime (pytest
collection, linters) the module stays importable and registers nothing.
"""

import json
import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

ENV_VAR = "MUSE_CODE_SUB_TOKEN"
_OPT_OUT = "HERMES_MUSE_CODE_SUB_AUTO"
_OMP_TIMEOUT_SECS = 25

try:
    from agent.reasoning_effort import META_AI_EFFORTS, clamp_effort
    from providers import register_provider
    from providers.base import ProviderProfile
except ImportError:
    _HERMES_AVAILABLE = False
else:
    _HERMES_AVAILABLE = True


def _fetch_omp_subscription_key() -> str:
    """Return the subscription-bound inference key from the omp login, or ""."""
    binary = next(
        (b for b in ("omp", "omp.exe", "omp.cmd") if shutil.which(b)), ""
    )
    if not binary:
        return ""
    try:
        startupinfo = None
        if os.name == "nt":  # hide the helper's console window
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        proc = subprocess.run(
            [binary, "token", "muse-code", "--raw"],
            capture_output=True,
            text=True,
            timeout=_OMP_TIMEOUT_SECS,
            startupinfo=startupinfo,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    try:
        blob = json.loads(proc.stdout.strip())
    except Exception:
        return ""
    if not isinstance(blob, dict):
        return ""
    key = blob.get("apiKey", "")
    return key.strip() if isinstance(key, str) else ""


def _ensure_subscription_key() -> None:
    if os.getenv(ENV_VAR, "").strip():
        return  # explicit configuration keeps priority
    if os.getenv(_OPT_OUT, "1") == "0":
        return
    try:
        key = _fetch_omp_subscription_key()
    except Exception:
        return
    if key:
        os.environ[ENV_VAR] = key


_ensure_subscription_key()


if _HERMES_AVAILABLE:

    class MuseCodeSubscriptionProfile(ProviderProfile):
        """Meta Model API via the Muse Code subscription (reasoning quirks
        mirror the bundled ``meta-ai`` provider profile)."""

        _NON_CHAT_PREFIXES = ("muse-image-", "muse-voice-")

        def fetch_models(
            self,
            *,
            api_key: str | None = None,
            base_url: str | None = None,
            timeout: float = 8.0,
        ) -> list[str] | None:
            live = super().fetch_models(
                api_key=api_key, base_url=base_url, timeout=timeout
            )
            if live is None:
                return None
            return [
                m
                for m in live
                if not any(m.startswith(p) for p in self._NON_CHAT_PREFIXES)
            ]

        def build_api_kwargs_extras(
            self,
            *,
            reasoning_config: dict | None = None,
            supports_reasoning: bool = False,
            **context,
        ):
            rc = reasoning_config or {}
            effort = str(rc.get("effort") or "").strip().lower()
            if rc.get("enabled") is False or effort == "none":
                mapped = "minimal"
            else:
                clamped = clamp_effort(effort, META_AI_EFFORTS)
                mapped = clamped if clamped in META_AI_EFFORTS else "medium"
            return {}, {"reasoning_effort": mapped}

    muse_code = MuseCodeSubscriptionProfile(
        name="muse-code",
        display_name="Muse Code (subscription)",
        description="Muse Spark billed to the local Muse Code monthly login (via omp)",
        signup_url="https://developer.meta.com/ai/",
        env_vars=(ENV_VAR,),
        base_url="https://api.meta.ai/v1",
        auth_type="api_key",
        # Responses API engages Muse prompt caching; same wire as meta-ai.
        api_mode="codex_responses",
        supports_vision=True,
        supports_vision_tool_messages=False,
        default_max_tokens=16384,
        fallback_models=("muse-spark-1.3",),
    )

    register_provider(muse_code)
