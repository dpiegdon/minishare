"""The file-sharing blueprint: browse, download, upload, mkdir, delete.

Design goal: every page is self-documenting so that both humans and
automated agents can use the server without external instructions. Each
HTML listing carries a "CLI / API" help box, ``GET /help`` returns the
same docs as plain text, and any listing endpoint can return JSON via
``?format=json`` or an ``Accept: application/json`` header.
"""
from __future__ import annotations

import hmac
import os
import re
import shutil
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

    Sanitised to URL-safe characters only: the docs are rendered with
    ``|safe`` (so quotes and text are not HTML-mangled), so a crafted
    Host header must not be able to inject markup through this value.
    """
    raw = url_for("share.help_text", _external=True)[: -len("/help")].rstrip(
        "/"
    )
    return re.sub(r"[^A-Za-z0-9:/._~%@\[\]-]", "", raw)


def _resolve(subpath: str | None) -> str:
    """Resolve ``subpath`` under the storage root, refusing traversal.

    ``werkzeug.safe_join`` returns ``None`` for anything that would escape
    the root (``..``, absolute paths, etc.); we turn that into a 400.
    As defence in depth we also canonicalise the path and reject anything
    that resolves (e.g. via a symlink) outside the storage root.
    """
    root = _storage_root()
    full = safe_join(root, subpath or "")
    if full is None:
        abort(400, description="Illegal path")
    real_root = os.path.realpath(root)
    real_full = os.path.realpath(full)
    if real_full != real_root and not real_full.startswith(
        real_root + os.sep
    ):
        abort(400, description="Illegal path")
    return full


def _rel(full: str) -> str:
    """Path of ``full`` relative to the storage root, '/'-separated."""
    rel = os.path.relpath(full, _storage_root()).replace(os.sep, "/")
    return "" if rel == "." else rel


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


def _client_wants_json() -> bool:
    """For mutating endpoints: agents get JSON, browsers get a redirect.

    Browsers send ``Accept: text/html``; curl/agents send ``*/*`` (or ask
    for JSON explicitly), so default to JSON unless HTML was requested.
    """
    if _wants_json():
        return True
    return "text/html" not in request.headers.get("Accept", "")


def _respond(payload: dict, redirect_subpath: str, status: int = 200):
    """Reply to a mutating request.

    Agents (and ``DELETE``) get ``payload`` as JSON; browsers get a
    redirect back to the listing they were on. Centralised so upload /
    mkdir / delete behave identically.
    """
    if request.method == "DELETE" or _client_wants_json():
        return jsonify(payload), status
    return redirect(url_for("share.browse", subpath=redirect_subpath))


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
    """The single source of API documentation (primary audience: agents).

    Served verbatim at ``GET /help`` (plain text) and embedded in every
    HTML listing's folded ``<details>`` block near the top of the page —
    an agent fetching the page sees this in the raw HTML even though it
    is visually collapsed for humans. Edit it here only, once.
    """
    auth_note = (
        "\nAuthentication\n"
        "  This server requires HTTP Basic auth. Send credentials with every\n"
        "  request, e.g.  curl -u USER:PASS '%s/browse/?format=json'\n" % base
        if auth_on
        else ""
    )
    return f"""minishare - API (the HTML pages are just a UI; the API is
self-service for agents/scripts).

AGENTS: add ?format=json to any listing for a JSON response; mutating
endpoints already return JSON to non-browser clients. This exact text is
also at GET {base}/help .
{auth_note}

Browse (HTML):      GET    {base}/
Browse (JSON):      GET    {base}/browse/$path?format=json
Download a file:    GET    {base}/get/$path
View inline:        GET    {base}/get/$path?inline=1
Upload (multipart): POST   {base}/upload[/$dir]    field name: file
Upload (raw body):  PUT    {base}/put/$path        body = file contents
Make a directory:   POST   {base}/mkdir/$path      (mkdir -p)
Delete file or dir: DELETE {base}/delete/$path     (dirs: RECURSIVE)
                    (bulk: POST {base}/delete with repeated sel=$path)
This help (text):   GET    {base}/help

curl examples
  # list the root as JSON
  curl '{base}/browse/?format=json'

  # download a file
  curl -O '{base}/get/notes/todo.txt'

  # upload via multipart form into the 'docs' folder
  curl -F file=@report.pdf '{base}/upload/docs'

  # upload raw bytes to an exact path (parent dirs auto-created)
  curl -T report.pdf '{base}/put/docs/report.pdf'

  # create a directory (parents included)
  curl -X POST '{base}/mkdir/docs/2026'

  # delete a file, or a whole directory tree
  curl -X DELETE '{base}/delete/docs/old-stuff'

Notes
  * $path is relative to the share root; "../" and absolute paths are rejected.
  * PUT creates missing parent directories and overwrites existing files.
  * mkdir is idempotent; deleting a directory removes it RECURSIVELY.
  * No auth configured == anyone who can reach the server can also delete.
"""


# --------------------------------------------------------------------------- #
# HTML template
# --------------------------------------------------------------------------- #
_PAGE = """<!doctype html>
<title>{{ title }} · /{{ subpath }}</title>
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
  form.inline{display:inline;margin:0;padding:0;background:none}
  form.inline button{border:0;background:none;cursor:pointer;font-size:1rem;color:#c00;padding:0}
  input[type=text]{padding:.25rem .4rem}
  .ops{display:flex;gap:1rem;margin:1rem 0;flex-wrap:wrap}
  .ops form{flex:1;margin:0;min-width:15rem}
  button:disabled{opacity:.45;cursor:not-allowed}
  form.drop{outline:2px dashed #06c;outline-offset:-4px}
  .hint{color:#000;font-weight:600;margin-left:.4rem}
  td.sel,th.sel{text-align:center;width:5rem}
  details{margin:.5rem 0}
  summary{color:#aaa;font-size:12px;cursor:pointer}
  pre{white-space:pre-wrap;font-size:12px;color:#666;margin:.4rem 0 0}
  .crumb{color:#666}
</style>
<details>
  <summary>CLI / API usage (for agents &amp; scripts)</summary>
  <pre>{{ doc|safe }}</pre>
</details>
<h1>📂 <a href="{{ root_url }}" title="go to share root">{{ title }}</a>
  <span class="crumb">/
  {%- for c, href in crumbs -%}
    <a href="{{ href }}">{{ c }}</a>/
  {%- endfor -%}
  </span>
</h1>

<form method="post" action="{{ delete_url }}" id="delform"
      onsubmit="return confirm('Delete ' + this.querySelectorAll('input[name=sel]:checked').length + ' selected item(s)? Folders are deleted recursively. This cannot be undone.')">
<table>
  <tr><th>Name</th><th class="r">Size</th><th>Modified</th>
      <th class="sel"><button type="submit" id="delbtn" title="delete the selected items">Delete</button></th></tr>
  {% if subpath %}
  <tr><td class="dir"><a href="{{ parent_url }}">⬆ ..</a></td><td></td><td></td><td></td></tr>
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
    <td class="sel">
      <input type="checkbox" name="sel" value="{{ e.path }}"
             aria-label="select {{ e.name }}">
    </td>
  </tr>
  {% endfor %}
  {% if not entries %}
  <tr><td colspan="4"><em>empty directory</em></td></tr>
  {% endif %}
</table>
</form>

<div class="ops">
  <form method="post" action="{{ mkdir_url }}">
    <input type="text" name="name" placeholder="folder name" required>
    <button type="submit">Create folder</button>
  </form>

  <form method="post" action="{{ upload_url }}" enctype="multipart/form-data" id="up">
    <input type="file" name="file" id="upf" multiple required>
    <span class="hint">&larr; or drop files here</span>
    <button type="submit" id="upb">Upload files</button>
  </form>
</div>
<script>
(function () {
  var f = document.getElementById('upf'),
      b = document.getElementById('upb'),
      box = document.getElementById('up');
  function sync() { b.disabled = !(f.files && f.files.length); }
  f.addEventListener('change', sync);
  ['dragenter', 'dragover'].forEach(function (ev) {
    box.addEventListener(ev, function (e) {
      e.preventDefault();
      box.classList.add('drop');
    });
  });
  ['dragleave', 'dragend', 'drop'].forEach(function (ev) {
    box.addEventListener(ev, function () { box.classList.remove('drop'); });
  });
  box.addEventListener('drop', function (e) {
    e.preventDefault();
    f.files = e.dataTransfer.files;   // FileList is assignable in modern browsers
    sync();
  });
  sync();   // progressive enhancement: only JS disables the button
})();
(function () {
  var btn = document.getElementById('delbtn'),
      form = document.getElementById('delform');
  if (!btn || !form) return;
  function sync() {
    btn.disabled = !form.querySelector('input[name=sel]:checked');
  }
  form.addEventListener('change', sync);
  sync();   // progressive enhancement: only JS disables the button
})();
</script>
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
        "401 Unauthorized - this minishare requires HTTP Basic auth.\n"
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
    base = _doc_base()
    auth_on = bool(current_app.config.get("MINISHARE_AUTH"))
    return render_template_string(
        _PAGE,
        subpath=subpath,
        title=current_app.config.get("MINISHARE_TITLE") or "minishare",
        entries=entries,
        crumbs=crumbs,
        root_url=url_for("share.browse"),
        parent_url=url_for("share.browse", subpath=parent) if subpath else "",
        upload_url=url_for("share.upload", subpath=subpath),
        mkdir_url=url_for("share.mkdir", subpath=subpath),
        delete_url=url_for("share.delete"),
        human=_human_size,
        doc=_api_doc(base, auth_on),
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

    if not saved:
        abort(400, description="No usable filenames in the upload")
    return _respond({"saved": saved}, subpath, 201)


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


@share_bp.route("/mkdir", methods=["POST"])
@share_bp.route("/mkdir/", methods=["POST"])
@share_bp.route("/mkdir/<path:subpath>", methods=["POST"])
def mkdir(subpath: str = ""):
    """Create a directory, ``mkdir -p`` style (idempotent).

    ``POST /mkdir/<path>`` creates ``<path>``. If a ``name`` form/query
    field is supplied, ``<path>/<name>`` is created instead — that is how
    the browser's "New folder" box works (it posts the current directory
    as ``<path>`` and the typed name as ``name``).
    """
    subpath = subpath.strip("/")
    name = (request.values.get("name") or "").strip()
    if name:
        name = secure_filename(name)
        if not name:
            abort(400, description="Invalid folder name")
        subpath = (subpath + "/" + name).strip("/")
    if not subpath:
        abort(400, description="No directory name given")

    full = _resolve(subpath)
    if os.path.isfile(full):
        abort(400, description="A file with that name already exists")
    os.makedirs(full, exist_ok=True)

    parent = subpath.rsplit("/", 1)[0] if "/" in subpath else ""
    return _respond({"created": _rel(full)}, parent, 201)


@share_bp.route("/delete", methods=["POST", "DELETE"])
@share_bp.route("/delete/", methods=["POST", "DELETE"])
@share_bp.route("/delete/<path:subpath>", methods=["POST", "DELETE"])
def delete(subpath: str = ""):
    """Delete file(s)/directory(ies); directories go **recursively**.

    Two shapes:

    * single, for agents:  ``DELETE`` (or ``POST``) ``/delete/<path>``
    * bulk, for the browser: ``POST /delete`` with one repeated ``sel``
      form field per checked item.

    Refuses to delete the share root. Single-delete returns
    ``{"deleted": "<path>"}``; bulk returns ``{"deleted": [...]}``.
    """
    subpath = subpath.strip("/")
    single = bool(subpath)
    if single:
        targets = [subpath]
    else:
        targets = [
            p.strip("/")
            for p in request.values.getlist("sel")
            if p.strip("/")
        ]
    if not targets:
        abort(
            400,
            description="Nothing to delete (the share root cannot be deleted)",
        )

    # Resolve and existence-check everything first, so a bad entry does
    # not leave a half-applied bulk delete.
    fulls = []
    for rel in targets:
        full = _resolve(rel)
        if not os.path.exists(full):
            abort(404, description=f"No such path: {rel}")
        fulls.append(full)

    for full in fulls:
        if os.path.isdir(full):
            shutil.rmtree(full)
        else:
            os.remove(full)

    deleted = targets
    here = deleted[0].rsplit("/", 1)[0] if "/" in deleted[0] else ""
    return _respond({"deleted": deleted[0] if single else deleted}, here)


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
