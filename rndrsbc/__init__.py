"""rndrSBC Python package.

This package exposes the console entry point ``rndrsbc`` and the frame's
self-diagnostic subcommands (``rndrsbc doctor``). Widget/display/core modules
live as top-level sibling packages (``core``, ``displays``, ...).
"""

import importlib.metadata as _metadata

try:
    # Authoritative version is the installed distribution metadata (kept in
    # lockstep with the release by python-semantic-release). Never hardcode it
    # here -- that drifted to "0.1.0" while releases moved to 0.6+/0.7.
    __version__ = _metadata.version("rndrsbc")
except _metadata.PackageNotFoundError:  # pragma: no cover - source checkout / editable install
    __version__ = "0.0.0.dev0"
