# AGENTS.md

Project knowledge and working agreement for anyone (agent or human)
changing this repo. Read this before editing; keep it true after editing.

## What this is

`minishare` — a deliberately small Flask **blueprint** file-sharing
server: browse / download / upload / mkdir / delete. Two ways to run it:
standalone (`python -m minishare`) or embedded as a git submodule
(`app.register_blueprint(make_blueprint(...))`). See `README.md`.

## The one rule everything else serves: dual audience

Every page and endpoint must work well for **both agents and humans at
the same time**. Concretely:

- Humans get a clean, unobtrusive HTML UI. Machine docs are folded away
  (`<details>`), styling stays quiet, controls are obvious.
- Agents get self-service affordances: `?format=json` on any listing,
  `GET /help` (plain text), and the full API embedded at the **top** of
  every page's raw HTML. Mutating endpoints answer agents with JSON and
  browsers with a redirect (`_respond`).
- When you add a feature, add **both** the human control and the
  documented machine path, and update the single API doc.

## Design principles

- **Keep it minimal & simple.** Stdlib + Flask only. One blueprint
  (`minishare/share.py`), one inline HTML template, tiny inline JS. No
  new dependencies, no build step, no client framework. Justify any new
  file.
- **Single source of truth for docs.** `minishare/API.md` is the *only*
  API text. It is linked from the README for humans and, via
  `_api_doc()` / `_load_api_template()`, served verbatim at `/help` and
  embedded in the in-page `<details>` for agents. Edit `API.md`, never
  fork it; don't re-add an endpoint table to the README. Its Markdown
  code fences (```` ``` ````) are stripped before serving (they exist
  only for GitHub rendering). It must stay **pure ASCII** and avoid the
  HTML-significant characters `< > &` — it is rendered with `|safe`, so
  those are *not* escaped and would corrupt the page; that's why
  placeholders are `$path` / `$dir`, not `<path>`. Quotes (`'` `"`) are
  fine and used deliberately in the curl examples. The only substitution
  is `$BASE` → `_doc_base()` (Host-derived), which is sanitised to
  URL-safe chars so `|safe` stays injection-proof. `API.md` ships with
  the package (`[tool.setuptools.package-data]`) so every install mode
  has it next to `share.py`.
- **Security is not optional.** All filesystem access goes through
  `_resolve()` (`werkzeug.safe_join` + realpath containment against
  symlink escape). Don't bypass it. Any path-handling change needs a
  traversal/symlink test. Other invariants to preserve (each has a
  test): `/get` never serves HTML/SVG `inline` (`_inline_safe`
  allowlist) and carries `Content-Security-Policy: sandbox` + `nosniff`;
  every response gets `X-Frame-Options: DENY` / `nosniff` /
  `Referrer-Policy: same-origin` via `_security_headers` (must NOT be
  `no-referrer` — that makes browsers send `Origin: null` on same-site
  POSTs and `_csrf_guard` rejects it, breaking uploads); mutating
  requests pass `_csrf_guard` (host of `Origin`/`Referer` must equal
  ours; a literal `Origin: null` is treated as cross-site and blocked;
  curl/agents send neither and are allowed — the dual-audience
  contract);
  the 401 stays generic (no software name in body or realm). Auth has a
  per-IP brute-force backoff (`auth_rate_limit`, default 10 s, per-blueprint
  `ms_state` as `{ip: (count, ts)}`): the first `_AUTH_FAIL_GRACE` (4)
  *credentialed* wrong attempts are a grace zone (browsers retry — a
  1-strike limiter throttles honest logins), then the IP is blocked hard
  for `auth_rate_limit` s with a `429` advising `+5` s; a no-credential
  request (the browser challenge) must never be counted or throttled or
  login breaks; a correct login clears the IP; entries idle past the
  advised wait are purged every pass so the map stays small. Don't
  regress those.
- **Blueprint factory; integrator registers it.** `make_blueprint(...)`
  returns a fresh `Blueprint` with its config stashed on the object
  (`bp.ms_config`, read via `_cfg()`); the integrator calls
  `app.register_blueprint(...)` themselves. No `init_app`, no module
  singleton. Multiple independent instances on one app are supported and
  tested — give each a unique `name`. Every internal/template `url_for`
  is **blueprint-relative** (`url_for(".browse")`, never
  `"share.browse"`) so any name/prefix works; keep it that way.
- **Submodule-safe / config by parameter.** Nothing is written to
  `app.config`; all settings (`storage_dir`, `name`, `auth`, `title`,
  `max_mb`, `max_total_mb`) are `make_blueprint()` parameters. Never set
  Flask globals (e.g. `MAX_CONTENT_LENGTH`). The `MINISHARE_*` env vars
  and CLI flags are conveniences for `create_app()` / the standalone
  runner only, never required to embed.
- **Size limits enforced on real bytes, not Content-Length.** `max_mb`
  (single upload) and `max_total_mb` (whole store; `None` == unlimited):
  `_request_ceiling()` + `_early_reject()` give a fast 413 for honest
  clients; `put` then `_stream_to_file()`s the body with a hard ceiling
  (chunked → bounded memory even when uncapped, atomic rename); `upload`
  re-checks actual bytes and rolls the files back. Don't reintroduce
  `request.get_data()`/Content-Length trust — the guarantee must hold
  with no proxy. Downloads, deletes and mkdir always work; pages show a
  small `storage:` indicator.
- **Progressive enhancement.** JS only *enhances* (disable buttons until
  valid, drag-and-drop). The app must remain usable with JS off; never
  hard-disable a control in markup.
- **Stable contracts.** `DELETE /delete/<path>` → `{"deleted":"<path>"}`
  (string); bulk `POST /delete` with `sel=` → `{"deleted":[...]}`
  (list). Agents get JSON, browsers redirect. Don't break these.

## Dev criteria (definition of done)

1. **Write/extend tests.** `tests/test_minishare.py` (pytest). Every
   behaviour change or fix gets a test that would fail without it. Run
   `pytest` — it must be green before you call it done.
2. **Refactor as you go.** Leave the code cleaner than you found it:
   dedupe, extract helpers (`_respond`, `_resolve`), kill dead code. Do
   a quick self code-review every round.
3. **Keep docs true.** Update `minishare/API.md` (→ `/help` + in-page)
   and `README.md` whenever behaviour, routes, flags, or signatures change.
   Re-audit them when asked.
4. **Verify for real.** Exercise the change against a live server or the
   test client; don't claim behaviour you didn't observe. Report
   failures honestly.
5. **Commit each completed, verified change** to `main` with a
   descriptive message ending in the `Co-Authored-By` trailer. Group
   related edits into one commit. Do **not** `git push` or open PRs
   unless asked.

## Run / test

```bash
pip install -e ".[dev]"
python -m minishare -p 8000      # dev server (no autoreload w/o --debug)
pytest                           # full suite
```

## Gotchas / lessons learned

- **No autoreload:** after editing, restart the server to see changes
  (unless started with `--debug`).
- **Never `rm -rf data/` while a server points at it.** That is the
  live store; deleting it 404s the running instance (happened twice).
  Tests must use `tmp_path` / temporary dirs, never the real `data/`.
- `data/` is gitignored and is user content — don't wipe or "tidy" it.
- The CLI process is matched by `pkill -f 'python -m minishare -p 8000'`
  for restarts; this is expected to exit non-zero in the harness.
- Files dropped for upload land on the file picker; the drag hint must
  sit next to the picker, not the Upload button.
