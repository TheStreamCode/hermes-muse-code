"""One-time Meta device-code login for the muse-code subscription plugin.

Performs the login, mints the stable account-bound inference key, and
writes the credential cache the provider reads. Stdlib only.

The flow parameters are compatible with the published behavior of oh-my-pi
(MIT-licensed; see NOTICE). No third-party CLI is required.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CLIENT_ID = "1031625952748946"
DEVICE_URL = "https://auth.meta.com/oidc/device/authorization/"
TOKEN_URL = "https://auth.meta.com/oidc/device/token/"
KEY_URL = "https://api.meta.ai/muse-code/key"
API_VERSION = "1.0.0"
REQUEST_TIMEOUT_SECS = 25
MIN_POLL_INTERVAL_SECS = 1.0
SLOW_DOWN_INCREMENT_SECS = 5.0


class LoginError(Exception):
    """Fatal login failure with a human-readable message."""


def _post_form(url, params):
    """POST form-encoded params; return the decoded JSON body or raise."""
    try:
        request = urllib.request.Request(
            url,
            data=urllib.parse.urlencode(params).encode(),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "x-api-version": API_VERSION,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECS) as response:
            return json.load(response), response.status
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:300].strip()
        except Exception:
            detail = ""
        raise LoginError(f"request to {url} failed: HTTP {exc.code} {detail}".strip())
    except LoginError:
        raise
    except Exception as exc:
        raise LoginError(f"request to {url} failed: {exc}")


def _post_form_lenient(url, params):
    """POST form params; return (body, status) without raising on HTTP errors.

    The token endpoint answers pending/slow_down polls with HTTP errors as
    part of the normal flow: callers classify instead of catching.
    """
    try:
        request = urllib.request.Request(
            url,
            data=urllib.parse.urlencode(params).encode(),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "x-api-version": API_VERSION,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECS) as response:
            status = response.status
            try:
                return json.load(response), status
            except Exception:
                return None, status
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8", "replace")), exc.code
        except Exception:
            return None, exc.code
    except Exception as exc:
        raise LoginError(f"request to {url} failed: {exc}")


def _post_json(url, payload, bearer):
    """POST a JSON payload with bearer auth; return the decoded body."""
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {bearer}",
                "x-api-version": API_VERSION,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECS) as response:
            return json.load(response), response.status
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:300].strip()
        except Exception:
            detail = ""
        raise LoginError(f"request to {url} failed: HTTP {exc.code} {detail}".strip())
    except LoginError:
        raise
    except Exception as exc:
        raise LoginError(f"request to {url} failed: {exc}")


def device_authorize():
    """Start the device flow; return device_code, user_code, urls, timing."""
    body, _ = _post_form(DEVICE_URL, {"client_id": CLIENT_ID})
    if not isinstance(body, dict):
        raise LoginError("device authorization returned an unexpected response")
    required = ("device_code", "user_code", "verification_uri", "interval", "expires_in")
    if any(not body.get(field) for field in required):
        raise LoginError("device authorization response is missing fields")
    return body


def poll_token(device_code, interval_seconds, expires_in_seconds):
    """Poll until the user completes the browser step; return access_token."""
    interval = max(MIN_POLL_INTERVAL_SECS, float(interval_seconds or 5))
    deadline = time.time() + float(expires_in_seconds or 600)
    slow_downs = 0
    while time.time() < deadline:
        body, _ = _post_form_lenient(
            TOKEN_URL,
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": CLIENT_ID,
                "device_code": device_code,
            },
        )
        if not isinstance(body, dict):
            body = {"error": "unreadable token response"}
        error = body.get("error")
        if not error:
            access = body.get("access_token")
            if not access:
                raise LoginError("token response has no access_token")
            return access
        if error == "slow_down":
            slow_downs += 1
            interval = max(MIN_POLL_INTERVAL_SECS, interval + SLOW_DOWN_INCREMENT_SECS)
        elif error != "authorization_pending":
            raise LoginError(f"login failed: {error}")
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))
    if slow_downs:
        raise LoginError("login timed out after slow_down responses (check VM clock drift)")
    raise LoginError("login timed out waiting for browser approval")


def mint_key(access_token, onboard=True):
    """Exchange the account token for the stable subscription key."""
    body, _ = _post_json(KEY_URL, {"onboard": bool(onboard)}, access_token)
    if not isinstance(body, dict):
        raise LoginError("key endpoint returned an unexpected response")
    if body.get("is_subs_active") is False:
        raise LoginError("Muse Code subscription is inactive for this account")
    api_key = body.get("api_key", "")
    if not isinstance(api_key, str) or not api_key.strip():
        action = (body.get("action_url") or body.get("require_payment_action_url") or "").strip()
        if body.get("require_payment") is True or action:
            raise LoginError(
                f"Muse Code subscription is required{': ' + action if action else ''}"
            )
        raise LoginError("key response is missing api_key")
    email = (body.get("user_email") or "").strip().lower() or None
    account = (body.get("user_id") or "").strip() or email
    if not account:
        raise LoginError("key response is missing account identity")
    return {
        "oauthAccessToken": access_token,
        "apiKey": api_key.strip(),
        "accountId": account,
        "email": email,
    }


def write_cache(credentials, path):
    """Persist credentials with owner-only permissions where supported."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(credentials, fh, indent=2)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass  # Windows ACLs: file inherits user profile permissions


def main(argv=None):
    parser = argparse.ArgumentParser(description="Meta device-code login for muse-code")
    parser.add_argument("--cache", default=None, help="credential cache path")
    args = parser.parse_args(argv)
    home = os.getenv("HERMES_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".hermes")
    cache = args.cache or os.getenv("MUSE_CODE_SUB_CREDENTIALS", "").strip() or os.path.join(
        home, "muse-code-sub.json"
    )
    try:
        device = device_authorize()
        print(f"Open {device['verification_uri_complete'] or device['verification_uri']}")
        print(f"Enter code: {device['user_code']}")
        access = poll_token(device["device_code"], device["interval"], device["expires_in"])
        credentials = mint_key(access)
        write_cache(credentials, cache)
    except LoginError as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1
    print(f"Logged in as {credentials.get('email') or credentials['accountId']} (key cached, never printed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
