# rndrSBC

> **Ultra-lightweight, high-performance native E-Paper operating platform designed for Single-Board Computers.**

---

### Why rndrSBC?

* **Chromium-Free**: 0MB browser bloat. No headless Chrome, no WebKit, no X11.
* **Insanely Fast**: Renders vector dashboards in **35ms–60ms** (vs. 10–15 seconds on Chrome).
* **Zero Swap & Memory Thrashing**: Runs in **< 15MB RAM** (ideal for Pi Zero W / Pi Zero 2W / Pi 3).
* **Responsive Canvas Engine**: Resolution-independent vector layout with fractional split boxes and auto-scaling typography.
* **100% Clean-Room Architecture**: Fully standalone with modular display drivers and widget system.

---

### Quick Start (Development / PC / Mac)

```bash
# 1. (Optional) Vendor the runtime deps for a fully-offline copy
python3 build_vendor_deps.py

# 2. Run single-pass render test
python3 main.py --once
```

Output is rendered directly to `live_screen.png`.

### Run the web dashboard (any platform)

```bash
python3 main.py 8080          # foreground
python3 main.py --daemon 8080 # background, self-detaches (no sudo)
```

---

### Raspberry Pi — no `install.sh` required

rndrSBC is **relocatable**: it anchors every path (config, previews, photo
library) to its own folder, and it can load its Python deps straight from a
vendored wheel bundle. So deployment is simply:

```bash
# Copy the project folder onto the Pi, then:
python3 main.py 8080
```

Optional, per-target dependency bundle (needs internet once, on the Pi):

```bash
python3 build_vendor_deps.py            # detects this machine's arch
# or for a Raspberry Pi explicitly:
python3 build_vendor_deps.py --platform aarch64   # 64-bit OS / Pi 4+ / Pi 5
python3 build_vendor_deps.py --platform armv7l    # 32-bit OS
```

The resulting `vendor/deps/*.whl` files let the platform run **fully offline
with zero pip installs**. Skip this and rndrSBC will fall back to your
system's packages (or create a private `.venv` on first run).

If you want rndrSBC to start at boot (optional), use the relocatable unit:

```bash
# edit the two placeholders in rndrSBC.service, then:
sudo cp rndrSBC.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rndrSBC
```

> A legacy `install.sh` is still included for compatibility, but it is no
> longer required.

### Recent Additions (this build)

**Physical Buttons (GPIO)** — `core/buttons.py`
- Wire three buttons to GPIO 17 / 27 / 22 (to GND), enabled in the dashboard.
- NEXT cycles widgets, PREV goes back, TOGGLE-QUIET pauses/restarts refreshes.
- Falls back gracefully when RPi.GPIO is absent (simulator / laptop / CI) — buttons are auto-disabled.

**CalDAV + iCal Calendar** — `widgets/calendar/widget.py`
- Calendar widget now accepts a `caldav` config (URL + basic-auth user/password) in addition to public `iCal` feed URLs.
- Fetches via HTTPS `REPORT` query, parses with the native ICS parser (no extra deps); per-date event-dot markers on the month grid.

**Localization (i18n)** — `core/i18n.py`
- New `language` global setting (English, Español, Français, Deutsch, Italiano, Português, Nederlands, Türkçe).
- Localizes the calendar weekday headers + agenda labels and the weather "Updated / Feels Like / Wind / Humidity" labels.
- Add languages in `core/i18n.py` with three dicts (`MONTHS`, `WEEKDAYS`, `LABELS`).

**Reflect-mode refresh** — `core/transitions.py` + scheduler
- `refresh_mode: "auto"` (default) uses dirty-rect partial refresh when the panel supports it, with a full-refresh recharge every 20 partials.
- `refresh_mode: "full"` opt-out forces full-frame always (dashboard Display Transitions card).

