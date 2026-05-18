"""minishare — a minimal Flask file-sharing server.

Two ways to use it:

* **Standalone:** ``create_app()`` builds a fully configured Flask app
  (this is what ``python -m minishare`` / ``run.py`` use).
* **As a git submodule in a larger project:** build a blueprint with
  ``make_blueprint(...)`` and register it on **your** app yourself —
  optionally several independent instances::

      from minishare import make_blueprint

      app.register_blueprint(
          make_blueprint(name="files", storage_dir="/srv/a",
                         auth={"alice": "s3cret"}),
          url_prefix="/files",
      )
      app.register_blueprint(
          make_blueprint(name="pub", storage_dir="/srv/b"),
          url_prefix="/pub",
      )

Configuration is purely by blueprint parameter — nothing is written to
``app.config`` — so instances never collide and the host app's config is
untouched. The ``MINISHARE_*`` env vars below are a convenience for the
standalone runner only.
"""
from __future__ import annotations

import os

from flask import Flask

from .share import make_blueprint

__all__ = ["create_app", "make_blueprint"]


def _parse_auth_env(raw: str) -> dict[str, str]:
    """Parse ``MINISHARE_AUTH="user:pass,user2:pass2"`` into a dict."""
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        user, _, pw = pair.partition(":")
        out[user] = pw
    return out


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    return int(raw) if raw else None


def create_app(
    storage_dir: str | None = None,
    auth: dict[str, str] | None = None,
    url_prefix: str | None = None,
    title: str | None = None,
    max_mb: int | None = None,
    max_total_mb: int | None = None,
) -> Flask:
    """Build a standalone Flask app serving a single minishare instance.

    For the standalone runner only: parameters fall back to
    ``MINISHARE_DIR`` / ``MINISHARE_AUTH`` / ``MINISHARE_TITLE`` /
    ``MINISHARE_MAX_MB`` / ``MINISHARE_MAX_TOTAL_MB``. To embed in a host
    app, use :func:`make_blueprint` and register it yourself.
    """
    storage_dir = (
        storage_dir
        or os.environ.get("MINISHARE_DIR")
        or os.path.join(os.getcwd(), "data")
    )
    if auth is None:
        env_auth = os.environ.get("MINISHARE_AUTH")
        auth = _parse_auth_env(env_auth) if env_auth else None
    if title is None:
        title = os.environ.get("MINISHARE_TITLE") or "minishare"
    if max_mb is None:
        max_mb = _env_int("MINISHARE_MAX_MB")
    if max_total_mb is None:
        max_total_mb = _env_int("MINISHARE_MAX_TOTAL_MB")

    app = Flask(__name__)
    app.register_blueprint(
        make_blueprint(
            storage_dir=storage_dir,
            auth=auth,
            title=title,
            max_mb=max_mb,
            max_total_mb=max_total_mb,
        ),
        url_prefix=url_prefix,
    )
    return app
