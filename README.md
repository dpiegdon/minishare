# minishare

A minimal Flask **blueprint-based** file-sharing server: browse, download,
upload, create and delete (files & directories). Built to be trivially
usable by **both humans and agents**:

- the full API/curl reference is documented in **one** place
  (`_api_doc`) and shown two ways: served verbatim at `GET /help`
  (plain text), and embedded at the **very top** of every listing page
  (above the breadcrumb) in a **collapsed** `<details>` block — folded
  away for humans, but present in the raw HTML so an agent doing
  `curl /` sees it first. The text is pure ASCII and rendered unescaped
  (no `&lt;` / `&#39;` noise) — placeholders read `$path` / `$dir`;
- any listing can be requested as JSON (`?format=json`), and mutating
  endpoints reply with JSON to agents but redirect browsers back to the UI;
- optional per-upload and total-storage size caps; every page shows a
  small `storage: n.n / m MB` (or `n.n MB (unlimited)`) indicator.

## Run standalone

```bash
pip install -r requirements.txt
python -m minishare                 # serves ./data on http://0.0.0.0:8000, open
python -m minishare -d /srv/files -p 9000
python -m minishare -a alice:s3cret -a bob:hunter2   # require HTTP Basic auth
python -m minishare -x /files                        # mount under a prefix
python -m minishare -t "Acme Files"                  # custom brand name
python -m minishare --max-mb 50 --max-total-mb 2000  # size caps
```

`python run.py …` still works as a dev shim. The shared directory is created
if missing. Override via `-d/--dir` or the `MINISHARE_DIR` env var. The
header brand / page title defaults to `minishare`; override with
`-t/--title` or `MINISHARE_TITLE`. Clicking the brand goes back to the
share root. Size caps: `--max-mb` (single upload) and `--max-total-mb`
(whole store; default unlimited), or the `MINISHARE_MAX_MB` /
`MINISHARE_MAX_TOTAL_MB` env vars — once full, uploads get a `413` but
downloads and deletes still work.

## Use as a git submodule

Add the repo to a parent project, build a blueprint with `make_blueprint`
and register it on **your** app yourself. All configuration is by
parameter — nothing is written to `app.config`, so the host app is
untouched and you can mount **several independent instances**:

```bash
git submodule add <repo-url> third_party/minishare
git submodule update --init
pip install -e third_party/minishare      # or: add the dir to sys.path
```

```python
from flask import Flask
from minishare import make_blueprint

app = Flask(__name__)
app.register_blueprint(
    make_blueprint(name="files", storage_dir="/srv/files",
                   auth={"alice": "s3cret"}, title="Acme Files",
                   max_total_mb=2000),
    url_prefix="/files",
)
app.register_blueprint(                       # a second, fully independent mount
    make_blueprint(name="public", storage_dir="/srv/public"),
    url_prefix="/public",
)
```

The blueprint is self-contained (no extra templates/static). Give each
instance a unique `name`; every in-page link / `url_for` is
blueprint-relative, so any name and `url_prefix` just work. `make_blueprint`
parameters: `storage_dir` (required), `name`, `auth`, `title`, `max_mb`,
`max_total_mb`.

## Authentication (optional)

Pass a `{username: password}` dict to `make_blueprint`/`create_app`. If it
is non-empty, **every** request must pass HTTP Basic auth with one of those
pairs; otherwise access is fully open.

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

From a **browser** every listing page lets a human browse into/out of
directories, download files, upload files (multi-select or drag & drop),
create a folder, and delete entries: tick the checkboxes in the last
column (or the **all** button to select/clear everything) and hit
**Delete** (greyed out until something is selected, with a confirm
prompt). The same actions are available as documented endpoints for
**agents/scripts**:

| Action            | Request                                       |
|-------------------|-----------------------------------------------|
| Browse (HTML)     | `GET /` · `GET /browse/<path>`                |
| Browse (JSON)     | `GET /browse/<path>?format=json`              |
| Download          | `GET /get/<path>`                             |
| View inline       | `GET /get/<path>?inline=1`                    |
| Upload (form)     | `POST /upload[/<dir>]` field `file`           |
| Upload (raw)      | `PUT /put/<path>` body = file bytes           |
| Create directory  | `POST /mkdir/<path>` (`mkdir -p`)             |
| Delete file/dir   | `DELETE /delete/<path>` (dirs: **recursive**) |
| Delete (bulk)     | `POST /delete` repeated `sel=<path>` fields    |
| Docs (plain text) | `GET /help`                                   |

```bash
curl 'http://host:8000/browse/?format=json'      # list root as JSON
curl -O 'http://host:8000/get/notes/todo.txt'    # download
curl -F file=@report.pdf 'http://host:8000/upload/docs'   # multipart
curl -T report.pdf 'http://host:8000/put/docs/report.pdf' # raw body
curl -X POST 'http://host:8000/mkdir/docs/2026'           # create dir
curl -X DELETE 'http://host:8000/delete/docs/old'         # delete (recursive)
```

`<path>` is relative to the share root; `../` and absolute paths are
rejected (`werkzeug.safe_join`). `PUT` creates parent directories and
overwrites; multipart filenames and new folder names are sanitized.
Deleting a directory removes it **recursively**; the share root itself
cannot be deleted. Agents use `DELETE /delete/<path>` for a single item
(reply `{"deleted": "<path>"}`); the browser checkbox UI POSTs the
checked rows as repeated `sel=<path>` fields to `/delete`
(reply `{"deleted": [...]}`), since browsers can't send `DELETE` from a
form.

## Tests

```bash
pip install -e ".[dev]"   # Flask + pytest
pytest                    # 34 tests: routes, auth, traversal/symlink,
                          # content negotiation, single-source docs, UI
```

## Layout

```
minishare/__init__.py   # create_app() + make_blueprint() — public API
minishare/share.py      # make_blueprint() + all routes (self-contained)
minishare/cli.py        # argparse runner (python -m minishare)
minishare/__main__.py   # enables `python -m minishare`
run.py                  # dev shim -> minishare.cli:main
tests/ + conftest.py    # pytest suite
pyproject.toml          # installable: pip install -e ".[dev]"
AGENTS.md               # project knowledge + dev criteria (read first)
```

> Development server only — not hardened for untrusted public exposure.
> There is no per-user permission model: anyone who can reach the server
> (everyone, if no `auth` dict is configured) can also **delete** files
> and directories. Configure `auth` and/or put it behind a reverse proxy
> / network controls for anything beyond a trusted environment.
