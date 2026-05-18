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

from minishare import _parse_auth_env, create_app, init_app
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
def test_config_namespaced_and_defaults(root):
    app = create_app(storage_dir=str(root))
    assert app.config["MINISHARE_DIR"] == os.path.realpath(str(root)) or \
        os.path.abspath(str(root)) == app.config["MINISHARE_DIR"]
    assert app.config["MINISHARE_TITLE"] == "minishare"
    assert app.config["MINISHARE_AUTH"] is None
    assert "STORAGE_DIR" not in app.config  # old generic key gone


def test_title_param_and_env(root, monkeypatch):
    assert create_app(storage_dir=str(root), title="Acme").config[
        "MINISHARE_TITLE"
    ] == "Acme"
    monkeypatch.setenv("MINISHARE_TITLE", "FromEnv")
    assert create_app(storage_dir=str(root)).config[
        "MINISHARE_TITLE"
    ] == "FromEnv"


def test_parse_auth_env():
    assert _parse_auth_env("a:1,b:2") == {"a": "1", "b": "2"}
    assert _parse_auth_env(" a:1 , ,b:2 ") == {"a": "1", "b": "2"}


def test_storage_dir_created(tmp_path):
    target = tmp_path / "does" / "not" / "exist"
    create_app(storage_dir=str(target))
    assert target.is_dir()


def test_init_app_with_prefix_isolated(tmp_path):
    app = Flask(__name__)

    @app.route("/")
    def home():
        return "HOST"

    init_app(app, storage_dir=str(tmp_path), url_prefix="/files")
    c = app.test_client()
    assert c.get("/").data == b"HOST"
    assert c.get("/files/?format=json").status_code == 200
    assert c.get("/browse/").status_code == 404  # not mounted at root


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
    assert '<a href="/browse/" title="go to share root">minishare</a>' in html
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
    c = create_app(storage_dir=str(root), auth={"u": "p"}).test_client()
    r = c.get("/help")
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"].startswith("Basic")
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
    assert "curl '" in pre and '"../"' in pre       # quotes literal
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
    h = html.index("or drop files onto the picker")
    b = html.index('id="upb"')
    assert i < h < b  # hint sits with the picker, before the Upload button


def test_delete_ui_is_multiselect(client, root):
    (root / "f.txt").write_text("x")
    html = client.get("/").get_data(as_text=True)
    assert 'type="checkbox" name="sel" value="f.txt"' in html
    assert 'id="delbtn"' in html and 'id="delbtn" disabled' not in html
    assert "\U0001f5d1" not in html  # old per-row trash button gone


def test_content_negotiation_json_variants(client, root):
    (root / "f.txt").write_text("x")
    assert client.get("/browse/?format=json").is_json
    assert client.get(
        "/browse/", headers={"Accept": "application/json"}
    ).is_json
    assert not client.get(
        "/browse/", headers={"Accept": "text/html"}
    ).is_json
