# rndrSBC

> **Ultra-lightweight, high-performance native E-Paper operating platform designed for Single-Board Computers.**

![PyPI](https://img.shields.io/pypi/v/rndrsbc?color=3776AB&label=rndrsbc)
![PyPI - Python](https://img.shields.io/pypi/pyversions/rndrsbc)
![PyPI - Wheel](https://img.shields.io/pypi/wheel/rndrsbc)
![GitHub Release](https://img.shields.io/github/v/release/thetaylormcrae/rndrsbc?color=blue)
![License](https://img.shields.io/github/license/thetaylormcrae/rndrsbc)
![GitHub last commit](https://img.shields.io/github/last-commit/thetaylormcrae/rndrsbc)

**Deployment companion:** [`rndrsbc-deploy`](https://github.com/thetaylormcrae/rndrsbc-deploy) — operator-side scaffold for the Pi (config, catalog, systemd unit).

---

## What it is

A **Chromium-free, sub-60ms, sub-15MB** E-Paper dashboard OS for Single-Board
Computers. It renders live dashboards (weather, calendar, news, custom widgets)
directly to a low-power E-Paper panel with no web browser, no X11, no swap
thrashing — designed for always-on, wall-powered frames that must run for
months on a Pi Zero W. **v0.3.0** adds automatic panel detection, a conventional-
commit-driven semantic release pipeline, and an auto-generated `CHANGELOG`.

### Key metrics

| Metric | rndrSBC | Chrome-based frame |
|--------|---------|--------------------|
| Render time | **35–60ms** | 10–15s |
| Resident RAM | **< 15MB** | 300MB–1GB+ |
| Browser/WebKit/X11 | **None** | Required |
| Boot-to-frame | Seconds | Tens of seconds |

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

---

## Feature highlights

- **Zero-browser rendering** — vector canvas engine, resolution-independent layout, auto-scaling typography, fractional split boxes.
- **Display auto-detection (`v0.3.0`)** — `driver: auto` probes for a Pimoroni Inky panel; falls back to the virtual display if absent/headless. No manual model selection for standard panels.
- **Community widget system** — `rndrsbc search | install | remove | list`; artifacts are SHA-256 verified from the catalog feed and installed into the deploy home (never into site-packages).
- **GPIO buttons** — next / prev / quiet-toggle on GPIO 17 / 27 / 22; auto-disabled when `RPi.GPIO` is absent.
- **CalDAV + iCal** — calendar widget fetches private CalDAV (`REPORT`) or public iCal feeds; native ICS parser, no extra deps.
- **i18n** — English, Español, Français, Deutsch, Italiano, Português, Nederlands, Türkçe.
- **Relocatable** — anchors every path to its own folder; optional `vendor/deps` wheel bundle for fully-offline operation.

## Releases & versioning

`rndrsbc` ships on **PyPI** and versions via [Semantic Release](https://python-semantic-release.readthedocs.io/) driven by
**conventional commits**:

- `feat:` → minor · `fix:` → patch · `BREAKING`/`feat!:` → major (on the `0.x` line, breaking bumps the minor digit)
- Merging to `main` auto-bumps `core/__init__.py`, regenerates `CHANGELOG.md`, cuts the `vX.Y.Z` tag, and publishes to PyPI (`publish.yml`, trusted publishing / OIDC).
- Deploy-side state is **never touched** by an upgrade — `pip install -U rndrsbc` is the whole upgrade.

## License

See [`LICENSE`](LICENSE). Built as a clean-room implementation; all widget and
driver code is original.

<!--
Maintained via the automated release pipeline. `CHANGELOG.md` is generated by
Python Semantic Release from conventional commits.
-->


---

## QA & Display Validation

The rest of this repo is rendering *logic*; this section is the *prove-it-works*
loop. Run these on the Pi to confirm the panel reproduces the intended colours
deterministically.

### `rndrsbc calibrate` — push the reference colour pattern + verify primaries
Renders the canonical 7-colour test pattern (black/white/yellow/red/blue/green
swatches + dither band + border frame), pushes it to the physical panel, and
verifies each block is a true primary (software check before the panel fires).
Saves `calibration.png` under `/rool-drive` so you can audit remotely.

```bash
sudo -E rndrsbc calibrate            # -> verdict OK/BLOCK-FAIL, block table
sudo -E rndrsbc calibrate --json     # machine-readable report
```

If a block reports `FAIL`, that channel's palette entry is wrong for the panel
generation — fix the colour in `core/calibrate.PRIMARIES` (or the driver path)
before trusting any widget render.

### `rndrsbc snapshot` — capture the *intended* frame to a PNG (no panel)
Renders the current configured first playlist item exactly as the daemon would,
and saves the pre-dither RGB canvas to `/rool-drive/screen.png` **without
touching the panel**. This is your remote-verifiable ground truth: compare it
against a photo of the panel to spot palette drift without a camera on the Pi.

```bash
sudo -E rndrsbc snapshot --out /rool-drive/screen.png
```

### `rndrsbc panel-spec` — which panel is actually attached?
Reads the eeprom to say *which* Inky generation (Spectra 6 `/` 7-colour vs E6
`/` 4-colour) is on the SPI bus, plus its native palette. Confirms your driver
targets the right colour set so a hardware swap surfaces as a boot-time
mismatch, not mysteriously-off colours days later.

```bash
sudo -E rndrsbc panel-spec
```

### Golden image regression (CI)
`tests/test_qa_regression.py` asserts (a) the colour pattern maps to exact native
primaries, (b) `render_preview()` yields a deterministic 800×480 RGB frame, and
(c) panel classification resolves the correct palette. Run:

```bash
python3 -m pytest tests/ -k qa
```

All four run under the same config → display wiring as the live daemon
(`core/qa.build_display`), so the QA path and the production path can't diverge.
