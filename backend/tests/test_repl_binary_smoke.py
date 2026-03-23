from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPL_DIR = BACKEND_DIR / "repl"
MATHLIB_DIR = BACKEND_DIR / "mathlib4"
REPL_BIN = REPL_DIR / ".lake/build/bin/repl"


def _mathlib_repl_env() -> dict[str, str]:
    lean_path = subprocess.run(
        ["lake", "env", "printenv", "LEAN_PATH"],
        cwd=MATHLIB_DIR,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert lean_path
    env = os.environ.copy()
    env["LEAN_PATH"] = lean_path
    return env


def _run_repl(
    *,
    cwd: Path,
    command: list[str],
    payload: dict[str, str],
    timeout: int,
    env: dict[str, str] | None = None,
) -> dict:
    proc = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        input=(json.dumps(payload) + "\n\n").encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=True,
    )
    raw = proc.stdout.decode("utf-8").strip()
    assert raw, proc.stderr.decode("utf-8", "replace")
    return json.loads(raw)


def test_compiled_repl_binary_responds_in_repl_workspace() -> None:
    response = _run_repl(
        cwd=REPL_DIR,
        command=[str(REPL_BIN)],
        payload={"cmd": "#check Nat"},
        timeout=10,
    )

    assert response["env"] == 0
    assert response["messages"][0]["data"] == "Nat : Type"


def test_compiled_repl_binary_responds_from_mathlib_workspace() -> None:
    response = _run_repl(
        cwd=MATHLIB_DIR,
        command=[str(REPL_BIN)],
        payload={"cmd": "import Mathlib"},
        timeout=30,
        env=_mathlib_repl_env(),
    )

    assert response["env"] == 0
