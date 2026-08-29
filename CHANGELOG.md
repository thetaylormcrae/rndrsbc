# CHANGELOG

<!-- version list -->

## v0.6.5 (2026-08-29)

### Bug Fixes

- **inky**: Quantize to Spectra 6 primaries in software before set_image() (P-mode path) - kills
  Pimoroni's inverted SATURATED/DESATURATED palette blend that caused muddy/inconsistent colors;
  output now deterministic primaries
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
