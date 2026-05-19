#!/usr/bin/env python3
"""Generate a password hash for minishare's ``auth`` config.

    python -m minishare.hashpw

minishare stores only hashes, never plaintext. This prompts for the
password (no echo; it never reaches argv or your shell history) and
prints a Werkzeug hash to use as the ``auth={user: hash}`` value, or
after the ``:`` in ``-a user:HASH`` / ``MINISHARE_AUTH``.
"""
from __future__ import annotations

import getpass
import sys

from werkzeug.security import generate_password_hash


def main() -> int:
    try:
        pw = getpass.getpass("Password: ")
        if not pw:
            print("empty password; aborted", file=sys.stderr)
            return 2
        if getpass.getpass("Confirm:  ") != pw:
            print("passwords did not match", file=sys.stderr)
            return 1
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return 130
    print(generate_password_hash(pw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
