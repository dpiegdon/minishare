# AGENTS.md

Project knowledge and working agreement for anyone (agent or human)
changing this repo. Read this before editing; keep it true after editing.

## What this is

`minishare` — a deliberately small Flask **blueprint** file-sharing
server: browse / download / upload / mkdir / delete. Two ways to run it:
standalone (`python -m minishare`) or embedded as a git submodule
(`from minishare import init_app`). See `README.md` for usage.

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
- **Single source of truth for docs.** `_api_doc()` is the *only* API
  text. It is served verbatim at `/help` and embedded in the in-page
  `<details>`. Never fork it. It must stay **pure ASCII** and avoid the
  HTML-significant characters `< > &` — it is rendered with `|safe`, so
  those are *not* escaped and would corrupt the page; that's why
  placeholders are `$path` / `$dir`, not `<path>`. Quotes (`'` `"`) are
  fine and used deliberately in the curl examples — `|safe` no longer
  mangles them. The only dynamic value, `_doc_base()` (Host-derived), is
  sanitised to URL-safe chars so `|safe` stays injection-proof.
- **Security is not optional.** All filesystem access goes through
  `_resolve()` (`werkzeug.safe_join` + realpath containment against
  symlink escape). Don't bypass it. Any path-handling change needs a
  traversal/symlink test.
- **Submodule-safe.** All config lives under `MINISHARE_*` keys so it
  never clobbers a host app. Don't set Flask globals (e.g.
  `MAX_CONTENT_LENGTH`) unless explicitly requested.
- **Configuration is by blueprint parameter.** In the standard
  blueprint case, *all* configuration is passed explicitly to
  `init_app()` / `create_app()` (`storage_dir`, `auth`, `url_prefix`,
  `title`, `max_mb`). That is the canonical, supported surface. The
  `MINISHARE_*` env vars and CLI flags are conveniences for the
  standalone runner only and must never be *required* to embed the
  blueprint — a host app configures it purely through parameters.
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
3. **Keep docs true.** Update `_api_doc()` (→ `/help` + in-page) and
   `README.md` whenever behaviour, routes, flags, or signatures change.
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
