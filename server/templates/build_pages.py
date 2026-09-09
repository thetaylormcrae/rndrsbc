"""Builds the 6 distinct admin pages from segments of server/app.py.

Extraction is MARKER-BASED, not line-based: every segment is located by a
unique HTML comment in the source, so edits to app.py that shift line
numbers can never silently skew a slice (the bug that produced duplicate
navs). Every segment must contain balanced <div> tags - the builder fails
loudly rather than emitting a broken page.
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "app.py")
PARTS = os.path.join(HERE, ".build_parts.json")

# Marker comments (must be unique in app.py) that bracket each segment.
# A segment runs from its START marker line to the line before its END
# marker (or, when END is None, to the end of the enclosing top-level
# block determined by div balance).
SEGMENTS = {
    # head: from DOCTYPE (inside DASHBOARD_HTML literal) to end of </head>
    "HEAD": ("<!DOCTYPE html>", "</head>"),
    # header: <!-- Navigation Bar --> ... </header>
    "HEADER": ("<!-- Navigation Bar -->", "</header>"),
    "SEC_DASH": ("<!-- Top Row: Screen Mirror + Quick Stats -->", "</main>"),
    "SEC_PLAY": ("<!-- Active Playlist Widgets Editor -->", "</main>"),
    "SEC_DEV": ("<!-- Dev Studio: widget render preview (authenticated) -->", "</main>"),
    "SEC_FIND": ("<!-- Widget Finder: catalogue of all discovered widgets -->", "</main>"),
    "SEC_HW": ("<!-- Display Hardware & Quiet Hours Settings -->", None),
    "SEC_QH": ("<!-- Quiet Hours & System Settings -->", None),
    "SEC_SEC": ("<!-- Admin Security & Password Settings -->", None),
    "MODALS": ("<!-- First-Run Admin Setup Modal -->", "<!-- MODALS END -->"),
}

INIT = {
    "dashboard": "await checkAuthStatus(); await loadStatus(); if (isAuthenticated) { try { renderPlaylistTabs(); } catch (e) {} }",
    "playlists": "await checkAuthStatus(); const lock = document.getElementById('auth-lock'); if (isAuthenticated) { lock.classList.add('hidden'); await loadStatus(); try { renderPlaylistTabs(); renderPlaylist(); } catch (e) {} } else { lock.classList.remove('hidden'); } if (!setupRequired) buildTimezoneSelect();",
    "widgets": "await checkAuthStatus(); const lock = document.getElementById('auth-lock'); if (isAuthenticated) { lock.classList.add('hidden'); try { devStudioInit(); } catch (e) {} } else { lock.classList.remove('hidden'); }",
    "settings": "await checkAuthStatus(); const lock = document.getElementById('auth-lock'); if (isAuthenticated) { lock.classList.add('hidden'); await loadStatus(); try { updateHardwareSettings(); updateQuietHoursSettings(); updateDeviceSettings(); } catch (e) {} } else { lock.classList.remove('hidden'); } if (!setupRequired) buildTimezoneSelect();",
    "photos": "await checkAuthStatus(); const lock = document.getElementById('auth-lock'); if (isAuthenticated) { lock.classList.add('hidden'); try { loadPhotos(); } catch (e) {} } else { lock.classList.remove('hidden'); }",
    "system": "await checkAuthStatus(); const lock = document.getElementById('auth-lock'); if (isAuthenticated) { lock.classList.add('hidden'); try { loadTelemetry(); } catch (e) {} try { checkUpdate(); } catch (e) {} } else { lock.classList.remove('hidden'); }",
}


def _wrap_init(js: str) -> str:
    """Wrap an init snippet in an async IIFE.

    Top-level await in a classic <script> is a SyntaxError in browsers,
    which kills the ENTIRE script silently (the exact bug that left the
    auth toggle, lock overlay, and page panels dead). The IIFE runs the
    same code with real async semantics.
    """
    return ("(async () => {\n"
            + "\n".join("  " + line for line in js.strip().split("\n"))
            + "\n})();")


# Mobile nav: accordion under the header. Hidden by default on desktop;
# toggled by the hamburger button in the header shell.
MOBILE_NAV = """
  <!-- Mobile Nav (accordion) -->
  <div id="mobile-nav" class="hidden md:hidden bg-slate-900/95 backdrop-blur border-b border-slate-800 px-4 py-3 space-y-1">
    <!-- populated by JS below; keeps markup in one place -->
  </div>
  <script>
  (function () {
    const LINKS = [
      ['/', 'Dashboard'],
      ['/playlists', 'Playlists'],
      ['/widgets', 'Widgets'],
      ['/settings', 'Settings'],
      ['/photos', 'Photos'],
      ['/system', 'System'],
    ];
    const LOCKED = new Set(['playlists', 'widgets', 'settings', 'photos', 'system']);
    const active = window.location.pathname.replace(/\\/$/, '') || '/';
    const nav = document.getElementById('mobile-nav');
    if (!nav) return;
    nav.innerHTML = LINKS.map(function (l) {
      const isActive = l[0] === active;
      const locked = LOCKED.has(l[0].replace(/\\/$/, '').slice(1));
      const cls = 'flex items-center justify-between px-4 py-2.5 rounded-lg text-sm font-semibold transition '
        + (isActive
          ? 'bg-orange-600 text-white shadow-lg shadow-orange-600/30'
          : 'text-slate-300 hover:bg-slate-800 hover:text-white');
      return '<a href="' + l[0] + '" class="' + cls + '">'
        + '<span>' + l[1] + '</span>'
        + (locked ? '<span class="page-lock text-xs opacity-60">\\uD83D\\uDD12</span>' : '')
        + '</a>';
    }).join('');
    const btn = document.getElementById('nav-toggle');
    if (btn) btn.addEventListener('click', function () { nav.classList.toggle('hidden'); });
  })();
  </script>
"""

HEADER_EXTRAS = """
    <!-- Mobile hamburger (md:hidden) -->
    <button id="nav-toggle" class="md:hidden p-2 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 transition" aria-label="Menu">
      <svg class="w-5 h-5 text-slate-200" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" /></svg>
    </button>
"""


LOCK = (
    '    <!-- Auth Lock Overlay -->\n'
    '    <div id="auth-lock" class="hidden fixed inset-0 z-40 bg-slate-950/95 backdrop-blur flex items-center justify-center p-4">\n'
    '      <div class="bg-slate-900 border border-slate-700 rounded-2xl max-w-sm w-full p-6 shadow-2xl space-y-4 text-center">\n'
    '        <div class="text-4xl">&#128274;</div>\n'
    '        <h3 class="font-bold text-base text-slate-100">Administrator sign-in required</h3>\n'
    '        <p class="text-xs text-slate-400">This page is locked behind admin authentication.</p>\n'
    '        <button onclick="showLoginModal()" class="w-full py-2.5 rounded-lg bg-orange-600 hover:bg-orange-500 font-semibold text-sm text-white shadow-lg shadow-orange-600/30 transition">Log In</button>\n'
    '      </div>\n'
    '    </div>'
)

try:
    PHOTOS_HTML = open(os.path.join(HERE, ".photos_fragment.html"), encoding="utf-8").read()
    SYSTEM_HTML = open(os.path.join(HERE, ".system_fragment.html"), encoding="utf-8").read()
except OSError:
    PHOTOS_HTML = ""
    SYSTEM_HTML = ""


def _find_marker(lines, marker):
    for i, line in enumerate(lines):
        if marker in line:
            return i
    raise SystemExit(f"build_pages: marker not found in {SRC}: {marker!r}")


def _extract(lines, start_marker, end_marker):
    start = _find_marker(lines, start_marker) if start_marker else 0
    if end_marker is not None:
        end = next((i for i in range(start + 1, len(lines)) if end_marker in lines[i]), None)
        if end is None:
            raise SystemExit(f"build_pages: end marker not found after start for {start_marker!r}: {end_marker!r}")
        if end <= start:
            raise SystemExit(f"build_pages: end marker before start for {start_marker!r}")
    else:
        # No end marker: slice to the matching close of the div opened on
        # the start line (tracks nesting from that line forward).
        depth = 0
        end = None
        for i in range(start, len(lines)):
            depth += len(re.findall(r"<div\b", lines[i])) - len(re.findall(r"</div>", lines[i]))
            if depth <= 0 and i > start:
                end = i
                break
        if end is None:
            raise SystemExit(f"build_pages: unbalanced divs for segment {start_marker!r}")
    seg = list(lines[start:end + 1])
    # Trim Python-literal residue: start marker may sit mid-line
    # (e.g. 'DASHBOARD_HTML = """<!DOCTYPE html>') - cut to the marker.
    if start_marker:
        idx = seg[0].index(start_marker)
        seg[0] = seg[0][idx:]
    if end_marker:
        # Keep text up to the END of the marker occurrence only.
        last = seg[-1].index(end_marker) + len(end_marker)
        seg[-1] = seg[-1][:last]
    return "\n".join(seg).strip()


def _balance_check(name, text):
    opens = len(re.findall(r"<div\b", text))
    closes = len(re.findall(r"</div>", text))
    if opens != closes:
        raise SystemExit(f"build_pages: segment {name} unbalanced ({opens} opens / {closes} closes)")


def extract_parts():
    src = open(SRC, encoding="utf-8").read()
    lines = src.split("\n")
    p = {}
    for name, (start_marker, end_marker) in SEGMENTS.items():
        text = _extract(lines, start_marker, end_marker)
        _balance_check(name, text)
        p[name] = text
    # Scripts: locate by their first distinctive line rather than offsets.
    def js_between(start_pat, end_marker):
        s = _find_marker(lines, start_pat)
        while s >= 0 and "<script" not in lines[s]:
            s -= 1
        e = next((i for i in range(s + 1, len(lines)) if "</script>" in lines[i]), None)
        if e is None:
            raise SystemExit(f"build_pages: no closing script tag for JS segment {start_pat!r}")
        return "\n".join(lines[s + 1:e]).strip("\n")
    p["JS_CORE"] = js_between("    let currentConfig", "</script>")
    p["JS_TAIL"] = js_between("<!-- Device Telemetry", "</script>")
    json.dump(p, open(PARTS, "w"))
    return p


# Which source sections (in order) compose each in-source page, and the
# route each page maps to (used to highlight the active nav link).
SECTIONS = {
    "dashboard": ["SEC_DASH"],
    "playlists": ["SEC_PLAY"],
    "widgets": ["SEC_DEV", "SEC_FIND"],
    "settings": ["SEC_HW", "SEC_QH", "SEC_SEC"],
}
ACTIVE = {"dashboard": "/", "playlists": "/playlists", "widgets": "/widgets",
          "settings": "/settings", "photos": "/photos", "system": "/system"}


def nav_links(active):
    links = [("/", "Dashboard"), ("/playlists", "Playlists"), ("/widgets", "Widgets"),
             ("/settings", "Settings"), ("/photos", "Photos"), ("/system", "System")]
    out = []
    for href, label in links:
        cls = "px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition "
        cls += ("bg-orange-600 text-white shadow-lg shadow-orange-600/30"
                if href == active else "text-slate-400 hover:text-slate-100 hover:bg-slate-800")
        out.append('    <a href="%s" class="%s">%s</a>' % (href, cls, label))
    return "\n".join(out)


def build():
    p = extract_parts()
    for page in ("dashboard", "playlists", "widgets", "settings", "photos", "system"):
        parts = [
            p["HEAD"], "",
            # header: strip the source's own section navigator (it came in
            # with the fixed-line slice in the previous builder) and add the
            # mobile hamburger button.
            re.sub(
                r'<!-- Section Navigator -->\n\s*<div id="section-tabs".*?</div>\s*\n?',
                "",
                p["HEADER"], flags=re.S,
            ).rstrip(),
            HEADER_EXTRAS, "",
            # Single shared nav bar (one per page - the source nav was being
            # duplicated before).
            '  <!-- Page Nav -->\n  <div id="section-tabs" class="sticky top-[64px] z-30 bg-slate-950/90 backdrop-blur border-b border-slate-800 px-4 sm:px-6 py-2 flex gap-1 overflow-x-auto">',
            nav_links(ACTIVE[page]), "  </div>", "",
            MOBILE_NAV, "",
            LOCK, "",
            '  <main class="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">',
        ]
        if page == "photos":
            parts.append(PHOTOS_HTML)
        elif page == "system":
            parts.append(SYSTEM_HTML)
        else:
            for key in SECTIONS[page]:
                parts.append(p[key]); parts.append("")
            if page == "settings":
                # SEC_SEC's slice ends before its two closing tags (card +
                # grid wrapper) so close both explicitly.
                parts.append("      </div>"); parts.append("")
                parts.append("    </div>"); parts.append("")
        parts += ["  </main>", "", p["MODALS"], "",
                  "  <script>", p["JS_CORE"], "  </script>",
                  "  <script>", p["JS_TAIL"], "  </script>",
                  "  <script>", _wrap_init(INIT[page]), "  </script>",
                  "</body>", "</html>"]
        html = "\n".join(parts) + "\n"
        open(os.path.join(HERE, page + ".html"), "w", encoding="utf-8").write(html)
        print(page, len(html), "bytes")


if __name__ == "__main__":
    build()
