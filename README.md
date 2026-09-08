# minishare

A minimal Flask **blueprint** file-sharing server: browse, download,
upload, rename/move, create and delete (files & directories) — usable
by **both humans and agents**. Browsers get a quiet HTML UI; agents get
JSON on any listing (`?format=json`) and a plain-text API reference at
`GET /help`. Mutating endpoints answer agents with JSON and browsers
with a redirect. Optional per-upload / total-storage caps; every page
shows a small `storage:` indicator.

## Run standalone

```bash
make run                          # serve ./data on :8000, venv and all
make run PORT=9000 DIR=/srv/files
```

For anything the two `make` variables don't cover, call the CLI (after
`pip install -e .`):

```bash
python -m minishare -d /srv/files -p 9000 -t "Acme Files"
python -m minishare -x /files --max-mb 50 --max-total-mb 2000
python -m minishare -a alice:'scrypt:...'   # HTTP Basic auth, see below
```

The shared dir is created if missing. `python -m minishare --help` lists
every flag; each has a `MINISHARE_*` env equivalent.

## Use as a git submodule

All configuration is by parameter — nothing touches `app.config`, so the
host app is untouched and you can mount several independent instances:

```bash
git submodule add <repo-url> third_party/minishare
pip install -e third_party/minishare
```

```python
from flask import Flask
from minishare import make_blueprint

app = Flask(__name__)
app.register_blueprint(
    make_blueprint(name="files", storage_dir="/srv/files",
                   auth={"alice": "scrypt:..."}, title="Acme Files",
                   max_total_mb=2000),
    url_prefix="/files",
)
```

`make_blueprint` parameters: `storage_dir` (required), `name`, `auth`,
`title`, `max_mb`, `max_total_mb`, `auth_rate_limit` (see below). Give
each instance a unique `name`; in-page links are blueprint-relative, so
any name and `url_prefix` just work.

## Authentication (optional)

Pass a `{username: hash}` dict. If non-empty, **every** request needs
HTTP Basic auth with one of those pairs; otherwise access is fully open.
Each value must be a **Werkzeug password hash** — plaintext is rejected
(`make_blueprint` raises `ValueError`), so the password is never stored
in clear. Generate one (no echo; never on argv or in shell history):

```bash
python -m minishare.hashpw
# Password: ····
# -> scrypt:32768:8:1$....$....
# use that string as the dict value, or after the ':' in
# -a alice:'scrypt:...' / MINISHARE_AUTH (split on the 1st ':')
```

Hashes are verified with `check_password_hash` (the KDF runs per
request, so a costly hash trades CPU for not storing the password).
There are no roles — plain all-or-nothing access. The first 4
wrong-credential attempts from an IP just get `401` (browsers retry on
a normal login); past that the IP is blocked hard for `auth_rate_limit`
seconds (default `10`) — further credentialed attempts get a `429`
advising a ~15 s wait, with no password check. A correct login clears
the IP; no-credential challenge requests are never counted or
throttled.

Don't pass credentials on the command line (`curl -u USER:PASS` leaks
them into shell history and `ps`); `GET /help` documents a curl-config
(`-K ms.curl`) recipe that keeps the secret in a locked-down file.

## API

The browser UI covers browse, download, multi-select/drag-drop upload,
create folder, checkbox delete, and a per-row `⋯` menu to rename/move
or delete that one entry. On a phone the listing reflows to one block
per entry — name, then size and date underneath, checkbox and `⋯` to
the right. It follows your device's light/dark setting; there is no
theme switch and nothing is stored.

The same actions are documented endpoints for agents and scripts in
**[API.md](minishare/API.md)** — the single source, served verbatim
(with the live base URL) at `GET /help` and folded into the top of
every page, so an agent doing `curl /` sees it immediately.

## Security

Path traversal blocked (`safe_join` + realpath, incl. symlink escape);
constant-time password compare; generic 401 (no fingerprint); every
response carries `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy:
same-origin`; downloads get `Content-Security-Policy: sandbox` and
HTML/SVG is always sent as an attachment; `POST/PUT/DELETE` are refused
cross-origin (curl/agents send no `Origin`/`Referer` and are unaffected).
Size caps are enforced on bytes actually received (streamed `PUT`,
rolled-back oversize multipart), so they hold without a proxy.
Destructive ops fail closed: recursively deleting a non-empty directory
needs `?recursive=1` and overwriting an existing file (PUT, multipart
upload, or rename) needs `?overwrite=1`, else `409` — so a stray or
injection-nudged agent request can't silently nuke a tree or clobber a
file (the browser forms pass these flags, so the human UX is unchanged).

Deployment is on you (no proxy assumed): use a production WSGI server,
never `--debug` on a reachable port, terminate TLS before HTTP Basic, and
remember anyone who can reach an unauthenticated instance can delete. The
per-IP backoff is per-process / per-`remote_addr` — behind a proxy apply
`ProxyFix` and/or a real rate limiter.

## Tests / layout

`make test` builds `.venv` from `pyproject.toml` and runs the suite;
`make run` starts a dev server; `make clean` removes the venv and
caches. See the `Makefile`.

`minishare/__init__.py` (public API), `minishare/share.py`
(`make_blueprint` + all routes), `minishare/cli.py` (runner),
`minishare/hashpw.py` (`python -m minishare.hashpw`), `tests/`.
See `AGENTS.md` for the project working agreement — read it first.
