"""
rndrSBC - Production Web Management Dashboard & Secure API
Hardened server with first-run authentication, session validation, path traversal protection,
safe command execution, quiet hours scheduling, and multi-playlist management.
"""

import os
import sys
import json
import time
import io
import secrets
import logging
import subprocess
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import urllib.parse
from PIL import Image

# Secure password hashing from Werkzeug
from werkzeug.security import generate_password_hash, check_password_hash

from core.paths import CONFIG_PATH, resolve

# Onboarding: QR claim-token flow + AP-mode provisioning
from server.onboarding import (
    claim_url_for_token,
    validate_claim_token,
    consume_claim_token,
    issue_claim_token,
    invalidate_unclaimed_tokens,
    onboarding_state,
    ap_manager as onboarding_ap_manager,
)

logger = logging.getLogger("rndrSBC.server")

# In-memory active session tokens: {session_token: {"created_at": float, "user": "admin"}}
ACTIVE_SESSIONS: dict[str, dict] = {}
# Sessions are also persisted to CONFIG_PATH so a service restart does not
# log every client out (otherwise stats / OTA / photos / dev-studio all 401
# until a manual re-login).
_SESSION_LOCK = threading.Lock()
SESSION_TTL_SECS = 86400 * 7 # 7 days


def _load_sessions(force: bool = False) -> None:
    """Populate ACTIVE_SESSIONS from the persisted copy in CONFIG_PATH."""
    if not force and ACTIVE_SESSIONS:
        return
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
            saved = cfg.get("admin_sessions") or {}
            now = time.time()
            for tok, meta in saved.items():
                if now - float(meta.get("created_at", 0)) < SESSION_TTL_SECS:
                    ACTIVE_SESSIONS[tok] = meta
    except Exception:
        logger.debug("Could not load persisted admin sessions", exc_info=True)


def _save_sessions() -> None:
    """Persist ACTIVE_SESSIONS into CONFIG_PATH (best-effort, atomic)."""
    try:
        with _SESSION_LOCK:
            cfg = {}
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r") as f:
                    cfg = json.load(f)
            cfg["admin_sessions"] = {
                tok: m for tok, m in ACTIVE_SESSIONS.items()
            }
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, CONFIG_PATH)
    except Exception:
        logger.debug("Could not persist admin sessions", exc_info=True)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Cache-Control" content="no-store">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <title>rndrSBC Management Portal</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: '#e65c00',
            darkBg: '#090d16',
            darkCard: '#131b2e'
          }
        }
      }
    }
  </script>
  <style>
    body { background-color: #090d16; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .checkerboard {
      background-image: linear-gradient(45deg, #182234 25%, transparent 25%), 
                        linear-gradient(-45deg, #182234 25%, transparent 25%), 
                        linear-gradient(45deg, transparent 75%, #182234 75%), 
                        linear-gradient(-45deg, transparent 75%, #182234 75%);
      background-size: 20px 20px;
    }
    .btn-spinner {
      display: inline-block;
      width: 12px; height: 12px;
      border: 2px solid rgba(255,255,255,0.35);
      border-top-color: #fff;
      border-radius: 50%;
      animation: btnspin 0.7s linear infinite;
      vertical-align: -2px;
      margin-right: 6px;
    }
    .btn-busy { opacity: 0.7; cursor: progress; }
    @keyframes btnspin { to { transform: rotate(360deg); } }
  </style>
  <!-- Leaflet Map Library (OpenStreetMap) -->
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
</head>
<body class="text-slate-100 min-h-screen flex flex-col antialiased">

  <!-- Navigation Bar -->
  <header class="bg-slate-900/90 backdrop-blur border-b border-slate-800 px-6 py-3.5 sticky top-0 z-40 flex items-center justify-between">
    <div class="flex items-center space-x-3">
      <div class="w-8 h-8 rounded-lg bg-orange-600 flex items-center justify-center font-bold text-white tracking-wider text-sm shadow-lg shadow-orange-600/30">
        rS
      </div>
      <div>
        <h1 class="font-bold text-base tracking-tight text-white flex items-center gap-2">
          rndrSBC <span class="text-[11px] font-normal px-2 py-0.5 rounded-full bg-orange-500/10 text-orange-400 border border-orange-500/20">PRO</span>
        </h1>
        <p class="text-xs text-slate-400 hidden sm:block">High-Performance Native E-Paper OS</p>
      </div>
    </div>

    <div class="flex items-center space-x-3">
      <button id="btn-refresh" onclick="refreshDisplayNow()" class="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-slate-200 border border-slate-700 transition">
        <svg class="w-3.5 h-3.5 text-orange-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
        <span>Refresh Screen</span>
      </button>
      <button id="btn-apply" onclick="saveAndApply()" class="flex items-center space-x-1.5 px-4 py-1.5 rounded-lg bg-orange-600 hover:bg-orange-500 text-xs font-semibold text-white shadow-lg shadow-orange-600/30 transition">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg>
        <span>Save & Apply</span>
      </button>
      
      <div id="auth-controls" class="pl-2 border-l border-slate-800">
        <button id="btn-logout" onclick="logout()" class="hidden text-xs text-slate-400 hover:text-rose-400 px-2 py-1.5 rounded border border-slate-800 hover:border-rose-900 transition">Logout</button>
        <button id="btn-login" onclick="showLoginModal()" class="text-xs text-slate-300 hover:text-white px-2.5 py-1.5 rounded bg-slate-800 hover:bg-slate-700 border border-slate-700 transition">Login</button>
      </div>
    </div>
  </header>

  <!-- Section Navigator -->
  <div id="section-tabs" class="sticky top-[64px] z-30 bg-slate-950/90 backdrop-blur border-b border-slate-800 px-4 sm:px-6 py-2 flex gap-1 overflow-x-auto">
    <button data-rtab="playlist" onclick="showTab('playlist')" class="rtab-btn px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition">🎛️ Playlist Config</button>
    <button data-rtab="widgets" onclick="showTab('widgets')" class="rtab-btn px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition">🧩 Widget Finder</button>
    <button data-rtab="photos" onclick="showTab('photos')" class="rtab-btn px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition">🖼️ Photo Management</button>
    <button data-rtab="backup" onclick="showTab('backup')" class="rtab-btn px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition">💾 Backup & Update</button>
  </div>

  <!-- Main Container -->
  <main class="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">

    <!-- Top Row: Screen Mirror + Quick Stats -->
    <div data-tab="playlist" class="grid grid-cols-1 lg:grid-cols-12 gap-6">
      
      <!-- Live Display Mirror -->
      <div class="lg:col-span-7 bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-xl flex flex-col justify-between">
        <div>
          <div class="flex items-center justify-between mb-3">
            <h2 class="font-bold text-sm text-slate-200 flex items-center gap-2">
              <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
              Live E-Paper Screen Mirror
            </h2>
            <span class="text-xs text-slate-500 font-mono" id="mirror-timestamp">Refreshed just now</span>
          </div>
          
          <div class="relative rounded-xl overflow-hidden border border-slate-800 bg-slate-950 checkerboard flex items-center justify-center p-2 min-h-[260px]">
            <img id="live-screen-img" src="/api/screen.png" alt="Current Screen Output" class="max-w-full h-auto rounded shadow-2xl border border-slate-800 object-contain" />
          </div>
        </div>

        <div class="mt-4 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs text-slate-400">
          <span id="active-widget-label">Rendering: Weather Dashboard</span>
          <div class="flex items-center space-x-2">
            <span class="px-2 py-0.5 rounded bg-slate-800 text-slate-300 font-mono text-[11px]" id="screen-dim-label">800×480 px</span>
          </div>
        </div>
      </div>

      <!-- Playlists Management -->
      <div data-tab="playlist" class="lg:col-span-5 bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-xl flex flex-col justify-between">
        <div class="space-y-4">
          <div class="flex items-center justify-between">
            <h2 class="font-bold text-sm text-slate-200">Playlists & Rotation Schedules</h2>
            <button onclick="promptCreatePlaylist()" class="text-xs text-orange-400 hover:text-orange-300 font-semibold flex items-center gap-1">
              + New Playlist
            </button>
          </div>

          <!-- Playlist Tabs -->
          <div id="playlist-tabs" class="flex flex-wrap gap-2 pt-1">
            <!-- Dynamically populated playlist tabs -->
          </div>

          <!-- Active Playlist Actions -->
          <div class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-3.5 space-y-2.5">
            <div class="flex items-center justify-between">
              <div class="text-xs text-slate-300 font-semibold flex items-center gap-1.5">
                <span id="current-tab-name">Main Rotation</span>
                <span id="active-badge" class="hidden text-[10px] font-bold px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">ACTIVE</span>
              </div>
              <div class="flex items-center space-x-2">
                <button id="btn-set-active" onclick="setActivePlaylist()" class="text-[11px] px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 font-semibold border border-slate-700">
                  Set as Active
                </button>
                <button onclick="deleteCurrentPlaylist()" id="btn-del-playlist" class="text-[11px] px-2 py-1 rounded hover:bg-rose-950/50 text-slate-500 hover:text-rose-400">
                  Delete
                </button>
              </div>
            </div>
            <p class="text-[11px] text-slate-400">Widgets in this playlist rotate sequentially based on duration intervals.</p>
          </div>
        </div>

        <div class="mt-4 pt-3 border-t border-slate-800 text-xs text-slate-400 flex justify-between">
          <span>Target Interval: <strong id="total-playlist-duration" class="text-slate-200">45 mins</strong></span>
          <span class="text-emerald-400 font-medium">Auto-rotates</span>
        </div>
      </div>

    </div>

    <!-- Active Playlist Widgets Editor -->
    <div data-tab="playlist" class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-4">
        <div>
          <h2 class="font-bold text-base text-slate-100 flex items-center gap-2">
            Playlist Widgets
            <span class="text-xs font-normal text-slate-400" id="playlist-item-count">(2 widgets configured)</span>
          </h2>
          <p class="text-xs text-slate-400">Configure parameters, durations, and layout options for each widget in the active rotation</p>
        </div>

        <!-- Add Widget Dropdown Catalog -->
        <div class="relative inline-block text-left">
          <button onclick="toggleAddDropdown()" class="px-3.5 py-1.5 rounded-lg bg-orange-600/10 hover:bg-orange-600/20 text-orange-400 hover:text-orange-300 border border-orange-500/30 text-xs font-semibold flex items-center gap-1.5 transition">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
            <span>+ Add Widget to Rotation</span>
          </button>
          
          <div id="add-dropdown-menu" class="hidden absolute right-0 mt-2 w-56 rounded-xl bg-slate-900 border border-slate-700 shadow-2xl py-1 z-50">
            <button onclick="addWidgetToPlaylist('weather')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition">
              <div class="w-7 h-7 rounded bg-orange-500/20 text-orange-400 flex items-center justify-center font-bold text-xs mt-0.5">☀️</div>
              <div>
                <div class="text-xs font-bold text-slate-200">Weather Dashboard</div>
                <div class="text-[10px] text-slate-400">Live conditions, AQI, & 7-day forecast</div>
              </div>
            </button>
            <button onclick="addWidgetToPlaylist('clock')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-emerald-500/20 text-emerald-400 flex items-center justify-center font-bold text-xs mt-0.5">🕒</div>
              <div>
                <div class="text-xs font-bold text-slate-200">Clock & Date</div>
                <div class="text-[10px] text-slate-400">Digital / Analog dial with local date</div>
              </div>
            </button>
            <button onclick="addWidgetToPlaylist('calendar')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-blue-500/20 text-blue-400 flex items-center justify-center font-bold text-xs mt-0.5">📅</div>
              <div>
                <div class="text-xs font-bold text-slate-200">Calendar & Agenda</div>
                <div class="text-[10px] text-slate-400">Monthly grid & synchronized iCal feed</div>
              </div>
            </button>
            <button onclick="addWidgetToPlaylist('news')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-red-500/20 text-red-400 flex items-center justify-center font-bold text-xs mt-0.5">📰</div>
              <div>
                <div class="text-xs font-bold text-slate-200">News & RSS Feed</div>
                <div class="text-[10px] text-slate-400">Live headlines from BBC, HN, Reuters, NYT</div>
              </div>
            </button>
            <button onclick="addWidgetToPlaylist('quotes')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-yellow-500/20 text-yellow-400 flex items-center justify-center font-bold text-xs mt-0.5">💡</div>
              <div>
                <div class="text-xs font-bold text-slate-200">Daily Quotes & Thoughts</div>
                <div class="text-[10px] text-slate-400">Inspirational & philosophical pull-quotes</div>
              </div>
            </button>
            <button onclick="addWidgetToPlaylist('crypto')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-amber-500/20 text-amber-400 flex items-center justify-center font-bold text-xs mt-0.5">⚡</div>
              <div>
                <div class="text-xs font-bold text-slate-200">Crypto & Markets</div>
                <div class="text-[10px] text-slate-400">Live BTC, ETH, and market asset prices</div>
              </div>
            </button>
            <button onclick="addWidgetToPlaylist('photo_frame')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-pink-500/20 text-pink-400 flex items-center justify-center font-bold text-xs mt-0.5">🖼️</div>
              <div>
                <div class="text-xs font-bold text-slate-200">Photo Frame</div>
                <div class="text-[10px] text-slate-400">Rotate uploaded personal photos</div>
              </div>
            </button>
            <button onclick="addWidgetToPlaylist('system_stats')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-emerald-500/20 text-emerald-400 flex items-center justify-center font-bold text-xs mt-0.5">💻</div>
              <div>
                <div class="text-xs font-bold text-slate-200">System Monitor</div>
                <div class="text-[10px] text-slate-400">Pi CPU, RAM, and storage stats</div>
              </div>
            </button>
            <button onclick="addWidgetToPlaylist('network')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-sky-500/20 text-sky-400 flex items-center justify-center font-bold text-xs mt-0.5">📶</div>
              <div>
                <div class="text-xs font-bold text-slate-200">Network Diagnostics</div>
                <div class="text-[10px] text-slate-400">Wi-Fi SSID, signal, IP, gateway ping</div>
              </div>
            </button>
            <button onclick="addWidgetToPlaylist('onboarding')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-purple-500/20 text-purple-400 flex items-center justify-center font-bold text-xs mt-0.5">📱</div>
              <div>
                <div class="text-xs font-bold text-slate-200">Device Setup & QR Claim</div>
                <div class="text-[10px] text-slate-400">QR code + Wi-Fi/AP provisioning instructions</div>
              </div>
            </button>
          </div>
        </div>
      </div>

      <div id="playlist-container" class="space-y-4">
        <!-- Dynamically rendered widget cards -->
      </div>
    </div>

    <!-- Dev Studio: widget render preview (authenticated) -->
    <div data-tab="widgets" class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl">
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="font-bold text-base text-slate-100">🧪 Dev Studio</h2>
          <p class="text-xs text-slate-400">Preview any discovered widget against your real panel dimensions</p>
        </div>
        <button id="btn-ds-refresh" onclick="devStudioRefresh()" class="px-3.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-xs font-semibold transition">↻ Refresh</button>
      </div>
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <!-- Controls -->
        <div class="lg:col-span-4 space-y-4">
          <div>
            <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wide">Widget</label>
            <select id="ds-widget" onchange="dsRebuildSettings(); dsRender();" class="mt-1 w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-orange-500/40">
              <option value="">Loading catalogue…</option>
            </select>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wide">Width px</label>
              <input id="ds-w" type="number" min="16" max="1600" value="800" onchange="dsRender()" class="mt-1 w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-orange-500/40">
            </div>
            <div>
              <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wide">Height px</label>
              <input id="ds-h" type="number" min="16" max="1600" value="480" onchange="dsRender()" class="mt-1 w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-orange-500/40">
            </div>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wide">Color mode</label>
              <select id="ds-color" onchange="dsRender()" class="mt-1 w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-orange-500/40">
                <option value="7color" selected>7-Color</option>
                <option value="rgb">RGB</option>
                <option value="bwr">BWR</option>
                <option value="bw">B/W</option>
              </select>
            </div>
            <div>
              <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wide">Dither</label>
              <select id="ds-dither" onchange="dsRender()" class="mt-1 w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-orange-500/40">
                <option value="0" selected>Off</option>
                <option value="1">On</option>
              </select>
            </div>
          </div>
          <div id="ds-settings"></div>
          <div class="flex items-center gap-2 pt-1">
            <button onclick="dsRender()" class="px-4 py-2 rounded-lg bg-orange-600 hover:bg-orange-500 text-white text-xs font-bold transition">▶ Render Preview</button>
            <span id="ds-status" class="text-[11px] text-slate-400"></span>
          </div>
        </div>
        <!-- Preview -->
        <div class="lg:col-span-8 bg-slate-950/60 border border-slate-800 rounded-xl flex items-center justify-center min-h-[360px] p-4 overflow-auto">
          <img id="ds-preview" alt="Widget preview" class="max-w-full max-h-[560px] object-contain rounded shadow-2xl ring-1 ring-slate-700/50" style="display:none">
          <div id="ds-preview-empty" class="text-slate-500 text-xs text-center space-y-1">
            <div class="text-3xl">🧪</div>
            <div>Select a widget to render a preview.</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Widget Finder: catalogue of all discovered widgets -->
    <div data-tab="widgets" class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl">
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="font-bold text-base text-slate-100">🔎 Widget Finder</h2>
          <p class="text-xs text-slate-400">Every widget discovered on this install, with its configuration schema</p>
        </div>
        <div class="flex items-center gap-2">
          <input id="wf-search" type="text" placeholder="Search widgets…" oninput="wfRender()" class="text-xs bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-slate-100 placeholder-slate-500">
          <button onclick="wfLoad()" class="px-3.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-xs font-semibold transition">↻ Refresh</button>
        </div>
      </div>
      <div id="wf-grid" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        <div class="text-xs text-slate-500">Loading widget catalogue…</div>
      </div>
    </div>

    <!-- Display Hardware & Quiet Hours Settings -->
    <div data-tab="playlist" class="grid grid-cols-1 md:grid-cols-2 gap-6">
      
      <!-- Display Driver Settings -->
      <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
        <div>
          <h2 class="font-bold text-base text-slate-100">Display Hardware Settings</h2>
          <p class="text-xs text-slate-400">Configure physical SPI screen drivers and resolution targets</p>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1.5">Driver Backend</label>
            <select id="cfg-driver" onchange="updateHardwareSettings()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:border-orange-500 focus:outline-none">
              <option value="auto">Auto-Detect Display (Recommended)</option>
              <option value="inky">Pimoroni Inky Driver</option>
              <option value="waveshare">Waveshare SPI Driver</option>
              <option value="virtual">Virtual (Browser Preview)</option>
              <option value="framebuffer">Linux Framebuffer (/dev/fb0)</option>
            </select>
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1.5">Display Panel Model</label>
            <select id="cfg-model" onchange="updateHardwareSettings()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:border-orange-500 focus:outline-none">
              <option value="impression_7_3">7.3" Inky Impression (800×480)</option>
              <option value="impression_5_7">5.7" Inky Impression (600×448)</option>
              <option value="impression_4_0">4.0" Inky Impression (640×400)</option>
              <option value="epd7in3f">7.3" 7-Color Waveshare (800×480)</option>
              <option value="epd5in65f">5.65" 7-Color Waveshare (600×448)</option>
              <option value="epd7in5_HD">7.5" HD Waveshare (880×528)</option>
              <option value="epd13in3k">13.3" Spectra 6 (1600×1200)</option>
              <option value="epd2in13_V4">2.13" Waveshare Hat (250×122)</option>
            </select>
          </div>
          <div class="sm:col-span-2">
            <label class="block text-xs font-semibold text-slate-400 mb-1.5">Orientation</label>
            <select id="cfg-orient" onchange="updateHardwareSettings()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:border-orange-500 focus:outline-none">
              <option value="0">Landscape (0°)</option>
              <option value="90">Portrait (90°)</option>
              <option value="180">Landscape Inverted (180°)</option>
              <option value="270">Portrait Inverted (270°)</option>
            </select>
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1.5">Color Saturation (Inky)</label>
            <input type="number" id="cfg-saturation" min="0.1" max="1" step="0.05" value="0.5" onchange="updateHardwareSettings()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:border-orange-500 focus:outline-none" />
            <p class="text-[10px] text-slate-500 mt-1">0.1–1.0; higher = more intense color on Inky panels</p>
          </div>
        </div>
      </div>

      <!-- Quiet Hours & System Settings -->
      <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
        <div>
          <h2 class="font-bold text-base text-slate-100">Quiet Hours & Timezone</h2>
          <p class="text-xs text-slate-400">Suspend physical e-Paper refreshes during sleeping hours</p>
        </div>

        <div class="space-y-4">
          <div class="flex items-center justify-between p-3 rounded-xl bg-slate-950/60 border border-slate-800">
            <div>
              <div class="text-xs font-bold text-slate-200">Enable Night Quiet Mode</div>
              <div class="text-[11px] text-slate-400">Puts display to sleep and prevents night flashing</div>
            </div>
            <input type="checkbox" id="cfg-qh-enabled" onchange="updateQuietHoursSettings()" class="w-4 h-4 accent-orange-600 rounded cursor-pointer" />
          </div>

          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="block text-xs font-semibold text-slate-400 mb-1.5">Start Time (Sleep)</label>
              <input type="time" id="cfg-qh-start" value="23:00" onchange="updateQuietHoursSettings()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:border-orange-500 focus:outline-none" />
            </div>
            <div>
              <label class="block text-xs font-semibold text-slate-400 mb-1.5">End Time (Wake)</label>
              <input type="time" id="cfg-qh-end" value="06:00" onchange="updateQuietHoursSettings()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:border-orange-500 focus:outline-none" />
            </div>
          </div>

          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1.5">Device Timezone</label>
            <select id="cfg-timezone" onchange="updateDeviceSettings()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:border-orange-500 focus:outline-none"></select>
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1.5">Device Name</label>
            <input type="text" id="cfg-device-name" value="rndrSBC Node" placeholder="e.g. Kitchen Display" onchange="updateDeviceSettings()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:border-orange-500 focus:outline-none" />
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1.5">Display Language</label>
            <select id="cfg-language" onchange="updateDeviceSettings()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:border-orange-500 focus:outline-none">
              <option value="en">English</option>
              <option value="es">Español</option>
              <option value="fr">Français</option>
              <option value="de">Deutsch</option>
              <option value="it">Italiano</option>
              <option value="pt">Português</option>
              <option value="nl">Nederlands</option>
              <option value="tr">Türkçe</option>
            </select>
          </div>
          <div class="col-span-2">
            <label class="block text-xs font-semibold text-slate-400 mb-1.5">Physical Buttons (GPIO)</label>
            <div class="grid grid-cols-3 gap-2 text-[10px] text-slate-500">
              <div class="bg-slate-900/40 border border-slate-800 rounded-lg px-2 py-1.5">NEXT <span class="text-slate-400">pin 5</span></div>
              <div class="bg-slate-900/40 border border-slate-800 rounded-lg px-2 py-1.5">PREV <span class="text-slate-400">pin 6</span></div>
              <div class="bg-slate-900/40 border border-slate-800 rounded-lg px-2 py-1.5">TOGGLE QUIET <span class="text-slate-400">pin 12</span></div>
            </div>
            <p class="text-[10px] text-slate-500 mt-1.5">Wire buttons to GND; cycle widgets, go back, or pause/restart refreshes. Disabled automatically on simulator/laptop (no GPIO).</p>
          </div>
        </div>
      </div>

      <div class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-5 space-y-3">
        <div class="text-sm font-bold text-slate-200 flex items-center gap-2">🔄 Display Transitions</div>
        <div>
          <label class="block text-xs font-semibold text-slate-400 mb-1.5">Widget Transition Style</label>
          <select id="cfg-transition" onchange="updateDeviceSettings()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:border-orange-500 focus:outline-none">
            <option value="cut">Cut (Instant)</option>
            <option value="wipe_horizontal">Wipe Horizontal</option>
            <option value="wipe_vertical">Wipe Vertical</option>
            <option value="invert_flash">Invert Flash (Anti-Ghosting)</option>
            <option value="cross_fade">Cross Fade</option>
          </select>
        </div>
        <div>
          <label class="block text-xs font-semibold text-slate-400 mb-1.5">Panel Refresh Mode</label>
          <select id="cfg-refresh-mode" onchange="updateDeviceSettings()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:border-orange-500 focus:outline-none">
            <option value="auto">Auto — partial when panel supports it (recommended)</option>
            <option value="full">Full refresh only (opt-out, max quality/contrast)</option>
          </select>
          <p class="text-[10px] text-slate-500 mt-1.5">Auto: B/W e-paper, LCD &amp; OLED partial-refresh automatically; 7-color/BWR panels always full-refresh. Full: forces full frame every refresh.</p>
        </div>
      </div>

      <!-- Admin Security & Password Settings -->
      <div data-tab="backup" class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
        <div>
          <h2 class="font-bold text-base text-slate-100">🔒 Admin Security & Password</h2>
          <p class="text-xs text-slate-400">Update your dashboard administrator password</p>
        </div>

        <div class="space-y-3">
          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1">Current Password</label>
            <input type="password" id="pwd-current" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:border-orange-500 focus:outline-none" placeholder="••••••••" />
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label class="block text-xs font-semibold text-slate-400 mb-1">New Password (min 8 chars)</label>
              <input type="password" id="pwd-new" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:border-orange-500 focus:outline-none" placeholder="••••••••" />
            </div>
            <div>
              <label class="block text-xs font-semibold text-slate-400 mb-1">Confirm New Password</label>
              <input type="password" id="pwd-new-confirm" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:border-orange-500 focus:outline-none" placeholder="••••••••" />
            </div>
          </div>
          <div id="pwd-feedback" class="hidden text-xs font-medium"></div>
          <button id="btn-save-pwd" onclick="updatePassword()" class="px-4 py-2 rounded-lg bg-orange-600 hover:bg-orange-500 font-semibold text-xs text-white shadow-md shadow-orange-600/30 transition">
            Update Admin Password
          </button>
        </div>
      </div>

    </div>

  </main>

  <!-- First-Run Admin Setup Modal -->
  <div id="modal-setup" class="hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-700 rounded-2xl max-w-md w-full p-6 shadow-2xl space-y-4">
      <div class="w-10 h-10 rounded-xl bg-orange-600 flex items-center justify-center font-bold text-white text-lg shadow-lg shadow-orange-600/30">
        🔒
      </div>
      <div>
        <h3 class="font-bold text-lg text-slate-100">Welcome to rndrSBC</h3>
        <p class="text-xs text-slate-400">Please establish an administrator password to secure your display management portal.</p>
      </div>

      <div class="space-y-3">
        <div>
          <label class="block text-xs font-semibold text-slate-400 mb-1">Admin Password (min. 8 characters)</label>
          <input type="password" id="setup-pwd" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:border-orange-500 focus:outline-none" placeholder="••••••••" />
        </div>
        <div>
          <label class="block text-xs font-semibold text-slate-400 mb-1">Confirm Password</label>
          <input type="password" id="setup-pwd-confirm" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:border-orange-500 focus:outline-none" placeholder="••••••••" />
        </div>
        <div id="setup-err" class="hidden text-xs text-rose-400 font-medium"></div>
      </div>

      <button id="btn-setup" onclick="submitSetup()" class="w-full py-2.5 rounded-lg bg-orange-600 hover:bg-orange-500 font-semibold text-sm text-white shadow-lg shadow-orange-600/30 transition">
        Complete Initial Setup & Secure Device
      </button>
    </div>
  </div>

  <!-- Admin Login Modal -->
  <div id="modal-login" class="hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-700 rounded-2xl max-w-sm w-full p-6 shadow-2xl space-y-4">
      <div class="flex items-center justify-between">
        <h3 class="font-bold text-base text-slate-100">Admin Authentication</h3>
        <button onclick="hideLoginModal()" class="text-slate-500 hover:text-slate-300 text-sm">✕</button>
      </div>
      <div>
        <label class="block text-xs font-semibold text-slate-400 mb-1">Password</label>
        <input type="password" id="login-pwd" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:border-orange-500 focus:outline-none" placeholder="••••••••" />
      </div>
      <div id="login-err" class="hidden text-xs text-rose-400 font-medium"></div>
      <button onclick="submitLogin()" class="w-full py-2 rounded-lg bg-orange-600 hover:bg-orange-500 font-semibold text-sm text-white shadow-lg shadow-orange-600/30 transition">
        Authenticate
      </button>
    </div>
  </div>

  <script>
    let currentConfig = null;
    let selectedPlaylistKey = "main";
    let isAuthenticated = false;
    let setupRequired = false;
    const weatherMaps = {};

    // Pull the one-time claim token out of the URL and POST it to
    // /api/onboarding/claim so the device is actually marked claimed. The
    // claim URL only carries the token in the fragment/query (browsers never
    // send either to the server), so we have to consume it client-side.
    async function consumeClaimFromUrl() {
      let token = null;
      const match = (window.location.search || window.location.hash).match(/[?&]claim=([^&]+)/);
      if (match) token = decodeURIComponent(match[1]);
      if (!token) return false;
      try {
        const res = await fetch('/api/onboarding/claim', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({token})
        });
        if (res.ok) {
          // Claimed. Drop the token from the URL so a refresh/re-share does not
          // attempt to re-consume an already-used token.
          history.replaceState({}, '', window.location.pathname + window.location.search.replace(/[?&]claim=[^&]+/, ''));
          return true;
        }
      } catch (e) { /* network error; fall through to normal setup flow */ }
      return false;
    }

    async function checkAuthStatus() {
      try {
        const res = await fetch('/api/auth/status');
        const data = await res.json();
        if (data.setup_required) {
          setupRequired = true;
          document.getElementById('modal-setup').classList.remove('hidden');
          return false;
        }
        setupRequired = false;
        isAuthenticated = data.authenticated;
        if (isAuthenticated) {
          document.getElementById('btn-logout').classList.remove('hidden');
          document.getElementById('btn-login').classList.add('hidden');
        } else {
          document.getElementById('btn-logout').classList.add('hidden');
          document.getElementById('btn-login').classList.remove('hidden');
        }
        return isAuthenticated;
      } catch (e) {
        return false;
      }
    }

    // Generic "saving…" animation: disables the button and swaps its
    // content for a spinner + busyLabel while awaitable runs, then restores
    // the original label. Original child nodes are saved so the exact markup
    // (icons, etc.) comes back unchanged.
    async function spinButton(btn, awaitable, busyLabel) {
      if (!btn) return await awaitable;
      const original = [...btn.childNodes];
      const busyText = (busyLabel || 'Saving') + '…';
      btn.disabled = true;
      btn.classList.add('btn-busy');
      btn.innerHTML = '<span class="btn-spinner"></span><span>' + busyText + '</span>';
      try {
        return await awaitable;
      } finally {
        btn.disabled = false;
        btn.classList.remove('btn-busy');
        btn.replaceChildren(...original);
      }
    }

    async function submitSetup() {
      const p1 = document.getElementById('setup-pwd').value;
      const p2 = document.getElementById('setup-pwd-confirm').value;
      const err = document.getElementById('setup-err');

      if (p1.length < 8) {
        err.textContent = "Password must be at least 8 characters long.";
        err.classList.remove('hidden');
        return;
      }
      if (p1 !== p2) {
        err.textContent = "Passwords do not match.";
        err.classList.remove('hidden');
        return;
      }

      const res = await spinButton(document.getElementById('btn-setup'),
        fetch('/api/setup', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({password: p1})
        }));

      if (res.ok) {
        document.getElementById('modal-setup').classList.add('hidden');
        // The setup password is now the admin password; log straight in so the
        // freshly-authenticated session can load the dashboard without a second
        // login prompt.
        const loginRes = await fetch('/api/auth/login', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({password: p1})
        });
        hideLoginModal();
        await checkAuthStatus();
        await loadStatus();
      } else {
        const data = await res.json();
        err.textContent = data.error || "Setup failed.";
        err.classList.remove('hidden');
      }
    }

    function showLoginModal() { document.getElementById('modal-login').classList.remove('hidden'); }
    function hideLoginModal() { document.getElementById('modal-login').classList.add('hidden'); }

    async function loadOnboardingState(idx) {
      try {
        const res = await fetch('/api/onboarding/status');
        const st = await res.json();
        const el = document.getElementById(`onboarding-token-${idx}`);
        if (el) el.textContent = st.token ? st.token.slice(0, 12) + '…' : '(generate on display refresh)';
      } catch (e) {}
    }

    async function submitLogin() {
      const pwd = document.getElementById('login-pwd').value;
      const err = document.getElementById('login-err');
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({password: pwd})
      });

      if (res.ok) {
        hideLoginModal();
        await checkAuthStatus();
        await loadStatus();
        // Re-fetch the auth-gated panels now that we have a session.
        await Promise.allSettled([loadTelemetry(), loadPhotos(), checkUpdate()]);
      } else {
        err.textContent = "Invalid administrator password.";
        err.classList.remove('hidden');
      }
    }

    async function logout() {
      await fetch('/api/auth/logout', {method: 'POST'});
      checkAuthStatus();
    }

    async function updatePassword() {
      const cur = document.getElementById('pwd-current').value;
      const p1 = document.getElementById('pwd-new').value;
      const p2 = document.getElementById('pwd-new-confirm').value;
      const fb = document.getElementById('pwd-feedback');

      if (!p1 || p1.length < 8) {
        fb.textContent = "New password must be at least 8 characters long.";
        fb.className = "text-xs text-rose-400 font-medium";
        fb.classList.remove('hidden');
        return;
      }
      if (p1 !== p2) {
        fb.textContent = "New passwords do not match.";
        fb.className = "text-xs text-rose-400 font-medium";
        fb.classList.remove('hidden');
        return;
      }

      try {
        const res = await spinButton(document.getElementById('btn-save-pwd'),
          fetch('/api/auth/password', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({current_password: cur, new_password: p1})
          }),
          'Saving');
        const data = await res.json();
        if (res.ok) {
          fb.textContent = "Admin password updated successfully!";
          fb.className = "text-xs text-emerald-400 font-medium";
          fb.classList.remove('hidden');
          document.getElementById('pwd-current').value = '';
          document.getElementById('pwd-new').value = '';
          document.getElementById('pwd-new-confirm').value = '';
          checkAuthStatus();
        } else {
          fb.textContent = data.error || "Failed to update password.";
          fb.className = "text-xs text-rose-400 font-medium";
          fb.classList.remove('hidden');
        }
      } catch (e) {
        fb.textContent = "Network error updating password.";
        fb.className = "text-xs text-rose-400 font-medium";
        fb.classList.remove('hidden');
      }
    }

    async function loadStatus() {
      try {
        const res = await fetch('/api/config');
        currentConfig = await res.json();
        
        if (!currentConfig.playlists) {
          const oldItems = currentConfig.playlist || [];
          currentConfig.playlists = {
            "main": { name: "Main Rotation", items: oldItems }
          };
          currentConfig.active_playlist = "main";
        }

        selectedPlaylistKey = currentConfig.active_playlist || Object.keys(currentConfig.playlists)[0] || "main";

        if (currentConfig.display) {
          document.getElementById('cfg-driver').value = currentConfig.display.driver || 'virtual';
          document.getElementById('cfg-model').value = currentConfig.display.model || 'epd7in3f';
          document.getElementById('cfg-orient').value = currentConfig.display.orientation || 0;
          document.getElementById('cfg-saturation').value = currentConfig.display.saturation ?? 0.5;
        }

        if (currentConfig.quiet_hours) {
          document.getElementById('cfg-qh-enabled').checked = !!currentConfig.quiet_hours.enabled;
          document.getElementById('cfg-qh-start').value = currentConfig.quiet_hours.start || '23:00';
          document.getElementById('cfg-qh-end').value = currentConfig.quiet_hours.end || '06:00';
        }

        if (currentConfig.device) {
          document.getElementById('cfg-timezone').value = currentConfig.device.timezone || 'America/New_York';
          document.getElementById('cfg-device-name').value = currentConfig.device.name || 'rndrSBC Node';
        }
        document.getElementById('cfg-transition').value = currentConfig.transition || 'cut';
        document.getElementById('cfg-language').value = (currentConfig.language || 'en').split('-')[0];
        document.getElementById('cfg-refresh-mode').value = currentConfig.refresh_mode || 'auto';

        renderPlaylistTabs();
        renderPlaylist();
      } catch (err) {
        console.error("Failed to load config:", err);
      }
    }

    function buildTimezoneSelect() {
      // Comprehensive IANA timezone list, grouped by geographic region, rendered
      // as a true native <select> drop-down (not a free-text field).
      const zones = [
        ['Africa', ['Abidjan','Accra','Addis_Ababa','Algiers','Cairo','Cape_Town','Casablanca','Johannesburg','Kampala','Khartoum','Lagos','Nairobi','Tripoli']],
        ['America', ['Anchorage','Argentina/Buenos_Aires','Asuncion','Atikokan','Bogota','Caracas','Chicago','Costa_Rica','Denver','Detroit','El_Salvador','Guatemala','Halifax','Indianapolis','Juneau','La_Paz','Lima','Los_Angeles','Managua','Mexico_City','Monterrey','Montreal','New_York','Phoenix','Port-au-Prince','Regina','Santiago','Sao_Paulo','Tijuana','Toronto','Vancouver','Winnipeg']],
        ['Asia', ['Almaty','Amman','Ashgabat','Baghdad','Baku','Bangkok','Beirut','Dhaka','Dubai','Hong_Kong','Irkutsk','Jakarta','Jerusalem','Kabul','Karachi','Kathmandu','Kolkata','Krasnoyarsk','Kuala_Lumpur','Manila','Muscat','Riyadh','Seoul','Shanghai','Singapore','Taipei','Tashkent','Tehran','Tel_Aviv','Tokyo','Ulaanbaatar']],
        ['Atlantic', ['Azores','Bermuda','Canary','Cape_Verde','Faroe','Madeira']],
        ['Australia', ['Adelaide','Brisbane','Darwin','Hobart','Melbourne','Perth','Sydney']],
        ['Europe', ['Amsterdam','Athens','Belgrade','Berlin','Bern','Brussels','Bucharest','Budapest','Chisinau','Copenhagen','Dublin','Helsinki','Istanbul','Kyiv','Lisbon','Ljubljana','London','Madrid','Minsk','Moscow','Oslo','Paris','Prague','Reykjavik','Rome','Sarajevo','Skopje','Sofia','Stockholm','Tallinn','Vienna','Warsaw','Zagreb','Zurich']],
        ['Indian', ['Chagos','Christmas','Cocos','Maldives','Mauritius','Reunion']],
        ['Pacific', ['Apia','Auckland','Chatham','Chuuk','Easter','Efate','Fakaofo','Fiji','Guadalcanal','Guam','Honolulu','Kiritimati','Majuro','Midway','Noumea','Pago_Pago','Palau','Port_Moresby','Tarawa','Tongatapu','Wake']]
      ];
      const sel = document.getElementById('cfg-timezone');
      sel.innerHTML = '';
      zones.forEach(([group, list]) => {
        const og = document.createElement('optgroup');
        og.label = group;
        list.forEach(z => {
          const o = document.createElement('option');
          o.value = `${group}/${z}`;
          o.textContent = `${group}/${z.replace(/_/g, ' ')}`;
          og.appendChild(o);
        });
        sel.appendChild(og);
      });
      ['UTC','UTC+01:00','UTC+02:00','UTC+03:00','UTC+04:00','UTC+05:00','UTC+05:30','UTC+06:00','UTC+07:00','UTC+08:00','UTC+09:00','UTC+10:00','UTC-01:00','UTC-02:00','UTC-03:00','UTC-04:00','UTC-05:00','UTC-06:00','UTC-07:00','UTC-08:00','UTC-09:00','UTC-10:00'].forEach(z => {
        const o = document.createElement('option');
        o.value = z;
        o.textContent = z;
        sel.appendChild(o);
      });
    }

    // Populate the timezone menu once at page load; its value is filled in by
    // updateConfigUI below when the saved config arrives.
    buildTimezoneSelect();

    function renderPlaylistTabs() {
      const tabsEl = document.getElementById('playlist-tabs');
      tabsEl.innerHTML = '';
      const playlists = currentConfig.playlists || {};
      const activeKey = currentConfig.active_playlist || 'main';

      for (const [key, pl] of Object.entries(playlists)) {
        const isSelected = (key === selectedPlaylistKey);
        const isActive = (key === activeKey);

        const btn = document.createElement('button');
        btn.className = `px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition ${
          isSelected 
            ? 'bg-orange-600 text-white shadow-md shadow-orange-600/20' 
            : 'bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700'
        }`;
        
        btn.innerHTML = `
          <span>${pl.name || key}</span>
          ${isActive ? '<span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>' : ''}
        `;
        btn.onclick = () => selectPlaylistTab(key);
        tabsEl.appendChild(btn);
      }

      const curPl = playlists[selectedPlaylistKey] || { name: selectedPlaylistKey, items: [] };
      document.getElementById('current-tab-name').textContent = curPl.name || selectedPlaylistKey;
      
      const isCurActive = (selectedPlaylistKey === activeKey);
      document.getElementById('active-badge').classList.toggle('hidden', !isCurActive);
      document.getElementById('btn-set-active').classList.toggle('hidden', isCurActive);
      
      const canDelete = Object.keys(playlists).length > 1;
      document.getElementById('btn-del-playlist').classList.toggle('hidden', !canDelete);
    }

    function selectPlaylistTab(key) {
      selectedPlaylistKey = key;
      renderPlaylistTabs();
      renderPlaylist();
    }

    function promptCreatePlaylist() {
      const name = prompt("Enter a name for the new rotation playlist:", "Weekend Schedule");
      if (!name) return;
      const key = "pl_" + Date.now();
      if (!currentConfig.playlists) currentConfig.playlists = {};
      currentConfig.playlists[key] = {
        name: name,
        items: [
          { widget: "weather", duration_minutes: 15, settings: { location: "New York City", latitude: 40.7128, longitude: -74.0060, units: "imperial", frame: "None" } }
        ]
      };
      selectedPlaylistKey = key;
      renderPlaylistTabs();
      renderPlaylist();
    }

    function setActivePlaylist() {
      currentConfig.active_playlist = selectedPlaylistKey;
      renderPlaylistTabs();
    }

    function deleteCurrentPlaylist() {
      const playlists = currentConfig.playlists || {};
      if (Object.keys(playlists).length <= 1) return;
      if (!confirm(`Delete playlist '${playlists[selectedPlaylistKey]?.name || selectedPlaylistKey}'?`)) return;

      delete currentConfig.playlists[selectedPlaylistKey];
      if (currentConfig.active_playlist === selectedPlaylistKey) {
        currentConfig.active_playlist = Object.keys(currentConfig.playlists)[0];
      }
      selectedPlaylistKey = Object.keys(currentConfig.playlists)[0];
      renderPlaylistTabs();
      renderPlaylist();
    }

    function toggleAddDropdown() {
      document.getElementById('add-dropdown-menu').classList.toggle('hidden');
    }

    document.addEventListener('click', (e) => {
      const drop = document.getElementById('add-dropdown-menu');
      if (drop && !e.target.closest('.relative')) {
        drop.classList.add('hidden');
      }
    });

    function addWidgetToPlaylist(widgetType) {
      toggleAddDropdown();
      const pl = currentConfig.playlists[selectedPlaylistKey];
      if (!pl) return;
      if (!pl.items) pl.items = [];

      let newWidget = {
        widget: widgetType,
        duration_minutes: 15,
        settings: { frame: "None" }
      };

      if (widgetType === "weather") {
        newWidget.settings = { location: "New York City", latitude: 40.7128, longitude: -74.0060, units: "imperial", frame: "None", weatherProvider: "OpenMeteo", displayGraph: true, moonPhase: true, graphIconStep: 3, time_format: "12h" };
      } else if (widgetType === "clock") {
        newWidget.settings = { style: "digital", time_format: "12h", show_date: true, frame: "None" };
      } else if (widgetType === "calendar") {
        newWidget.settings = { title: "My Schedule", ics_url: "", first_day_sunday: true, frame: "None" };
      } else if (widgetType === "news") {
        newWidget.settings = { feed_source: "BBC World News", custom_url: "", max_stories: 4, frame: "None" };
      } else if (widgetType === "quotes") {
        newWidget.settings = { category: "Inspirational", custom_quote: "", custom_author: "", frame: "None" };
      } else if (widgetType === "crypto") {
        newWidget.settings = { currency: "USD ($)", coins: "bitcoin,ethereum,solana", frame: "None" };
      } else if (widgetType === "system_stats") {
        newWidget.settings = { hostname: "rndrSBC Node", frame: "None" };
      } else if (widgetType === "onboarding") {
        newWidget.settings = { title: "Let's set up your display", frame: "None" };
      } else if (widgetType === "photo_frame") {
        newWidget.settings = { caption: true, mode: "sequential", scale_mode: "cover", frame: "None" };
      } else if (widgetType === "composite_grid") {
        newWidget.settings = { layout_type: "sidebar_right", gap: 8, zones: {}, frame: "None" };
      } else if (widgetType === "network") {
        newWidget.settings = { frame: "None" };
      }

      pl.items.push(newWidget);
      renderPlaylist();
    }

    function renderPlaylist() {
      for (const k in weatherMaps) {
        if (weatherMaps[k]) {
          try { weatherMaps[k].remove(); } catch(e) {}
          delete weatherMaps[k];
        }
      }

      const container = document.getElementById('playlist-container');
      container.innerHTML = '';
      const pl = currentConfig.playlists[selectedPlaylistKey] || { items: [] };
      const items = pl.items || [];

      document.getElementById('playlist-item-count').textContent = `(${items.length} widget${items.length === 1 ? '' : 's'} configured)`;

      let totalMins = 0;
      items.forEach((item) => { totalMins += parseInt(item.duration_minutes || 15); });
      document.getElementById('total-playlist-duration').textContent = `${totalMins} mins`;

      items.forEach((item, idx) => {
        const card = buildWidgetCard(item, idx, items.length);
        container.appendChild(card);
      });
    }

    // ---- Drag & drop playlist reordering ----
    let _dragFromIdx = null;
    function onWidgetDragStart(ev, idx) {
      _dragFromIdx = idx;
      ev.dataTransfer.effectAllowed = 'move';
      try { ev.dataTransfer.setData('text/plain', String(idx)); } catch (e) {}
      ev.currentTarget.classList.add('opacity-40');
    }
    function onWidgetDragOver(ev, idx) {
      if (_dragFromIdx === null || _dragFromIdx === idx) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = 'move';
    }
    function onWidgetDrop(ev, idx) {
      ev.preventDefault();
      const from = _dragFromIdx;
      _dragFromIdx = null;
      if (from === null || from === idx) return;
      reorderWidget(from, idx);
    }
    function onWidgetDragEnd(ev) {
      ev.currentTarget.classList.remove('opacity-40');
      _dragFromIdx = null;
    }
    function reorderWidget(fromIdx, toIdx) {
      const pl = currentConfig.playlists[selectedPlaylistKey] || { items: [] };
      const items = pl.items || [];
      if (fromIdx < 0 || fromIdx >= items.length || toIdx < 0 || toIdx >= items.length) return;
      const [moved] = items.splice(fromIdx, 1);
      items.splice(toIdx, 0, moved);
      renderPlaylist();
    }

    function buildWidgetCard(item, idx, total) {
      const card = document.createElement('div');
      card.className = "bg-slate-950/70 border border-slate-800 rounded-xl p-4 transition space-y-3";

      let settingsFieldsHTML = '';
      const s = item.settings || {};

      if (item.widget === 'weather') {
        const lat = s.latitude || 40.7128;
        const lon = s.longitude || -74.0060;
        const locName = s.location || 'New York City';
        const titleOverride = s.title || '';
        const isOWM = s.weatherProvider === 'OpenWeatherMap';

        settingsFieldsHTML = `
          <div class="space-y-3">
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Search City / Location</label>
              <div class="relative">
                <input type="text" id="loc-search-${idx}" placeholder="Type city or landmark to search..." oninput="searchLocation(${idx}, this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
                <div id="search-results-${idx}" class="hidden absolute left-0 right-0 top-full mt-1 bg-slate-900 border border-slate-700 rounded-lg shadow-xl max-h-48 overflow-y-auto z-50 divide-y divide-slate-800 text-xs"></div>
              </div>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Location (resolved)</label>
                <input type="text" id="w-loc-${idx}" value="${locName}" readonly class="w-full bg-slate-900 border border-slate-700/60 rounded-lg px-3 py-1.5 text-xs text-slate-400 cursor-not-allowed select-all" />
              </div>
              <div>
                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Title Override (optional)</label>
                <input type="text" value="${titleOverride}" placeholder="Overrides location header on display" onchange="updateSetting(${idx}, 'title', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
              </div>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div>
                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Latitude</label>
                <input type="number" step="0.0001" id="w-lat-${idx}" value="${lat}" onchange="updateSettingFromMap(${idx}, 'latitude', parseFloat(this.value))" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
              </div>
              <div>
                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Longitude</label>
                <input type="number" step="0.0001" id="w-lon-${idx}" value="${lon}" onchange="updateSettingFromMap(${idx}, 'longitude', parseFloat(this.value))" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
              </div>
              <div>
                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Units</label>
                <select onchange="updateSetting(${idx}, 'units', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                  <option value="imperial" ${s.units === 'imperial' ? 'selected' : ''}>Imperial (°F, mph)</option>
                  <option value="metric" ${s.units === 'metric' ? 'selected' : ''}>Metric (°C, m/s)</option>
                  <option value="standard" ${s.units === 'standard' ? 'selected' : ''}>Standard (Kelvin)</option>
                </select>
              </div>
            </div>

            <!-- Provider & Options -->
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1">
              <div>
                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Weather Provider</label>
                <select onchange="updateSetting(${idx}, 'weatherProvider', this.value); toggleWeatherKey(${idx}, this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                  <option value="OpenMeteo" ${s.weatherProvider !== 'OpenWeatherMap' ? 'selected' : ''}>Open-Meteo (Zero Key)</option>
                  <option value="OpenWeatherMap" ${s.weatherProvider === 'OpenWeatherMap' ? 'selected' : ''}>OpenWeatherMap (API Key)</option>
                </select>
              </div>
              <div>
                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Time Format</label>
                <select onchange="updateSetting(${idx}, 'time_format', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                  <option value="12h" ${s.time_format !== '24h' ? 'selected' : ''}>12-Hour (AM/PM)</option>
                  <option value="24h" ${s.time_format === '24h' ? 'selected' : ''}>24-Hour</option>
                </select>
              </div>
              <div id="w-key-box-${idx}" class="${isOWM ? '' : 'hidden'}">
                <label class="block text-[11px] font-semibold text-slate-400 mb-1">OWM API Key</label>
                <input type="password" value="${s.api_key || ''}" placeholder="32-char API Key" onchange="updateSetting(${idx}, 'api_key', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
              </div>
            </div>

            <!-- Display options grid (full InkyPi parity) -->
            <div class="grid grid-cols-2 sm:grid-cols-3 gap-3 pt-1">
              <label class="flex items-center space-x-2 text-xs text-slate-300 cursor-pointer">
                <input type="checkbox" ${s.displayGraph !== false ? 'checked' : ''} onchange="updateSetting(${idx}, 'displayGraph', this.checked)" class="rounded accent-orange-600" />
                <span>Hourly Graph</span>
              </label>
              <label class="flex items-center space-x-2 text-xs text-slate-300 cursor-pointer">
                <input type="checkbox" ${s.displayRefreshTime !== false ? 'checked' : ''} onchange="updateSetting(${idx}, 'displayRefreshTime', this.checked)" class="rounded accent-orange-600" />
                <span>Refresh Time</span>
              </label>
              <label class="flex items-center space-x-2 text-xs text-slate-300 cursor-pointer">
                <input type="checkbox" ${s.displayMetrics !== false ? 'checked' : ''} onchange="updateSetting(${idx}, 'displayMetrics', this.checked)" class="rounded accent-orange-600" />
                <span>Metrics (Humidity/Wind)</span>
              </label>
              <label class="flex items-center space-x-2 text-xs text-slate-300 cursor-pointer">
                <input type="checkbox" ${s.moonPhase ? 'checked' : ''} onchange="updateSetting(${idx}, 'moonPhase', this.checked)" class="rounded accent-orange-600" />
                <span>Moon Phase</span>
              </label>
              <label class="flex items-center space-x-2 text-xs text-slate-300 cursor-pointer">
                <input type="checkbox" ${s.displayRain ? 'checked' : ''} onchange="updateSetting(${idx}, 'displayRain', this.checked)" class="rounded accent-orange-600" />
                <span>Rain Bars</span>
              </label>
              <label class="flex items-center space-x-2 text-xs text-slate-300 cursor-pointer">
                <input type="checkbox" ${s.displayGraphIcons ? 'checked' : ''} onchange="updateSetting(${idx}, 'displayGraphIcons', this.checked)" class="rounded accent-orange-600" />
                <span>Graph Hourly Icons</span>
              </label>
              <label class="flex items-center space-x-2 text-xs text-slate-300 cursor-pointer">
                <input type="checkbox" ${s.displayForecast !== false ? 'checked' : ''} onchange="updateSetting(${idx}, 'displayForecast', this.checked)" class="rounded accent-orange-600" />
                <span>7-Day Forecast</span>
              </label>
            </div>

            <!-- Advanced: forecast days, icon step, title source, timezone -->
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-1">
              <div>
                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Forecast Days</label>
                <select onchange="updateSetting(${idx}, 'forecastDays', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                  <option value="3" ${s.forecastDays == '3' ? 'selected' : ''}>3 Days</option>
                  <option value="5" ${s.forecastDays == '5' ? 'selected' : ''}>5 Days</option>
                  <option value="7" ${s.forecastDays == '7' || !s.forecastDays ? 'selected' : ''}>7 Days</option>
                </select>
              </div>
              <div>
                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Icon Step (hours)</label>
                <select onchange="updateSetting(${idx}, 'graphIconStep', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                  ${['1','2','4','6','12'].map(v => `<option value="${v}" ${String(s.graphIconStep||6)===v ? 'selected' : ''}>Every ${v} hr${v>1?'s':''}</option>`).join('')}
                </select>
              </div>
              <div>
                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Title Source</label>
                <select onchange="updateSetting(${idx}, 'titleSelection', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                  <option value="location" ${s.titleSelection !== 'custom' ? 'selected' : ''}>Resolve Location</option>
                  <option value="custom" ${s.titleSelection === 'custom' ? 'selected' : ''}>Custom Title</option>
                </select>
              </div>
              <div>
                <label class="block text-[11px] font-semibold text-slate-400 mb-1">Time Zone</label>
                <select onchange="updateSetting(${idx}, 'weatherTimeZone', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                  <option value="locationTimeZone" ${s.weatherTimeZone !== 'localTimeZone' ? 'selected' : ''}>Location Time Zone</option>
                  <option value="localTimeZone" ${s.weatherTimeZone === 'localTimeZone' ? 'selected' : ''}>Device (Local)</option>
                </select>
              </div>
            </div>

            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Custom Title (Set Title Source to "Custom Title")</label>
              <input type="text" value="${s.customTitle || ''}" placeholder="e.g. Home Forecourt, Grand Central" onchange="updateSetting(${idx}, 'customTitle', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
            </div>

            <!-- Leaflet Interactive Map -->
            <div>
              <div class="flex items-center justify-between mb-1">
                <label class="text-[11px] font-semibold text-slate-400">Map Pin (Drag marker to update coordinates)</label>
              </div>
              <div id="wmap-${idx}" class="w-full h-36 rounded-lg border border-slate-700 overflow-hidden bg-slate-900 z-0"></div>
            </div>
          </div>
        `;
        setTimeout(() => initWeatherMap(idx, lat, lon), 0);
      } else if (item.widget === 'calendar') {
        settingsFieldsHTML = `
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Calendar Header Title</label>
              <input type="text" value="${s.title || 'My Schedule'}" onchange="updateSetting(${idx}, 'title', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
            </div>
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">First Day of Week</label>
              <select onchange="updateSetting(${idx}, 'first_day_sunday', this.value === 'true')" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                <option value="true" ${s.first_day_sunday !== false ? 'selected' : ''}>Sunday</option>
                <option value="false" ${s.first_day_sunday === false ? 'selected' : ''}>Monday</option>
              </select>
            </div>
            <div class="sm:col-span-2">
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">iCal / Google Calendar URL (.ics)</label>
              <input type="url" value="${s.ics_url || ''}" placeholder="https://calendar.google.com/calendar/ical/.../basic.ics" onchange="updateSetting(${idx}, 'ics_url', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
            </div>
          </div>
        `;
      } else if (item.widget === 'clock') {
        settingsFieldsHTML = `
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Clock Style</label>
              <select onchange="updateSetting(${idx}, 'style', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                <option value="digital" ${s.style !== 'analog' ? 'selected' : ''}>Digital Clock</option>
                <option value="analog" ${s.style === 'analog' ? 'selected' : ''}>Analog Dial</option>
              </select>
            </div>
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Time Format</label>
              <select onchange="updateSetting(${idx}, 'time_format', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                <option value="12h" ${s.time_format !== '24h' ? 'selected' : ''}>12-Hour (AM/PM)</option>
                <option value="24h" ${s.time_format === '24h' ? 'selected' : ''}>24-Hour</option>
              </select>
            </div>
            <div class="flex items-center pt-5">
              <label class="flex items-center space-x-2 text-xs text-slate-300 cursor-pointer">
                <input type="checkbox" ${s.show_date !== false ? 'checked' : ''} onchange="updateSetting(${idx}, 'show_date', this.checked)" class="rounded accent-orange-600" />
                <span>Show Date & Day</span>
              </label>
            </div>
          </div>
        `;
      } else if (item.widget === 'news') {
        settingsFieldsHTML = `
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Feed Source Preset</label>
              <select onchange="updateSetting(${idx}, 'feed_source', this.value); toggleNewsCustom(${idx}, this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                <option value="BBC World News" ${s.feed_source === 'BBC World News' ? 'selected' : ''}>BBC World News</option>
                <option value="Hacker News" ${s.feed_source === 'Hacker News' ? 'selected' : ''}>Hacker News</option>
                <option value="Reuters" ${s.feed_source === 'Reuters' ? 'selected' : ''}>Reuters</option>
                <option value="New York Times" ${s.feed_source === 'New York Times' ? 'selected' : ''}>New York Times</option>
                <option value="Custom URL" ${s.feed_source === 'Custom URL' ? 'selected' : ''}>Custom RSS / Atom URL</option>
              </select>
            </div>
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Max Headlines</label>
              <input type="number" min="1" max="8" value="${s.max_stories || 4}" onchange="updateSetting(${idx}, 'max_stories', parseInt(this.value))" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
            </div>
            <div id="news-custom-${idx}" class="${s.feed_source === 'Custom URL' ? '' : 'hidden'} sm:col-span-3">
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Custom RSS / Atom Feed URL</label>
              <input type="url" value="${s.custom_url || ''}" placeholder="https://example.com/feed.xml" onchange="updateSetting(${idx}, 'custom_url', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
            </div>
          </div>
        `;
      } else if (item.widget === 'quotes') {
        settingsFieldsHTML = `
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Quote Style</label>
              <select onchange="updateSetting(${idx}, 'category', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                <option value="Inspirational" ${s.category === 'Inspirational' ? 'selected' : ''}>Inspirational (ZenQuotes)</option>
                <option value="Philosophy" ${s.category === 'Philosophy' ? 'selected' : ''}>Philosophy & Ideas</option>
                <option value="Minimalist" ${s.category === 'Minimalist' ? 'selected' : ''}>Minimalist Quotes</option>
              </select>
            </div>
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Custom Author (Optional)</label>
              <input type="text" value="${s.custom_author || ''}" placeholder="Overrides attribution" onchange="updateSetting(${idx}, 'custom_author', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
            </div>
            <div class="sm:col-span-2">
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Custom Quote Text (Overrides daily feed)</label>
              <input type="text" value="${s.custom_quote || ''}" placeholder="Leave blank to use daily rotating quotes" onchange="updateSetting(${idx}, 'custom_quote', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
            </div>
          </div>
        `;
      } else if (item.widget === 'crypto') {
        settingsFieldsHTML = `
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Display Currency</label>
              <select onchange="updateSetting(${idx}, 'currency', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                <option value="USD ($)" ${s.currency !== 'EUR (€)' && s.currency !== 'GBP (£)' ? 'selected' : ''}>USD ($)</option>
                <option value="EUR (€)" ${s.currency === 'EUR (€)' ? 'selected' : ''}>EUR (€)</option>
                <option value="GBP (£)" ${s.currency === 'GBP (£)' ? 'selected' : ''}>GBP (£)</option>
              </select>
            </div>
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Tracked Coins (CoinGecko IDs)</label>
              <input type="text" value="${s.coins || 'bitcoin,ethereum,solana'}" placeholder="e.g. bitcoin,ethereum,solana" onchange="updateSetting(${idx}, 'coins', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
            </div>
          </div>
        `;
      } else if (item.widget === 'photo_frame') {
        settingsFieldsHTML = `
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Display Mode</label>
              <select onchange="updateSetting(${idx}, 'mode', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                <option value="sequential" ${s.mode !== 'random' ? 'selected' : ''}>Sequential Rotation</option>
                <option value="random" ${s.mode === 'random' ? 'selected' : ''}>Random / Shuffle</option>
              </select>
            </div>
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Image Fit & Scale</label>
              <select onchange="updateSetting(${idx}, 'scale_mode', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none">
                <option value="cover" ${s.scale_mode !== 'contain' ? 'selected' : ''}>Fill / Cover (No Bars)</option>
                <option value="contain" ${s.scale_mode === 'contain' ? 'selected' : ''}>Fit / Contain (Full Photo)</option>
              </select>
            </div>
            <div class="flex items-center pt-5">
              <label class="flex items-center space-x-2 text-xs text-slate-300 cursor-pointer">
                <input type="checkbox" ${s.caption !== false ? 'checked' : ''} onchange="updateSetting(${idx}, 'caption', this.checked)" class="rounded accent-orange-600" />
                <span>Show Photo Date / Filename</span>
              </label>
            </div>
          </div>
        `;
        settingsFieldsHTML = `
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Device Label / Hostname</label>
              <input type="text" value="${s.hostname || 'rndrSBC Node'}" onchange="updateSetting(${idx}, 'hostname', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
            </div>
          </div>
        `;
      } else if (item.widget === 'onboarding') {
        settingsFieldsHTML = `
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label class="block text-[11px] font-semibold text-slate-400 mb-1">Header Title</label>
              <input type="text" value="${s.title || "Let's set up your display"}" onchange="updateSetting(${idx}, 'title', this.value)" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:border-orange-500 focus:outline-none" />
            </div>
            <div class="sm:col-span-2">
              <div class="text-[11px] text-slate-400 mb-1.5 flex items-center gap-2">
                <span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                Shows the live QR claim-code + Wi-Fi setup instructions on the display
              </div>
              <div class="bg-slate-900 border border-slate-800 rounded-lg p-2.5 text-[11px] text-slate-400">
                Claim token: <code id="onboarding-token-${idx}" class="text-orange-400">—</code>
                <button onclick="loadOnboardingState(${idx})" class="ml-2 text-[10px] px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300">Refresh</button>
              </div>
            </div>
          </div>
        `;
        setTimeout(() => loadOnboardingState(idx), 0);
      }

      card.innerHTML = `
        <div class="flex items-center justify-between cursor-grab" draggable="true" data-idx="${idx}" ondragstart="onWidgetDragStart(event, ${idx})" ondragover="onWidgetDragOver(event, ${idx})" ondrop="onWidgetDrop(event, ${idx})" ondragend="onWidgetDragEnd(event)">
          <div class="flex items-center space-x-2.5">
            <span class="text-slate-500 cursor-grab select-none" title="Drag to reorder">⠿</span>
            <span class="w-6 h-6 rounded bg-slate-800 text-slate-400 font-bold text-xs flex items-center justify-center">${idx + 1}</span>
            <span class="font-bold text-sm text-slate-200">${getWidgetTitle(item.widget)}</span>
          </div>
          <div class="flex items-center space-x-2">
            <label class="text-xs text-slate-400">Duration:</label>
            <input type="number" min="1" max="1440" value="${item.duration_minutes || 15}" onchange="updateDuration(${idx}, parseInt(this.value))" class="w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-100 text-center focus:outline-none" />
            <span class="text-xs text-slate-500">min</span>
            <button onclick="removeWidget(${idx})" class="text-slate-500 hover:text-rose-400 p-1 transition ml-2">✕</button>
          </div>
        </div>
        <div class="pt-2 border-t border-slate-800/60">
          ${settingsFieldsHTML}
        </div>
      `;

      return card;
    }

    function toggleWeatherKey(idx, provider) {
      const el = document.getElementById(`w-key-box-${idx}`);
      if (el) el.classList.toggle('hidden', provider !== 'OpenWeatherMap');
    }

    function getWidgetTitle(type) {
      const map = {
        weather: '☀️ Weather Dashboard',
        clock: '🕒 Clock & Date',
        calendar: '📅 Calendar & Agenda',
        news: '📰 News & RSS Feed',
        quotes: '💡 Daily Quotes & Thoughts',
        crypto: '⚡ Crypto & Markets',
        system_stats: '💻 System Monitor',
        onboarding: '📱 Device Setup & QR Claim',
        photo_frame: '🖼️ Photo Frame',
        composite_grid: '🧩 Multi-Zone Layout Grid',
        network: '📶 Network Diagnostics'
      };
      return map[type] || type;
    }

    function toggleNewsCustom(idx, val) {
      const el = document.getElementById(`news-custom-${idx}`);
      if (el) el.classList.toggle('hidden', val !== 'Custom URL');
    }

    function initWeatherMap(idx, lat, lon) {
      const mapDiv = document.getElementById(`wmap-${idx}`);
      if (!mapDiv || weatherMaps[idx]) return;

      const map = L.map(mapDiv, { zoomControl: false, attributionControl: false }).setView([lat, lon], 10);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18 }).addTo(map);

      const marker = L.marker([lat, lon], { draggable: true }).addTo(map);
      marker.on('dragend', function() {
        const pos = marker.getLatLng();
        updateSettingFromMap(idx, 'latitude', parseFloat(pos.lat.toFixed(4)));
        updateSettingFromMap(idx, 'longitude', parseFloat(pos.lng.toFixed(4)));
      });

      weatherMaps[idx] = map;
    }

    function updateSettingFromMap(idx, field, val) {
      const pl = currentConfig.playlists[selectedPlaylistKey];
      if (!pl || !pl.items[idx]) return;
      if (!pl.items[idx].settings) pl.items[idx].settings = {};
      pl.items[idx].settings[field] = val;

      const latEl = document.getElementById(`w-lat-${idx}`);
      const lonEl = document.getElementById(`w-lon-${idx}`);
      if (latEl && field === 'latitude') latEl.value = val;
      if (lonEl && field === 'longitude') lonEl.value = val;

      const map = weatherMaps[idx];
      if (map) {
        const curLat = pl.items[idx].settings.latitude || 40.7128;
        const curLon = pl.items[idx].settings.longitude || -74.0060;
        map.setView([curLat, curLon], map.getZoom());
      }
    }

    let searchTimeout = null;
    function searchLocation(idx, query) {
      clearTimeout(searchTimeout);
      const resultsEl = document.getElementById(`search-results-${idx}`);
      if (!query || query.length < 2) {
        if (resultsEl) resultsEl.classList.add('hidden');
        return;
      }

      searchTimeout = setTimeout(async () => {
        try {
          const res = await fetch(`/api/geocode?q=${encodeURIComponent(query)}`);
          const results = await res.json();
          if (!results || results.length === 0) {
            resultsEl.innerHTML = '<div class="px-3 py-2 text-slate-400">No matching locations found</div>';
            resultsEl.classList.remove('hidden');
            return;
          }

          resultsEl.innerHTML = results.map(r => `
            <button type="button" onclick="pickLocation(${idx}, '${r.name.replace(/'/g, "\\'")}', ${r.latitude}, ${r.longitude})" class="w-full text-left px-3 py-2 hover:bg-slate-800 text-slate-200 block transition">
              <div class="font-semibold text-white">${r.name}</div>
              <div class="text-[10px] text-slate-400">${r.label}</div>
            </button>
          `).join('');
          resultsEl.classList.remove('hidden');
        } catch (e) {
          console.error("Geocoding failed:", e);
        }
      }, 300);
    }

    function pickLocation(idx, name, lat, lon) {
      const resultsEl = document.getElementById(`search-results-${idx}`);
      if (resultsEl) resultsEl.classList.add('hidden');

      const locInput = document.getElementById(`w-loc-${idx}`);
      if (locInput) locInput.value = name;

      updateSetting(idx, 'location', name);
      updateSettingFromMap(idx, 'latitude', lat);
      updateSettingFromMap(idx, 'longitude', lon);
    }

    function updateSetting(idx, field, val) {
      const pl = currentConfig.playlists[selectedPlaylistKey];
      if (pl && pl.items[idx]) {
        if (!pl.items[idx].settings) pl.items[idx].settings = {};
        pl.items[idx].settings[field] = val;
      }
    }

    function updateDuration(idx, mins) {
      const pl = currentConfig.playlists[selectedPlaylistKey];
      if (pl && pl.items[idx]) {
        pl.items[idx].duration_minutes = Math.max(1, mins || 15);
        renderPlaylist();
      }
    }

    function removeWidget(idx) {
      const pl = currentConfig.playlists[selectedPlaylistKey];
      if (pl && pl.items) {
        pl.items.splice(idx, 1);
        renderPlaylist();
      }
    }

    function updateHardwareSettings() {
      if (!currentConfig.display) currentConfig.display = {};
      currentConfig.display.driver = document.getElementById('cfg-driver').value;
      currentConfig.display.model = document.getElementById('cfg-model').value;
      currentConfig.display.orientation = parseInt(document.getElementById('cfg-orient').value);
      currentConfig.display.saturation = parseFloat(document.getElementById('cfg-saturation').value) || 0.5;
      // Drop stale fixed resolution: the server re-derives width/height from
      // the selected panel model (DISPLAY_MODELS) so the screen size actually
      // changes. Keeping stale width/height would silently win over the model
      // map and freeze the preview at the old resolution.
      delete currentConfig.display.width;
      delete currentConfig.display.height;
    }

    function updateQuietHoursSettings() {
      if (!currentConfig.quiet_hours) currentConfig.quiet_hours = {};
      currentConfig.quiet_hours.enabled = document.getElementById('cfg-qh-enabled').checked;
      currentConfig.quiet_hours.start = document.getElementById('cfg-qh-start').value;
      currentConfig.quiet_hours.end = document.getElementById('cfg-qh-end').value;
      currentConfig.quiet_hours.mode = "suspend";
    }

    function updateDeviceSettings() {
      if (!currentConfig.device) currentConfig.device = {};
      currentConfig.device.timezone = document.getElementById('cfg-timezone').value;
      currentConfig.device.name = document.getElementById('cfg-device-name').value || 'rndrSBC Node';
      currentConfig.transition = document.getElementById('cfg-transition').value || 'cut';
      currentConfig.language = document.getElementById('cfg-language').value || 'en';
      currentConfig.refresh_mode = document.getElementById('cfg-refresh-mode').value || 'auto';
    }

    async function saveAndApply() {
      updateHardwareSettings();
      updateQuietHoursSettings();
      updateDeviceSettings();

      const res = await spinButton(document.getElementById('btn-apply'),
        fetch('/api/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(currentConfig)
        }),
        'Saving');

      if (res.status === 401) {
        showLoginModal();
        return;
      }

      if (res.ok) {
        setTimeout(refreshDisplayNow, 500);
      }
    }

    async function refreshDisplayNow() {
      const res = await spinButton(document.getElementById('btn-refresh'),
        fetch('/api/refresh', { method: 'POST' }),
        'Refreshing');
      if (res.status === 401) {
        showLoginModal();
        return;
      }
      setTimeout(() => {
        const img = document.getElementById('live-screen-img');
        img.src = '/api/screen.png?t=' + Date.now();
        document.getElementById('mirror-timestamp').textContent = 'Refreshed: ' + new Date().toLocaleTimeString();
      }, 1000);
    }

    // Initialize
    (async () => {
      await consumeClaimFromUrl();
      await checkAuthStatus();
      // Gate the management UI behind authentication once an admin password
      // exists: a device that has finished onboarding must not expose its
      // configuration, photo library, or live screen to an unauthenticated
      // visitor. Before setup completes we still load (the setup flow needs it).
      if (!isAuthenticated && !setupRequired) {
        showLoginModal();
        return;
      }
      await loadStatus();
      setInterval(() => {
        const img = document.getElementById('live-screen-img');
        if (img) img.src = '/api/screen.png?t=' + Date.now();
      }, 30000);
    })();
  </script>
  <!-- Device Telemetry, Appliance Administration, OTA Update & Photo Library Panels -->
  <div class="max-w-6xl mx-auto px-4 pb-10 pt-4 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
    <div data-tab="backup" class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4">
      <div class="text-xs font-bold text-slate-200 mb-2 flex items-center gap-2">📡 Device Health</div>
      <div id="telemetry-content" class="text-[11px] text-slate-400 space-y-1">
        <div>Load monitoring…</div>
      </div>
      <button onclick="loadTelemetry()" class="mt-2 text-[10px] px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300">Refresh</button>
    </div>

    <div data-tab="backup" class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4">
      <div class="text-xs font-bold text-slate-200 mb-2 flex items-center gap-2">⚡ System & Power</div>
      <div class="space-y-1.5 pt-1">
        <button onclick="triggerPanelClean()" class="w-full text-left text-[11px] px-2.5 py-1.5 rounded bg-slate-900 hover:bg-slate-800 text-amber-300 border border-amber-500/20 flex items-center justify-between">
          <span>🧹 Clean Panel Cycle</span>
          <span class="text-[9px] text-slate-500">Anti-Ghosting</span>
        </button>
        <button onclick="systemAction('restart')" class="w-full text-left text-[11px] px-2.5 py-1.5 rounded bg-slate-900 hover:bg-slate-800 text-slate-200 border border-slate-800 flex items-center justify-between">
          <span>🔄 Restart rndrSBC</span>
          <span class="text-[9px] text-slate-500">Service</span>
        </button>
        <div class="grid grid-cols-2 gap-1.5 pt-0.5">
          <button onclick="systemAction('reboot')" class="text-center text-[11px] px-2 py-1 rounded bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800">
            Reboot Pi
          </button>
          <button onclick="systemAction('shutdown')" class="text-center text-[11px] px-2 py-1 rounded bg-rose-950/30 hover:bg-rose-900/40 text-rose-300 border border-rose-800/40">
            Power Off
          </button>
        </div>
      </div>
    </div>

    <div data-tab="backup" class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4">
      <div class="text-xs font-bold text-slate-200 mb-2 flex items-center gap-2">💾 Backup & Updates</div>
      <div id="update-content" class="text-[11px] text-slate-400 mb-2">Checking…</div>
      <div class="flex items-center space-x-1.5 mb-2.5">
        <button onclick="checkUpdate()" class="text-[10px] px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300">Check OTA</button>
        <button onclick="applyUpdate()" class="text-[10px] px-2 py-1 rounded bg-orange-600/80 hover:bg-orange-500 text-white">Apply</button>
      </div>
      <div class="pt-2 border-t border-slate-800 flex items-center justify-between">
        <button onclick="exportConfigBackup()" class="text-[10px] px-2 py-1 rounded bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800">Export JSON</button>
        <label class="text-[10px] px-2 py-1 rounded bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800 cursor-pointer">
          Import JSON
          <input type="file" accept=".json" onchange="importConfigBackup(event)" class="hidden" />
        </label>
      </div>
    </div>

    <div data-tab="photos" class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4">
      <div class="text-xs font-bold text-slate-200 mb-2 flex items-center gap-2">🖼️ Photo Library</div>
      <input type="file" id="photo-upload" accept="image/*" class="text-[10px] text-slate-400 mb-2 w-full" />
      <button id="btn-upload-photo" onclick="uploadPhoto()" class="text-[10px] px-2 py-1 rounded bg-pink-600/80 hover:bg-pink-500 text-white">Upload Photo</button>
      <button onclick="loadPhotos()" class="text-[10px] px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300">Refresh</button>
      <div id="photos-content" class="mt-2 text-[11px] text-slate-500"></div>
    </div>
  </div>

  <script>
    async function loadTelemetry() {
      try {
        const r = await fetch('/api/telemetry');
        const t = await r.json();
        const el = document.getElementById('telemetry-content');
        if (!r.ok || t.error) {
          el.innerHTML = `<div class="text-amber-400">Sign in to view device health${t.detail ? ' (HTTP ' + r.status + ': ' + t.detail + ')' : (t.error ? ' (' + t.error + ')' : (r.ok ? '' : ' (HTTP ' + r.status + ')'))}</div>`;
          return;
        }
        const health = t.health === 'healthy' ? 'text-emerald-400' : 'text-rose-400';
        const healthTxt = t.health || 'unknown';
        el.innerHTML =
          `<div>Health: <strong class="${health}">${healthTxt}</strong></div>` +
          `<div>Uptime: ${t.uptime_human || '—'}</div>` +
          `<div>Renders: ${t.render_count ?? '—'} · Errors: ${t.error_count ?? '—'}</div>` +
          `<div>Last render: ${t.last_render_duration_ms != null ? t.last_render_duration_ms + 'ms' : '—'}</div>` +
          (t.last_error ? `<div class="text-rose-400">⚠ ${t.last_error}</div>` : '');
      } catch (e) { document.getElementById('telemetry-content').innerHTML = '<div class="text-amber-400">Monitoring unavailable: ' + (e && e.message ? e.message : 'network error') + '</div>'; }
    }

    async function checkUpdate() {
      try {
        const r = await fetch('/api/update/check');
        const u = await r.json();
        document.getElementById('update-content').innerHTML = u.error
          ? `<div class="text-amber-400">Could not check for updates: ${u.error}</div>`
          : u.update_available
            ? `<div class="text-emerald-400">Update available: v${u.latest_version}</div>`
            : `<div class="text-slate-300">You're on the latest version (v${u.current_version})</div>`;
      } catch (e) { document.getElementById('update-content').innerHTML = '<div>Auth required</div>'; }
    }

    async function applyUpdate() {
      const el = document.getElementById('update-content');
      el.innerHTML = '<div class="text-sky-300">Checking update state…</div>';
      try {
        const r = await fetch('/api/update/apply', { method: 'POST' });
        const resp = await r.json();
        if (resp.status === 'in-progress') {
          el.innerHTML = '<div class="text-amber-400">An update is already applying — wait for it to finish.</div>';
          setTimeout(() => pollApplyStatus(el), 2000);
          return;
        }
        if (resp.error) {
          el.innerHTML = `<div class="text-rose-400">Update failed: ${resp.error}</div>`;
          return;
        }
        el.innerHTML = '<div class="text-sky-300">Applying update (pip install --upgrade rndrsbc)… this can take a few minutes.</div>';
        await pollApplyStatus(el);
      } catch (e) { el.innerHTML = `<div class="text-rose-400">Update failed: ${e.message}</div>`; }
    }

    async function pollApplyStatus(el) {
      for (let i = 0; i < 180; i++) {
        try {
          const s = await fetch('/api/update/apply-status');
          const st = await s.json();
          if (st.status === 'finished') {
            el.innerHTML = st.success
              ? '<div class="text-emerald-400">Update applied! Restarting…</div>'
              : `<div class="text-rose-400">Update failed: ${st.error || 'unknown'}</div>`;
            return;
          }
          el.innerHTML = `<div class="text-sky-300">Applying update… (${i * 2}s elapsed)</div>`;
          await new Promise(res => setTimeout(res, 2000));
        } catch (e) { el.innerHTML = `<div class="text-rose-400">Update failed: ${e.message}</div>`; return; }
      }
      el.innerHTML = '<div class="text-amber-400">Update still running — check again later.</div>';
    }

    async function uploadPhoto() {
      const fileInput = document.getElementById('photo-upload');
      if (!fileInput.files.length) return;
      const fd = new FormData();
      fd.append('file', fileInput.files[0]);
      const r = await spinButton(document.getElementById('btn-upload-photo'),
        fetch('/api/photos/upload', { method: 'POST', body: fd }),
        'Uploading');
      const resp = await r.json();
      document.getElementById('photos-content').innerHTML = `<div class="text-emerald-400">${resp.path || resp.error || 'uploaded'} ${resp.error ? '(failed)' : ''}</div>`;
      fileInput.value = '';
      loadPhotos();
    }

    async function loadPhotos() {
      const el = document.getElementById('photos-content');
      try {
        const r = await fetch('/api/photos');
        const d = await r.json();
        if (!r.ok || d.error) { el.innerHTML = `<div class="text-amber-400">Unable to load photos${d.error ? ': ' + d.error : ''}</div>`; return; }
        const photos = d.photos || [];
        if (!photos.length) { el.innerHTML = '<div class="text-slate-500">No photos yet — upload one above.</div>'; return; }
        el.innerHTML = `<div class="photo-grid grid grid-cols-3 gap-2">` +
          photos.map((p) =>
            `<div class="relative group rounded-lg overflow-hidden border border-slate-800 bg-slate-900">` +
              `<img src="/api/photos/file?path=${encodeURIComponent(p.path || '')}" alt="${(p.name || '').replace(/"/g, '&quot;')}" class="w-full h-24 object-cover" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\'p-2 text-[10px] text-slate-500\'>unavailable</div>'">` +
              `<div class="px-1.5 py-1 text-[9px] text-slate-400 truncate" title="${(p.path || '').replace(/"/g, '&quot;')}">${p.name}${p.album ? ' · ' + p.album : ''}${p.width ? ' · ' + p.width + '×' + p.height : ''}</div>` +
              `<button onclick="deletePhoto('${(p.path || '').replace(/'/g, "\\'")}')" class="absolute top-1 right-1 text-[9px] px-1.5 py-0.5 rounded bg-rose-950/90 text-rose-300 opacity-0 group-hover:opacity-100 hover:bg-rose-900 border border-rose-800">Delete</button>` +
            `</div>`
          ).join('') + `</div>`;
      } catch (e) { el.innerHTML = '<div class="text-amber-400">Photo library unavailable</div>'; }
    }

    async function deletePhoto(path) {
      if (!confirm('Delete this photo?\n' + path)) return;
      const r = await fetch('/api/photos/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: path }) });
      const d = await r.json().catch(() => ({}));
      document.getElementById('photos-content').innerHTML = `<div class="${r.ok ? 'text-emerald-400' : 'text-rose-400'}">${d.message || d.error || 'done'}</div>`;
      loadPhotos();
    }

    function exportConfigBackup() {
      if (!currentConfig) return;
      const blob = new Blob([JSON.stringify(currentConfig, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `rndrsbc-config-backup-${new Date().toISOString().slice(0,10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    }

    function importConfigBackup(event) {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = async (e) => {
        try {
          const imported = JSON.parse(e.target.result);
          if (!imported || typeof imported !== 'object') throw new Error('Invalid JSON');
          if (!confirm('Restore configuration from this backup? All current settings will be replaced.')) return;
          currentConfig = imported;
          await saveAndApply();
          alert('Configuration restored and applied successfully!');
          window.location.reload();
        } catch (err) {
          alert('Failed to import configuration: ' + err.message);
        }
      };
      reader.readAsText(file);
      event.target.value = '';
    }

    async function triggerPanelClean() {
      if (!confirm('Run full anti-ghosting panel clean cycle now?')) return;
      try {
        const res = await fetch('/api/panel/clean', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          alert('Clean cycle scheduled. The display will refresh shortly.');
          setTimeout(refreshDisplayNow, 1500);
        } else {
          alert('Failed: ' + (data.error || 'Unknown error'));
        }
      } catch (e) {
        alert('Network error triggering clean cycle: ' + e.message);
      }
    }

    async function systemAction(action) {
      const labels = { restart: 'Restart rndrSBC background daemon', reboot: 'Reboot Raspberry Pi hardware', shutdown: 'Power off system completely' };
      if (!confirm(`Are you sure you want to ${labels[action] || action}?`)) return;
      try {
        const res = await fetch(`/api/system/${action}`, { method: 'POST' });
        if (res.ok) {
          alert(`Command sent: ${labels[action]}. The system is processing.`);
        } else {
          const data = await res.json().catch(() => ({}));
          alert(`Failed: ${data.error || 'Server error'}`);
        }
      } catch (e) {
        alert('Network error: ' + e.message);
      }
    }

    // ---------- Dev Studio ----------
    let DS_SCHEMAS = {};

    async function devStudioInit() {
      try {
        const res = await fetch('/api/dev-studio/widgets');
        if (!res.ok) return;
        const data = await res.json();
        const sel = document.getElementById('ds-widget');
        if (!sel) return;
        sel.innerHTML = '';
        (data.widgets || []).forEach(w => {
          const o = document.createElement('option');
          o.value = w.name;
          o.textContent = w.name;
          DS_SCHEMAS[w.name] = w.schema || [];
          sel.appendChild(o);
        });
        dsRebuildSettings();
        dsRender();
      } catch (e) { /* quiet */ }
    }

    function devStudioRefresh() {
      DS_SCHEMAS = {};
      devStudioInit();
    }

    // Render a simple settings field from a widget schema entry (best-effort).
    function _dsField(f) {
      const kind = (f.type && f.type.kind) || 'string';
      const id = 'ds_set_' + f.name;
      if (kind === 'boolean') {
        return '<label class="flex items-center justify-between text-xs text-slate-300"><span>' + f.name +
               '</span><input id="' + id + '" type="checkbox" onclick="dsRender()" class="accent-orange-500"></label>';
      }
      if (kind === 'enum' && Array.isArray(f.type.values)) {
        let opts = f.type.values.map(v => '<option value="' + v + '">' + v + '</option>').join('');
        return '<label class="block text-xs text-slate-300"><span class="text-[11px] font-bold text-slate-400 uppercase">' + f.name +
               '</span><select id="' + id + '" onchange="dsRender()" class="mt-1 w-full bg-slate-800 border border-slate-700 rounded-lg px-2 py-1.5 text-sm text-slate-100">' + opts + '</select></label>';
      }
      return '<label class="block text-xs text-slate-300"><span class="text-[11px] font-bold text-slate-400 uppercase">' + f.name +
             '</span><input id="' + id + '" type="text" oninput="dsDebounced()" class="mt-1 w-full bg-slate-800 border border-slate-700 rounded-lg px-2 py-1.5 text-sm text-slate-100" placeholder="' + (f.name) + '"></label>';
    }

    let _dsDebounce = null;
    function dsDebounced() {
      clearTimeout(_dsDebounce);
      _dsDebounce = setTimeout(dsRender, 400);
    }

    function dsRebuildSettings() {
      const sel = document.getElementById('ds-widget');
      const holder = document.getElementById('ds-settings');
      const name = sel ? sel.value : '';
      if (!holder) return;
      holder.innerHTML = '';
      const schema = DS_SCHEMAS[name] || [];
      if (schema.length) {
        const box = document.createElement('div');
        box.className = 'space-y-2 border-t border-slate-800 pt-3 mt-1';
        box.innerHTML = schema.map(_dsField).join('');
        holder.appendChild(box);
      }
    }

    async function dsRender() {
      const st = document.getElementById('ds-status');
      const img = document.getElementById('ds-preview');
      const empty = document.getElementById('ds-preview-empty');
      const sel = document.getElementById('ds-widget');
      if (!sel || !sel.value) return;
      const w = Math.max(16, Math.min(1600, parseInt(document.getElementById('ds-w').value || '800', 10) || 800));
      const h = Math.max(16, Math.min(1600, parseInt(document.getElementById('ds-h').value || '480', 10) || 480));
      const color = document.getElementById('ds-color').value;
      const dither = document.getElementById('ds-dither').value;

      // Collect settings fields.
      const settings = {};
      (DS_SCHEMAS[sel.value] || []).forEach(f => {
        const el = document.getElementById('ds_set_' + f.name);
        if (!el) return;
        if (f.type && f.type.kind === 'boolean') settings[f.name] = el.checked;
        else if (el.value !== undefined && el.value !== '') {
          if (f.type && f.type.kind === 'number') settings[f.name] = Number(el.value);
          else settings[f.name] = el.value;
        }
      });

      if (st) st.textContent = 'Rendering…';
      const params = new URLSearchParams({ w, h, widget: sel.value, color, dither, settings: JSON.stringify(settings) });
      try {
        const res = await fetch('/api/dev-studio/render?' + params.toString());
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          if (st) st.textContent = (d.error || 'Render failed');
          if (img) img.style.display = 'none';
          if (empty) empty.style.display = '';
          return;
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        img.src = url;
        img.style.display = '';
        if (empty) empty.style.display = 'none';
        if (st) st.textContent = w + '×' + h + ' · ' + color;
      } catch (e) {
        if (st) st.textContent = 'Network error';
      }
    }

    // ---------- Section tabs ----------
    const RTABS = ['playlist', 'widgets', 'photos', 'backup'];
    function showTab(tab) {
      RTABS.forEach(t => {
        const show = (t === tab);
        document.querySelectorAll('[data-tab="' + t + '"]').forEach(el => {
          el.style.display = show ? '' : 'none';
        });
        const btn = document.querySelector('[data-rtab="' + t + '"]');
        if (btn) {
          btn.className = 'rtab-btn px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ' +
            (show ? 'bg-orange-600 text-white shadow-lg shadow-orange-600/30' : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800');
        }
      });
      window._activeTab = tab;
    }

    // ---------- Widget Finder catalogue ----------
    let WF_WIDGETS = [];
    async function wfLoad() {
      const grid = document.getElementById('wf-grid');
      if (!grid) return;
      grid.innerHTML = '<div class="text-xs text-slate-500">Loading widget catalogue…</div>';
      try {
        const res = await fetch('/api/dev-studio/widgets');
        if (!res.ok) {
          grid.innerHTML = '<div class="text-xs text-rose-400">Failed to load catalogue</div>';
          return;
        }
        const data = await res.json();
        WF_WIDGETS = data.widgets || [];
        wfRender();
      } catch (e) {
        grid.innerHTML = '<div class="text-xs text-rose-400">Network error</div>';
      }
    }

    function wfRender() {
      const grid = document.getElementById('wf-grid');
      const q = (document.getElementById('wf-search').value || '').trim().toLowerCase();
      if (!grid) return;
      const list = WF_WIDGETS.filter(w => !q || w.name.toLowerCase().includes(q));
      if (!list.length) {
        grid.innerHTML = '<div class="text-xs text-slate-500 col-span-full">No widgets match “' + q + '”.</div>';
        return;
      }
      grid.innerHTML = list.map(w => {
        const fields = (w.schema && w.schema.fields) ? w.schema.fields : [];
        const fieldList = fields.slice(0, 5).map(f =>
          '<span class="inline-block text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">' + f.name +
          '<span class="text-slate-500">:' + (f.type && f.type.kind || '?') + '</span></span>'
        ).join(' ') + (fields.length > 5 ? ' <span class="text-[10px] text-slate-500">+' + (fields.length - 5) + ' more</span>' : '');
        return '<div class="bg-slate-950/60 border border-slate-800 rounded-xl p-4 hover:border-slate-700 transition">' +
          '<div class="flex items-center justify-between mb-1">' +
            '<div class="font-mono text-sm text-slate-100">' + w.name + '</div>' +
            '<button onclick="dsSelectWidget(\'' + w.name + '\')" class="text-[11px] px-2 py-1 rounded bg-orange-600/20 hover:bg-orange-600/40 text-orange-300 border border-orange-600/40 font-semibold transition">Preview</button>' +
          '</div>' +
          '<div class="text-[11px] text-slate-500 mb-2">' + (fields.length ? fields.length + ' settings' : 'no settings') + '</div>' +
          '<div class="flex flex-wrap gap-1">' + fieldList + '</div>' +
        '</div>';
      }).join('');
    }

    function dsSelectWidget(name) {
      const sel = document.getElementById('ds-widget');
      if (sel && sel.querySelector('option[value="' + name + '"]')) {
        sel.value = name;
        dsRebuildSettings();
        showTab('widgets');
        dsRender();
      }
    }

    // Default to the Playlist Config tab; run finder + studio init.
    wfLoad();
    showTab('playlist');

    loadTelemetry();
    loadPhotos();
    checkUpdate();
    devStudioInit();
  </script>
</body>
</html>
"""


class QuietServer(ThreadingHTTPServer):
    """Thread-per-request server: a slow request (OTA pip upgrade, backup export)
    must never block the rest of the dashboard (telemetry, photos, config saves)."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc_type, exc_val, _ = sys.exc_info()
        if issubclass(exc_type, (ConnectionResetError, BrokenPipeError)):
            logger.debug(f"Client {client_address} disconnected mid-request ({exc_type.__name__})")
            return
        super().handle_error(request, client_address)

class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info("http: " + format, *args)

class ProductionHandler(QuietHandler):
    scheduler = None
    config_path = CONFIG_PATH

    def _get_cookie(self, key: str) -> str:
        cookie_header = self.headers.get("Cookie", "")
        if not cookie_header: return ""
        for item in cookie_header.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                if k.strip() == key:
                    return v.strip()
        return ""

    def _is_authenticated(self) -> bool:
        """Verifies session cookie or Bearer token against active sessions."""
        token = self._get_cookie("rndrsbc_session")
        if not token:
            auth_h = self.headers.get("Authorization", "")
            if auth_h.startswith("Bearer "):
                token = auth_h[7:].strip()

        if token and token in ACTIVE_SESSIONS:
            sess = ACTIVE_SESSIONS[token]
            if time.time() - sess.get("created_at", 0) < SESSION_TTL_SECS:
                return True
            else:
                del ACTIVE_SESSIONS[token]
                _save_sessions()
        return False

    def _has_admin_setup(self) -> bool:
        """Returns True if admin_password_hash exists in config.json."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    cfg = json.load(f)
                    return bool(cfg.get("admin_password_hash"))
            except Exception:
                pass
        return False

    def _require_auth(self) -> bool:
        """Require an authenticated session once an admin password exists.
        Returns True when the request is permitted; writes a 401 response and
        returns False when access is denied. Before an admin password is set the
        device is in first-run/setup state, so management endpoints stay open so
        the local onboarding flow can configure the device."""
        if self._has_admin_setup() and not self._is_authenticated():
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b'{"error":"Authentication required"}')
            return False
        return True

    def _send_json(self, status: int, payload: dict) -> None:
        """Send a JSON response with a consistent Content-Type/no-cache header."""
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("Client disconnected before JSON response was fully sent")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        
        # 1. Web Dashboard
        if parsed.path in ["/", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(DASHBOARD_HTML.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))
            return

        # 1b. Health telemetry (protected)
        if parsed.path == "/api/telemetry":
            if not self._require_auth():
                return
            try:
                from core.telemetry import TELEMETRY
                status = TELEMETRY.get_status()
            except Exception as exc:
                logger.exception("Telemetry status call failed")
                self._send_json(500, {"error": "telemetry-unavailable", "detail": str(exc)})
                return
            self._send_json(200, status)
            return

        # 1c. OTA update check (protected)
        if parsed.path == "/api/update/check":
            if not self._require_auth():
                return
            # Single source of truth: the PyPI/pip path (_update.py). GitHub
            # archive OTA is intentionally not used -- pip is the one upgrade
            # mechanism, identical from the web dashboard and the CLI.
            from rndrsbc import _update
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(_update.status(quiet=True)).encode("utf-8"))
            return

        # 1d. Uploaded photos list (protected)
        if parsed.path == "/api/photos":
            if not self._require_auth():
                return
            try:
                from widgets.photo_frame.widget import list_photos
                self._send_json(200, {"photos": list_photos()})
            except Exception as e:
                logger.exception("list_photos failed")
                self._send_json(200, {"photos": [], "error": str(e)})
            return

        # 1d-bis. Serve a photo file for thumbnails/preview (protected)
        if parsed.path == "/api/photos/file" and self.command == "GET":
            if not self._require_auth():
                return
            try:
                from urllib.parse import parse_qs, urlparse as _up
                from widgets.photo_frame.widget import PHOTO_DIR
                import os as _os
                q = parse_qs(_up(self.path).query)
                target = (q.get("path", [""])[0]).strip()
                base = _os.path.realpath(PHOTO_DIR)
                cand = _os.path.realpath(target)
                if not cand.startswith(base + _os.sep) or not _os.path.isfile(cand):
                    self.send_response(404); self.send_header("Content-Type", "application/json"); self.end_headers()
                    self.wfile.write(json.dumps({"error": "Photo not found"}).encode("utf-8")); return
                with open(cand, "rb") as fh:
                    data = fh.read()
                ctype = "image/jpeg" if cand.lower().endswith(".jpg") or cand.lower().endswith(".jpeg") else ("image/png" if cand.lower().endswith(".png") else "image/webp")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_response(500); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # 1e. Delete an uploaded photo (protected)
        if parsed.path == "/api/photos/delete" and self.command == "POST":
            try:
                if not self._is_authenticated():
                    self.send_response(401); self.send_header("Content-Type", "application/json"); self.end_headers()
                    self.wfile.write(b'{"error":"Authentication required"}'); return
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                target = (payload.get("path") or "").strip()
                from widgets.photo_frame.widget import PHOTO_DIR
                import os as _os
                base = _os.path.realpath(PHOTO_DIR)
                cand = _os.path.realpath(target)
                if not cand.startswith(base + _os.sep):
                    self.send_response(400); self.send_header("Content-Type", "application/json"); self.end_headers()
                    self.wfile.write(json.dumps({"error": "Refusing to delete outside photo library"}).encode("utf-8")); return
                if _os.path.isfile(cand):
                    _os.remove(cand)
                    msg = f"Deleted {_os.path.basename(cand)}"
                else:
                    msg = f"Not found: {target}"
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps({"message": msg}).encode("utf-8"))
            except Exception as e:
                self.send_response(500); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # 2. Onboarding status + QR (available pre-auth so the setup flow can start)
        if parsed.path == "/api/onboarding/status":
            state = onboarding_state()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(state).encode("utf-8"))
            return

        if parsed.path == "/api/onboarding/claim-url":
            state = onboarding_state()
            token = state.get("token") or issue_claim_token().get("token")
            url = claim_url_for_token(token)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"url": url, "token": token}).encode("utf-8"))
            return

        if parsed.path == "/api/onboarding/qr.png":
            try:
                import qrcode
                state = onboarding_state()
                token = state.get("token") or issue_claim_token().get("token")
                url = claim_url_for_token(token)
                qr = qrcode.QRCode(box_size=10, border=2)
                qr.add_data(url)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                data = buf.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                self.send_response(500)
                self.end_headers()
            return

        # Auth status endpoint
        if parsed.path == "/api/auth/status":
            setup_req = not self._has_admin_setup()
            auth = self._is_authenticated()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "setup_required": setup_req,
                "authenticated": auth,
                "user": "admin" if auth else None
            }).encode("utf-8"))
            return

        # 3. Read config
        if parsed.path == "/api/config":
            if not self._require_auth():
                return
            if os.path.exists(self.config_path):
                with open(self.config_path, "r") as f:
                    data_obj = json.load(f)
                    # Don't leak the password hash in the config endpoint
                    if "admin_password_hash" in data_obj:
                        data_obj_sanitized = dict(data_obj)
                        del data_obj_sanitized["admin_password_hash"]
                        data = json.dumps(data_obj_sanitized).encode("utf-8")
                    else:
                        data = json.dumps(data_obj).encode("utf-8")
            else:
                data = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)
            return

        # 4. Geocoding proxy
        if parsed.path == "/api/geocode" and parsed.query:
            if not self._require_auth():
                return
            qs = urllib.parse.parse_qs(parsed.query)
            query = (qs.get("q") or [""])[0].strip()
            if not query:
                self.send_response(400); self.end_headers(); return
            try:
                import requests
                gurl = (f"https://geocoding-api.open-meteo.com/v1/search?name="
                        f"{urllib.parse.quote(query)}&count=8&language=en&format=json")
                r = requests.get(gurl, timeout=8)
                r.raise_for_status()
                result = r.json().get("results", [])
                out = [{ "label": f"{x.get('name','')} · {x.get('admin1','')}, {x.get('country','')}".strip(' .,'),
                         "name": x.get("name", ""),
                         "latitude": x.get("latitude"),
                         "longitude": x.get("longitude") } for x in result]
                body = json.dumps(out).encode("utf-8")
            except Exception as e:
                body = json.dumps([]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return

        # 5. Dev Studio widget preview (authenticated). Reuses the same render
        # pipeline as server/dev_studio.py so panel + studio can't drift.
        if parsed.path == "/api/dev-studio/render":
            if not self._require_auth():
                return
            qs = urllib.parse.parse_qs(parsed.query)

            def _clamp_dim(name: str, default: int):
                raw = qs.get(name, [str(default)])[0]
                try:
                    return max(16, min(1600, int(raw)))
                except (TypeError, ValueError):
                    return default

            w = _clamp_dim("w", 800)
            h = _clamp_dim("h", 480)
            widget_name = qs.get("widget", [""])[0]
            color_mode = qs.get("color", ["7color"])[0]
            use_dither = qs.get("dither", ["0"])[0] == "1"

            settings = {}
            for k, v in qs.items():
                if k not in ["w", "h", "widget", "color", "dither", "t", "settings"]:
                    settings[k] = v[0]
            # settings may arrive as a JSON-encoded blob from the panel preview.
            raw_settings = qs.get("settings", [None])[0]
            if raw_settings is not None:
                try:
                    blob = json.loads(raw_settings)
                    if isinstance(blob, dict):
                        settings.update(blob)
                except (ValueError, TypeError):
                    pass

            from server.dev_studio import render_widget_image, WIDGETS
            data, err = render_widget_image(widget_name, w, h,
                                            color_mode=color_mode,
                                            use_dither=use_dither,
                                            settings=settings)
            if data is not None:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
            else:
                if WIDGETS.get(widget_name) is None:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": err}).encode("utf-8"))
                else:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": err}).encode("utf-8"))
            return

        # 5b. Dev Studio widget catalogue (authenticated). Returns every
        # discovered widget + a settings form skeleton for the panel preview.
        if parsed.path == "/api/dev-studio/widgets":
            if not self._require_auth():
                return
            from server.dev_studio import WIDGETS
            out = []
            for name in sorted(WIDGETS.keys()):
                w = WIDGETS[name]
                schema = []
                try:
                    schema = w.get_config_schema() or []
                except Exception:
                    schema = []
                out.append({"name": name, "schema": schema})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(json.dumps({"widgets": out}).encode("utf-8"))
            return

        # 6. Live Screen Mirror
        if parsed.path == "/api/screen.png":
            if not self._require_auth():
                return
            img = None
            # Prefer the COLOR pre-dither frame for the web preview, so the
            # live mirror shows true colors instead of the 1-bit panel frame.
            if self.scheduler and getattr(self.scheduler, "last_preview_image", None):
                img = self.scheduler.last_preview_image
            elif self.scheduler and self.scheduler.last_rendered_image:
                img = self.scheduler.last_rendered_image
            elif os.path.exists(resolve("live_screen.png")):
                img = Image.open(resolve("live_screen.png"))
            elif os.path.exists(resolve("live_weather_full.png")):
                img = Image.open(resolve("live_weather_full.png"))
            else:
                img = Image.new("RGB", (800, 480), "white")

            # Convert to RGB so the preview is always color (never a
            # mode-1 palette-saved image that renders B&W in the browser).
            if img.mode != "RGB":
                img = img.convert("RGB")

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            data = buf.getvalue()

            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(data)
            return

        # 6. Static Asset Serving with Path Traversal Protection
        if parsed.path.startswith("/static/") or parsed.path.startswith("/assets/"):
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets"))
            rel_path = parsed.path.replace("/static/", "").replace("/assets/", "").lstrip("/")
            safe_target = os.path.abspath(os.path.join(base_dir, rel_path))

            # Security check: verify path remains within base_dir
            if os.path.commonpath([base_dir, safe_target]) == base_dir and os.path.isfile(safe_target):
                content_type = "application/octet-stream"
                if safe_target.endswith(".png"): content_type = "image/png"
                elif safe_target.endswith(".jpg"): content_type = "image/jpeg"
                elif safe_target.endswith(".ttf"): content_type = "font/ttf"

                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.end_headers()
                with open(safe_target, "rb") as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_response(404); self.end_headers(); return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length > 0 else b"{}"

        # Global authorization gate. Once an admin password exists on the device,
        # every mutating request must come from an authenticated session EXCEPT the
        # narrow pre-auth flow (first-run setup, login, logout, and the claim-token
        # onboarding sequence). Everything else - config writes, photo uploads, and
        # especially power/update operations - is locked down.
        PRE_AUTH_PATHS = (
            "/api/setup",
            "/api/auth/login",
            "/api/auth/logout",
            "/api/onboarding/claim",
        )
        if parsed.path not in PRE_AUTH_PATHS:
            if not self._require_auth():
                return

        # 1. First-Run Setup (Enforce password >= 8 characters)
        if parsed.path == "/api/setup":
            if self._has_admin_setup():
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"Admin setup has already been completed. Use Settings or CLI \'rndrsbc set-password\' to change password."}')
                return

            try:
                body = json.loads(raw_body.decode("utf-8"))
                pwd = body.get("password", "").strip()
                if len(pwd) < 8:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"Password must be at least 8 characters long."}')
                    return

                # Hash using werkzeug.security
                pwd_hash = generate_password_hash(pwd, method="pbkdf2:sha256")
                
                cfg = {}
                if os.path.exists(self.config_path):
                    with open(self.config_path, "r") as f:
                        cfg = json.load(f)
                
                cfg["admin_password_hash"] = pwd_hash
                with open(self.config_path, "w") as f:
                    json.dump(cfg, f, indent=2)

                # Auto-login after setup
                token = secrets.token_hex(32)
                ACTIVE_SESSIONS[token] = {"created_at": time.time(), "user": "admin"}
                _save_sessions()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", f"rndrsbc_session={token}; Path=/; HttpOnly; SameSite=Lax")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "token": token}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500); self.end_headers(); return

        # 2. Login
        if parsed.path == "/api/auth/login":
            try:
                body = json.loads(raw_body.decode("utf-8"))
                pwd = body.get("password", "")

                cfg = {}
                if os.path.exists(self.config_path):
                    with open(self.config_path, "r") as f:
                        cfg = json.load(f)
                
                pwd_hash = cfg.get("admin_password_hash", "")
                if pwd_hash and check_password_hash(pwd_hash, pwd):
                    token = secrets.token_hex(32)
                    ACTIVE_SESSIONS[token] = {"created_at": time.time(), "user": "admin"}
                    _save_sessions()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Set-Cookie", f"rndrsbc_session={token}; Path=/; HttpOnly; SameSite=Lax")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "ok", "token": token}).encode("utf-8"))
                else:
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"Invalid credentials"}')
                return
            except Exception:
                self.send_response(400); self.end_headers(); return

        # 2b. Password Change / Reset
        if parsed.path == "/api/auth/password":
            try:
                body = json.loads(raw_body.decode("utf-8"))
                current_pwd = body.get("current_password", "")
                new_pwd = body.get("new_password", "").strip()

                if len(new_pwd) < 8:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"New password must be at least 8 characters long."}')
                    return

                cfg = {}
                if os.path.exists(self.config_path):
                    with open(self.config_path, "r") as f:
                        cfg = json.load(f)

                pwd_hash = cfg.get("admin_password_hash", "")

                # If an admin password already exists, require valid session OR current password
                if pwd_hash:
                    if not self._is_authenticated() and not (current_pwd and check_password_hash(pwd_hash, current_pwd)):
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b'{"error":"Current password is required or invalid."}')
                        return

                new_hash = generate_password_hash(new_pwd, method="pbkdf2:sha256")
                cfg["admin_password_hash"] = new_hash
                with open(self.config_path, "w") as f:
                    json.dump(cfg, f, indent=2)

                token = secrets.token_hex(32)
                ACTIVE_SESSIONS[token] = {"created_at": time.time(), "user": "admin"}
                _save_sessions()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", f"rndrsbc_session={token}; Path=/; HttpOnly; SameSite=Lax")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "Password updated successfully."}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        # 3. Logout
        if parsed.path == "/api/auth/logout":
            token = self._get_cookie("rndrsbc_session")
            if token in ACTIVE_SESSIONS:
                del ACTIVE_SESSIONS[token]
                _save_sessions()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", "rndrsbc_session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        # 4. Onboarding actions (usable pre-setup so the first-run flow completes)
        if parsed.path == "/api/onboarding/claim":
            try:
                body = json.loads(raw_body.decode("utf-8"))
                token = body.get("token", "")
                if consume_claim_token(token):
                    state = onboarding_state()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "claimed", **state}).encode("utf-8"))
                else:
                    self.send_response(403)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"Invalid, expired, or already-claimed token"}')
                return
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                return

        # Onboarding state-mutators that can disrupt connectivity are gated once an
        # admin password exists, so they cannot be fired anonymously post-setup.
        # (/api/onboarding/claim stays open: it is bounded by claim-token validity and
        # is required to complete the pre-setup first-run flow.)
        if parsed.path in ("/api/onboarding/ap/start", "/api/onboarding/ap/stop", "/api/onboarding/wifi"):
            if self._has_admin_setup() and not self._is_authenticated():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"Authentication required"}')
                return

        if parsed.path == "/api/onboarding/ap/start":
            ok = onboarding_ap_manager.start_ap(force=True)
            self.send_response(200 if ok else 500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "active" if ok else "failed"}).encode("utf-8"))
            return

        if parsed.path == "/api/onboarding/ap/stop":
            onboarding_ap_manager.stop_ap()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"stopped"}')
            return

        if parsed.path == "/api/onboarding/wifi":
            try:
                body = json.loads(raw_body.decode("utf-8"))
                ssid = (body.get("ssid") or "").strip()
                password = body.get("password", "") or ""
                # Reject anything that could break out of the wpa_supplicant
                # network={ ssid="..." psk="..." } block or carry control chars.
                def _safe_wifi_field(value: str, max_len: int = 63) -> str:
                    if len(value) > max_len:
                        raise ValueError("field too long")
                    if any(ch in value for ch in ('"', "\\", "\n", "\r", "\t", "\x00")):
                        raise ValueError("field contains invalid characters")
                    return value
                ssid = _safe_wifi_field(ssid)
                password = _safe_wifi_field(password)
                if not ssid:
                    self.send_response(400); self.end_headers(); return

                cfg = {}
                if os.path.exists(self.config_path):
                    with open(self.config_path, "r") as f:
                        cfg = json.load(f)

                cfg["wifi"] = {"ssid": ssid, "password": password}
                cfg.setdefault("device", {})["name"] = cfg.get("device", {}).get("name", "rndrSBC Node")

                # Store Wi-Fi credentials to wpa_supplicant (Linux path)
                if os.path.exists("/etc/wpa_supplicant/wpa_supplicant.conf") or os.path.isdir("/etc/wpa_supplicant"):
                    try:
                        wpa_conf = f"ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\nupdate_config=1\ncountry=US\nnetwork={{\n    ssid=\"{ssid}\"\n    psk=\"{password}\"\n    key_mgmt=WPA-PSK\n}}\n"
                        with open("/etc/wpa_supplicant/wpa_supplicant.conf", "w") as f:
                            f.write(wpa_conf)
                    except Exception as e2:
                        logger.warning(f"Could not write wpa_supplicant: {e2}")

                with open(self.config_path, "w") as f:
                    json.dump(cfg, f, indent=2)

                # Stop AP and let the device reconnect to the supplied Wi-Fi
                onboarding_ap_manager.stop_ap()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"saved"}')
                return
            except Exception as e:
                logger.error(f"Wi-Fi provisioning failed: {e}")
                self.send_response(500); self.end_headers(); return

        # 5. Protected Endpoints (Require Auth if admin password is configured)
        if self._has_admin_setup() and not self._is_authenticated():
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"Authentication required"}')
            return

        if parsed.path == "/api/refresh":
            if self.scheduler:
                self.scheduler.trigger_render_now(0, force_hardware=True)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        if parsed.path == "/api/panel/clean":
            try:
                from core.panel_health import get_health
                gov = get_health()
                gov.record_full_refresh()
                if self.scheduler:
                    self.scheduler.trigger_render_now(0, force_hardware=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"clean_cycle_scheduled"}')
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        if parsed.path == "/api/config":
            try:
                cfg_obj = json.loads(raw_body.decode("utf-8"))

                # Normalize display dimensions so the running display can be
                # hot-resized on a panel / screen-size change. Prefer explicit
                # width/height from the request; otherwise derive from the panel model.
                d = cfg_obj.get("display")
                if isinstance(d, dict):
                    w = d.get("width") or d.get("screen_width")
                    h = d.get("height") or d.get("screen_height")
                    model = d.get("model")
                    # Fill missing dims from the authoritative model map.
                    if model:
                        from displays.waveshare import DISPLAY_MODELS as _mods
                        md = _mods.get(model) or {}
                        w = w or md.get("width")
                        h = h or md.get("height")
                        if "color_mode" not in d and md.get("color_mode"):
                            d["color_mode"] = md.get("color_mode")
                    d["width"] = int(w) if w else None
                    d["height"] = int(h) if h else None
                    if not d.get("width") or not d.get("height"):
                        d.pop("width", None)
                        d.pop("height", None)


                # Preserve critical keys that a partial/naive client payload
                # might omit. A dashboard save must never strip the playlist/
                # rotation config off disk, or the next boot hard-crashes with
                # "active_playlist/playlists required missing".
                if os.path.exists(self.config_path):
                    with open(self.config_path, "r") as f:
                        old_cfg = json.load(f)
                        if "admin_password_hash" in old_cfg and "admin_password_hash" not in cfg_obj:
                            cfg_obj["admin_password_hash"] = old_cfg["admin_password_hash"]
                        for _k in ("active_playlist", "playlists", "schema_version"):
                            if _k in old_cfg and _k not in cfg_obj:
                                cfg_obj[_k] = old_cfg[_k]

                with open(self.config_path, "w") as f:
                    json.dump(cfg_obj, f, indent=2)

                if self.scheduler:
                    self.scheduler.update_config(cfg_obj)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"updated"}')
            except Exception as e:
                self.send_response(400)
                self.end_headers()
            return

        # 5. Safe System Command Execution (No shell=True)
        if parsed.path == "/api/system/restart":
            try:
                subprocess.run(["systemctl", "restart", "rndrsbc"], check=False)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"restarting"}')
            except Exception as e:
                self.send_response(500); self.end_headers(); return
            return

        if parsed.path == "/api/system/reboot":
            try:
                subprocess.run(["systemctl", "reboot"], check=False)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"rebooting"}')
            except Exception:
                self.send_response(500); self.end_headers(); return
            return

        if parsed.path == "/api/system/shutdown":
            try:
                subprocess.run(["systemctl", "poweroff"], check=False)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"powering_off"}')
            except Exception:
                self.send_response(500); self.end_headers(); return
            return

        # 6. OTA Self-Update (async: pip upgrade runs in background so a slow
        #    install never blocks the single-dashboard UX; poll apply-status)
        if parsed.path == "/api/update/apply":
            import threading
            if getattr(ProductionHandler, "_apply_thread_active", False):
                self.send_response(409)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "in-progress", "error": "An update is already applying"}).encode("utf-8"))
                return

            def _do_apply():
                # Same pip process as the CLI (`rndrsbc update self`):
                #   python -m pip install --upgrade rndrsbc  then post-upgrade bootstrap.
                try:
                    from rndrsbc import _update
                    rc = _update.apply()
                    result = {"success": rc == 0, "error": None if rc == 0 else "pip upgrade exited %d" % rc}
                except Exception as exc:  # noqa: BLE001
                    result = {"success": False, "error": str(exc)}
                ProductionHandler._apply_result = result
                ProductionHandler._apply_thread_active = False
                ProductionHandler._apply_finished_ts = time.time()

            ProductionHandler._apply_result = None
            ProductionHandler._apply_thread_active = True
            t = threading.Thread(target=_do_apply, daemon=True)
            t.start()
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started"}).encode("utf-8"))
            return

        if parsed.path == "/api/update/apply-status":
            res = getattr(ProductionHandler, "_apply_result", None)
            active = getattr(ProductionHandler, "_apply_thread_active", False)
            if res is not None:
                status = "finished"
                payload = {"status": status, "success": res.get("success"), "error": res.get("error")}
            elif active:
                payload = {"status": "in-progress"}
            else:
                payload = {"status": "idle"}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        # Rollback via pip is intentionally not exposed: pip only upgrades to the
        # latest release, so there is no GitHub-archive snapshot to restore. Use
        # the CLI against an explicitly pinned older release if a downgrade is
        # ever needed. GitHub OTA rollback was removed -- single pip process.

        # 7. Photo upload (multipart/form-data)
        if parsed.path == "/api/photos/upload" and self.headers.get("Content-Type", "").startswith("multipart/form-data"):
            # Self-contained multipart/form-data parser using ONLY the standard
            # library. No dependency on `cgi` (removed in Py3.13/PEP 594) and no
            # reliance on `werkzeug` being present at runtime -- the declared
            # dependency is not installed on every target SBC. This keeps photo
            # uploads working on every supported Python version off the shelf.
            try:
                content_type = self.headers.get("Content-Type", "")
                boundary = None
                for part in content_type.split(";")[1:]:
                    if "boundary=" in part:
                        boundary = part.split("=", 1)[1].strip().strip('"').encode()
                if not boundary:
                    raise ValueError("Missing multipart boundary")

                # raw_body was already consumed by do_POST top-of-method.
                body = raw_body

                # Minimal, spec-faithful multipart extraction: locate each
                # part's header block and a name="file" filename="..." entry,
                # then take the chunk between the blank line and the next
                # boundary token.
                delimiter = b"--" + boundary
                idx = 0
                found_file = None
                filename = None
                while True:
                    header_start = body.find(delimiter, idx)
                    if header_start == -1:
                        break
                    line_end = body.find(b"\r\n", header_start)
                    if line_end == -1:
                        break
                    # Skip the boundary line itself.
                    cursor = line_end + 2
                    headers_end = body.find(b"\r\n\r\n", cursor)
                    if headers_end == -1:
                        break
                    headers_blob = body[cursor:headers_end]
                    part_headers = {}
                    for hline in headers_blob.split(b"\r\n"):
                        if b":" in hline:
                            name, _, val = hline.partition(b":")
                            part_headers[name.strip().lower()] = val.strip()
                    cd = part_headers.get(b"content-disposition", b"")
                    has_file_field = b'name="file"' in cd or b'name=" file"' in cd
                    fname = None
                    if b"filename=" in cd:
                        fname = cd.split(b"filename=", 1)[1].strip()
                        if fname.startswith(b'"') and fname.endswith(b'"'):
                            fname = fname[1:-1]
                        fname = fname.decode("utf-8", "replace")
                    # Next boundary after this part's headers.
                    next_delim = body.find(delimiter, headers_end)
                    if next_delim == -1:
                        content = body[headers_end + 4:]
                    else:
                        content = body[headers_end + 4:next_delim]
                    # Strip trailing CRLF belonging to the boundary separator.
                    if content.endswith(b"\r\n"):
                        content = content[:-2]
                    if has_file_field and fname:
                        found_file = content
                        filename = fname
                        break
                    # Advance over this part (including closing -- or CRLF).
                    if next_delim == -1:
                        break
                    idx = next_delim

                if found_file is None:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"No file uploaded"}')
                    return

                from widgets.photo_frame.widget import save_photo
                photo_path = save_photo(found_file, filename or "photo.jpg")
                # Repaint the panel immediately so the upload shows up instead
                # of leaving the previous frame up until the next rotation tick.
                if self.scheduler is not None:
                    self.scheduler.refresh_display()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "uploaded", "path": photo_path}).encode("utf-8"))
            except Exception as e:
                self.log_message("Photo upload failed: %s", e)
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Upload failed: {e}"}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


def run_production_server(scheduler, port=80):
    ProductionHandler.scheduler = scheduler
    actual_port = port
    try:
        server = QuietServer(("0.0.0.0", actual_port), ProductionHandler)
    except PermissionError:
        actual_port = 8080
        server = QuietServer(("0.0.0.0", actual_port), ProductionHandler)

    logger.info(f"Production Web Dashboard active at: http://localhost:{actual_port}")
    # Rehydrate persisted admin sessions so a restart does not log clients out.
    _load_sessions()
    server.serve_forever()
