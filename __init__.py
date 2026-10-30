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

Inference goes through a loopback wire adapter (``muse_adapter.py``): Meta
refuses subscription requests whose flat tool names collide with Muse Code's
builtin names, so the adapter aliases exactly those names on the wire and
restores them in the response. If the adapter cannot bind a loopback port,
the provider keeps the direct endpoint and logs a warning.

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
# Direct upstream and fallback endpoint; the profile normally points at the
# loopback wire adapter started by _adapter_base_url().
DEFAULT_BASE_URL = "https://api.meta.ai/v1"

# The subscription key is exported into the process environment because
# Hermes resolves api_key providers from env_vars (providers/base.py and
# agent/client_lifecycle.py); there is no provider callback for a raw key.
# Only values written here are touched on refresh; an explicit user value wins.
_ENV_KEY_OWNED: str | None = None

try:
    from providers import register_provider
    from providers.base import ProviderProfile
except ImportError:
    _HERMES_AVAILABLE = False
    _start_adapter = None
else:
    _HERMES_AVAILABLE = True
    try:
        from .muse_adapter import start_adapter as _start_adapter
    except ImportError:  # pragma: no cover - only a broken install layout
        _start_adapter = None


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
    """Keep the provider env var aligned with the login cache.

    Refresh the value when the cache changed, remove it when the cache is gone,
    and never overwrite a value the user set explicitly.
    """
    global _ENV_KEY_OWNED
    explicit = os.getenv(ENV_VAR, "").strip()
    if explicit and explicit != _ENV_KEY_OWNED:
        _ENV_KEY_OWNED = None
        return
    key = read_cached_key()
    if key:
        if key != _ENV_KEY_OWNED:
            os.environ[ENV_VAR] = key
            _ENV_KEY_OWNED = key
    elif _ENV_KEY_OWNED is not None:
        os.environ.pop(ENV_VAR, None)
        _ENV_KEY_OWNED = None


def _adapter_base_url() -> str:
    """Start the loopback wire adapter once and return the provider base URL.

    Meta refuses subscription requests whose flat tool names collide with Muse
    Code's builtin names; ``muse_adapter`` aliases exactly those names on the
    wire and restores them in the response. Starting is idempotent, so repeated
    imports and registrations reuse the same server. If no loopback port can be
    bound, keep the direct endpoint (the pre-0.6 behavior) and warn: only the
    exception type is logged, never credentials or payloads.
    """
    if _start_adapter is None:
        return DEFAULT_BASE_URL
    try:
        adapter = _start_adapter(log=logger.debug)
    except Exception as error:  # noqa: BLE001 - a bind failure must not break import
        logger.warning(
            "Muse Code wire adapter unavailable (%s); using %s directly",
            type(error).__name__,
            DEFAULT_BASE_URL,
        )
        return DEFAULT_BASE_URL
    return adapter.base_url


def _refresh_base_url() -> None:
    """Point the registered profile at the adapter, or at the fallback endpoint."""
    base_url = _adapter_base_url()
    if muse_code.base_url != base_url:
        muse_code.base_url = base_url


def register(ctx) -> None:
    """PluginManager entry point (also required by `hermes plugins validate`).

    Provider registration already happened at import; re-read the cache so
    a login that completed after import is picked up, and retry the wire
    adapter in case the first bind failed. Stdlib-only, so the admission probe
    can run it in a bare interpreter.
    """
    if _HERMES_AVAILABLE:
        _ensure_subscription_key()
        _refresh_base_url()


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


def _classify_client_restriction(error, *, status_code, error_code, message, body, model):
    """Distinguish Meta's client restriction from a revoked credential."""
    if status_code != 403:
        return None
    text = str(message or "").lower()
    if error_code == "client_attestation_denied" or (
        "muse code subscriptions cover requests from the muse code client only" in text
    ):
        return {
            "reason": "provider_policy_blocked",
            "retryable": False,
            "should_rotate_credential": False,
        }
    return None


def _run_browser_login(*, open_browser=True):
    from .muse_code_login import main

    return main([], open_browser=open_browser)


def _auth_handler(action, args):
    if action != "add":
        return False
    if _run_browser_login(open_browser=not getattr(args, "no_browser", False)) != 0:
        raise SystemExit("Muse Code login failed.")
    _ensure_subscription_key()
    return True


if _HERMES_AVAILABLE:
    _ensure_subscription_key()
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
            _ensure_subscription_key()
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
        # Loopback wire adapter in front of api.meta.ai; DEFAULT_BASE_URL when
        # the adapter could not bind and the provider runs direct.
        base_url=_adapter_base_url(),
        auth_type="api_key",
        auth_handler=_auth_handler,
        classify_api_error=_classify_client_restriction,
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
