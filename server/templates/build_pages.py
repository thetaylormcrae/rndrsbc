"""Builds the 6 distinct admin pages from segments of server/app.py."""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "app.py")
PARTS = os.path.join(HERE, ".build_parts.json")


def extract_parts():
    src = open(SRC, encoding="utf-8").read()
    lines = src.split("\n")
    def seg(a, b): return "\n".join(lines[a - 1:b])
    def js(a, b):
        t = seg(a, b)
        s = t.index("<script>") + len("<script>")
        return t[s:t.rindex("</script>")].strip("\n")
    p = {
        "HEAD": seg(86, 135).strip().replace('DASHBOARD_HTML = """', "", 1),
        "HEADER": seg(136, 176).strip(),
        "SEC_DASH": seg(181, 249).strip(),
        "SEC_PLAY": seg(251, 347).strip(),
        "SEC_DEV": seg(349, 410).strip(),
        "SEC_FIND": seg(412, 427).strip(),
        "SEC_HW": seg(429, 475).strip(),
        "SEC_QH": seg(476, 560).strip(),
        "SEC_SEC": seg(562, 588).strip(),
        "MODALS": seg(595, 640).strip(),
        "JS_CORE": js(642, 1692),
        "JS_TAIL": js(1750, 2094),
    }
    json.dump(p, open(PARTS, "w"))
    return p


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

INIT = {
    "dashboard": "await checkAuthStatus(); await loadStatus(); if (isAuthenticated) { try { renderPlaylistTabs(); } catch (e) {} }",
    "playlists": "await checkAuthStatus(); const lock = document.getElementById('auth-lock'); if (isAuthenticated) { lock.classList.add('hidden'); await loadStatus(); try { renderPlaylistTabs(); renderPlaylist(); } catch (e) {} } else { lock.classList.remove('hidden'); } if (!setupRequired) buildTimezoneSelect();",
    "widgets": "await checkAuthStatus(); const lock = document.getElementById('auth-lock'); if (isAuthenticated) { lock.classList.add('hidden'); try { devStudioInit(); } catch (e) {} } else { lock.classList.remove('hidden'); }",
    "settings": "await checkAuthStatus(); const lock = document.getElementById('auth-lock'); if (isAuthenticated) { lock.classList.add('hidden'); await loadStatus(); try { updateHardwareSettings(); updateQuietHoursSettings(); updateDeviceSettings(); } catch (e) {} } else { lock.classList.remove('hidden'); } if (!setupRequired) buildTimezoneSelect();",
    "photos": "await checkAuthStatus(); const lock = document.getElementById('auth-lock'); if (isAuthenticated) { lock.classList.add('hidden'); try { loadPhotos(); } catch (e) {} } else { lock.classList.remove('hidden'); }",
    "system": "await checkAuthStatus(); const lock = document.getElementById('auth-lock'); if (isAuthenticated) { lock.classList.add('hidden'); try { loadTelemetry(); } catch (e) {} try { checkUpdate(); } catch (e) {} } else { lock.classList.remove('hidden'); }",
}

SECTIONS = {
    "dashboard": ["SEC_DASH"],
    "playlists": ["SEC_PLAY"],
    "widgets": ["SEC_DEV", "SEC_FIND"],
    "settings": ["SEC_HW", "SEC_QH", "SEC_SEC"],
}
ACTIVE = {"dashboard": "/", "playlists": "/playlists", "widgets": "/widgets",
          "settings": "/settings", "photos": "/photos", "system": "/system"}


def build():
    p = extract_parts()
    for page in ("dashboard", "playlists", "widgets", "settings", "photos", "system"):
        parts = [
            p["HEAD"], "", p["HEADER"], "",
            '  <!-- Page Nav -->\n  <div id="section-tabs" class="sticky top-[64px] z-30 bg-slate-950/90 backdrop-blur border-b border-slate-800">',
            nav_links(ACTIVE[page]), "  </div>", "", LOCK, "",
            '  <main class="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">',
        ]
        if page == "photos":
            parts.append(PHOTOS_HTML)
        elif page == "system":
            parts.append(SYSTEM_HTML)
        else:
            for key in SECTIONS[page]:
                parts.append(p[key]); parts.append("")
        parts += ["  </main>", "", p["MODALS"], "",
                  "  <script>", p["JS_CORE"], "  </script>",
                  "  <script>", p["JS_TAIL"], "  </script>",
                  "  <script>", INIT[page], "  </script>",
                  "</body>", "</html>"]
        html = "\n".join(parts) + "\n"
        open(os.path.join(HERE, page + ".html"), "w", encoding="utf-8").write(html)
        print(page, len(html), "bytes")


if __name__ == "__main__":
    build()
