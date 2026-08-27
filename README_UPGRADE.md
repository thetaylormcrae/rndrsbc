# rndrSBC — Post-Upgrade Feature Matrix

## Security & First-Run Flow
- First-run `/api/setup` password gate (min 8 chars), `werkzeug.security` PBKDF2 hashing.
- Session-token auth (`secrets.token_hex(32)`, HttpOnly SameSite=Lax cookies, Bearer fallback).
- All shell execution is list-based `subprocess.run` — **zero `shell=True`** anywhere.
- Path traversal protection: strict `os.path.commonpath` scoping on all file-serving endpoints.
- Setup + Login UI modals baked into the dashboard.

## Zero-Config Widget Discovery
- `@register_widget("name")` decorator + `discover_widgets()` auto-imports every plugin.
- Safe render isolation: `safe_render()` catches every exception → visual error card.
- Managed async caching: `fetch_remote_json(url, ttl)` returns stale cache instantly, refreshes in a daemon thread — never blocks.

## Pipeline & Performance
- **Stage caching**: quantized/dithered frames are cached by `(widget, settings-hash, resolution)`.
- **Dirty-rect partial updates**: `RenderStageCache.get_dirty_rects(new, prev)` → only changed regions flash.
- **Transitions**: cut, wipe horizontal/vertical, invert-flash anti-ghosting, cross-fade.
- **Telemetry**: render counts, durations, error counters, health status, uptime.

## Device Onboarding (#4 AP Fallback / #5 QR Claim)
- QR claim-token onboarding widget (prints on first boot).
- `/api/onboarding/claim`, `/api/onboarding/qr.png`, `/api/onboarding/wifi`, `/api/onboarding/ap/start|stop`.
- Ad-hoc Wi-Fi AP fallback + recovery mode via `networkd-dispatcher` watchdog hook.

## New Widgets
| Widget | Purpose |
|---|---|
| `onboarding` | QR claim + 3-step setup instructions on the display |
| `photo_frame` | Rotate uploaded personal photos (cover-fit, caption) |
| `network` | Live Wi-Fi SSID, signal, IP, gateway ping diagnostics |
| `composite_grid` | Multi-zone layout grid (sidebar_left/right, quad, top_bar, plus) |

## Dashboard Upgrades
- Device Name, Timezone, Quiet Hours, Display Transition selectors.
- Device Health telemetry panel, Software Update checker/applier, Photo uploader.
- New widget catalog entries for all above.

## OTA Self-Update
- `core/updates.py`: GitHub Releases check, staged download, atomic apply, one-click rollback.
- Endpoints: `/api/update/check`, `/api/update/apply`, `/api/update/rollback`.

## Hardware-Capability-Gated Partial Refresh
- `displays/base.py` now declares per-driver capability flags:
  - `SUPPORTS_PARTIAL_REFRESH` — B/W Waveshare (`epd7in5_V2`, `epd7in5_HD`, `epd4in2`, `epd2in13_V4`) = True; 7-color `epd7in3f`/`epd5in65f`/`epd13in3k`, BWR `epd7in5b_V2`, and Inky Impression = False (full-refresh-only).
  - `PARTIAL_RECHARGE_LIMIT` — max consecutive partials before a forced full refresh (default 20, B/W driver 20).
  - `PARTIAL_PRESERVES_GRAYSCALE` — whether partial mode preserves gray levels.
- `server/scheduler.py` now gates the hardware push:
  - `panel_partial = display.supports_partial()` — dirty-rect partial updates only reach panels that can handle them.
  - `_consecutive_partials` counter forces a full refresh at the recharge limit (charge accumulation / ghosting prevention).
  - Multi-color panels (BWR/7-color/ACeP) always receive full-frame refreshes — **no partial ever sent**.
- `displays/framebuffer.py` actually writes only the dirty region (LCD/TFT panels update per-pixel natively).
- `displays/waveshare.py` defensively drops `dirty_rects` if a panel lacks partial support (logs a warning), and warns loudly on unknown model names instead of silently falling back to 7-color defaults.
