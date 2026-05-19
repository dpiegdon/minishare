"""Test suite for minishare.

Covers the things we previously only checked by hand: the app factory /
config, every route, content negotiation, auth, path-traversal and
symlink hardening, the single-source docs, and the browser UI markup.
"""
from __future__ import annotations

import base64
import io
import os

import pytest

from minishare import _parse_auth_env, create_app, make_blueprint
from flask import Flask


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def root(tmp_path):
    return tmp_path


@pytest.fixture
def client(root):
    return create_app(storage_dir=str(root)).test_client()


def auth_header(user, pw):
    raw = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def upload(client, field_files, url="/upload/"):
    """field_files: list of (name, bytes)."""
    data = {"file": [(io.BytesIO(b), n) for n, b in field_files]}
    return client.post(
        url, data=data, content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )


# --------------------------------------------------------------------------- #
# App factory / config
# --------------------------------------------------------------------------- #
def test_config_is_on_blueprint_not_app_config(root):
    app = create_app(storage_dir=str(root))
    cfg = app.blueprints["minishare"].ms_config
    assert cfg["storage_dir"] == os.path.abspath(str(root))
    assert cfg["title"] == "minishare"
    assert cfg["auth"] is None
    assert cfg["max_total_mb"] is None  # unlimited by default
    # we never set a Flask global size cap, and nothing leaks into config
    assert app.config.get("MAX_CONTENT_LENGTH") is None
    for k in ("MINISHARE_DIR", "MINISHARE_TITLE", "MINISHARE_AUTH",
              "STORAGE_DIR"):
        assert k not in app.config


def test_title_param_and_env(root, monkeypatch):
    def title_of(app):
        return app.blueprints["minishare"].ms_config["title"]

    assert title_of(create_app(storage_dir=str(root), title="Acme")) == "Acme"
    monkeypatch.setenv("MINISHARE_TITLE", "FromEnv")
    assert title_of(create_app(storage_dir=str(root))) == "FromEnv"


def test_make_blueprint_multi_instance_isolated(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    app = Flask(__name__)

    @app.route("/")
    def home():
        return "HOST"

    app.register_blueprint(
        make_blueprint(name="a", storage_dir=str(a), title="AAA"),
        url_prefix="/a",
    )
    app.register_blueprint(
        make_blueprint(name="b", storage_dir=str(b), title="BBB",
                       auth={"u": "p"}),
        url_prefix="/b",
    )
    c = app.test_client()
    assert c.get("/").data == b"HOST"          # host route intact
    assert c.get("/browse/").status_code == 404  # not at root

    # independent titles
    assert "AAA" in c.get("/a/").get_data(as_text=True)
    assert "BBB" in c.get("/b/", headers=auth_header("u", "p")).get_data(
        as_text=True
    )
    # independent auth: a is open, b requires creds
    assert c.get("/a/help").status_code == 200
    assert c.get("/b/help").status_code == 401

    # independent storage + relative url_for keeps each prefix
    c.post("/a/upload/", data={"file": [(io.BytesIO(b"x"), "x.txt")]},
           content_type="multipart/form-data")
    aj = c.get("/a/?format=json").get_json()
    assert [e["name"] for e in aj["entries"]] == ["x.txt"]
    assert aj["entries"][0]["download"] == "/a/get/x.txt"
    assert c.get("/b/?format=json", headers=auth_header("u", "p")).get_json()[
        "entries"
    ] == []


def test_parse_auth_env():
    assert _parse_auth_env("a:1,b:2") == {"a": "1", "b": "2"}
    assert _parse_auth_env(" a:1 , ,b:2 ") == {"a": "1", "b": "2"}


def test_storage_dir_created(tmp_path):
    target = tmp_path / "does" / "not" / "exist"
    create_app(storage_dir=str(target))
    assert target.is_dir()


def test_init_app_removed():
    import minishare
    assert not hasattr(minishare, "init_app")
    assert not hasattr(minishare, "share_bp")
    assert set(minishare.__all__) == {"create_app", "make_blueprint"}


# --------------------------------------------------------------------------- #
# Browse / download
# --------------------------------------------------------------------------- #
def test_browse_json_lists_dirs_first(client, root):
    (root / "b.txt").write_text("bb")
    (root / "adir").mkdir()
    (root / "a.txt").write_text("a")
    j = client.get("/browse/?format=json").get_json()
    assert j["path"] == "" and j["type"] == "directory"
    names = [(e["name"], e["type"]) for e in j["entries"]]
    assert names[0] == ("adir", "dir")  # directories sorted first
    txt = next(e for e in j["entries"] if e["name"] == "b.txt")
    assert txt["size"] == 2 and txt["download"].endswith("/get/b.txt")


def test_browse_html_has_brand_and_details_on_top(client):
    html = client.get("/").get_data(as_text=True)
    assert 'title="go to share root">minishare</a>' in html
    assert html.index("<details>") < html.index("<h1>")


def test_browse_missing_404_and_file_redirects(client, root):
    assert client.get("/browse/nope").status_code == 404
    (root / "f.txt").write_text("x")
    r = client.get("/browse/f.txt")
    assert r.status_code in (301, 302) and "/get/f.txt" in r.headers["Location"]


def test_download_attachment_vs_inline(client, root):
    (root / "f.txt").write_text("hello")
    a = client.get("/get/f.txt")
    assert a.data == b"hello"
    assert a.headers["Content-Disposition"].startswith("attachment")
    i = client.get("/get/f.txt?inline=1")
    assert i.headers["Content-Disposition"].startswith("inline")
    assert client.get("/get/missing").status_code == 404


# --------------------------------------------------------------------------- #
# Upload / put
# --------------------------------------------------------------------------- #
def test_multipart_upload_single_and_multi(client, root):
    r = upload(client, [("a.txt", b"A"), ("b.txt", b"B")], "/upload/sub")
    assert r.status_code == 201
    assert sorted(r.get_json()["saved"]) == ["sub/a.txt", "sub/b.txt"]
    assert (root / "sub" / "a.txt").read_bytes() == b"A"


def test_upload_browser_redirects(client):
    data = {"file": [(io.BytesIO(b"x"), "x.txt")]}
    r = client.post(
        "/upload/", data=data, content_type="multipart/form-data",
        headers={"Accept": "text/html"},
    )
    assert r.status_code in (301, 302)


def test_upload_sanitizes_filename(client, root):
    upload(client, [("../../evil.txt", b"P")])
    assert (root / "evil.txt").is_file()
    assert not (root.parent / "evil.txt").exists()


def test_upload_no_file_and_no_usable_name(client):
    assert client.post("/upload/").status_code == 400
    r = upload(client, [("..", b"x")])  # secure_filename -> empty
    assert r.status_code == 400


def test_put_creates_parents_and_overwrites(client, root):
    r = client.put("/put/x/y.txt", data=b"hello")
    assert r.status_code == 201
    assert r.get_json() == {"saved": "x/y.txt", "size": 5}
    client.put("/put/x/y.txt", data=b"hi")
    assert (root / "x" / "y.txt").read_bytes() == b"hi"


def test_put_dotfile_name_preserved(client, root):
    # regression: _rel used to strip leading dots (".env" -> "env")
    r = client.put("/put/.env", data=b"A=B")
    assert r.get_json()["saved"] == ".env"
    assert (root / ".env").read_bytes() == b"A=B"


def test_put_directory_target_400(client, root):
    (root / "d").mkdir()
    assert client.put("/put/d", data=b"x").status_code == 400


# --------------------------------------------------------------------------- #
# mkdir
# --------------------------------------------------------------------------- #
def test_mkdir_path_and_name_field(client, root):
    assert client.post(
        "/mkdir/a/b", headers={"Accept": "application/json"}
    ).get_json() == {"created": "a/b"}
    assert (root / "a" / "b").is_dir()
    client.post("/mkdir/", data={"name": "C"},
                headers={"Accept": "application/json"})
    assert (root / "C").is_dir()


def test_mkdir_idempotent_and_conflict(client, root):
    client.post("/mkdir/x", headers={"Accept": "application/json"})
    assert client.post(
        "/mkdir/x", headers={"Accept": "application/json"}
    ).status_code == 201  # idempotent
    (root / "file").write_text("x")
    assert client.post("/mkdir/file").status_code == 400


def test_mkdir_empty_400(client):
    assert client.post("/mkdir/").status_code == 400


# --------------------------------------------------------------------------- #
# delete (single + bulk)
# --------------------------------------------------------------------------- #
def test_delete_single_string_reply(client, root):
    (root / "f.txt").write_text("x")
    r = client.delete("/delete/f.txt")
    assert r.get_json() == {"deleted": "f.txt"}
    assert not (root / "f.txt").exists()


def test_delete_bulk_list_and_recursive(client, root):
    (root / "a.txt").write_text("a")
    (root / "tree").mkdir()
    (root / "tree" / "inner.txt").write_text("i")
    r = client.post(
        "/delete", data={"sel": ["a.txt", "tree"]},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200
    assert sorted(r.get_json()["deleted"]) == ["a.txt", "tree"]
    assert not (root / "a.txt").exists() and not (root / "tree").exists()


def test_delete_bulk_is_atomic_on_missing(client, root):
    (root / "keep.txt").write_text("k")
    r = client.post("/delete", data={"sel": ["keep.txt", "ghost"]})
    assert r.status_code == 404
    assert (root / "keep.txt").exists()  # nothing deleted because one missing


def test_delete_root_refused(client):
    assert client.delete("/delete/").status_code == 400
    assert client.post("/delete").status_code == 400
    assert client.post("/delete", data={"sel": ""}).status_code == 400


def test_delete_browser_redirects(client, root):
    (root / "f.txt").write_text("x")
    r = client.post("/delete/f.txt", headers={"Accept": "text/html"})
    assert r.status_code in (301, 302)


# --------------------------------------------------------------------------- #
# Security: traversal + symlink
# --------------------------------------------------------------------------- #
def test_traversal_via_sel_rejected(client):
    assert client.post(
        "/delete", data={"sel": "../outside"}
    ).status_code == 400


def test_symlink_escape_blocked(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    secret = tmp_path / "secret.txt"  # OUTSIDE the share root
    secret.write_text("TOPSECRET")
    os.symlink(secret, store / "link.txt")
    c = create_app(storage_dir=str(store)).test_client()
    r = c.get("/get/link.txt")
    assert r.status_code == 400
    assert b"TOPSECRET" not in r.data


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def test_open_mode_no_auth(client):
    assert client.get("/help").status_code == 200


def test_auth_enforced(root):
    # rate limit off here so rapid wrong attempts isolate auth behaviour
    c = create_app(storage_dir=str(root), auth={"u": "p"},
                    auth_rate_limit=0).test_client()
    r = c.get("/help")
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"].startswith("Basic")
    # generic 401: must not leak the software name in body or realm
    assert r.get_data(as_text=True).strip() == "Unauthorized Access"
    assert "minishare" not in r.headers["WWW-Authenticate"].lower()
    assert c.get("/help", headers=auth_header("u", "x")).status_code == 401
    assert c.get("/help", headers=auth_header("v", "p")).status_code == 401
    assert c.get("/help", headers=auth_header("u", "p")).status_code == 200


# --------------------------------------------------------------------------- #
# Docs: single source, pure ASCII, unescaped, injection-safe
# --------------------------------------------------------------------------- #
def test_docs_single_source_ascii_unescaped(client):
    html = client.get("/").get_data(as_text=True)
    helptxt = client.get("/help").get_data(as_text=True)
    pre = html.split("<pre>", 1)[1].split("</pre>", 1)[0]
    assert pre.strip() == helptxt.strip()           # one source
    assert pre.isascii()                            # pure ASCII
    for bad in ("&lt;", "&gt;", "&#39;", "&#34;", "&amp;"):
        assert bad not in pre                       # not HTML-mangled
    assert "curl -sS '" in pre and '"../"' in pre    # quotes literal
    assert "$path" in pre and "<path>" not in pre


def test_help_is_plain_text(client):
    r = client.get("/help")
    assert r.mimetype == "text/plain"


def test_host_header_cannot_inject(client):
    r = client.get("/", headers={"Host": 'x"><script>boom</script>'})
    assert "<script>boom" not in r.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# Browser UI markup
# --------------------------------------------------------------------------- #
def test_upload_hint_between_picker_and_button(client):
    html = client.get("/").get_data(as_text=True)
    i = html.index('id="upf"')
    h = html.index("or drop files here")
    b = html.index('id="upb"')
    assert i < h < b  # hint sits with the picker, before the Upload button


def test_delete_ui_is_multiselect(client, root):
    (root / "f.txt").write_text("x")
    html = client.get("/").get_data(as_text=True)
    assert 'type="checkbox" name="sel" value="f.txt"' in html
    assert 'id="delbtn"' in html and 'id="delbtn" disabled' not in html
    assert "\U0001f5d1" not in html  # old per-row trash button gone
    # select-all is a plain button (must not submit/trigger the delete form)
    assert '<button type="button" id="selall"' in html


def test_content_negotiation_json_variants(client, root):
    (root / "f.txt").write_text("x")
    assert client.get("/browse/?format=json").is_json
    assert client.get(
        "/browse/", headers={"Accept": "application/json"}
    ).is_json
    assert not client.get(
        "/browse/", headers={"Accept": "text/html"}
    ).is_json


# --------------------------------------------------------------------------- #
# Size / storage quotas
# --------------------------------------------------------------------------- #
def _app(tmp_path, **kw):
    return create_app(storage_dir=str(tmp_path), **kw).test_client()


def test_max_mb_rejects_big_single_upload(tmp_path):
    c = _app(tmp_path, max_mb=1)
    big = b"x" * (1024 * 1024 + 50)
    assert c.put("/put/big.bin", data=big).status_code == 413
    # PUT under the cap is fine
    assert c.put("/put/ok.bin", data=b"x" * 1000).status_code == 201


def test_max_total_mb_blocks_when_full_but_allows_read_delete(tmp_path):
    c = _app(tmp_path, max_total_mb=1)
    # fill to ~just over 1 MB
    c.put("/put/a.bin", data=b"x" * (1024 * 1024 - 100))
    assert c.put("/put/b.bin", data=b"y" * 500).status_code == 413  # full
    # download still works while full
    assert c.get("/get/a.bin").status_code == 200
    # delete still works while full, and frees space
    assert c.delete("/delete/a.bin").status_code == 200
    assert c.put("/put/c.bin", data=b"z" * 500).status_code == 201
    # mkdir is allowed even when full (adds no file bytes)
    c.put("/put/fill.bin", data=b"x" * (1024 * 1024))
    assert c.post("/mkdir/folder",
                  headers={"Accept": "application/json"}).status_code == 201


def test_max_total_mb_none_is_unlimited(tmp_path):
    c = _app(tmp_path)  # default: no cap
    assert c.put("/put/big.bin", data=b"x" * (3 * 1024 * 1024)).status_code == 201


def test_storage_indicator_rendered(tmp_path):
    limited = _app(tmp_path / "l", max_total_mb=100)
    html = limited.get("/").get_data(as_text=True)
    assert 'class="su"' in html and "/ 100 MB" in html

    unlimited = _app(tmp_path / "u")
    assert "MB (unlimited)" in unlimited.get("/").get_data(as_text=True)


# --------------------------------------------------------------------------- #
# Security hardening (audit follow-up)
# --------------------------------------------------------------------------- #
def test_inline_only_for_safe_types(client, root):
    (root / "evil.html").write_text("<script>alert(1)</script>")
    (root / "d.svg").write_text("<svg onload=alert(1)>")
    (root / "n.txt").write_text("hi")
    # scriptable types are forced to attachment even with ?inline=1
    for f in ("evil.html", "d.svg"):
        r = client.get(f"/get/{f}?inline=1")
        assert r.headers["Content-Disposition"].startswith("attachment")
    # safe types may still be viewed inline
    assert client.get("/get/n.txt?inline=1").headers[
        "Content-Disposition"
    ].startswith("inline")


def test_download_has_nosniff_and_sandbox(client, root):
    (root / "a.bin").write_text("x")
    r = client.get("/get/a.bin")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Content-Security-Policy"] == "sandbox"


def test_security_headers_on_pages(client):
    h = client.get("/").headers
    assert h["X-Frame-Options"] == "DENY"
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["Referrer-Policy"] == "no-referrer"


def test_csrf_same_origin_guard(client, root):
    (root / "v.txt").write_text("s")
    # cross-origin browser POST is refused, file untouched
    r = client.post("/delete", data={"sel": "v.txt"},
                     headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and (root / "v.txt").exists()
    # same-origin browser POST works
    r = client.post("/delete", data={"sel": "v.txt"},
                     headers={"Origin": "http://localhost"})
    assert r.status_code == 200 and not (root / "v.txt").exists()
    # agents/curl (no Origin/Referer) are unaffected
    (root / "w.txt").write_text("s")
    assert client.delete("/delete/w.txt").status_code == 200


def test_put_over_cap_is_atomic(tmp_path):
    c = _app(tmp_path, max_mb=1)
    r = c.put("/put/big.bin", data=b"x" * (1024 * 1024 + 500))
    assert r.status_code == 413
    # no partial file and no leftover temp from the streamed write
    assert not (tmp_path / "big.bin").exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".ul-")] == []


def test_upload_total_cap_enforced_on_actual_bytes(tmp_path):
    c = _app(tmp_path, max_total_mb=1)
    # near the 1 MB cap
    assert c.put("/put/a.bin", data=b"a" * (1024 * 1024 - 200)).status_code == 201
    # a multipart upload that pushes the store over the cap is rolled back
    big = b"b" * 4000
    r = c.post("/upload/", data={"file": [(io.BytesIO(big), "b.bin")]},
               content_type="multipart/form-data",
               headers={"Accept": "application/json"})
    assert r.status_code == 413
    assert not (tmp_path / "b.bin").exists()          # rolled back
    assert (tmp_path / "a.bin").exists()              # earlier data intact


def test_put_streams_large_body_when_unlimited(tmp_path):
    c = _app(tmp_path)  # no caps
    r = c.put("/put/big.bin", data=b"z" * (5 * 1024 * 1024))
    assert r.status_code == 201 and r.get_json()["size"] == 5 * 1024 * 1024
    assert (tmp_path / "big.bin").stat().st_size == 5 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Brute-force backoff (per-IP auth rate limit)
# --------------------------------------------------------------------------- #
def _ip(addr):
    return {"environ_base": {"REMOTE_ADDR": addr}}


def test_auth_backoff_per_ip(tmp_path):
    import time
    c = create_app(storage_dir=str(tmp_path), auth={"u": "p"},
                    auth_rate_limit=0.3).test_client()
    a, b = _ip("9.9.9.9"), _ip("8.8.8.8")
    # no-credential request is the browser-challenge path: never throttled
    assert c.get("/help", **a).status_code == 401
    # first wrong credential -> 401 and arms the IP
    assert c.get("/help", headers=auth_header("u", "x"), **a).status_code == 401
    # next credentialed attempt within the window -> 429 + Retry-After
    r = c.get("/help", headers=auth_header("u", "x"), **a)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    # a no-credential request is still NOT throttled (login flow intact)
    assert c.get("/help", **a).status_code == 401
    # a different IP is unaffected
    assert c.get("/help", headers=auth_header("u", "p"), **b).status_code == 200
    # after the window the IP is forgotten (map stays small) -> works again
    time.sleep(0.35)
    assert c.get("/help", headers=auth_header("u", "p"), **a).status_code == 200


def test_auth_backoff_disabled(tmp_path):
    c = create_app(storage_dir=str(tmp_path), auth={"u": "p"},
                    auth_rate_limit=0).test_client()
    codes = [c.get("/help", headers=auth_header("u", "x")).status_code
             for _ in range(4)]
    assert codes == [401, 401, 401, 401]  # never 429 when disabled


def test_open_mode_never_rate_limited(tmp_path):
    c = create_app(storage_dir=str(tmp_path)).test_client()  # no auth
    assert [c.get("/help").status_code for _ in range(4)] == [200] * 4
