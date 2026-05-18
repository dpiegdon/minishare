"""minishare — a minimal Flask file-sharing server.

Two ways to use it:

* **Standalone:** ``create_app()`` builds a fully configured Flask app
  (this is what ``python -m minishare`` / ``run.py`` use).
* **As a git submodule in a larger project:** keep your own Flask app and
  call ``init_app(app, ...)`` to mount the ``share`` blueprint onto it,
  optionally under a URL prefix so it does not collide with your routes::

      from minishare import init_app
      init_app(app, storage_dir="shared", url_prefix="/files",
               auth={"alice": "s3cret"})

All config is namespaced under ``MINISHARE_*`` keys so it never clobbers
the host application's own configuration.
"""
from __future__ import annotations

import os

from flask import Flask

from .share import share_bp

__all__ = ["create_app", "init_app", "share_bp"]


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


def init_app(
    app: Flask,
    storage_dir: str | None = None,
    auth: dict[str, str] | None = None,
    url_prefix: str | None = None,
    max_mb: int | None = None,
) -> Flask:
    """Mount the file-sharing blueprint onto an existing Flask ``app``.

    Use this when embedding minishare as a submodule in a bigger project.

    :param storage_dir: directory that holds shared files. Defaults to the
        ``MINISHARE_DIR`` env var, or ``<cwd>/data``. Created if missing.
    :param auth: optional ``{username: password}`` dict. If non-empty,
        every minishare request needs HTTP Basic auth with one of these
        pairs. Falls back to the ``MINISHARE_AUTH`` env var
        (``"user:pass,user2:pass2"``); omitted entirely == open access.
    :param url_prefix: mount point, e.g. ``"/files"`` (default: app root).
    :param max_mb: optional per-upload size cap in MiB (falls back to the
        ``MINISHARE_MAX_MB`` env var). Note: this sets Flask's global
        ``MAX_CONTENT_LENGTH``, so only pass it if that is acceptable for
        the whole host app.
    """
    storage_dir = (
        storage_dir
        or os.environ.get("MINISHARE_DIR")
        or os.path.join(os.getcwd(), "data")
    )
    storage_dir = os.path.abspath(storage_dir)
    os.makedirs(storage_dir, exist_ok=True)
    app.config["MINISHARE_DIR"] = storage_dir

    if auth is None:
        env_auth = os.environ.get("MINISHARE_AUTH")
        auth = _parse_auth_env(env_auth) if env_auth else None
    app.config["MINISHARE_AUTH"] = auth or None  # empty dict -> open access

    if max_mb is None:
        env_mb = os.environ.get("MINISHARE_MAX_MB")
        max_mb = int(env_mb) if env_mb else None
    if max_mb:
        app.config["MAX_CONTENT_LENGTH"] = max_mb * 1024 * 1024

    app.register_blueprint(share_bp, url_prefix=url_prefix)
    return app


def create_app(
    storage_dir: str | None = None,
    auth: dict[str, str] | None = None,
    url_prefix: str | None = None,
) -> Flask:
    """Build a standalone Flask app serving only minishare.

    Thin wrapper around :func:`init_app` for the standalone/CLI case; see
    that function for the parameter semantics.
    """
    app = Flask(__name__)
    return init_app(
        app, storage_dir=storage_dir, auth=auth, url_prefix=url_prefix
    )
