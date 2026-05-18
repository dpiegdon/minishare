"""The file-sharing blueprint: browse, download, upload.

Design goal: every page is self-documenting so that both humans and
automated agents can use the server without external instructions. Each
HTML listing carries a "CLI / API" help box, ``GET /help`` returns the
same docs as plain text, and any listing endpoint can return JSON via
``?format=json`` or an ``Accept: application/json`` header.
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_file,
    url_for,
)
from werkzeug.utils import safe_join, secure_filename

share_bp = Blueprint("share", __name__)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _storage_root() -> str:
    return current_app.config["MINISHARE_DIR"]


def _doc_base() -> str:
    """External URL of the blueprint root, honouring any ``url_prefix``.

    Derived from the single-rule ``/help`` endpoint so it is deterministic
    regardless of where the blueprint is mounted (``browse`` has several
    rules, so ``url_for`` on it is ambiguous for doc purposes).
    """
    return url_for("share.help_text", _external=True)[: -len("/help")].rstrip(
        "/"
    )


def _resolve(subpath: str | None) -> str:
    """Resolve ``subpath`` under the storage root, refusing traversal.

    ``werkzeug.safe_join`` returns ``None`` for anything that would escape
    the root (``..``, absolute paths, etc.); we turn that into a 400.
    """
    full = safe_join(_storage_root(), subpath or "")
    if full is None:
        abort(400, description="Illegal path")
    return full


def _rel(full: str) -> str:
    return os.path.relpath(full, _storage_root()).replace(os.sep, "/").lstrip(".")


def _human_size(num: int) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < step:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= step
    return f"{num:.1f} PB"


def _wants_json() -> bool:
    if request.args.get("format") == "json":
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


def _listing(full_dir: str, subpath: str) -> list[dict]:
    entries: list[dict] = []
    for name in sorted(os.listdir(full_dir), key=str.lower):
        fp = os.path.join(full_dir, name)
        is_dir = os.path.isdir(fp)
        rel = (subpath.rstrip("/") + "/" + name).lstrip("/")
        stat = os.stat(fp)
        entries.append(
            {
                "name": name,
                "type": "dir" if is_dir else "file",
                "path": rel,
                "size": None if is_dir else stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(
                    timespec="seconds"
                ),
                "download": None if is_dir else url_for("share.get", subpath=rel),
            }
        )
    # Directories first, then files, each alphabetically.
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return entries


def _api_doc(base: str, auth_on: bool = False) -> str:
    """Plain-text usage, also embedded in every HTML page."""
    auth_note = (
        "\nAuthentication\n"
        "  This server requires HTTP Basic auth. Send credentials with every\n"
        "  request, e.g.  curl -u USER:PASS '%s/browse/?format=json'\n" % base
        if auth_on
        else ""
    )
    return f"""minishare — usage
{auth_note}

Browse (HTML):      GET  {base}/
Browse (JSON):      GET  {base}/browse/<path>?format=json
Download a file:    GET  {base}/get/<path>
View inline:        GET  {base}/get/<path>?inline=1
Upload (multipart): POST {base}/upload[/<dir>]      field name: file
Upload (raw body):  PUT  {base}/put/<path>          body = file contents
This help (text):   GET  {base}/help

curl examples
  # list the root as JSON
  curl '{base}/browse/?format=json'

  # download a file
  curl -O '{base}/get/notes/todo.txt'

  # upload via multipart form into the 'docs' folder
  curl -F file=@report.pdf '{base}/upload/docs'

  # upload raw bytes to an exact path (parent dirs auto-created)
  curl -T report.pdf '{base}/put/docs/report.pdf'

Notes
  * <path> is relative to the share root; "../" and absolute paths are rejected.
  * PUT creates missing parent directories and overwrites existing files.
"""


# --------------------------------------------------------------------------- #
# HTML template
# --------------------------------------------------------------------------- #
_PAGE = """<!doctype html>
<title>minishare · /{{ subpath }}</title>
<style>
  body{font:14px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:60rem;padding:0 1rem}
  h1{font-size:1.1rem}
  a{color:#06c;text-decoration:none}a:hover{text-decoration:underline}
  table{border-collapse:collapse;width:100%;margin:1rem 0}
  td,th{padding:.35rem .6rem;border-bottom:1px solid #eee;text-align:left}
  th{font-weight:600;border-bottom:2px solid #ccc}
  td.r,th.r{text-align:right;font-variant-numeric:tabular-nums}
  .dir{font-weight:600}
  form{margin:1rem 0;padding:1rem;background:#f6f8fa;border-radius:6px}
  details{margin-top:2rem;background:#f6f8fa;border-radius:6px;padding:.5rem 1rem}
  pre{white-space:pre-wrap;font-size:13px;margin:0}
  .crumb{color:#666}
</style>
<h1>📂 minishare
  <span class="crumb">/
  {%- for c, href in crumbs -%}
    <a href="{{ href }}">{{ c }}</a>/
  {%- endfor -%}
  </span>
</h1>

<table>
  <tr><th>Name</th><th class="r">Size</th><th>Modified</th></tr>
  {% if subpath %}
  <tr><td class="dir"><a href="{{ parent_url }}">⬆ ..</a></td><td></td><td></td></tr>
  {% endif %}
  {% for e in entries %}
  <tr>
    {% if e.type == 'dir' %}
      <td class="dir">📁 <a href="{{ url_for('share.browse', subpath=e.path) }}">{{ e.name }}/</a></td>
      <td class="r">—</td>
    {% else %}
      <td>📄 <a href="{{ e.download }}">{{ e.name }}</a></td>
      <td class="r">{{ human(e.size) }}</td>
    {% endif %}
    <td>{{ e.modified }}</td>
  </tr>
  {% endfor %}
  {% if not entries %}
  <tr><td colspan="3"><em>empty directory</em></td></tr>
  {% endif %}
</table>

<form method="post" action="{{ upload_url }}" enctype="multipart/form-data">
  <strong>Upload here:</strong>
  <input type="file" name="file" multiple required>
  <button type="submit">Upload</button>
</form>

<details open>
  <summary><strong>CLI / API usage</strong> (for agents &amp; scripts)</summary>
  <pre>{{ doc }}</pre>
</details>
"""


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
@share_bp.before_request
def _enforce_auth():
    """If a {username: password} dict is configured, require HTTP Basic auth.

    No dict (or empty) == fully open access, exactly as before.
    """
    users = current_app.config.get("MINISHARE_AUTH")
    if not users:
        return None  # open access

    a = request.authorization
    ok = False
    if a is not None and a.password is not None:
        expected = users.get(a.username or "")
        ok = expected is not None and hmac.compare_digest(
            str(expected), a.password
        )
    if ok:
        return None

    body = (
        "401 Unauthorized — this minishare requires HTTP Basic auth.\n"
        "Humans: your browser will prompt for username and password.\n"
        "CLI / agents:  curl -u USER:PASS <url>\n"
    )
    resp = current_app.response_class(body, status=401, mimetype="text/plain")
    resp.headers["WWW-Authenticate"] = 'Basic realm="minishare"'
    return resp


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@share_bp.route("/")
@share_bp.route("/browse/")
@share_bp.route("/browse/<path:subpath>")
def browse(subpath: str = ""):
    """Directory listing as HTML (default) or JSON (``?format=json``)."""
    subpath = subpath.strip("/")
    full = _resolve(subpath)

    if not os.path.exists(full):
        abort(404, description=f"No such path: {subpath}")
    if os.path.isfile(full):
        # Browsing a file just means "download it".
        return redirect(url_for("share.get", subpath=subpath))

    entries = _listing(full, subpath)

    if _wants_json():
        return jsonify(
            {
                "path": subpath,
                "type": "directory",
                "entries": entries,
                "usage": "GET /help for the full API",
            }
        )

    # Build clickable breadcrumb segments.
    crumbs, acc = [], ""
    for part in [p for p in subpath.split("/") if p]:
        acc = f"{acc}/{part}".lstrip("/")
        crumbs.append((part, url_for("share.browse", subpath=acc)))

    parent = subpath.rsplit("/", 1)[0] if "/" in subpath else ""
    return render_template_string(
        _PAGE,
        subpath=subpath,
        entries=entries,
        crumbs=crumbs,
        parent_url=url_for("share.browse", subpath=parent) if subpath else "",
        upload_url=url_for("share.upload", subpath=subpath),
        human=_human_size,
        doc=_api_doc(
            _doc_base(), bool(current_app.config.get("MINISHARE_AUTH"))
        ),
    )


@share_bp.route("/get/<path:subpath>")
def get(subpath: str):
    """Download a file. ``?inline=1`` serves it for in-browser viewing."""
    full = _resolve(subpath.strip("/"))
    if not os.path.isfile(full):
        abort(404, description=f"Not a file: {subpath}")
    as_attachment = request.args.get("inline") not in ("1", "true", "yes")
    return send_file(full, as_attachment=as_attachment)


@share_bp.route("/upload", methods=["POST"])
@share_bp.route("/upload/", methods=["POST"])
@share_bp.route("/upload/<path:subpath>", methods=["POST"])
def upload(subpath: str = ""):
    """Multipart upload of one or more files into directory ``subpath``."""
    subpath = subpath.strip("/")
    dest_dir = _resolve(subpath)
    os.makedirs(dest_dir, exist_ok=True)
    if not os.path.isdir(dest_dir):
        abort(400, description="Upload target is not a directory")

    files = request.files.getlist("file")
    if not files or all(f.filename == "" for f in files):
        abort(400, description="No 'file' field in multipart form data")

    saved = []
    for f in files:
        name = secure_filename(f.filename)
        if not name:
            continue
        f.save(os.path.join(dest_dir, name))
        saved.append((subpath + "/" + name).lstrip("/"))

    if _wants_json() or request.headers.get("Accept", "").startswith(
        "application/json"
    ):
        return jsonify({"saved": saved}), 201
    return redirect(url_for("share.browse", subpath=subpath))


@share_bp.route("/put/<path:subpath>", methods=["PUT"])
def put(subpath: str):
    """Raw-body upload to an exact path; creates parent dirs, overwrites."""
    full = _resolve(subpath.strip("/"))
    if os.path.isdir(full):
        abort(400, description="Target is a directory; include a filename")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(request.get_data())
    return jsonify({"saved": _rel(full), "size": os.path.getsize(full)}), 201


@share_bp.route("/help")
def help_text():
    """Plain-text API docs — handy for `curl host/help`."""
    return current_app.response_class(
        _api_doc(
            _doc_base(), bool(current_app.config.get("MINISHARE_AUTH"))
        ),
        mimetype="text/plain",
    )


@share_bp.errorhandler(400)
@share_bp.errorhandler(404)
def _errors(err):
    msg = getattr(err, "description", str(err))
    if _wants_json():
        return jsonify({"error": msg, "code": err.code}), err.code
    return current_app.response_class(
        f"{err.code} {msg}\n\nTry GET /help for usage.\n",
        status=err.code,
        mimetype="text/plain",
    )
