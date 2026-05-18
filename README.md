# minishare

A minimal Flask **blueprint-based** file-sharing server: browse, download,
upload. Built to be trivially usable by **both humans and agents** — every
listing page documents the API inline, `GET /help` returns the same docs as
plain text, and any listing can be requested as JSON.

## Run standalone

```bash
pip install -r requirements.txt
python -m minishare                 # serves ./data on http://0.0.0.0:8000, open
python -m minishare -d /srv/files -p 9000
python -m minishare -a alice:s3cret -a bob:hunter2   # require HTTP Basic auth
python -m minishare -x /files                        # mount under a prefix
```

`python run.py …` still works as a dev shim. The shared directory is created
if missing. Override via `-d/--dir` or the `MINISHARE_DIR` env var. Cap
upload size with `MINISHARE_MAX_MB`.

## Use as a git submodule

Add the repo to a parent project and mount the blueprint onto **its** Flask
app — config is namespaced under `MINISHARE_*` so it never clobbers the host
app's settings:

```bash
git submodule add <repo-url> third_party/minishare
git submodule update --init
pip install -e third_party/minishare      # or: add the dir to sys.path
```

```python
from flask import Flask
from minishare import init_app          # or: from minishare import share_bp

app = Flask(__name__)
init_app(app, storage_dir="shared", url_prefix="/files",
         auth={"alice": "s3cret"})       # all args optional
```

`init_app` registers the self-contained `share` blueprint (no extra
templates/static needed). Mounting under `url_prefix` keeps it clear of your
own routes, and all in-page docs / `url_for`s respect that prefix. For full
manual control, register `share_bp` yourself after setting the
`MINISHARE_DIR` / `MINISHARE_AUTH` config keys.

## Authentication (optional)

Pass a `{username: password}` dict to `init_app`/`create_app`. If it is
non-empty, **every** request must pass HTTP Basic auth with one of those
pairs; otherwise access is fully open (the previous behaviour).

```python
from minishare import create_app
app = create_app(storage_dir="data", auth={"alice": "s3cret", "bob": "hunter2"})
```

Equivalent without code: `-a USER:PASS` (repeatable) on the CLI, or the
`MINISHARE_AUTH="alice:s3cret,bob:hunter2"` env var. Passwords are compared
in constant time (`hmac.compare_digest`). There are no roles or per-path
rules — it is plain all-or-nothing access.

```bash
curl -u alice:s3cret 'http://host:8000/browse/?format=json'
```

Browsers prompt automatically; a `401` body tells CLI/agent callers exactly
how to authenticate, and `GET /help` documents it when auth is enabled.

## API

| Action            | Request                                   |
|-------------------|-------------------------------------------|
| Browse (HTML)     | `GET /` · `GET /browse/<path>`            |
| Browse (JSON)     | `GET /browse/<path>?format=json`          |
| Download          | `GET /get/<path>`                         |
| View inline       | `GET /get/<path>?inline=1`                |
| Upload (form)     | `POST /upload[/<dir>]` field `file`       |
| Upload (raw)      | `PUT /put/<path>` body = file bytes       |
| Docs (plain text) | `GET /help`                              |

```bash
curl 'http://host:8000/browse/?format=json'      # list root as JSON
curl -O 'http://host:8000/get/notes/todo.txt'    # download
curl -F file=@report.pdf 'http://host:8000/upload/docs'   # multipart
curl -T report.pdf 'http://host:8000/put/docs/report.pdf' # raw body
```

`<path>` is relative to the share root; `../` and absolute paths are
rejected (`werkzeug.safe_join`). `PUT` creates parent directories and
overwrites; multipart filenames are sanitized.

## Layout

```
minishare/__init__.py   # init_app() + create_app() — public API
minishare/share.py      # the self-contained "share" blueprint (all routes)
minishare/cli.py        # argparse runner (python -m minishare)
minishare/__main__.py   # enables `python -m minishare`
run.py                  # dev shim -> minishare.cli:main
pyproject.toml          # installable: pip install -e .
```

> Development server only — not hardened for untrusted public exposure
> (no auth; put it behind a reverse proxy / network controls if needed).
