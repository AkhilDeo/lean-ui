from __future__ import annotations

import sys
from pathlib import Path


def _prefer_vendored_client() -> None:
    client_dir = Path(__file__).resolve().parent / "client"
    client_path = str(client_dir)
    if client_path in sys.path:
        sys.path.remove(client_path)
    sys.path.insert(0, client_path)


_prefer_vendored_client()
