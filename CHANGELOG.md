# CHANGELOG

<!-- version list -->

## v0.14.0 (2026-09-05)

### Features

- **admin**: Split dashboard into focused section tabs
  ([`0d4fc9c`](https://github.com/thetaylormcrae/rndrsbc/commit/0d4fc9cf626bda4394b7921ce69ed955e2fe3622))


## v0.13.0 (2026-09-05)

### Features

- **dev-studio**: Add render preview UI to admin panel
  ([`a92467d`](https://github.com/thetaylormcrae/rndrsbc/commit/a92467d2e57ed8a1d417dc8f5d04c1a61fbdf05a))


## v0.12.0 (2026-09-04)

### Features

- **dev-studio**: Expose authenticated widget render in admin panel
  ([`9af216f`](https://github.com/thetaylormcrae/rndrsbc/commit/9af216f4ad5b62575ca7e23986d2aebbe1830143))


## v0.11.2 (2026-09-03)

### Bug Fixes

- Harden developer studio for production
  ([`73b0c39`](https://github.com/thetaylormcrae/rndrsbc/commit/73b0c39237401c1be1b015af6132bf8df2898230))


## v0.11.1 (2026-09-02)

### Bug Fixes

- Durable production logging and telemetry hardening
  ([`153c990`](https://github.com/thetaylormcrae/rndrsbc/commit/153c990ee1e023bc017b44cbc5cdc85d54393791))


## v0.11.0 (2026-09-01)

### Features

- **dashboard**: Add saving animations to config-save operations
  ([`1fa1447`](https://github.com/thetaylormcrae/rndrsbc/commit/1fa144733a3711ae255e630c058766798930027f))


## v0.10.4 (2026-09-01)

### Bug Fixes

- **dashboard**: Re-load auth-gated panels after SPA login
  ([`1bb5b12`](https://github.com/thetaylormcrae/rndrsbc/commit/1bb5b12f875eabc79dac8c39095a4e59d1f2251d))


## v0.10.3 (2026-09-01)

### Bug Fixes

- **update**: Query PyPI JSON API for latest version so OTA check no longer depends on the
  widgets-only registry feed
  ([`dfb6956`](https://github.com/thetaylormcrae/rndrsbc/commit/dfb69565d78399778579150d55a3b2055c6077e0))


## v0.10.2 (2026-09-01)

### Bug Fixes

- **update**: Bypass stale pip index cache + verify version actually moved
  ([`efaf233`](https://github.com/thetaylormcrae/rndrsbc/commit/efaf233f722c33386abf71c6422dbac040e71a6d))


## v0.10.1 (2026-09-01)


## v0.10.0 (2026-08-31)

### Features

- **admin**: Complete administrative configurability, backup/restore, power controls, and widget
  editors
  ([`d50ec57`](https://github.com/thetaylormcrae/rndrsbc/commit/d50ec578a49de4ac63b244bc17103b79c157ffa7))


## v0.9.0 (2026-08-31)

### Features

- **widgets**: Complete 100% InkyPi feature parity with News/RSS, Quotes, and Crypto market widgets
  ([`c434031`](https://github.com/thetaylormcrae/rndrsbc/commit/c4340314c03b966fac800090f05d02c4fdc56832))


## v0.8.5 (2026-08-31)

### Bug Fixes

- **engine**: Comprehensive prime-time stability, background caching, network resolution, and
  hardware metric polish
  ([`7a525d0`](https://github.com/thetaylormcrae/rndrsbc/commit/7a525d0ee86ea0d63ff62c3c9ee70e88e0a63024))


## v0.8.4 (2026-08-31)

### Bug Fixes

- **onboarding**: Consume the claim token client-side so QR/claim URLs actually claim the device
  ([`87f5851`](https://github.com/thetaylormcrae/rndrsbc/commit/87f58518cff8bdf8185dd4ab75d0ea4275878135))


## v0.8.3 (2026-08-31)

### Bug Fixes

- **qa**: Comprehensive end-to-end QA test suite, zero-warning schema validation, and widget
  attribute safety
  ([`7e8a7d0`](https://github.com/thetaylormcrae/rndrsbc/commit/7e8a7d0e2ec8a0a2df991bf20f8e888bde8eca9f))


## v0.8.2 (2026-08-31)

### Bug Fixes

- **display**: Anchor virtual display preview to deploy home so boot no longer crashes with
  PermissionError
  ([`489eb96`](https://github.com/thetaylormcrae/rndrsbc/commit/489eb96029bea6fa884322bbda6c62fcc4421b3e))


## v0.8.1 (2026-08-30)

### Bug Fixes

- **security**: Require authentication before serving any config, media, or control endpoint once
  admin password is set
  ([`cf8ca5d`](https://github.com/thetaylormcrae/rndrsbc/commit/cf8ca5da791a9ef94683b1d4a02225167451ca78))


## v0.8.0 (2026-08-30)

### Features

- **onboarding**: Pin fresh device on setup tile until configured via dashboard
  ([`a7ef377`](https://github.com/thetaylormcrae/rndrsbc/commit/a7ef377f0415defb430a0c412f2adbbd046076ae))


## v0.7.7 (2026-08-30)

### Bug Fixes

- **boot**: Stop crash-loop when config.json is missing required playlist keys
  ([`805a6c2`](https://github.com/thetaylormcrae/rndrsbc/commit/805a6c27c063952d3a3842c903975d30889bfd2b))


## v0.7.6 (2026-08-30)

### Bug Fixes

- **buttons**: Remap defaults off the display driver's pins; skip+skip-warn on collision
  ([`d357a4a`](https://github.com/thetaylormcrae/rndrsbc/commit/d357a4a50579e9af02fb52bf8850e2f690d4f08e))


## v0.7.5 (2026-08-30)

### Bug Fixes

- **config**: Fresh-device default is onboarding-first, omits calendar/photo_frame
  ([`5c3fd81`](https://github.com/thetaylormcrae/rndrsbc/commit/5c3fd814e01ff8614ba4b08ef3705f1df0a2e39b))


## v0.7.4 (2026-08-30)

### Bug Fixes

- **dashboard**: Correct default playlist schema, telemetry, and photo management
  ([`fe896a8`](https://github.com/thetaylormcrae/rndrsbc/commit/fe896a872339e5738cd9540996f3ba2846959bca))


## v0.7.3 (2026-08-30)

### Bug Fixes

- **display**: Surface start-to-end refresh duration in logs
  ([`9317ad3`](https://github.com/thetaylormcrae/rndrsbc/commit/9317ad3d49e4a4cd907e2d64bb1be6d55a0d7c85))

### Chores

- **deploy**: Delegate Pi install to rndrsbc-deploy repo
  ([`8844e85`](https://github.com/thetaylormcrae/rndrsbc/commit/8844e8501abe689f2fee6b5f9e002556d39df599))

### Documentation

- **panel_verify**: Correct e673 push semantics
  ([`a95672d`](https://github.com/thetaylormcrae/rndrsbc/commit/a95672df83250b629f22958e81834d5efe30c935))


## v0.7.2 (2026-08-30)

### Bug Fixes

- **display**: Verify panel update, serialize SPI, auto-retry stalls
  ([`81d7b58`](https://github.com/thetaylormcrae/rndrsbc/commit/81d7b58f3b54d1b4a73196e79d6e50d0522ac500))

### Chores

- **deploy**: Consolidate Pi setup into deploy/bootstrap.sh
  ([`6013028`](https://github.com/thetaylormcrae/rndrsbc/commit/601302837702cff941954649752d4efb626f2a2d))

### Continuous Integration

- **release**: Publish inline in release job + add deterministic Pi bootstrap
  ([`8ee9e79`](https://github.com/thetaylormcrae/rndrsbc/commit/8ee9e79cab58a89bfea63584cd69490a61f8bac1))


## v0.7.1 (2026-08-29)

### Bug Fixes

- **version**: Derive rndrsbc.__version__ from installed metadata
  ([`3119e01`](https://github.com/thetaylormcrae/rndrsbc/commit/3119e011fe2ae22d39483ef9970d3e229ad83eb8))


## v0.7.0 (2026-08-29)

### Features

- **qa**: Add calibrate/snapshot/panel-spec subcommands + golden-image regression + core.qa shared
  display builder
  ([`dd026d8`](https://github.com/thetaylormcrae/rndrsbc/commit/dd026d842619114bbaef9f6f8829c831502bb70b))


## v0.6.6 (2026-08-29)

### Features

- **QA tooling**: new `calibrate`, `snapshot`, and `panel-spec` subcommands for
  deterministic display validation (see README *QA & Display Validation*)
- **core.qa**: single headless display-builder shared by doctor / calibrate / snapshot /
  panel-spec so the QA path and the production daemon can't diverge
- **calibrate**: renders the reference 7-colour pattern, verifies each block is a true
  primary, pushes it to the panel, and persists `calibration.png` under /rool-drive
- **snapshot**: captures the intended pre-dither RGB frame to /rool-drive/screen.png
  without touching the panel — a remote-verifiable ground truth
- **panel-spec**: reads the eeprom to identify the exact Inky generation + native palette,
  so a hardware swap surfaces as a boot-time mismatch
- **golden-image regression**: `tests/test_qa_regression.py` locks in pattern primaries
  and a deterministic render_preview
- **core.qa.resolve_display**: single driver-dispatch source of truth now shared by
  `main.py` and the doctor, so `driver: "auto"` correctly probes the attached panel
  instead of failing with `No module named 'displays.auto'`; refactored
  `main.py` to use it so production and QA can't diverge

## v0.6.5 (2026-08-29)

### Bug Fixes

- **inky**: quantize to true Spectra 6 primaries in software before set_image()
  (P-mode branch) — kills Pimoroni's inverted SATURATED/DESATURATED palette blend that
  caused muddy/inconsistent colours; output is now deterministic primaries
  ([`74259f4`](https://github.com/thetaylormcrae/rndrsbc/commit/74259f4050e16756264a4d2e3f5ba4ad460277cd))


## v0.6.4 (2026-08-29)

### Bug Fixes

- **scheduler**: Dedupe same-widget renders within 10s (kills startup/button/API render bursts on
  slow e-Paper); stagger first loop tick 3s so main's initial render wins
  ([`f86e403`](https://github.com/thetaylormcrae/rndrsbc/commit/f86e40363593d6004beef5371981c9624a11b06f))


## v0.6.3 (2026-08-29)

### Bug Fixes

- **server**: Bind ProductionHandler to QuietServer so GET/POST are handled (was 501 Unsupported
  Method) and suppress socket disconnect tracebacks
  ([`1741619`](https://github.com/thetaylormcrae/rndrsbc/commit/1741619864d4b4ffadc14b1d99c378e41cda6aed))


## v0.6.2 (2026-08-29)

### Bug Fixes

- **display**: Clamp Inky saturation to [0.1, 1.0]
  ([`d426ee8`](https://github.com/thetaylormcrae/rndrsbc/commit/d426ee833fc5a1767f94404768da46cf6a68e7ce))


## v0.6.1 (2026-08-29)

### Bug Fixes

- **server**: Suppress ConnectionReset/BrokenPipe noise from browser aborts; route through
  QuietHandler
  ([`791aad0`](https://github.com/thetaylormcrae/rndrsbc/commit/791aad03d2a444fef5fea4ffa06463067a67f147))


## v0.6.0 (2026-08-29)

### Features

- **ui**: Expose Inky saturation in dashboard; default new widgets to frame None
  ([`5d7f6a1`](https://github.com/thetaylormcrae/rndrsbc/commit/5d7f6a1a5aaa658dcdd2dd0e86faff84e6266702))


## v0.5.2 (2026-08-29)

### Bug Fixes

- **widgets**: Default frame style to None (edge-to-edge); expose inky saturation in config template
  ([`390a82e`](https://github.com/thetaylormcrae/rndrsbc/commit/390a82e5c358c93ede4f2e424771f2072be7ed85))


## v0.5.1 (2026-08-29)

### Bug Fixes

- **inky**: Patch upstream Pimoroni SPI buffer chunking bug on Spectra 6 (E673)
  ([`560001a`](https://github.com/thetaylormcrae/rndrsbc/commit/560001af497c48623536ddcbcc936ccc98ad2b22))


## v0.5.0 (2026-08-29)

### Features

- **inky**: Allow explicit model override (e673, ac073tc1a, impression_5_7) in config
  ([`a6a0297`](https://github.com/thetaylormcrae/rndrsbc/commit/a6a0297e8683db432392710e7f63d5d1fa2c720f))


## v0.4.6 (2026-08-29)

### Bug Fixes

- **inky**: Set border black on initialization matching InkyPi
  ([`4d33f62`](https://github.com/thetaylormcrae/rndrsbc/commit/4d33f621464a2924ca0f46b44de6e3af9808fd6d))


## v0.4.5 (2026-08-29)

### Bug Fixes

- **inky**: Align inky driver with InkyPi architecture for full-color rendering
  ([`9db6e19`](https://github.com/thetaylormcrae/rndrsbc/commit/9db6e19c720577fb23562583342dcb2035fdcfeb))


## v0.4.4 (2026-08-29)

### Bug Fixes

- **inky**: Add nibble-swap + flip handling for skewed 7.3" panels
  ([`f724434`](https://github.com/thetaylormcrae/rndrsbc/commit/f724434dfc7f53f218b4c306fcf9f1b0519d93a4))


## v0.4.3 (2026-08-29)

### Bug Fixes

- **inky**: Add live buffer diagnostics to confirm physical-skew root cause
  ([`5b13e45`](https://github.com/thetaylormcrae/rndrsbc/commit/5b13e452ffb1a7c7a056669215f112d1d3b267f1))


## v0.4.2 (2026-08-29)

### Bug Fixes

- **inky**: Auto-transpose landscape buffers for portrait-native Inky Impression hardware
  ([`ce4fd86`](https://github.com/thetaylormcrae/rndrsbc/commit/ce4fd8687c0e51ab1d509b941aec9963b6bbb2ba))


## v0.4.1 (2026-08-29)

### Bug Fixes

- **display**: Honor 'rotation' fallback for display orientation in config
  ([`64a254b`](https://github.com/thetaylormcrae/rndrsbc/commit/64a254b3176e904f974c86c2ab84536d81cedc94))


## v0.4.0 (2026-08-29)

### Features

- **inky,auth**: Robust inky impression hardware init + admin password management
  ([`28d57bd`](https://github.com/thetaylormcrae/rndrsbc/commit/28d57bde8abde513b53e541443e9aa48e37574a9))


## v0.3.1 (2026-08-29)

### Bug Fixes

- **migrations**: Tolerate list-shaped playlists in _v1_to_v2
  ([`20b2dd4`](https://github.com/thetaylormcrae/rndrsbc/commit/20b2dd4d20bef89282577595180cc915e906bedf))


## v0.3.0 (2026-08-29)

### Bug Fixes

- Document auto-detection fallback in config schema
  ([`f230302`](https://github.com/thetaylormcrae/rndrsbc/commit/f230302a5b2c0150d4f0ffc8f38a5ad1a332a105))

- Keep semver on 0.x line (allow_zero_version + major_on_zero)
  ([`0472908`](https://github.com/thetaylormcrae/rndrsbc/commit/04729081f0986cb6a0f5cafce0a7be3d8eda34e6))

### Features

- Drive semver releases from conventional commits via PSR
  ([`a8d9d0e`](https://github.com/thetaylormcrae/rndrsbc/commit/a8d9d0e641a73d4639625dcc2002efe8918d8b00))


## v0.2.0 (2026-08-29)


## v0.1.0 (2026-08-29)

- Initial Release
