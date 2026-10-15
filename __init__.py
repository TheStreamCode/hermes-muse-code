"""Muse Code subscription provider — Muse chat models billed to the monthly sub.

No third-party CLI required. Authentication is a Meta device-code login
(performed once via ``muse_code_login.py``): the device flow mints a
stable, account-bound inference key that this provider reads from a local
credential cache. The flow parameters are compatible with the published
behavior of oh-my-pi (MIT-licensed; see NOTICE).

Resolution order: an explicitly configured ``MUSE_CODE_SUB_TOKEN`` wins;
otherwise the cached key from the login step. Any failure is a silent
miss (provider simply shows as unconfigured); misses are debug-logged
without secret material.

Credential cache: ``$HERMES_HOME/muse-code-sub.json`` (override with
``MUSE_CODE_SUB_CREDENTIALS``).

The Hermes imports are guarded: outside the Hermes runtime (pytest
collection, linters) the module stays importable and registers nothing.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

ENV_VAR = "MUSE_CODE_SUB_TOKEN"
CREDENTIALS_ENV_VAR = "MUSE_CODE_SUB_CREDENTIALS"
CREDENTIALS_FILENAME = "muse-code-sub.json"

try:
    from providers import register_provider
    from providers.base import ProviderProfile
except ImportError:
    _HERMES_AVAILABLE = False
else:
    _HERMES_AVAILABLE = True


def default_credentials_path() -> str:
    """Location of the device-login credential cache."""
    override = os.getenv(CREDENTIALS_ENV_VAR, "").strip()
    if override:
        return override
    home = os.getenv("HERMES_HOME", "").strip() or os.path.join(
        os.path.expanduser("~"), ".hermes"
    )
    return os.path.join(home, CREDENTIALS_FILENAME)


def read_cached_key(path: str | None = None) -> str:
    """Return the cached subscription key, or "" when absent/unusable."""
    try:
        with open(path or default_credentials_path(), encoding="utf-8") as fh:
            blob = json.load(fh)
    except Exception:
        return ""
    if not isinstance(blob, dict):
        return ""
    key = blob.get("apiKey", "")
    return key.strip() if isinstance(key, str) and key.strip() else ""


def _ensure_subscription_key() -> None:
    if os.getenv(ENV_VAR, "").strip():
        return  # explicit configuration keeps priority
    try:
        key = read_cached_key()
    except Exception:
        return
    if key:
        os.environ[ENV_VAR] = key
    else:
        logger.debug(
            "no %s and no usable credential cache; run muse_code_login.py",
            ENV_VAR,
        )


_ensure_subscription_key()


def register(ctx) -> None:
    """PluginManager entry point (also required by `hermes plugins validate`).

    Provider registration already happened at import; re-read the cache so
    a login that completed after import is picked up. Stdlib-only, so the
    admission probe can run it in a bare interpreter.
    """
    _ensure_subscription_key()


# Meta Model API (Muse) wire vocabulary, vendored from Hermes core
# (agent/reasoning_effort.py, META_AI_EFFORTS @ Hermes v0.22.0): Meta
# rejects ``none``. Catalog rule 9 forbids third-party plugins from
# importing private core modules, so the two names the bundled meta-ai
# profile imports are copied here instead of imported. Like base_url and
# api_mode below, this is a Meta wire fact the plugin already pins; if
# Meta's accepted levels ever change, update this tuple (and the pin test).
_META_WIRE_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")

# Canonical low→high ladder, vendored alongside the tuple above: clamp
# targets resolve against this ordering.
_EFFORT_LADDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


def _clamp_effort(effort, supported):
    """Clamp a requested effort onto a wire's supported levels.

    Same contract as Hermes core ``clamp_effort`` for this call shape
    (no vendor overrides on the Meta wire): verbatim when supported,
    unknown, or empty; else the nearest weaker supported level, never
    escalating cost; else the weakest supported level. ``none`` is
    never a degradation target.
    """
    requested = str(effort or "").strip().lower()
    if not requested or not supported:
        return effort
    supported_norm = [lvl for lvl in (str(s).strip().lower() for s in supported) if lvl in _EFFORT_LADDER]
    if not supported_norm or requested in supported_norm:
        return effort
    if requested not in _EFFORT_LADDER:
        return effort
    candidates = [level for level in supported_norm if level != "none"]
    if not candidates:
        return effort
    requested_idx = _EFFORT_LADDER.index(requested)
    below = [level for level in candidates if _EFFORT_LADDER.index(level) < requested_idx]
    return max(below, key=_EFFORT_LADDER.index) if below else min(candidates, key=_EFFORT_LADDER.index)


if _HERMES_AVAILABLE:
    # Intentional parity copy of the bundled `meta-ai` provider profile: the
    # wire quirks below (Responses API, reasoning_effort mapping, vision
    # limits) must stay in sync with it; do not "simplify" them away.
    # The effort clamp is vendored above (catalog rule 9 forbids importing
    # it); keep it in sync with agent/reasoning_effort.py too.
    class MuseCodeSubscriptionProfile(ProviderProfile):
        """Meta Model API via the Muse Code subscription."""

        # Dynamic discovery: admit every model id the live account catalog
        # returns, except the endpoint families verified as non-chat
        # (segmentation, image, voice). New chat models appear without a
        # plugin update; only a brand-new non-chat family needs an entry here.
        _NON_CHAT_PREFIXES = ("sam-", "muse-image-", "muse-voice-")

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
                clamped = _clamp_effort(effort, _META_WIRE_EFFORTS)
                mapped = clamped if clamped in _META_WIRE_EFFORTS else "medium"
            return {}, {"reasoning_effort": mapped}

    muse_code = MuseCodeSubscriptionProfile(
        name="muse-code",
        display_name="Muse Code (subscription)",
        description="Muse chat models billed to the local Muse Code monthly login",
        signup_url="https://github.com/TheStreamCode/hermes-muse-code#login-once",
        env_vars=(ENV_VAR,),
        base_url=os.getenv("META_BASE_URL", "").strip() or "https://api.meta.ai/v1",
        auth_type="api_key",
        # Responses API engages Muse prompt caching; same wire as meta-ai.
        api_mode="codex_responses",
        supports_vision=True,
        supports_vision_tool_messages=False,
        default_max_tokens=16384,
        # default_aux_model intentionally unset (= main model): the bundled
        # profile's contributor-tier default would silently opt background
        # compression into data training.
        fallback_models=("muse-spark-1.3",),
    )

    register_provider(muse_code)
