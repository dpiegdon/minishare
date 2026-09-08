# AGENTS.md

Project knowledge and working agreement for anyone (agent or human)
changing this repo. Read this before editing; keep it true after editing.

## What this is

`minishare` — a deliberately small Flask **blueprint** file-sharing
server: browse / download / upload / mkdir / rename / delete. Two ways
to run it: standalone (`python -m minishare`) or embedded as a git
submodule (`app.register_blueprint(make_blueprint(...))`). See
`README.md`.

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
  **`API.md` is a factual endpoint reference plus a minimal curl
  how-to, and nothing else** — no usage advice, editorialising, or
  agent-directed imperatives ("treat it as…", "do not retry…", "you
  must…"). The copy-paste brief in `_agent_brief()` truthfully scopes
  `/help` as exactly that; keep the artifact matching the claim. The
  *why* (rationale for the `-K` recipe, the destructive-flag design,
  etc.) lives in the README / this file — maintainer-facing, not in the
  agent-fetched doc. `test_help_is_reference_only_no_usage_directives`
  pins it.
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
  the 401 stays generic (no software name in body or realm). `auth`
  values are **Werkzeug password hashes only** — plaintext is rejected
  in `make_blueprint` with a `ValueError` (fail fast, not a silent
  lockout); verified per request via `check_password_hash`
  (`_password_ok`). `python -m minishare.hashpw` (`minishare/hashpw.py`)
  generates one via `getpass` (no echo / argv / shell history) — keep
  these properties. The auth
  docs in `API.md` must never show credentials on the curl command line
  (`-u USER:PASS` / building the file with `echo` leaks them to `ps` and
  shell history) — they teach the single `-K ms.curl` config-file
  recipe instead; keep it that way. Auth has a
  per-IP brute-force backoff (`auth_rate_limit`, default 10 s, per-blueprint
  `ms_state` as `{ip: (count, ts)}`): the first `_AUTH_FAIL_GRACE` (4)
  *credentialed* wrong attempts are a grace zone (browsers retry — a
  1-strike limiter throttles honest logins), then the IP is blocked hard
  for `auth_rate_limit` s with a `429` advising `+5` s; a no-credential
  request (the browser challenge) must never be counted or throttled or
  login breaks; a correct login clears the IP; entries idle past the
  advised wait are purged every pass so the map stays small.
  Destructive ops fail closed via `_flag()`: a non-empty-directory
  `DELETE` needs `?recursive=1` and clobbering a file (PUT, multipart
  upload *or* `rename`) needs `?overwrite=1`, else `409` and nothing is
  written/removed (bulk delete stays all-or-nothing) — a deliberate
  prompt-injection / fat-finger speed bump. `rename` additionally
  refuses to replace a **directory** at all (flag or not — that would
  discard a whole tree in one request) and, unlike `PUT`, does *not*
  `mkdir -p` its destination: a missing parent is a `404` pointing at
  `/mkdir`, because renaming into a non-existent folder is far more
  often a typo than an intent. The browser forms supply
  these flags themselves, so it's agent-facing only. Don't regress
  those.
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
  `max_mb`, `max_total_mb`, `auth_rate_limit`) are `make_blueprint()`
  parameters. Never set
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
  valid, drag-and-drop, confirm dialogs, closing a row menu). The app
  must remain usable with JS off; never hard-disable a control in
  markup. The per-row `⋯` menu is therefore a plain `<details>` holding
  real `<form>`s, which is *only* legal because the bulk-delete form no
  longer wraps the table: nested forms are dropped by the HTML parser,
  so `#delform` stands alone and the row checkboxes join it with
  `form="delform"` (`test_bulk_form_is_standalone_so_row_forms_are_valid_html`
  pins this). Consequence for the JS: the checkboxes are not descendants
  of that form, so query the *document*, never `form.querySelectorAll`.
  A filename never goes into JS source — the row-delete confirm reads it
  from `data-name`, so a quote in a name can't break the handler.
- **Mobile is a first-class view.** The page carries a
  `width=device-width` viewport meta — without it phones lay out at
  ~980px and zoom out, which was the single biggest thing wrong with
  the old page — plus one `@media (max-width: 40rem)` block that
  reflows the listing into one block per entry. The trick that makes it
  deterministic: the name cell is `calc(100% - 5.5rem)` and the actions
  cell `5.5rem`, so line one is exactly full and the size/modified
  cells *always* wrap underneath; don't drift those two widths apart.
  Text inputs are 16px there because anything smaller makes iOS Safari
  zoom the page on focus and leave it scrolled sideways. Tap targets
  are ~45px. It is pure CSS — no JS, no second template, identical
  markup at both widths, so there is nothing extra to keep in sync.
- **Theme follows the device.** Dark mode is one
  `@media (prefers-color-scheme: dark)` block and nothing else: no
  toggle, no cookie, no `localStorage`, no server-side state — so there
  is no theme to persist, leak, or get out of sync, and a phone that
  goes dark at night takes the page with it. Every colour in the
  stylesheet comes from a custom property on `:root`, and the dark
  block redefines *all* of them. A colour literal anywhere else fails
  `test_no_colour_literal_outside_the_palette`, because that is exactly
  how a dark theme rots — a new `color:#666` is invisible on a dark
  ground and nothing else in the suite would notice.
  `color-scheme:light dark` is load-bearing: without it the browser
  keeps painting checkboxes, text inputs and scrollbars light. Dark
  colours are re-picked, not inverted (`#06c` and `#c00` both go
  unreadable on dark). This is human-only chrome, like the drag-drop
  hint and `_agent_brief` — `API.md` gets nothing, deliberately; don't
  "fix" that omission.
- **Stable contracts (+ destructive-op guard).** `DELETE
  /delete/<path>` → `{"deleted":"<path>"}` (string); bulk `POST /delete`
  with `sel=` → `{"deleted":[...]}` (list); `POST /rename/<path>` with
  `to=` (a path relative to the share root) →
  `{"renamed":"<from>","to":"<to>"}` — one field does both rename and
  move, so the browser's text box and the agent's parameter are
  literally the same thing. Agents get JSON, browsers redirect.
  The destructive-op flags are part of these contracts too — the rules
  and the reasoning are under **Security** above. They were a
  *deliberate* break of the older "delete is always recursive / PUT
  always overwrites" behaviour; keep it, the safety beats the
  smoothness. Don't break any of this.

## Dev criteria (definition of done)

1. **Write/extend tests.** `tests/test_minishare.py` (pytest). Every
   behaviour change or fix gets a test that would fail without it. Run
   `make test` — it must be green before you call it done.
2. **Refactor as you go.** Leave the code cleaner than you found it:
   dedupe, extract helpers (`_respond`, `_resolve`), kill dead code. Do
   a quick self code-review every round.
3. **Keep docs true.** Update `minishare/API.md` (→ `/help` + in-page),
   `README.md` and this file whenever behaviour, routes, flags or
   signatures change — docstrings and examples included. An example that
   no longer runs is a bug; one class of those is pinned by
   `test_docs_never_show_a_plaintext_password_as_an_auth_value`.
   Re-audit when asked.
4. **Verify for real.** Exercise the change against a live server or the
   test client; don't claim behaviour you didn't observe. Report
   failures honestly.
5. **Commit each completed, verified change** to `main` with a
   descriptive message ending in the `Co-Authored-By` trailer. Group
   related edits into one commit. Do **not** `git push` or open PRs
   unless asked.

## Run / test

`make test` (the definition of done), `make run`, `make clean` — read
the `Makefile`, it is nine lines and it owns the venv. Don't restate its
commands in the docs; point at it.

## Gotchas / lessons learned

- **No autoreload:** after editing, restart the server to see changes
  (unless started with `--debug`).
- **Never `rm -rf data/` while a server points at it.** That is the
  live store; deleting it 404s the running instance (happened twice).
  Tests must use `tmp_path` / temporary dirs, never the real `data/`.
- `data/` is gitignored and is user content — don't wipe or "tidy" it.
- **Never `pkill -f` a pattern that occurs in your own command line.**
  The shell running `pkill -f 'python -m minishare'` has that string in
  *its* argv, so pkill kills the shell — the harness just reports exit
  144, which reads like an unrelated crash. Use the bracket trick, which
  matches the server but not the command that types it:
  `pgrep -f 'python -m minisha[r]e' | xargs -r kill` — and run it as its
  *own* command, because the trick fails the moment the same command
  line also starts the server (that literal `minishare` is then there
  for the regex to find).
- Files dropped for upload land on the file picker; the drag hint must
  sit next to the picker, not the Upload button.
