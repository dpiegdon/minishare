#!/usr/bin/env python3
"""Dev convenience shim — identical to ``python -m minishare``.

Kept so ``python run.py`` keeps working from the repo root. When this repo
is used as a git submodule, prefer ``python -m minishare`` or import
``minishare.init_app`` into your own app instead.
"""
from minishare.cli import main

if __name__ == "__main__":
    main()
