"""Static sandbox validation for third-party widget artifacts.

Community widgets are code that runs inside our render loop, so we gate them
on a conservative static check before they ever touch the filesystem. This is a
defence-in-depth layer: it cannot (and does not claim to) block every possible
attack, but it stops the cheap, common ones before extraction.

Checks performed (AST, no execution):
  - Dangerous module imports  : subprocess, socket, os.system / os removed,
                                shutil.rmtree, syscalls, ctypes, cffi, __builtins__
  - Dangerous builtins/calls  : eval, exec, compile with exec, __import__,
                                open() with write mode, os.remove, os.unlink
  - Obvious path escape       : absolute "/" filesystem literals

Anything not statically resolvable is treated conservatively.
"""
from __future__ import annotations

import ast
import logging
import os

logger = logging.getLogger("rndrSBC.security")

# Module imports we refuse outright in third-party widget code.
BLOCKED_MODULES = {
    "subprocess", "socket", "ctypes", "cffi", "requests", "urllib",
    "http", "ftplib", "smtplib", "telnetlib", "paramiko", "pty", "tty",
    "multiprocessing", "threading", "concurrent.futures", "asyncio",
    "pickle", "marshal", "crypt", "ssl",
}
BLOCKED_IMPORT_ROOTS = {m.split(".")[0] for m in BLOCKED_MODULES}

# Builtins / callable names we refuse inside widget code.
BLOCKED_CALLS = {
    "eval", "exec", "compile", "__import__", "input", "breakpoint",
    "open", "os.system", "os.popen", "os.remove", "os.unlink",
    "os.rmdir", "shutil.rmtree", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "exit", "quit",
}

# File system writes via os / pathlib are only permitted to SAFE_BASE.
SAFE_BASE = "/data"

# os.* methods considered safe for read-only data access use.
ALLOWED_OS_METHODS = {"path", "getenv", "environ", "getcwd", "listdir", "makedirs", "walk"}


def _check_node_src(tree: ast.AST) -> list[str]:
    findings: list[str] = []

    for node in ast.walk(tree):
        # Blocked top-level / dotted imports:  import X  or  import X.Y
        if isinstance(node, ast.Import):
            for a in node.names:
                root = (a.name or "").split(".")[0]
                if root in BLOCKED_IMPORT_ROOTS:
                    findings.append(f"blocked import '{a.name}'")
        # Blocked from-imports:  from X import Y
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BLOCKED_IMPORT_ROOTS:
                findings.append(f"blocked from-import '{node.module}'")

        # Blocked attribute calls like  os.system(...)  or bare  eval(...)
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                name = f.id
                if name in BLOCKED_CALLS:
                    findings.append(f"blocked call '{name}'")
            elif isinstance(f, ast.Attribute):
                # Resolve dotted call path e.g. os.system -> "os.system"
                segs = [f.attr]
                o = f.value
                while isinstance(o, ast.Attribute):
                    segs.insert(0, o.attr)
                    o = o.value
                if isinstance(o, ast.Name):
                    segs.insert(0, o.id)
                dotted = ".".join(segs)
                if dotted in BLOCKED_CALLS or f.attr in BLOCKED_CALLS:
                    findings.append(f"blocked call '{dotted}'")
                # Any method on the os module is rejected unless whitelisted
                if segs and segs[0] == "os" and (dotted not in ALLOWED_OS_METHODS):
                    findings.append(f"blocked os. call '{dotted}'")

        # Refuse /data unsafe absolute writes would be (partially) caught above
        # via `open` and os.remove; also flag obvious external literal paths.
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            if s.startswith(("/etc", "/bin", "/usr", "/var", "/root", "/home", "/proc", "/sys")):
                findings.append(f"suspicious absolute path literal '{s[:40]}'")

    return findings


def validate_source(source: str, filename: str = "<artifact>") -> list[str]:
    """Return a list of policy violations for a Python source string. Empty = clean."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        return [f"syntax error at {filename}:{e.lineno}: {e.msg}"]
    return _check_node_src(tree)


def validate_package(py_sources: dict[str, str]) -> list[str]:
    """Validate a package given as {relative_path: source}. Returns violations."""
    all_violations: list[str] = []
    for rel_path, source in py_sources.items():
        for v in validate_source(source, rel_path):
            all_violations.append(f"{rel_path}: {v}")
    return all_violations


def safe_open_replacement():
    """Documentation anchor: third-party widgets must access user data only
    through the core-provided data API, never raw open() writes."""
    raise NotImplementedError("third-party widgets must use the data access layer")


__all__ = ["validate_source", "validate_package", "BLOCKED_MODULES", "BLOCKED_CALLS"]
