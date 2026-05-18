#!/usr/bin/env python3
"""Standalone runner for minishare.

    python -m minishare                       # serve ./data on :8000
    python -m minishare -d /srv/share -p 9000
    python -m minishare -a alice:s3cret -x /files
"""
from __future__ import annotations

import argparse

from . import create_app


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="minishare", description="Minimal Flask file-sharing server"
    )
    p.add_argument("-d", "--dir", help="directory to share (default: ./data)")
    p.add_argument("-H", "--host", default="0.0.0.0", help="bind host")
    p.add_argument("-p", "--port", type=int, default=8000, help="bind port")
    p.add_argument(
        "-x", "--prefix", help="mount under this URL prefix, e.g. /files"
    )
    p.add_argument(
        "-a",
        "--auth",
        action="append",
        metavar="USER:PASS",
        help="require HTTP Basic auth; repeat for multiple users "
        "(omit == open access)",
    )
    p.add_argument("--debug", action="store_true", help="enable Flask debug")
    args = p.parse_args(argv)

    auth = None
    if args.auth:
        auth = {}
        for pair in args.auth:
            user, _, pw = pair.partition(":")
            auth[user] = pw

    app = create_app(
        storage_dir=args.dir, auth=auth, url_prefix=args.prefix
    )
    where = (args.prefix or "").rstrip("/") + "/"
    print(f"minishare serving {app.config['MINISHARE_DIR']} on "
          f"http://{args.host}:{args.port}{where}  (GET {where}help for the API)")
    print("  auth: " + (", ".join(sorted(auth)) if auth else "OPEN (no auth)"))
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
