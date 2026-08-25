# jupyter-server-proxy configuration for running Positron Server under a
# STANDALONE jupyter-server (no JupyterHub) — e.g. SageMaker Studio's per-app
# JupyterLab server, which serves under base URL /jupyterlab/default.
#
# Background
# ----------
# positron-server is a VS Code REH-web server. Behind a proxy it exhibits two
# quirks that must be corrected:
#
#  1. Doubled relative URLs. Given --server-base-path /positron it emits asset
#     URLs that repeat the base path (".../positron/positron/<commit>/static").
#
#  2. X-Forwarded-Prefix. If the proxy sends X-Forwarded-Prefix, positron uses
#     it (not --server-base-path) to build redirect Location headers, so the
#     Location carries the *full* mount path; jupyter-server-proxy then prepends
#     the mount again, doubling it.
#
# The jupyter-positron-server PyPI package addresses these, but:
#  - its mappath/rewrite_response regexes are hardcoded to JupyterHub's
#    /user/<name>/positron URL structure (no match under standalone jupyter), and
#  - its `request_headers_override: {"X-Forwarded-Prefix": ""}` is INEFFECTIVE
#    against jupyter-server-proxy >= 4.5, which sets X-Forwarded-Prefix
#    unconditionally in handlers.py:_build_proxy_request AFTER overrides apply.
#
# So here we (a) strip the X-Forwarded-* prefix headers at the source by
# patching _build_proxy_request, and (b) collapse the residual "/positron/
# positron" doubling in both request paths (mappath) and redirect Location
# headers (rewrite_response). These rules are base_url-independent: after
# jupyter-server-proxy strips its own mount, the doubling is always the literal
# "/positron/positron", whether the outer base URL is "/" or "/jupyterlab/default".

import logging
import os as _os
import secrets as _secrets

# Fix positron-server's web connection token for THIS process BEFORE importing
# jupyter_positron_server (which reads POSITRON_CONNECTION_TOKEN at import time).
# We don't need it stable across containers — we need jupyter_positron_server and
# our own proxy-side token injection (see proxy_request_headers patch below) to
# agree on the SAME value within this process, so a random per-container token is
# both sufficient and more secure than a baked constant. setdefault() lets an
# explicit env override win if ever provided.
_os.environ.setdefault("POSITRON_CONNECTION_TOKEN", _secrets.token_hex(16))
_POSITRON_TOKEN = _os.environ["POSITRON_CONNECTION_TOKEN"]

import jupyter_server_proxy.handlers as _jsp_handlers  # noqa: E402
from jupyter_positron_server import setup_positron_server  # noqa: E402
from urllib.parse import urlparse, urlunparse  # noqa: E402

logger = logging.getLogger("positron_proxy_config")

# (a) Stop jupyter-server-proxy from advertising the proxy prefix to positron,
# so positron builds URLs from --server-base-path (/positron) alone.
_PREFIX_HEADERS = ("X-Forwarded-Prefix", "X-Forwarded-Context", "X-ProxyContextPath")
_orig_build = _jsp_handlers.ProxyHandler._build_proxy_request


def _build_proxy_request(self, host, port, proxied_path, body, **extra):
    req = _orig_build(self, host, port, proxied_path, body, **extra)
    for header in _PREFIX_HEADERS:
        if header in req.headers:
            del req.headers[header]
    return req


_jsp_handlers.ProxyHandler._build_proxy_request = _build_proxy_request


# (a2) Present positron-server's connection token to the backend on EVERY proxied
# request — both HTTP and WebSocket. positron-server mandates a connection token
# in web mode and otherwise returns 403; normally the browser obtains it via a
# `?tkn=` handshake that sets a `vscode-tkn` cookie. But SageMaker deep-links the
# browser straight to /jupyterlab/default/positron/ (no handshake, no cookie), so
# we cannot depend on the browser ever having the token. Instead, the proxy — which
# is already gated by jupyter authentication (SageMaker identity) — injects the
# token cookie itself. proxy_request_headers() is the single header builder used by
# BOTH the HTTP path (_build_proxy_request) and the WebSocket path (proxy_open), so
# patching it covers everything. We strip any (possibly stale) vscode-tkn the
# browser sent and set the correct one, leaving other cookies intact.
_orig_proxy_headers = _jsp_handlers.ProxyHandler.proxy_request_headers


def proxy_request_headers(self):
    headers = _orig_proxy_headers(self)
    if _POSITRON_TOKEN:
        cookie = headers.get("Cookie", "")
        parts = [
            c.strip()
            for c in cookie.split(";")
            if c.strip() and not c.strip().lower().startswith("vscode-tkn=")
        ]
        parts.append("vscode-tkn=" + _POSITRON_TOKEN)
        headers["Cookie"] = "; ".join(parts)
    return headers


_jsp_handlers.ProxyHandler.proxy_request_headers = proxy_request_headers


# (b) Collapse the literal "/positron/positron" doubling, anywhere it appears.
def _collapse(path):
    marker = "/positron/positron"
    idx = path.find(marker)
    if idx != -1:
        return path[:idx] + "/positron" + path[idx + len(marker):]
    return path


def mappath(path):
    return _collapse(path)


def rewrite_response(response, request):
    for header, value in list(response.headers.items()):
        if header.lower() == "location":
            parsed = urlparse(value)
            collapsed = _collapse(parsed.path)
            if collapsed != parsed.path:
                response.headers[header] = urlunparse(parsed._replace(path=collapsed))
    return response


# (c) jupyter-server-proxy >= 4.5 added ProxyHandler._rewrite_location_header,
# which prepends the proxy prefix (/jupyterlab/default/positron) to a backend
# redirect's Location — and it runs AFTER rewrite_response, so the rule above
# cannot see its output. positron-server's connection-token handshake
# (GET /positron/?tkn=...) returns "Location: /positron" (built from its
# --server-base-path), so the prepend DOUBLES it to
# "/jupyterlab/default/positron/positron", which 404/403s. We wrap the method and
# collapse the doubling on its result. Without this, a fresh first landing 403s on
# the handshake; only a reload works (it already has the vscode-tkn cookie and so
# skips the handshake redirect). Guarded so older jsp (no such method) is a no-op.
if hasattr(_jsp_handlers.ProxyHandler, "_rewrite_location_header"):
    _orig_rewrite_location = _jsp_handlers.ProxyHandler._rewrite_location_header

    def _rewrite_location_header(self, location, host, port, proxied_path):
        return _collapse(
            _orig_rewrite_location(self, location, host, port, proxied_path)
        )

    _jsp_handlers.ProxyHandler._rewrite_location_header = _rewrite_location_header


cfg = setup_positron_server()
cfg["mappath"] = mappath
cfg["rewrite_response"] = rewrite_response

# Posit Assistant: Amazon Bedrock enforced settings.
# AdminPolicyService calls JSON.parse() directly on the env var value, so the
# var must carry inline JSON (not a file path). Read the baked file at launch
# time and inject it into the positron-server subprocess environment.
_enforced_settings_path = "/etc/positron/enforced-settings.json"
try:
    _enforced_settings = open(_enforced_settings_path).read().strip()
    # jupyter-server-proxy runs str.format() on every environment value (to
    # substitute {port} etc.), so literal braces in the JSON must be doubled to
    # survive templating — otherwise the JSON braces raise KeyError.
    cfg.setdefault("environment", {})["POSITRON_ENFORCED_SETTINGS"] = (
        _enforced_settings.replace("{", "{{").replace("}", "}}")
    )
    logger.info(f"Loaded POSITRON_ENFORCED_SETTINGS from {_enforced_settings_path}")
except FileNotFoundError:
    logger.warning(f"POSITRON_ENFORCED_SETTINGS file not found: {_enforced_settings_path}")

# Positron license: check real entitlement, then mint a signed license TOKEN at
# each positron-server launch.
#
# THIS IS THE FALLBACK PATH. Images built with the AWS License Manager client
# (the default — see LICENSE_MANAGER_CLIENT_VERSION in the Containerfile) take
# their licensing verdict from that client instead, and skip every Secrets
# Manager lookup described below. Everything from here down to the LM branch
# applies only to a build that deliberately opts out of the client.
#
# positron-server >= 2026.07.0 hardened licensing: it no longer accepts a raw
# `.lic` file as its OWN input — it requires a short-lived RSA-signed JSON token
# bound to the server's --connection-token, and it FAILS CLOSED (the process
# exits) without a valid one. So we fetch the licensee's SIGNING KEY (whose
# public half is embedded in positron-server) and mint the token in-process.
#
# BUT: the signing key alone lets anyone who holds it mint a validly-signed
# token regardless of whether there is a genuine Positron entitlement — the
# token proves the *session* is legitimate, not that the *deployment* is
# licensed. The real entitlement check is a SEPARATE mechanism: Posit's
# `license-manager` binary (bundled in positron-server's own
# resources/activation dir; it's the same rstudio::activation / TurboActivate
# machinery RStudio Server Pro/Workbench use) validates a genuine `.lic` file
# via `license-manager verify --output=json`. In the reference JupyterHub
# deployment this check lives in jupyter_positron_verifier's EntitlementChecker,
# gating its Hub-side minting service. We have no JupyterHub here (SageMaker's
# JupyterLab app is a standalone jupyter-server, not a Hub) and that service's
# /mint endpoint hard-requires a Hub API to authenticate callers anyway — so we
# vendor just the entitlement CHECK (a ~20-line subprocess call) synchronously,
# the same way we already vendor the Signer, rather than depending on
# jupyter-positron-verifier's full package (which would pull in fastapi/httpx/
# uvicorn for a Hub-auth path we can't use).
#
# Two load-bearing details:
#   * The token is valid only within +/-5 min of its timestamp, and positron-server
#     is launched LAZILY by jupyter-server-proxy (possibly long after this config
#     loads). So we check entitlement and mint inside a per-spawn `command`
#     callable, which jupyter-server-proxy re-evaluates at each process start
#     (handlers.py get_cmd) — NOT here at load time, where the token would be
#     stale by the time the user opens Positron.
#   * We sign inline with `cryptography` (RSA PKCS#1 v1.5 / SHA-256 over
#     connection_token + issuer + licensee + timestamp, timestamp = JavaScript
#     toISOString()). This is vendored verbatim from jupyter_positron_verifier's
#     Signer.mint (0.0.1) and matches positron-server's remoteLicenseKey.ts
#     verifier exactly — keep in sync if you bump POSITRON_VERSION. issuer/licensee
#     come from the entitlement check below (not cosmetic — they're the genuine
#     licensee/issuer off the activated license); positron-server itself does not
#     independently verify them, so getting them right matters for OUR gate, not
#     positron-server's.
#
# NOTE ON SECRECY (fallback path only): unlike the signing key's original
# intent, this does NOT hide the license file from the end user. SageMaker's
# single-container topology means the user already holds the execution role and
# can fetch either secret directly — that boundary was already gone before this change. What this DOES
# do is require a genuine, currently-activated license for minting to happen at
# all: without one, no token is issued and positron-server fails closed exactly
# as it does today for a missing signing key.
#
# Best-effort: any failure logs LOUDLY and leaves positron-server unlicensed (it
# then refuses to start), but never breaks the JupyterLab server itself.
import base64 as _base64
import json as _json
import subprocess as _subprocess
from datetime import datetime as _datetime, timezone as _timezone  # noqa: E402

_POSITRON_SERVER_DIR = "/opt/positron-server"
_POSITRON_ACTIVATION_DIR = f"{_POSITRON_SERVER_DIR}/resources/activation/linux/x86_64"
_LICENSE_MANAGER_PATH = f"{_POSITRON_ACTIVATION_DIR}/license-manager"
_LICENSE_FILE_PATH = f"{_POSITRON_ACTIVATION_DIR}/license.lic"

# --- AWS License Manager (preferred, when the client is present) -----------
# positron-server 2026.08+ accepts POSITRON_LICENSE_MANAGER_PATH: it runs the
# named binary, treats that client's verdict as the licensing decision, and
# ignores the key sources entirely (positron-dev/positron#15538). That is the
# mechanism for this image — the entitlement lives in AWS License Manager and
# is checked out per session under the execution role, so there is no signing
# key and no .lic to distribute.
#
# The variable is only exported when the client is actually installed: a
# POSITRON_LICENSE_MANAGER_PATH pointing at a missing binary makes
# positron-server fail immediately rather than fall back. The Containerfile
# installs the client by default (LICENSE_MANAGER_CLIENT_VERSION), so a build
# that deliberately empties that ARG is the only one that still takes the
# signing-key path below.
#
# Resolved HERE, before the Secrets Manager bootstrap, because that bootstrap is
# skipped whenever this is true (see the two blocks below). Only depends on _os,
# so it is safe this early.
_LM_CLIENT_PATH = "/usr/lib/positron-server/bin/license-manager-aws-sagemaker"
_LM_CLIENT_PRESENT = _os.path.isfile(_LM_CLIENT_PATH) and _os.access(_LM_CLIENT_PATH, _os.X_OK)


def _load_signing_key_pem():
    """Resolve the RSA signing-key PEM: explicit env first (local testing /
    override), then AWS Secrets Manager (the admin-managed, rotatable source)."""
    _pem = _os.environ.get("POSITRON_SIGNING_KEY")
    if _pem:
        logger.info("Positron license: signing key from POSITRON_SIGNING_KEY env")
        return _pem
    _key_file = _os.environ.get("POSITRON_SIGNING_KEY_FILE")
    if _key_file:
        with open(_key_file) as _fh:
            logger.info(f"Positron license: signing key from file {_key_file}")
            return _fh.read()
    _secret_id = _os.environ.get("POSITRON_SIGNING_KEY_SECRET_ID", "positron-signing-key")
    _region = (
        _os.environ.get("POSITRON_SIGNING_KEY_SECRET_REGION")
        or _os.environ.get("AWS_REGION")
        or _os.environ.get("AWS_DEFAULT_REGION")
    )
    import boto3  # present in the sagemaker-distribution base

    _resp = boto3.client("secretsmanager", region_name=_region).get_secret_value(
        SecretId=_secret_id
    )
    _pem = _resp.get("SecretString")
    if not _pem:
        raise ValueError("secret has no SecretString value")
    logger.info(
        f"Positron license: signing key from Secrets Manager secret '{_secret_id}'"
    )
    return _pem


def _load_license_content():
    """Resolve the Positron license (.lic) content: explicit env first (local
    testing / override), then AWS Secrets Manager (the admin-managed source).
    Unlike the signing key, the license is NOT tied to a specific
    POSITRON_VERSION build — entitlement and the signed-token public key rotate
    independently."""
    _lic = _os.environ.get("POSITRON_LICENSE")
    if _lic:
        logger.info("Positron license: license file from POSITRON_LICENSE env")
        return _lic
    _lic_file = _os.environ.get("POSITRON_LICENSE_FILE")
    if _lic_file:
        with open(_lic_file) as _fh:
            logger.info(f"Positron license: license file from file {_lic_file}")
            return _fh.read()
    _secret_id = _os.environ.get("POSITRON_LICENSE_SECRET_ID", "positron-license")
    _region = (
        _os.environ.get("POSITRON_LICENSE_SECRET_REGION")
        or _os.environ.get("AWS_REGION")
        or _os.environ.get("AWS_DEFAULT_REGION")
    )
    import boto3  # present in the sagemaker-distribution base

    _resp = boto3.client("secretsmanager", region_name=_region).get_secret_value(
        SecretId=_secret_id
    )
    _lic = _resp.get("SecretString")
    if not _lic:
        raise ValueError("secret has no SecretString value")
    logger.info(
        f"Positron license: license file from Secrets Manager secret '{_secret_id}'"
    )
    return _lic


# Install the license file once, next to license-manager, so license-manager can
# validate it directly off disk (mirrors Posit's own TLJH install script, which
# `install`s an admin-supplied .lic to the same path — we just source it from
# Secrets Manager instead of a local file). Failure here isn't fatal by itself;
# it surfaces as an entitlement-check failure below, which blocks minting.
#
# SKIPPED WHOLESALE when the License Manager client is present. That build never
# reads this file — the licensing verdict comes from the client — and an LM
# deployment has no 'positron-license' secret to read, so attempting the lookup
# can only fail and log a licensing ERROR that is alarming, unactionable, and
# untrue of a correctly-licensed image.
if _LM_CLIENT_PRESENT:
    logger.info(
        "Positron license: AWS License Manager client present; skipping the "
        "Secrets Manager license-file install"
    )
else:
    try:
        _os.makedirs(_POSITRON_ACTIVATION_DIR, exist_ok=True)
        with open(_LICENSE_FILE_PATH, "w") as _fh:
            _fh.write(_load_license_content())
        _os.chmod(_LICENSE_FILE_PATH, 0o600)
        logger.info(f"Positron license: license file installed to {_LICENSE_FILE_PATH}")
    except Exception as _exc:  # noqa: BLE001 - best-effort, must never break startup
        logger.error(
            f"Positron license: could NOT install license file "
            f"({type(_exc).__name__}: {_exc}). The entitlement check below will "
            f"fail and positron-server will not be licensed — store the license in "
            f"Secrets Manager (POSITRON_LICENSE_SECRET_ID, default "
            f"'positron-license') and grant the execution role "
            f"secretsmanager:GetSecretValue, or set POSITRON_LICENSE_FILE for "
            f"local runs"
        )


def _check_entitlement():
    """Check real entitlement via license-manager. Vendored (and made
    synchronous, since we run inline in a per-spawn callable rather than an
    async FastAPI service) from jupyter_positron_verifier.entitlement.
    EntitlementChecker (0.0.1). Returns (valid, licensee, issuer); never
    raises — any failure is treated as unlicensed."""
    try:
        _proc = _subprocess.run(
            [_LICENSE_MANAGER_PATH, "verify", "--output=json"],
            capture_output=True,
            timeout=10,
        )
        _raw = _proc.stdout.decode()
        # The verify command prefixes output with a hash line; find the JSON.
        _start = _raw.find("{")
        if _start >= 0:
            _raw = _raw[_start:]
        _data = _json.loads(_raw)
        _status = (_data.get("status") or "").lower()
        if _status in ("activated", "evaluation"):
            _licensee = _data.get("licensee", "")
            logger.info(f"Positron license: entitlement valid (status={_status}, licensee={_licensee})")
            return True, _licensee, _data.get("issuer", "")
        logger.error(f"Positron license: entitlement invalid ({_data})")
        return False, "", ""
    except Exception as _exc:  # noqa: BLE001
        logger.error(
            f"Positron license: entitlement check failed "
            f"({type(_exc).__name__}: {_exc}); treating as unlicensed"
        )
        return False, "", ""


# License-error landing page: if licensing fails for any reason (missing signing
# key, failed entitlement check, minting error), we don't want the user to just
# see jupyter-server-proxy's generic "process didn't start in time" error. So
# instead of launching positron-server at all, we launch a tiny stdlib-only HTTP
# server on the SAME port jupyter-server-proxy assigned, serving a plain page
# that explains the license is missing/invalid and how to reach out. This works
# because jupyter-server-proxy's readiness check
# (SuperviseAndProxyHandler._http_ready_func) only cares that *something*
# answers on the port — "We only care if we get back *any* response, not just
# 200" — so it proxies straight through to whatever we bind there, no custom
# Jupyter/tornado handler required.
_LICENSE_ERROR_SERVER_PATH = "/tmp/positron_license_error_server.py"
_LICENSE_ERROR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Positron license required</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
         background: #1e1e1e; color: #d4d4d4; display: flex; align-items: center;
         justify-content: center; height: 100vh; margin: 0; }
  .card { max-width: 32rem; padding: 2.5rem; border: 1px solid #3c3c3c; border-radius: 8px;
          background: #252526; }
  h1 { font-size: 1.3rem; margin-top: 0; color: #f5f5f5; }
  p { line-height: 1.5; }
  a { color: #4daafc; }
  .tag { display: inline-block; font-size: 0.75rem; text-transform: uppercase;
         letter-spacing: 0.04em; color: #e5934a; border: 1px solid #e5934a;
         border-radius: 4px; padding: 0.15rem 0.5rem; margin-bottom: 1rem; }
</style>
</head>
<body>
  <div class="card">
    <div class="tag">License required</div>
    <h1>Positron could not be started</h1>
    <p>This SageMaker space does not have a valid Positron license. Positron
    requires a currently-activated license to run, and none could be found
    for this deployment.</p>
    <p>If you believe this is a mistake, check with whoever administers this
    image about the Positron signing key and license file configuration.</p>
    <p>To get set up with a Positron license, please contact
    <a href="mailto:sales@posit.co">sales@posit.co</a>.</p>
  </div>
</body>
</html>
"""
_LICENSE_ERROR_SERVER_SCRIPT = '''
import http.server
import sys

PORT = int(sys.argv[1])
HTML = %r


class _Handler(http.server.BaseHTTPRequestHandler):
    def _respond(self):
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._respond()

    def do_POST(self):
        self._respond()

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", PORT), _Handler).serve_forever()
''' % (_LICENSE_ERROR_HTML,)

try:
    with open(_LICENSE_ERROR_SERVER_PATH, "w") as _fh:
        _fh.write(_LICENSE_ERROR_SERVER_SCRIPT)
    logger.info(
        f"Positron license: error-page server script written to "
        f"{_LICENSE_ERROR_SERVER_PATH}"
    )
except Exception as _exc:  # noqa: BLE001 - best-effort, must never break startup
    logger.error(
        f"Positron license: could NOT write error-page server script "
        f"({type(_exc).__name__}: {_exc}); a license failure will fall back to "
        f"jupyter-server-proxy's generic error instead of the friendly page"
    )


def _license_error_command(port):
    """Command that serves the license-error landing page instead of
    positron-server, bound to the same port jupyter-server-proxy assigned."""
    return ["/usr/bin/env", "python3", _LICENSE_ERROR_SERVER_PATH, str(port)]


# Load the signing key once (the key is stable; only the token is minted per spawn).
#
# SKIPPED WHOLESALE when the License Manager client is present, for the same
# reason as the license-file install above: an LM deployment distributes no
# signing key, so the Secrets Manager lookup can only fail and log a misleading
# licensing ERROR. _signing_key stays None, which is correct — the LM branch
# below never mints a token.
_signing_key = None
_sign_padding = None
_sign_hash = None
if _LM_CLIENT_PRESENT:
    logger.info(
        "Positron license: AWS License Manager client present; skipping the "
        "Secrets Manager signing-key load"
    )
else:
    try:
        from cryptography.hazmat.primitives import hashes as _c_hashes  # noqa: E402
        from cryptography.hazmat.primitives import serialization as _c_serialization
        from cryptography.hazmat.primitives.asymmetric import padding as _c_padding
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey as _RSAKey

        _signing_key = _c_serialization.load_pem_private_key(
            _load_signing_key_pem().encode(), password=None
        )
        if not isinstance(_signing_key, _RSAKey):
            raise TypeError("signing key is not an RSA private key")
        _sign_padding = _c_padding.PKCS1v15()
        _sign_hash = _c_hashes.SHA256()
        logger.info(
            "Positron license: RSA signing key loaded; minting a signed token per launch"
        )
    except Exception as _exc:  # noqa: BLE001 - best-effort, must never break startup
        _signing_key = None
        logger.error(
            f"Positron license: could NOT load signing key "
            f"({type(_exc).__name__}: {_exc}). positron-server requires a signed "
            f"license token and will fail to start without one — store the key in "
            f"Secrets Manager (POSITRON_SIGNING_KEY_SECRET_ID, default "
            f"'positron-signing-key') and grant the execution role "
            f"secretsmanager:GetSecretValue, or set POSITRON_SIGNING_KEY_FILE for "
            f"local runs"
        )


def _mint_license_token(connection_token, issuer="", licensee=""):
    """Mint a positron-server license token. Vendored verbatim from
    jupyter_positron_verifier.signing.Signer.mint (0.0.1): RSA PKCS#1 v1.5 / SHA-256
    over connection_token + issuer + licensee + timestamp, with the timestamp in
    JavaScript ``new Date().toISOString()`` form. Matches positron-server's
    remoteLicenseKey.ts verifier exactly."""
    _now = _datetime.now(_timezone.utc)
    _timestamp = _now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{_now.microsecond // 1000:03d}Z"
    _payload = (connection_token + issuer + licensee + _timestamp).encode()
    _signature = _base64.b64encode(
        _signing_key.sign(_payload, _sign_padding, _sign_hash)
    ).decode()
    return _json.dumps(
        {
            "connection_token": connection_token,
            "issuer": issuer,
            "licensee": licensee,
            "timestamp": _timestamp,
            "signature": _signature,
        }
    )

# Wrap positron-server's launch command so a FRESH entitlement check runs and a
# FRESH token is minted at each spawn, injected as POSITRON_LICENSE_KEY. On ANY
# licensing failure (no signing key, failed entitlement, minting error), we
# launch the error-page server (above) instead of positron-server, so the user
# gets a clear message instead of jupyter-server-proxy's generic "didn't start
# in time" failure. We declare `port` so jupyter-server-proxy's
# call_with_asked_args injects the port it assigned (handlers.py
# ServerProxyHandler.process_args) — needed either way, since positron-server's
# own command already has it baked in via templating, but our error-page
# fallback must bind to that exact same port.
# setup_positron_server()'s direct-launch command is
# ["/usr/bin/env", "LD_LIBRARY_PATH=...", <binary>, ...args]; we splice the
# license var into that leading `/usr/bin/env` prefix. The JSON braces are
# doubled so jupyter-server-proxy's str.format() templating leaves them intact
# (mirrors jupyter-positron-server's own hub-minting command builder).
# Splice the licensing environment into positron-server's launch command.
# _LM_CLIENT_PATH / _LM_CLIENT_PRESENT are resolved much earlier (just above the
# Secrets Manager bootstrap), since that bootstrap is skipped when the client is
# present.
_orig_command = cfg.get("command")
if _LM_CLIENT_PRESENT and isinstance(_orig_command, list) and _orig_command:
    logger.info(
        "Positron license: using AWS License Manager via "
        f"POSITRON_LICENSE_MANAGER_PATH={_LM_CLIENT_PATH}"
    )
    _cmd = list(_orig_command)
    _lm_vars = [
        f"POSITRON_LICENSE_MANAGER_PATH={_LM_CLIENT_PATH}",
        f"LM_LOG_FILE={_os.environ.get('LM_LOG_FILE', '/tmp/sagemaker-lm.log')}",
    ]
    if _cmd[0] == "/usr/bin/env":
        cfg["command"] = _cmd[:1] + _lm_vars + _cmd[1:]
    else:
        cfg["command"] = ["/usr/bin/env"] + _lm_vars + _cmd

elif isinstance(_orig_command, list) and _orig_command:

    def _positron_command_with_license(port):
        if _signing_key is None:
            logger.error(
                "Positron license: no signing key available; serving the "
                "license-error page instead of launching positron-server."
            )
            return _license_error_command(port)
        _valid, _licensee, _issuer = _check_entitlement()
        if not _valid:
            logger.error(
                "Positron license: entitlement check failed; serving the "
                "license-error page instead of launching positron-server "
                "(this is fail-closed, working as intended)."
            )
            return _license_error_command(port)
        try:
            _token = _mint_license_token(_POSITRON_TOKEN, issuer=_issuer, licensee=_licensee)
        except Exception as _exc:  # noqa: BLE001
            logger.error(
                f"Positron license: token minting failed "
                f"({type(_exc).__name__}: {_exc}); serving the license-error "
                f"page instead of launching positron-server"
            )
            return _license_error_command(port)
        _escaped = _token.replace("{", "{{").replace("}", "}}")
        _cmd = list(_orig_command)
        if _cmd[0] == "/usr/bin/env":
            _cmd.insert(1, f"POSITRON_LICENSE_KEY={_escaped}")
        else:
            _cmd = ["/usr/bin/env", f"POSITRON_LICENSE_KEY={_escaped}"] + _cmd
        return _cmd

    cfg["command"] = _positron_command_with_license

# AWS Toolkit: make the bundled extension (and the integrated terminal / boto3)
# resolve the SageMaker execution role out of the box. SageMaker delivers the
# role via the container-credentials endpoint, which the AWS default chain reads
# automatically — but the AWS Toolkit's connection UI resolves a *profile*, not
# the raw chain. So we point AWS_CONFIG_FILE at a baked [default] profile that
# uses credential_process to export the role's creds. AWS_SDK_LOAD_CONFIG=1 is
# required or the SDK/toolkit may ignore config-file profiles.
# Only set this when the user has NO ~/.aws/config of their own, so a user who
# configures their own profiles is never shadowed. Best-effort: never crash.
_aws_config_file = "/etc/positron/aws-config"
try:
    _user_aws_config = _os.path.expanduser("~/.aws/config")
    if _os.path.exists(_user_aws_config):
        logger.info(
            f"AWS: user config present at {_user_aws_config}; not overriding "
            f"AWS_CONFIG_FILE"
        )
    elif _os.path.exists(_aws_config_file):
        _aws_env = cfg.setdefault("environment", {})
        _aws_env["AWS_CONFIG_FILE"] = _aws_config_file
        _aws_env["AWS_SDK_LOAD_CONFIG"] = "1"
        logger.info(
            f"AWS: pointing AWS_CONFIG_FILE at baked default profile "
            f"{_aws_config_file} (execution-role credential_process)"
        )
    else:
        logger.warning(f"AWS: baked config file not found: {_aws_config_file}")
except Exception as _exc:  # noqa: BLE001 - best-effort, must never break startup
    logger.warning(
        f"AWS: could not configure AWS_CONFIG_FILE "
        f"({type(_exc).__name__}: {_exc}); continuing"
    )

# We set JSP_POSITRON_LAUNCHER_DISABLED=1 (see Dockerfile) to suppress the
# DUPLICATE launcher tile registered by jupyter-positron-server's own entry
# point. But setup_positron_server() reads that same env var and disables the
# launcher entry on the cfg it returns — including ours. Re-enable it so the
# (single) working Positron tile shows in the JupyterLab launcher.
cfg.setdefault("launcher_entry", {})["enabled"] = True
# Keep the default "Notebook" launcher category (set by setup_positron_server).
# JupyterLab only renders a launcher tile's icon (icon_path -> kernelIconUrl) for
# the "Notebook"/"Console" categories; a custom category would show a BLANK tile.
# With the Jupyter kernelspecs removed (Dockerfile), the Positron tile is the only
# one under "Notebook", so the launcher is effectively Positron-only and the
# Positron logo still renders.

c.ServerProxy.servers = {"positron": cfg}  # noqa: F821

# Positron-only launcher: the Dockerfile deletes every Jupyter kernelspec dir, but
# ipykernel always synthesizes a native "python3" kernel (KernelSpecManager's
# ensure_native_kernel default), which would still render a Python tile under
# Notebook/Console. Suppress it so the launcher has no kernel tiles at all —
# Positron uses its own runtimes, not Jupyter kernels.
c.KernelSpecManager.ensure_native_kernel = False  # noqa: F821

# Land directly in Positron: make the proxied Positron route the server's default
# landing page instead of the JupyterLab UI. Must be LabApp.default_url, NOT
# ServerApp.default_url — under `jupyter lab` the LabApp overrides ServerApp's
# value (which is why a stock image lands on /lab). This fires when the browser
# hits the server base path and 302s into Positron. The SageMaker health check
# (/api/status) is unaffected.
#
# No ?tkn= needed here: the proxy injects positron-server's connection token on
# every backend request (see the proxy_request_headers patch), so Positron is
# authenticated regardless of how the browser arrives (SageMaker in fact deep-links
# straight to /positron/, bypassing this redirect entirely).
c.LabApp.default_url = "/positron/"  # noqa: F821

# ...and land directly in Positron even when the launcher DEEP-LINKS the JupyterLab
# page, which default_url alone cannot cover — it is only consulted when the browser
# requests the server BASE PATH. The two SageMaker products differ here (both
# verified from the live /aws/sagemaker/studio logs, 2026-08-06):
#
#   - SageMaker AI (classic Studio) opens /jupyterlab/default, which jupyter-server
#     answers with ONE 302 to default_url (its redirect rule is r"/?", so the
#     no-trailing-slash form matches too) -> the user lands in Positron already.
#   - SageMaker Unified Studio opens /jupyterlab/default/lab/tree/<mount> directly
#     (its Notebook flow deep-links the project's shared mount), so default_url is
#     never consulted and the user had to click the Positron launcher tile.
#
# Redirecting the JupyterLab PAGE closes that gap. We patch jupyterlab_server's
# LabHandler rather than adding a URL rule because it is exactly as narrow as we
# need: LabHandler is registered for MASTER_URL_PATTERN, i.e. "/lab" followed by
# only /workspaces/... or /tree/..., so JupyterLab's own APIs (/lab/api/*), the
# federated labextension files and jupyter-server's /api/* are served by OTHER
# handlers and are untouched. LabHandler.get is sync in the shipped
# jupyterlab_server (2.28.0) — the Dockerfile asserts both of those facts at build
# time so a base-image bump fails loudly instead of silently disabling this.
#
# Escape hatch: ?jupyterlab=1 falls through to the real JupyterLab page, so it stays
# reachable for debugging. Little is lost if it isn't used — the Jupyter kernelspecs
# are removed from this image (see the Dockerfile), so JupyterLab cannot run
# notebooks here; Positron uses its own runtimes.
try:
    from jupyter_server.utils import url_path_join as _url_path_join  # noqa: E402
    from jupyterlab_server.handlers import LabHandler as _LabHandler  # noqa: E402
    from tornado import web as _web  # noqa: E402

    _orig_lab_get = _LabHandler.get

    @_web.authenticated
    def _lab_get(self, *args, **kwargs):
        if self.get_argument("jupyterlab", None) is not None:
            return _orig_lab_get(self, *args, **kwargs)
        target = _url_path_join(self.base_url, "positron/")
        self.log.info(
            "Redirecting JupyterLab page %s to Positron (%s)", self.request.uri, target
        )
        return self.redirect(target)

    _LabHandler.get = _lab_get
except Exception:  # noqa: BLE001 - never fail the server over a landing redirect
    logger.exception(
        "Could not install the /lab -> Positron redirect; JupyterLab will still "
        "serve its own page and users may need the Positron launcher tile."
    )
