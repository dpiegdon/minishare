"""The file-sharing blueprint: browse, download, upload, mkdir, delete.

Design goal: every page is self-documenting so that both humans and
automated agents can use the server without external instructions. Each
HTML listing carries a "CLI / API" help box, ``GET /help`` returns the
same docs as plain text, and any listing endpoint can return JSON via
``?format=json`` or an ``Accept: application/json`` header.
"""
from __future__ import annotations

import hmac
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

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


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _cfg() -> dict:
    """Per-instance config of the blueprint handling this request.

    Each ``make_blueprint()`` stashes its settings on the Blueprint
    object, so several independent minishare mounts can coexist on one
    Flask app without sharing storage / auth / title.
    """
    return current_app.blueprints[request.blueprint].ms_config


def _state() -> dict:
    """Per-instance mutable runtime state (auth-failure rate limiter)."""
    return current_app.blueprints[request.blueprint].ms_state


def _storage_root() -> str:
    return _cfg()["storage_dir"]


def _dir_used_bytes(root: str) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            fp = os.path.join(dirpath, fn)
            if not os.path.islink(fp):
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    return total


def _storage_use() -> str:
    """Small human string: ``n.n / m MB`` or ``n.n MB (unlimited)``."""
    used_mb = _dir_used_bytes(_storage_root()) / (1024 * 1024)
    limit = _cfg()["max_total_mb"]
    if limit is None:
        return f"{used_mb:.1f} MB (unlimited)"
    return f"{used_mb:.1f} / {limit} MB"


_OVER_MSG = "Upload rejected: exceeds the configured size/storage limit."


def _request_ceiling() -> int | None:
    """Hard byte limit for this request, or None if no caps apply.

    Rejects (413) up front when the store is already full. The returned
    ceiling is enforced against the *actual* bytes received (not the
    client's Content-Length), so it holds even without a proxy.
    """
    cfg = _cfg()
    caps = []
    if cfg["max_mb"] is not None:
        caps.append(cfg["max_mb"] * 1024 * 1024)
    tot = cfg["max_total_mb"]
    if tot is not None:
        rem = tot * 1024 * 1024 - _dir_used_bytes(_storage_root())
        if rem <= 0:
            abort(
                413,
                description=(
                    f"Storage is full ({tot} MB limit) - delete files to "
                    f"free space. Downloads and deletes still work."
                ),
            )
        caps.append(rem)
    return min(caps) if caps else None


def _early_reject(ceiling: int | None) -> None:
    """Courtesy 413 for honest clients (before reading the body)."""
    cl = request.content_length
    if ceiling is not None and cl is not None and cl > ceiling:
        abort(413, description=_OVER_MSG)


def _stream_to_file(dest_full: str, ceiling: int | None) -> int:
    """Stream the request body to ``dest_full`` atomically.

    Bounded memory (chunked, never buffers the whole body) and the
    ``ceiling`` is enforced on bytes actually read, so a lying/omitted
    Content-Length cannot beat it. Returns the byte count.
    """
    d = os.path.dirname(dest_full)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".ul-")
    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = request.stream.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if ceiling is not None and total > ceiling:
                    abort(413, description=_OVER_MSG)
                out.write(chunk)
        os.replace(tmp, dest_full)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return total


def _doc_base() -> str:
    """External URL of the blueprint root, honouring any ``url_prefix``.

    Derived from the single-rule ``/help`` endpoint so it is deterministic
    regardless of where the blueprint is mounted (``browse`` has several
    rules, so ``url_for`` on it is ambiguous for doc purposes).

    Sanitised to URL-safe characters only: the docs are rendered with
    ``|safe`` (so quotes and text are not HTML-mangled), so a crafted
    Host header must not be able to inject markup through this value.
    """
    raw = url_for(".help_text", _external=True)[: -len("/help")].rstrip(
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
    return redirect(url_for(".browse", subpath=redirect_subpath))


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
                "download": None if is_dir else url_for(".get", subpath=rel),
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
  * Uploads may return 413 if a per-upload or total-storage limit is set;
    downloads and deletes always work. The HTML pages show storage use.
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
  td.sel,th.sel{text-align:center;width:8rem}
  #selall{font-size:12px;margin-right:.3rem}
  details{margin:.5rem 0}
  summary{color:#aaa;font-size:12px;cursor:pointer}
  pre{white-space:pre-wrap;font-size:12px;color:#666;margin:.4rem 0 0}
  .crumb{color:#666}
  .su{color:#888;font-size:12px;margin:-.4rem 0 .8rem}
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
<div class="su">storage: {{ storage_use }}</div>

<form method="post" action="{{ delete_url }}" id="delform"
      onsubmit="return confirm('Delete ' + this.querySelectorAll('input[name=sel]:checked').length + ' selected item(s)? Folders are deleted recursively. This cannot be undone.')">
<table>
  <tr><th>Name</th><th class="r">Size</th><th>Modified</th>
      <th class="sel">
        <button type="button" id="selall" title="select / clear all">all</button>
        <button type="submit" id="delbtn" title="delete the selected items">Delete</button>
      </th></tr>
  {% if subpath %}
  <tr><td class="dir"><a href="{{ parent_url }}">⬆ ..</a></td><td></td><td></td><td></td></tr>
  {% endif %}
  {% for e in entries %}
  <tr>
    {% if e.type == 'dir' %}
      <td class="dir">📁 <a href="{{ url_for('.browse', subpath=e.path) }}">{{ e.name }}/</a></td>
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
      all = document.getElementById('selall'),
      form = document.getElementById('delform');
  if (!btn || !form) return;
  function sync() {
    btn.disabled = !form.querySelector('input[name=sel]:checked');
  }
  form.addEventListener('change', sync);
  if (all) all.addEventListener('click', function () {
    var boxes = form.querySelectorAll('input[name=sel]');
    var every = boxes.length > 0;
    boxes.forEach(function (b) { if (!b.checked) every = false; });
    boxes.forEach(function (b) { b.checked = !every; });  // toggle select/clear
    sync();
  });
  sync();   // progressive enhancement: only JS disables the button
})();
</script>
"""


# --------------------------------------------------------------------------- #
# Security: headers, CSRF, safe inline types
# --------------------------------------------------------------------------- #
def _security_headers(resp):
    """Defence-in-depth headers on every response."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


def _csrf_guard():
    """Block cross-origin state-changing browser requests.

    Browsers send ``Origin`` (and usually ``Referer``) on mutating
    requests; if present it must match this host. curl / agents send
    neither, so they are unaffected — only a browser tricked by another
    site is rejected (relevant when ``auth`` is enabled).
    """
    if request.method not in ("POST", "PUT", "DELETE"):
        return None
    for header in ("Origin", "Referer"):
        val = request.headers.get(header)
        if val:
            if urlparse(val).netloc != request.host:
                abort(403, description="Cross-origin request refused")
            return None
    return None


def _inline_safe(mime: str | None) -> bool:
    """May this content type be served ``inline`` without XSS risk?

    Anything that can script in the page origin (HTML, SVG, ...) is
    excluded and will be sent as an attachment instead.
    """
    if not mime:
        return False
    if mime in ("application/pdf", "text/plain"):
        return True
    top = mime.split("/", 1)[0]
    return top in ("image", "audio", "video") and mime != "image/svg+xml"


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
def _unauthorized():
    """Generic 401 — no software name / hints in body or realm."""
    resp = current_app.response_class(
        "Unauthorized Access\n", status=401, mimetype="text/plain"
    )
    resp.headers["WWW-Authenticate"] = 'Basic realm="Restricted"'
    return resp


def _enforce_auth():
    """Require HTTP Basic auth when a ``{user: password}`` dict is set.

    No dict (or empty) == fully open access (nothing to brute-force, so
    no rate limiting either). Each mounted blueprint enforces its own
    ``auth`` independently.

    Brute-force backoff: a *wrong-credential* attempt arms a per-IP
    cooldown of ``auth_rate_limit`` seconds; another credentialed
    attempt from that IP inside the window gets ``429`` (no password
    check, no worker held). Requests without credentials (the normal
    browser challenge flow) are never throttled. A correct login clears
    the IP. The map only ever holds IPs that failed within the window —
    entries older than the interval are dropped on every pass.
    """
    cfg = _cfg()
    users = cfg["auth"]
    if not users:
        return None  # open access

    rl = cfg["auth_rate_limit"] or 0
    a = request.authorization
    ip = request.remote_addr or "?"
    now = time.monotonic()

    if rl > 0:
        st = _state()
        with st["lock"]:
            fails = st["fails"]
            for stale in [k for k, t in fails.items() if now - t >= rl]:
                del fails[stale]
            ts = fails.get(ip) if a is not None else None
        if ts is not None and now - ts < rl:
            resp = current_app.response_class(
                "Too Many Requests\n", status=429, mimetype="text/plain"
            )
            resp.headers["Retry-After"] = str(int(rl - (now - ts)) + 1)
            return resp

    ok = False
    if a is not None and a.password is not None:
        expected = users.get(a.username or "")
        ok = expected is not None and hmac.compare_digest(
            str(expected), a.password
        )

    if ok:
        if rl > 0:
            st = _state()
            with st["lock"]:
                st["fails"].pop(ip, None)  # legit user: forget this IP
        return None

    if rl > 0 and a is not None:  # an actual wrong-credential guess
        st = _state()
        with st["lock"]:
            st["fails"][ip] = now
    return _unauthorized()


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def browse(subpath: str = ""):
    """Directory listing as HTML (default) or JSON (``?format=json``)."""
    subpath = subpath.strip("/")
    full = _resolve(subpath)

    if not os.path.exists(full):
        abort(404, description=f"No such path: {subpath}")
    if os.path.isfile(full):
        # Browsing a file just means "download it".
        return redirect(url_for(".get", subpath=subpath))

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
        crumbs.append((part, url_for(".browse", subpath=acc)))

    parent = subpath.rsplit("/", 1)[0] if "/" in subpath else ""
    cfg = _cfg()
    auth_on = bool(cfg["auth"])
    return render_template_string(
        _PAGE,
        subpath=subpath,
        title=cfg["title"],
        entries=entries,
        crumbs=crumbs,
        storage_use=_storage_use(),
        root_url=url_for(".browse"),
        parent_url=url_for(".browse", subpath=parent) if subpath else "",
        upload_url=url_for(".upload", subpath=subpath),
        mkdir_url=url_for(".mkdir", subpath=subpath),
        delete_url=url_for(".delete"),
        human=_human_size,
        doc=_api_doc(_doc_base(), auth_on),
    )


def get(subpath: str):
    """Download a file. ``?inline=1`` views it in the browser, but only
    for safe content types — HTML/SVG/etc. are always sent as an
    attachment so a stored file cannot script in this origin."""
    full = _resolve(subpath.strip("/"))
    if not os.path.isfile(full):
        abort(404, description=f"Not a file: {subpath}")
    want_inline = request.args.get("inline") in ("1", "true", "yes")
    mime, _ = mimetypes.guess_type(full)
    as_attachment = not (want_inline and _inline_safe(mime))
    resp = send_file(full, as_attachment=as_attachment)
    # Untrusted user content: kill scripting even if a client renders it.
    resp.headers["Content-Security-Policy"] = "sandbox"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def upload(subpath: str = ""):
    """Multipart upload of one or more files into directory ``subpath``."""
    ceiling = _request_ceiling()
    _early_reject(ceiling)
    subpath = subpath.strip("/")
    dest_dir = _resolve(subpath)
    os.makedirs(dest_dir, exist_ok=True)
    if not os.path.isdir(dest_dir):
        abort(400, description="Upload target is not a directory")

    files = request.files.getlist("file")
    if not files or all(f.filename == "" for f in files):
        abort(400, description="No 'file' field in multipart form data")

    saved, saved_full = [], []
    for f in files:
        name = secure_filename(f.filename)
        if not name:
            continue
        path = os.path.join(dest_dir, name)
        f.save(path)
        saved.append((subpath + "/" + name).lstrip("/"))
        saved_full.append(path)

    if not saved:
        abort(400, description="No usable filenames in the upload")

    # Enforce on the *actual* bytes written (a lying Content-Length, or
    # none at all, cannot beat the quota). Roll back if it does.
    cfg = _cfg()
    added = sum(os.path.getsize(p) for p in saved_full)
    over = cfg["max_mb"] is not None and added > cfg["max_mb"] * 1024 * 1024
    if not over and cfg["max_total_mb"] is not None:
        over = _dir_used_bytes(_storage_root()) > cfg[
            "max_total_mb"
        ] * 1024 * 1024
    if over:
        for p in saved_full:
            try:
                os.unlink(p)
            except OSError:
                pass
        abort(413, description=_OVER_MSG)
    return _respond({"saved": saved}, subpath, 201)


def put(subpath: str):
    """Raw-body upload to an exact path; creates parent dirs, overwrites.

    Streamed to disk with a hard byte ceiling, so it neither buffers the
    whole body in memory nor lets a lying Content-Length exceed the cap.
    """
    full = _resolve(subpath.strip("/"))
    if os.path.isdir(full):
        abort(400, description="Target is a directory; include a filename")
    ceiling = _request_ceiling()
    _early_reject(ceiling)
    size = _stream_to_file(full, ceiling)
    return jsonify({"saved": _rel(full), "size": size}), 201


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


def help_text():
    """Plain-text API docs — handy for `curl host/help`."""
    return current_app.response_class(
        _api_doc(_doc_base(), bool(_cfg()["auth"])),
        mimetype="text/plain",
    )


def _errors(err):
    msg = getattr(err, "description", str(err))
    if _wants_json():
        return jsonify({"error": msg, "code": err.code}), err.code
    return current_app.response_class(
        f"{err.code} {msg}\n\nTry GET /help for usage.\n",
        status=err.code,
        mimetype="text/plain",
    )


# --------------------------------------------------------------------------- #
# Blueprint factory
# --------------------------------------------------------------------------- #
def make_blueprint(
    *,
    storage_dir: str,
    name: str = "minishare",
    auth: dict[str, str] | None = None,
    title: str = "minishare",
    max_mb: int | None = None,
    max_total_mb: int | None = None,
    auth_rate_limit: float = 2.0,
) -> Blueprint:
    """Build a ready-to-register minishare blueprint.

    The integrator registers it themselves and may mount several
    independent instances on one app (use a unique ``name`` per
    instance)::

        app.register_blueprint(
            make_blueprint(name="files", storage_dir="/srv/a"),
            url_prefix="/files",
        )

    All configuration is by parameter (no app.config, no env): each
    blueprint carries its own settings, so instances never collide.

    :param storage_dir: directory to share; created if missing.
    :param name: blueprint name (must be unique per Flask app).
    :param auth: ``{user: password}``; non-empty == HTTP Basic required.
    :param title: brand shown in the header / page title.
    :param max_mb: reject a single upload larger than this (413).
    :param max_total_mb: reject uploads once the storage directory
        reaches this many MB; ``None`` == unlimited. Downloads and
        deletes always work.
    :param auth_rate_limit: min seconds between *failed* credentialed
        auth attempts per client IP (brute-force backoff); offenders get
        ``429``. ``0`` disables. Only relevant when ``auth`` is set.
        Note: uses ``request.remote_addr`` — behind a proxy apply
        ``ProxyFix``; the limiter is per worker process.
    """
    storage_dir = os.path.abspath(storage_dir)
    os.makedirs(storage_dir, exist_ok=True)

    bp = Blueprint(name, __name__)
    bp.ms_config = {
        "storage_dir": storage_dir,
        "auth": auth or None,
        "title": title or "minishare",
        "max_mb": max_mb,
        "max_total_mb": max_total_mb,
        "auth_rate_limit": auth_rate_limit,
    }
    bp.ms_state = {"fails": {}, "lock": threading.Lock()}

    bp.before_request(_enforce_auth)
    bp.before_request(_csrf_guard)
    bp.after_request(_security_headers)
    for code in (400, 403, 404, 413):
        bp.register_error_handler(code, _errors)

    bp.add_url_rule("/", "browse", browse)
    bp.add_url_rule("/browse/", "browse", browse)
    bp.add_url_rule("/browse/<path:subpath>", "browse", browse)
    bp.add_url_rule("/get/<path:subpath>", "get", get)
    for rule in ("/upload", "/upload/", "/upload/<path:subpath>"):
        bp.add_url_rule(rule, "upload", upload, methods=["POST"])
    bp.add_url_rule("/put/<path:subpath>", "put", put, methods=["PUT"])
    for rule in ("/mkdir", "/mkdir/", "/mkdir/<path:subpath>"):
        bp.add_url_rule(rule, "mkdir", mkdir, methods=["POST"])
    for rule in ("/delete", "/delete/", "/delete/<path:subpath>"):
        bp.add_url_rule(rule, "delete", delete, methods=["POST", "DELETE"])
    bp.add_url_rule("/help", "help_text", help_text)
    return bp
