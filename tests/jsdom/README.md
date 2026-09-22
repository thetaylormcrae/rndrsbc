# jsdom cross-page harness

Renders every server template with the real `assets/app.js`, mocks `/api/config`
and `/api/auth/status`, then reports which dynamic containers (`*container*`,
`*tabs*`, `*list*`, `*grid*`, `*cards*`) remain empty after `loadStatus()`.

Purpose: catches template↔JS contract drift (e.g. playlists page rendering
zero widget cards because `renderPlaylistTabs()` targeted `#playlist-tabs`,
which only exists on `dashboard.html`).

Run:

    cd tests/jsdom && pnpm add jsdom && node allpages.mjs

Expects all six pages to print `OK`. Unrelated Leaflet stub errors are
tolerated (app.js guards them since 80b51c5).
