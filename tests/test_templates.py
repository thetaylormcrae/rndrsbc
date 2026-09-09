"""Template integrity guards for the multi-page admin portal.

Regression coverage for the python-preamble leak (v0.21.0): template files
accidentally regenerated with app.py's module header prepended, which browsers
rendered as visible text above the page. Every served page must:

  1. start with <!DOCTYPE html> (no python/module preamble),
  2. contain no python artifacts (imports, docstrings, session globals),
  3. carry the shared top-nav shell (logo, all six page links),
  4. have balanced <script>/<style> tags and a closing </html>.
"""
import os
import re
import sys

import pytest

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)

TEMPLATES_DIR = os.path.join(root, "server", "templates")

PAGES = (
    "dashboard.html",
    "playlists.html",
    "widgets.html",
    "settings.html",
    "photos.html",
    "system.html",
)

ROUTES = {"/": "dashboard.html", "/playlists": "playlists.html", "/widgets": "widgets.html",
          "/settings": "settings.html", "/photos": "photos.html", "/system": "system.html"}

NAV_LINKS = ("/", "/playlists", "/widgets", "/settings", "/photos", "/system")

# python artifacts that leaked in v0.21.0 — must never appear in served html
PYTHON_MARKERS = (
    "import os\nimport sys",
    "from http.server import",
    "ACTIVE_SESSIONS",
    "SESSION_TTL_SECS",
    '"""',
)


def _load(name):
    with open(os.path.join(TEMPLATES_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("name", PAGES)
def test_starts_with_doctype(name):
    content = _load(name)
    assert content.startswith("<!DOCTYPE html>"), f"{name} missing <!DOCTYPE html> prologue"


@pytest.mark.parametrize("name", PAGES)
def test_no_python_artifacts(name):
    content = _load(name)
    for marker in PYTHON_MARKERS:
        assert marker not in content, f"{name} contains python artifact: {marker!r}"


@pytest.mark.parametrize("name", PAGES)
def test_shared_nav_shell(name):
    content = _load(name)
    for href in NAV_LINKS:
        assert f'href="{href}"' in content, f"{name} missing nav link {href}"
    assert "btn-login" in content and "btn-logout" in content, f"{name} missing login/logout buttons"


@pytest.mark.parametrize("name", PAGES)
def test_structure_wellformed(name):
    content = _load(name)
    assert content.rstrip().endswith("</html>"), f"{name} truncated (no closing </html>)"
    assert content.count("<script") == content.count("</script>"), f"{name} unbalanced <script> tags"
    assert content.count("<style") == content.count("</style>"), f"{name} unbalanced <style> tags"


def test_router_covers_all_templates():
    """Every template file must be routable, and every route must map to a real file."""
    on_disk = {f for f in os.listdir(TEMPLATES_DIR) if f.endswith(".html") and f != "fallback.html"}
    set(ROUTES.values()) == on_disk, "route map and templates dir out of sync"


def test_fallback_html_is_valid():
    """The in-module DASHBOARD_HTML fallback must also start with DOCTYPE (it is served on OSError)."""
    # Parse the literal from source (importing app pulls device-only deps like werkzeug)
    src = open(os.path.join(root, "server", "app.py"), encoding="utf-8").read()
    match = re.search(r'DASHBOARD_HTML\s*=\s*"""(.*?)"""', src, re.S)
    assert match, "DASHBOARD_HTML literal not found in server/app.py"
    assert match.group(1).lstrip().startswith("<!DOCTYPE html>"), \
        "DASHBOARD_HTML fallback missing <!DOCTYPE html> prologue"


@pytest.mark.parametrize("name", PAGES)
def test_single_nav_bar(name):
    """Regression: the builder once emitted two stacked navs (source's
    section navigator + injected page nav) - exactly one per page now."""
    content = _load(name)
    assert content.count('id="section-tabs"') == 1, f"{name} must have exactly one nav bar"


@pytest.mark.parametrize("name", PAGES)
def test_init_wrapped_in_iife(name):
    """Regression: init snippets used top-level await in a classic script,
    which is a SyntaxError in browsers and silently killed ALL page init
    (auth toggle, lock overlay, panels). Must be wrapped in an async IIFE."""
    content = _load(name)
    assert "await checkAuthStatus" in content, f"{name} missing auth init"
    # The bug signature: the init statement appeared as its own line right
    # after <script> with no IIFE wrapper. Require the wrapper.
    assert re.search(r"<script>\s*\(async \(\) => \{", content), f"{name} init not wrapped in async IIFE"


@pytest.mark.parametrize("name", PAGES)
def test_mobile_nav_present(name):
    """Mobile nav: accordion panel + hamburger toggle on every page."""
    content = _load(name)
    assert 'id="mobile-nav"' in content, f"{name} missing mobile nav accordion"
    assert 'id="nav-toggle"' in content, f"{name} missing hamburger toggle"
    assert "md:hidden" in content, f"{name} mobile nav not hidden on desktop"
