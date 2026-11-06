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

# Positron license: AWS License Manager.
#
# positron-server 2026.08+ accepts POSITRON_LICENSE_MANAGER_PATH: it runs the
# named binary, treats that client's verdict as the licensing decision, and
# needs no key or license file of its own (positron-dev/positron#15538). For
# SageMaker that client is `license-manager-aws-sagemaker` from
# rstudio/licensing-clients, which checks a seat out of AWS License Manager
# under the Space's execution role — the same entitlement path RStudio on
# SageMaker already uses.
#
# This is the ONLY licensing mechanism in this image. An earlier proof of
# concept minted an RSA-signed token in-process from a signing key plus a `.lic`
# file, both pulled from AWS Secrets Manager; that path has been removed along
# with the secrets it depended on. Nothing here reads Secrets Manager, so the
# image no longer emits misleading "store the license in Secrets Manager"
# errors on a correctly-licensed deployment.
#
# The Containerfile installs the client unconditionally and fails the build if
# it cannot, so the binary is expected to be present. We still check for it at
# runtime rather than assume: POSITRON_LICENSE_MANAGER_PATH pointing at a
# missing binary makes positron-server fail immediately, which surfaces to the
# user as an opaque proxy timeout, whereas a missing client detected here
# serves the explanatory license-error page below instead.
_LM_CLIENT_PATH = "/usr/lib/positron-server/bin/license-manager-aws-sagemaker"
_LM_CLIENT_PRESENT = _os.path.isfile(_LM_CLIENT_PATH) and _os.access(_LM_CLIENT_PATH, _os.X_OK)


# License-error landing page: if the License Manager client is missing, we don't
# want the user to just see jupyter-server-proxy's generic "process didn't start
# in time" error. So
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
    image about the AWS License Manager entitlement for Positron in this
    account.</p>
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


# Point positron-server at the License Manager client. setup_positron_server()'s
# direct-launch command is ["/usr/bin/env", "LD_LIBRARY_PATH=...", <binary>,
# ...args], so we splice the licensing vars into that leading `/usr/bin/env`
# prefix.
#
# If the client is missing the image is misbuilt — no other licensing path
# remains — so we serve the license-error page rather than launch an unlicensed
# positron-server, which would fail closed as an opaque "didn't start in time".
# That branch is a per-spawn callable so it can declare `port`, which
# jupyter-server-proxy's call_with_asked_args injects (handlers.py
# ServerProxyHandler.process_args); the error-page server has to bind the exact
# port the proxy assigned.
_orig_command = cfg.get("command")
if isinstance(_orig_command, list) and _orig_command:
    if _LM_CLIENT_PRESENT:
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
    else:
        logger.error(
            f"Positron license: AWS License Manager client not found at "
            f"{_LM_CLIENT_PATH} — this image is misbuilt. Serving the "
            f"license-error page instead of launching positron-server."
        )

        def _positron_command_unlicensed(port):
            return _license_error_command(port)

        cfg["command"] = _positron_command_unlicensed

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
