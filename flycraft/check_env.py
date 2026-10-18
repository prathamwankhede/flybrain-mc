"""Startup dependency check (spec F02). Run: python -m flycraft.check_env"""
from __future__ import annotations

import importlib
import importlib.util
import shutil
import subprocess
import sys

MIN_PYTHON = (3, 11)  # tomllib
MIN_NODE_MAJOR = 14


def check_python() -> str | None:
    if sys.version_info < MIN_PYTHON:
        return f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, found {sys.version.split()[0]}"
    return None


def check_flybrain() -> str | None:
    try:
        importlib.import_module("flybrain")
    except Exception as e:
        return f"cannot import flybrain ({e}); install with: pip install flybrain"
    return None


def check_numpy() -> str | None:
    if importlib.util.find_spec("numpy") is None:
        return "numpy not installed; install with: pip install numpy"
    return None


def check_node() -> str | None:
    node = shutil.which("node")
    if node is None:
        return f"Node.js not found on PATH; install Node.js {MIN_NODE_MAJOR}+"
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        major = int(out.lstrip("v").split(".")[0])
    except Exception as e:
        return f"could not read Node.js version from {node} ({e})"
    if major < MIN_NODE_MAJOR:
        return f"Node.js {MIN_NODE_MAJOR}+ required, found {out}"
    return None


def check_javascript() -> str | None:
    # find_spec only: importing `javascript` starts the Node bridge as a side effect.
    if importlib.util.find_spec("javascript") is None:
        return "javascript (JSPyBridge) not installed; install with: pip install javascript"
    return None


CHECKS = [
    ("Python version", check_python),
    ("flybrain import", check_flybrain),
    ("numpy", check_numpy),
    ("Node.js on PATH", check_node),
    ("javascript package", check_javascript),
]


def run_checks() -> list[tuple[str, str | None]]:
    return [(name, fn()) for name, fn in CHECKS]


def main() -> int:
    results = run_checks()
    for name, problem in results:
        print(f"  [{'OK' if problem is None else 'MISSING'}] {name}" + (f": {problem}" if problem else ""))
    missing = [name for name, problem in results if problem]
    if missing:
        print(f"\n{len(missing)} item(s) missing: {', '.join(missing)}")
        return 1
    print("\nAll OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
