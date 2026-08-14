"""
Path resolution for SES_Workspace scripts.
Import this module to get canonical paths without hardcoding.

Usage:
    import sys
    sys.path.insert(0, r'C:\\Users\\GilesHugo\\SES_Workspace\\lib')
    from path_config import PATHS, p

    DB_PATH      = PATHS['db']['ops']
    REQUESTS_DIR = PATHS['queues']['requests']
    SKILLS_ROOT  = PATHS['skills']['root']

    # Or use p() shorthand for dotted key access:
    DB_PATH = p('db.ops')
"""
from __future__ import annotations
import os
from pathlib import Path
from functools import reduce

_HERE = Path(__file__).resolve().parent          # SES_Workspace/lib/
_WORKSPACE = _HERE.parent                        # SES_Workspace/
_CONFIG_PATH = _WORKSPACE / "_copilot" / "PATH_CONFIG.yaml"

# ---------------------------------------------------------------------------
# Loader — yaml preferred, falls back to a minimal built-in parser so this
# module works even if PyYAML isn't installed in the active environment.
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ImportError:
        pass
    # Minimal fallback: indentation-aware parser for the subset of YAML that
    # PATH_CONFIG.yaml uses — nested maps of scalar strings, 2-space indents,
    # full-line and trailing comments. Keeps this module dependency-free so it
    # works under any venv (the cron venv has no PyYAML).
    root: dict = {}
    stack: list[tuple[int, dict]] = [(0, root)]  # (indent, container)
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            while len(stack) > 1 and indent < stack[-1][0]:
                stack.pop()
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            # strip trailing comment (only when preceded by whitespace, so
            # values containing '#' in paths are not truncated)
            if ' #' in value:
                value = value.split(' #', 1)[0].rstrip()
            value = value.strip('"').strip("'")
            parent = stack[-1][1]
            if value == "":
                child: dict = {}
                parent[key] = child
                stack.append((indent + 2, child))
            else:
                parent[key] = value
    return root


PATHS: dict = _load_yaml(_CONFIG_PATH)


def p(dotted_key: str) -> str:
    """Return a path string via dotted key, e.g. p('db.ops') → ops.db path."""
    try:
        return str(reduce(lambda d, k: d[k], dotted_key.split("."), PATHS))
    except (KeyError, TypeError) as exc:
        raise KeyError(f"PATH_CONFIG key not found: {dotted_key!r}") from exc


def workspace_root() -> Path:
    return Path(PATHS["workspace_root"])
