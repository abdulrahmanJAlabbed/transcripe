#!/usr/bin/env python3
"""Run the Transcripe studio from a git checkout.

The studio itself lives in `transcripe.studio` so that installs get it too;
this keeps `python server.py` working from the repo root.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from transcripe.studio import app, serve  # noqa: E402,F401  (app: for `uvicorn server:app`)

if __name__ == "__main__":
    serve()
