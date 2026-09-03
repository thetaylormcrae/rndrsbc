"""
rndrSBC - Live Developer Studio Server
Interactive local web studio for previewing, testing, and hot-reloading widgets on Windows, Mac, or Linux.
"""

import os
import sys
import json
import time
import io
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
from PIL import Image

# Import core and registry
import logging
from core.canvas import ResponsiveCanvas
from core.color import quantize_image
from core.paths import DATA_DIR
from widgets.base import discover_widgets

logger = logging.getLogger("rndrSBC.dev_studio")
_root = logging.getLogger()
if not any(getattr(h, "baseFilename", "") for h in _root.handlers):
    _base = getattr(logging, os.environ.get("RNDRSBC_LOG_LEVEL", "INFO").upper(), logging.INFO)
    _sh = logging.StreamHandler(); _sh.setLevel(_base)
    _sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    _root.addHandler(_sh)
    try:
        from logging.handlers import RotatingFileHandler
        _log_dir = os.path.join(DATA_DIR, "logs")
        os.makedirs(_log_dir, exist_ok=True)
        _fh = RotatingFileHandler(
            os.path.join(_log_dir, "rndrSBC.log"),
            maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        _fh.setLevel(_base)
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        _root.addHandler(_fh)
    except Exception as e:
        logger.warning("Could not attach studio file log handler: %s", e)

# Discover ALL bundled + community widget plugins (not a hardcoded subset) so the
# studio previews every loadable widget, including ones dropped into plugins/.
# Instances come from the @register_widget registry.
WIDGETS = discover_widgets()
if not WIDGETS:
    logger.warning("Widget discovery returned an empty registry; studio has nothing to preview.")

# Default widget for the initial render (not hardcoded to a module import).
DEFAULT_WIDGET = next(iter(WIDGETS)) if WIDGETS else None

# Build the widget <option> list for the studio selector from the live registry,
# so newly-added and community widgets appear automatically (fallback name → name).
def _build_widget_options():
    def _label(key):
        inst = WIDGETS.get(key)
        return getattr(inst, "name", None) or key
    rows = []
    for key in sorted(WIDGETS):
        selected = " selected" if key == DEFAULT_WIDGET else ""
        label = _label(key)
        rows.append(f'        <option value="{key}"{selected}>{label}</option>')
    return "\n".join(rows) if rows else '        <option value="">No widgets discovered</option>'

WIDGET_OPTIONS = _build_widget_options()

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>rndrSBC Dev Studio</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: '#e65c00',
            darkBg: '#0f172a',
            darkCard: '#1e293b'
          }
        }
      }
    }
  </script>
  <style>
    body { background-color: #0b0f19; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .checkerboard {
      background-image: linear-gradient(45deg, #182234 25%, transparent 25%), 
                        linear-gradient(-45deg, #182234 25%, transparent 25%), 
                        linear-gradient(45deg, transparent 75%, #182234 75%), 
                        linear-gradient(-45deg, transparent 75%, #182234 75%);
      background-size: 20px 20px;
      background-position: 0 0, 0 10px, 10px -10px, -10px 0px;
    }
  </style>
</head>
<body class="text-slate-100 min-h-screen flex flex-col">

  <!-- Header -->
  <header class="bg-slate-900 border-b border-slate-800 px-6 py-3 flex items-center justify-between sticky top-0 z-30">
    <div class="flex items-center space-x-3">
      <div class="w-8 h-8 rounded-lg bg-orange-600 flex items-center justify-center font-bold text-white tracking-wider text-sm shadow-lg shadow-orange-600/30">
        rS
      </div>
      <div>
        <h1 class="font-bold text-base tracking-tight text-white flex items-center gap-2">
          rndrSBC <span class="text-xs px-2 py-0.5 rounded bg-orange-500/20 text-orange-400 font-semibold border border-orange-500/30">DEV STUDIO</span>
        </h1>
      </div>
    </div>
    
    <div class="flex items-center space-x-4">
      <div class="flex items-center gap-2 text-xs bg-slate-800/80 px-3 py-1.5 rounded-full border border-slate-700">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
        <span class="text-slate-300 font-mono" id="render-latency">Ready</span>
      </div>
      <button onclick="triggerRender()" class="bg-orange-600 hover:bg-orange-500 text-white text-xs font-semibold px-4 py-1.5 rounded-lg transition shadow-md flex items-center gap-1.5 active:scale-95">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
        Re-render
      </button>
    </div>
  </header>

  <!-- Main Studio Layout -->
  <div class="flex-1 flex overflow-hidden">
    
    <!-- Controls Sidebar -->
    <aside class="w-80 bg-slate-900 border-r border-slate-800 p-5 overflow-y-auto space-y-6">
      
      <!-- Widget Selector -->
      <div>
        <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">Active Widget</label>
        <select id="widget-select" onchange="onWidgetChange()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-orange-500">
          <!-- Populated server-side from the widget registry -->
          {WIDGET_OPTIONS}
        </select>
      </div>

      <!-- Display Preset -->
      <div>
        <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">Display Profile</label>
        <select id="preset-select" onchange="onPresetChange()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-orange-500">
          <option value="800x480">7.3" Inky / Waveshare (800 × 480)</option>
          <option value="640x400">4.0" Inky Impression (640 × 400)</option>
          <option value="600x448">5.7" Inky Impression (600 × 448)</option>
          <option value="880x528">7.5" Waveshare HD (880 × 528)</option>
          <option value="1600x1200">13.3" Spectra 6 / Waveshare (1600 × 1200)</option>
          <option value="250x122">2.13" SBC Hat (250 × 122)</option>
          <option value="custom">Custom Resolution...</option>
        </select>
      </div>

      <!-- Orientation & Dimensions -->
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-xs text-slate-400 mb-1">Width (px)</label>
          <input type="number" id="dim-w" value="800" onchange="triggerRender()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm font-mono text-slate-200 focus:outline-none focus:border-orange-500">
        </div>
        <div>
          <label class="block text-xs text-slate-400 mb-1">Height (px)</label>
          <input type="number" id="dim-h" value="480" onchange="triggerRender()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm font-mono text-slate-200 focus:outline-none focus:border-orange-500">
        </div>
      </div>

      <div class="flex items-center justify-between bg-slate-800/50 p-2.5 rounded-lg border border-slate-800">
        <span class="text-xs text-slate-300">Orientation</span>
        <div class="flex rounded-md bg-slate-800 p-0.5">
          <button id="orient-land" onclick="setOrientation('landscape')" class="px-2.5 py-1 text-xs font-semibold rounded bg-orange-600 text-white shadow">Landscape</button>
          <button id="orient-port" onclick="setOrientation('portrait')" class="px-2.5 py-1 text-xs font-semibold rounded text-slate-400 hover:text-white">Portrait</button>
        </div>
      </div>

      <!-- E-Paper Palette Simulator -->
      <div>
        <div class="flex items-center justify-between mb-2">
          <label class="text-xs font-semibold uppercase tracking-wider text-slate-400">Color Palette</label>
          <label class="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
            <input type="checkbox" id="dither-toggle" onchange="triggerRender()" class="rounded bg-slate-800 border-slate-700 text-orange-500 focus:ring-0">
            <span>Dither Noise</span>
          </label>
        </div>

        <div class="space-y-1.5">
          <label class="flex items-center gap-2.5 p-2 rounded-lg bg-slate-800/40 hover:bg-slate-800 cursor-pointer border border-transparent hover:border-slate-700 transition">
            <input type="radio" name="color_mode" value="rgb" onchange="triggerRender()" class="text-orange-500 focus:ring-0">
            <div class="text-xs">
              <div class="font-medium text-slate-200">Full 24-bit RGB</div>
              <div class="text-slate-400 text-[10px]">Pure digital render</div>
            </div>
          </label>
          <label class="flex items-center gap-2.5 p-2 rounded-lg bg-slate-800/40 hover:bg-slate-800 cursor-pointer border border-transparent hover:border-slate-700 transition">
            <input type="radio" name="color_mode" value="7color" checked onchange="triggerRender()" class="text-orange-500 focus:ring-0">
            <div class="text-xs">
              <div class="font-medium text-slate-200">Spectra 6 (7-Color)</div>
              <div class="text-slate-400 text-[10px]">Crisp vector color snapping</div>
            </div>
          </label>
          <label class="flex items-center gap-2.5 p-2 rounded-lg bg-slate-800/40 hover:bg-slate-800 cursor-pointer border border-transparent hover:border-slate-700 transition">
            <input type="radio" name="color_mode" value="bwr" onchange="triggerRender()" class="text-orange-500 focus:ring-0">
            <div class="text-xs">
              <div class="font-medium text-slate-200">3-Color BWR</div>
              <div class="text-slate-400 text-[10px]">Black, White, Red e-paper</div>
            </div>
          </label>
          <label class="flex items-center gap-2.5 p-2 rounded-lg bg-slate-800/40 hover:bg-slate-800 cursor-pointer border border-transparent hover:border-slate-700 transition">
            <input type="radio" name="color_mode" value="bw" onchange="triggerRender()" class="text-orange-500 focus:ring-0">
            <div class="text-xs">
              <div class="font-medium text-slate-200">1-Bit Monochrome</div>
              <div class="text-slate-400 text-[10px]">Black & White e-paper</div>
            </div>
          </label>
        </div>
      </div>

      <!-- Live Settings Form -->
      <div class="border-t border-slate-800 pt-5">
        <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">Widget Settings</label>
        <div id="settings-form" class="space-y-3">
          <!-- Dynamically populated -->
        </div>
      </div>

    </aside>

    <!-- Canvas Preview Stage -->
    <main class="flex-1 bg-[#0b0f19] flex flex-col items-center justify-center p-8 overflow-auto relative checkerboard">
      
      <!-- Frame Container -->
      <div class="relative bg-slate-950 p-4 rounded-2xl shadow-2xl border-4 border-slate-800/80 flex flex-col items-center">
        <!-- Display Bezel Header -->
        <div class="w-full flex justify-between items-center pb-2 text-[11px] text-slate-500 font-mono">
          <span id="canvas-info">800 × 480 px</span>
          <span class="flex items-center gap-1.5">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span> CRISP E-PAPER SIMULATOR
          </span>
        </div>

        <!-- Rendered Image -->
        <div class="bg-white rounded overflow-hidden shadow-inner flex items-center justify-center">
          <img id="preview-img" src="/render?w=800&h=480&widget=weather&color=7color&dither=0" alt="Render Output" class="max-h-[72vh] object-contain transition-all">
        </div>
      </div>

    </main>

  </div>

  <script>
    let currentOrientation = 'landscape';

    function setOrientation(mode) {
      currentOrientation = mode;
      const wInput = document.getElementById('dim-w');
      const hInput = document.getElementById('dim-h');
      let w = parseInt(wInput.value);
      let h = parseInt(hInput.value);

      if (mode === 'portrait' && w > h) {
        wInput.value = h;
        hInput.value = w;
      } else if (mode === 'landscape' && h > w) {
        wInput.value = h;
        hInput.value = w;
      }

      document.getElementById('orient-land').className = mode === 'landscape' ? 'px-2.5 py-1 text-xs font-semibold rounded bg-orange-600 text-white shadow' : 'px-2.5 py-1 text-xs font-semibold rounded text-slate-400 hover:text-white';
      document.getElementById('orient-port').className = mode === 'portrait' ? 'px-2.5 py-1 text-xs font-semibold rounded bg-orange-600 text-white shadow' : 'px-2.5 py-1 text-xs font-semibold rounded text-slate-400 hover:text-white';

      triggerRender();
    }

    function onPresetChange() {
      const val = document.getElementById('preset-select').value;
      if (val === 'custom') return;
      const [w, h] = val.split('x').map(Number);
      if (currentOrientation === 'portrait') {
        document.getElementById('dim-w').value = Math.min(w, h);
        document.getElementById('dim-h').value = Math.max(w, h);
      } else {
        document.getElementById('dim-w').value = Math.max(w, h);
        document.getElementById('dim-h').value = Math.min(w, h);
      }
      triggerRender();
    }

    function onWidgetChange() {
      populateSettingsForm();
      triggerRender();
    }

    function populateSettingsForm() {
      const widget = document.getElementById('widget-select').value;
      const form = document.getElementById('settings-form');
      form.innerHTML = '';

      if (widget === 'weather') {
        form.innerHTML = `
          <div>
            <label class="block text-xs text-slate-400 mb-1">City / Title</label>
            <input type="text" id="cfg-title" value="Emmett, ID" onchange="triggerRender()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-200">
          </div>
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="block text-xs text-slate-400 mb-1">Latitude</label>
              <input type="number" step="0.0001" id="cfg-lat" value="43.8735" onchange="triggerRender()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-2 py-1.5 text-xs font-mono text-slate-200">
            </div>
            <div>
              <label class="block text-xs text-slate-400 mb-1">Longitude</label>
              <input type="number" step="0.0001" id="cfg-lon" value="-116.4993" onchange="triggerRender()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-2 py-1.5 text-xs font-mono text-slate-200">
            </div>
          </div>
          <div>
            <label class="block text-xs text-slate-400 mb-1">Units</label>
            <select id="cfg-units" onchange="triggerRender()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200">
              <option value="imperial">Imperial (°F, mph)</option>
              <option value="metric">Metric (°C, km/h)</option>
            </select>
          </div>
          <div>
            <label class="block text-xs text-slate-400 mb-1">Frame Style</label>
            <select id="cfg-frame" onchange="triggerRender()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200">
              <option value="Corner">Corner</option>
              <option value="Rectangle">Rectangle</option>
              <option value="None">None</option>
            </select>
          </div>
        `;
      } else if (widget === 'system_stats') {
        form.innerHTML = `
          <div>
            <label class="block text-xs text-slate-400 mb-1">Device Label</label>
            <input type="text" id="cfg-hostname" value="Raspberry Pi Zero 2W" onchange="triggerRender()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-200">
          </div>
          <div>
            <label class="block text-xs text-slate-400 mb-1">Frame Style</label>
            <select id="cfg-frame" onchange="triggerRender()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200">
              <option value="Corner">Corner</option>
              <option value="Rectangle">Rectangle</option>
              <option value="None">None</option>
            </select>
          </div>
        `;
      } else {
        // Generic fallback for any plugin widget not explicitly modeled below.
        // Renders with default settings via the /render endpoint.
        form.innerHTML = `
          <div class="text-xs text-slate-400 leading-relaxed rounded-lg bg-slate-800/40 border border-slate-700 p-3">
            This widget is auto-discovered from the plugin registry. It renders with its
            default settings. Fine-grained per-field editing is not modeled in this build of
            the studio yet.
          </div>
        `;
      }
    }

    function triggerRender() {
      const widget = document.getElementById('widget-select').value;
      const w = document.getElementById('dim-w').value;
      const h = document.getElementById('dim-h').value;
      const color = document.querySelector('input[name="color_mode"]:checked').value;
      const dither = document.getElementById('dither-toggle').checked ? '1' : '0';

      document.getElementById('canvas-info').innerText = `${w} × ${h} px (${currentOrientation})`;
      const latencyBadge = document.getElementById('render-latency');
      latencyBadge.innerText = 'Rendering...';

      const t0 = performance.now();
      const params = new URLSearchParams({ w, h, widget, color, dither, t: Date.now() });

      // Gather form settings
      if (widget === 'weather') {
        params.append('title', document.getElementById('cfg-title')?.value || 'Weather');
        params.append('latitude', document.getElementById('cfg-lat')?.value || '43.8735');
        params.append('longitude', document.getElementById('cfg-lon')?.value || '-116.4993');
        params.append('units', document.getElementById('cfg-units')?.value || 'imperial');
        params.append('frame', document.getElementById('cfg-frame')?.value || 'Corner');
      } else {
        params.append('hostname', document.getElementById('cfg-hostname')?.value || 'rndrSBC');
        params.append('frame', document.getElementById('cfg-frame')?.value || 'Corner');
      }

      const img = document.getElementById('preview-img');
      img.onload = () => {
        const t1 = performance.now();
        latencyBadge.innerText = `${Math.round(t1 - t0)}ms render`;
      };
      img.src = `/render?${params.toString()}`;
    }

    // Auto-init
    populateSettingsForm();
    triggerRender();
  </script>
</body>
</html>
"""

class DevStudioHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.replace("{WIDGET_OPTIONS}", WIDGET_OPTIONS).encode("utf-8"))
            return

        if parsed.path == "/render":
            query = urllib.parse.parse_qs(parsed.query)

            # Clamp canvas dimensions to the hardware-safe range. Without a bound a
            # request like ?w=999999 forces a huge ResponsiveCanvas then a
            # quantize/PNG encode - a memory/CPU DoS reachable without auth.
            def _clamp_dim(name: str, default: int):
                raw = query.get(name, [str(default)])[0]
                try:
                    return max(16, min(1600, int(raw)))
                except (TypeError, ValueError):
                    logger.warning("Non-integer %s=%r; using default %d", name, raw, default)
                    return default
            w = _clamp_dim("w", 800)
            h = _clamp_dim("h", 480)

            widget_name = query.get("widget", [DEFAULT_WIDGET or ""])[0]
            color_mode = query.get("color", ["7color"])[0]
            use_dither = query.get("dither", ["0"])[0] == "1"

            # Build settings
            settings = {}
            for k, v in query.items():
                if k not in ["w", "h", "widget", "color", "dither", "t"]:
                    settings[k] = v[0]

            widget = WIDGETS.get(widget_name)
            if widget is None:
                self.send_response(400)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(
                    f"Unknown widget: {widget_name!r}. Known: {', '.join(sorted(WIDGETS))}".encode("utf-8"))
                return

            try:
                img = widget.render((w, h), settings)
                if color_mode not in ("rgb", "7color", "bwr", "bw"):
                    logger.warning("Unknown color_mode=%r; falling back to 7color", color_mode)
                    color_mode = "7color"
                if color_mode != "rgb":
                    img = quantize_image(img, color_mode=color_mode, dither=use_dither, snap_white=True)

                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                data = buf.getvalue()

                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                logger.exception("Studio render failed for widget=%r dims=(%d,%d)", widget_name, w, h)
                self.send_response(500)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(f"Render failed: {e}".encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

def run_dev_studio(port=8080):
    server = HTTPServer(("0.0.0.0", port), DevStudioHandler)
    print(f"\n🚀 rndrSBC Dev Studio running at: http://localhost:{port}")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Dev Studio...")
        server.server_close()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run_dev_studio(port)
