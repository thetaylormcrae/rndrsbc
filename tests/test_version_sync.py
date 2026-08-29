"""The public ``rndrsbc.__version__`` must be truthful.

It was hardcoded to ``0.1.0`` at first boot and drifted out of sync with the
semantic-release-managed ``core/__init__.py``. It is now read from package
metadata (with a dev fallback for source checkouts) so it is always truthful
wherever the package is actually run from.
"""
import importlib.metadata
import re

import rndrsbc


def _dist_version_or_none():
    try:
        return importlib.metadata.version("rndrsbc")
    except importlib.metadata.PackageNotFoundError:
        return None


def test_version_matches_installed_distribution_when_installed():
    dist = _dist_version_or_none()
    if dist is not None:            # installed dist: must be in lockstep
        assert rndrsbc.__version__ == dist
    else:                            # source checkout: stable dev marker
        assert rndrsbc.__version__ == "0.0.0.dev0"


def test_version_is_pep440_sane():
    assert re.match(r"^\d+\.\d+\.\d+", rndrsbc.__version__)
    # Never the stale first-boot hardcode:
    assert not rndrsbc.__version__.startswith("0.1.")


def test_no_stale_hardcoded_version():
    # Guard: reject any literal assignment other than the dev fallback.
    # The real value must come from metadata, not a hand-edited literal.
    src = open("rndrsbc/__init__.py").read()
    assert "__version__ = _metadata.version(\"rndrsbc\")" in src
    for literal in re.findall(r'__version__\s*=\s*[\'"]([^\'"]+)[\'"]', src):
        assert literal == "0.0.0.dev0", f"stale/new literal version: {literal}"
