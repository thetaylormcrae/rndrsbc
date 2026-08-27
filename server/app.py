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
from http.server import HTTPServer, BaseHTTPRequestHandler
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
SESSION_TTL_SECS = 86400 * 7 # 7 days

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
      <button onclick="refreshDisplayNow()" class="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-slate-200 border border-slate-700 transition">
        <svg class="w-3.5 h-3.5 text-orange-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
        <span>Refresh Screen</span>
      </button>
      <button onclick="saveAndApply()" class="flex items-center space-x-1.5 px-4 py-1.5 rounded-lg bg-orange-600 hover:bg-orange-500 text-xs font-semibold text-white shadow-lg shadow-orange-600/30 transition">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg>
        <span>Save & Apply</span>
      </button>
      
      <div id="auth-controls" class="pl-2 border-l border-slate-800">
        <button id="btn-logout" onclick="logout()" class="hidden text-xs text-slate-400 hover:text-rose-400 px-2 py-1.5 rounded border border-slate-800 hover:border-rose-900 transition">Logout</button>
        <button id="btn-login" onclick="showLoginModal()" class="text-xs text-slate-300 hover:text-white px-2.5 py-1.5 rounded bg-slate-800 hover:bg-slate-700 border border-slate-700 transition">Login</button>
      </div>
    </div>
  </header>

  <!-- Main Container -->
  <main class="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">

    <!-- Top Row: Screen Mirror + Quick Stats -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
      
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
      <div class="lg:col-span-5 bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-xl flex flex-col justify-between">
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
    <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
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
            <button onclick="addWidgetToPlaylist('calendar')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-blue-500/20 text-blue-400 flex items-center justify-center font-bold text-xs mt-0.5">📅</div>
              <div>
                <div class="text-xs font-bold text-slate-200">Calendar & Agenda</div>
                <div class="text-[10px] text-slate-400">Monthly grid & synchronized iCal feed</div>
              </div>
            </button>
            <button onclick="addWidgetToPlaylist('system_stats')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-emerald-500/20 text-emerald-400 flex items-center justify-center font-bold text-xs mt-0.5">💻</div>
              <div>
                <div class="text-xs font-bold text-slate-200">System Monitor</div>
                <div class="text-[10px] text-slate-400">Pi CPU, RAM, and storage stats</div>
              </div>
            </button>
            <button onclick="addWidgetToPlaylist('onboarding')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-purple-500/20 text-purple-400 flex items-center justify-center font-bold text-xs mt-0.5">📱</div>
              <div>
                <div class="text-xs font-bold text-slate-200">Device Setup & QR Claim</div>
                <div class="text-[10px] text-slate-400">QR code + Wi-Fi/AP provisioning instructions</div>
              </div>
            </button>
            <button onclick="addWidgetToPlaylist('photo_frame')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-pink-500/20 text-pink-400 flex items-center justify-center font-bold text-xs mt-0.5">🖼️</div>
              <div>
                <div class="text-xs font-bold text-slate-200">Photo Frame</div>
                <div class="text-[10px] text-slate-400">Rotate uploaded personal photos</div>
              </div>
            </button>
            <button onclick="addWidgetToPlaylist('composite_grid')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-amber-500/20 text-amber-400 flex items-center justify-center font-bold text-xs mt-0.5">🧩</div>
              <div>
                <div class="text-xs font-bold text-slate-200">Multi-Zone Layout Grid</div>
                <div class="text-[10px] text-slate-400">Combine several widgets on one screen</div>
              </div>
            </button>
            <button onclick="addWidgetToPlaylist('network')" class="w-full text-left px-4 py-2.5 text-xs text-slate-200 hover:bg-slate-800 flex items-start gap-2.5 transition border-t border-slate-800">
              <div class="w-7 h-7 rounded bg-sky-500/20 text-sky-400 flex items-center justify-center font-bold text-xs mt-0.5">📶</div>
              <div>
                <div class="text-xs font-bold text-slate-200">Network Diagnostics</div>
                <div class="text-[10px] text-slate-400">Wi-Fi SSID, signal, IP, gateway ping</div>
              </div>
            </button>
          </div>
        </div>
      </div>

      <div id="playlist-container" class="space-y-4">
        <!-- Dynamically rendered widget cards -->
      </div>
    </div>

    <!-- Display Hardware & Quiet Hours Settings -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
      
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
              <option value="virtual">Virtual (Browser Preview)</option>
              <option value="waveshare">Waveshare SPI Driver</option>
              <option value="inky">Pimoroni Inky Driver</option>
              <option value="framebuffer">Linux Framebuffer (/dev/fb0)</option>
            </select>
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-400 mb-1.5">Display Panel Model</label>
            <select id="cfg-model" onchange="updateHardwareSettings()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:border-orange-500 focus:outline-none">
              <option value="epd7in3f">7.3" 7-Color (800×480)</option>
              <option value="epd4in0">4.0" Inky Impression (640×400)</option>
              <option value="epd5in65f">5.65" 7-Color (600×448)</option>
              <option value="epd7in5_HD">7.5" HD (880×528)</option>
              <option value="epd13in3k">13.3" Spectra 6 (1600×1200)</option>
              <option value="epd2in13_V4">2.13" SBC Hat (250×122)</option>
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
            <input type="text" id="cfg-timezone" value="America/New_York" placeholder="e.g. America/New_York, Europe/London" onchange="updateDeviceSettings()" class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:border-orange-500 focus:outline-none" />
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
              <div class="bg-slate-900/40 border border-slate-800 rounded-lg px-2 py-1.5">NEXT <span class="text-slate-400">pin 17</span></div>
              <div class="bg-slate-900/40 border border-slate-800 rounded-lg px-2 py-1.5">PREV <span class="text-slate-400">pin 27</span></div>
              <div class="bg-slate-900/40 border border-slate-800 rounded-lg px-2 py-1.5">TOGGLE QUIET <span class="text-slate-400">pin 22</span></div>
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

      <button onclick="submitSetup()" class="w-full py-2.5 rounded-lg bg-orange-600 hover:bg-orange-500 font-semibold text-sm text-white shadow-lg shadow-orange-600/30 transition">
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
    const weatherMaps = {};

    async function checkAuthStatus() {
      try {
        const res = await fetch('/api/auth/status');
        const data = await res.json();
        if (data.setup_required) {
          document.getElementById('modal-setup').classList.remove('hidden');
          return false;
        }
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

      const res = await fetch('/api/setup', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({password: p1})
      });

      if (res.ok) {
        document.getElementById('modal-setup').classList.add('hidden');
        checkAuthStatus();
        loadStatus();
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
        checkAuthStatus();
      } else {
        err.textContent = "Invalid administrator password.";
        err.classList.remove('hidden');
      }
    }

    async function logout() {
      await fetch('/api/auth/logout', {method: 'POST'});
      checkAuthStatus();
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
          { widget: "weather", duration_minutes: 15, settings: { location: "New York City", latitude: 40.7128, longitude: -74.0060, units: "imperial", frame: "Corner" } }
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
        settings: { frame: "Corner" }
      };

      if (widgetType === "weather") {
        newWidget.settings = { location: "New York City", latitude: 40.7128, longitude: -74.0060, units: "imperial", frame: "Corner", weatherProvider: "OpenMeteo", displayGraph: true, moonPhase: true, graphIconStep: 3, time_format: "12h" };
      } else if (widgetType === "calendar") {
        newWidget.settings = { title: "My Schedule", ics_url: "", first_day_sunday: true, frame: "Corner" };
      } else if (widgetType === "system_stats") {
        newWidget.settings = { hostname: "rndrSBC Node", frame: "Corner" };
      } else if (widgetType === "onboarding") {
        newWidget.settings = { title: "Let's set up your display", frame: "Corner" };
      } else if (widgetType === "photo_frame") {
        newWidget.settings = { caption: true, mode: "sequential", frame: "Corner" };
      } else if (widgetType === "composite_grid") {
        newWidget.settings = { layout_type: "sidebar_right", gap: 8, zones: {}, frame: "Corner" };
      } else if (widgetType === "network") {
        newWidget.settings = { frame: "Corner" };
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

            <!-- Toggles: Graph, Moon Phase -->
            <div class="grid grid-cols-2 sm:grid-cols-3 gap-3 pt-1">
              <label class="flex items-center space-x-2 text-xs text-slate-300 cursor-pointer">
                <input type="checkbox" ${s.displayGraph !== false ? 'checked' : ''} onchange="updateSetting(${idx}, 'displayGraph', this.checked)" class="rounded accent-orange-600" />
                <span>Show Hourly Graph</span>
              </label>
              <label class="flex items-center space-x-2 text-xs text-slate-300 cursor-pointer">
                <input type="checkbox" ${s.moonPhase ? 'checked' : ''} onchange="updateSetting(${idx}, 'moonPhase', this.checked)" class="rounded accent-orange-600" />
                <span>Show Moon Phase</span>
              </label>
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
      } else if (item.widget === 'system_stats') {
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
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-2.5">
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
      const map = { weather: '☀️ Weather Dashboard', calendar: '📅 Calendar & Agenda', system_stats: '💻 System Monitor', onboarding: '📱 Device Setup & QR Claim', photo_frame: '🖼️ Photo Frame', composite_grid: '🧩 Multi-Zone Layout Grid', network: '📶 Network Diagnostics' };
      return map[type] || type;
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

      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(currentConfig)
      });

      if (res.status === 401) {
        showLoginModal();
        return;
      }

      if (res.ok) {
        setTimeout(refreshDisplayNow, 500);
      }
    }

    async function refreshDisplayNow() {
      const res = await fetch('/api/refresh', { method: 'POST' });
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
      await checkAuthStatus();
      await loadStatus();
      setInterval(() => {
        const img = document.getElementById('live-screen-img');
        if (img) img.src = '/api/screen.png?t=' + Date.now();
      }, 30000);
    })();
  </script>
  <!-- Device Telemetry & OTA Update Panel -->
  <div class="max-w-6xl mx-auto px-4 pb-10 pt-4 grid grid-cols-1 md:grid-cols-3 gap-4">
    <div class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4">
      <div class="text-xs font-bold text-slate-200 mb-2 flex items-center gap-2">📡 Device Health</div>
      <div id="telemetry-content" class="text-[11px] text-slate-400 space-y-1">
        <div>Load monitoring…</div>
      </div>
      <button onclick="loadTelemetry()" class="mt-2 text-[10px] px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300">Refresh</button>
    </div>
    <div class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4">
      <div class="text-xs font-bold text-slate-200 mb-2 flex items-center gap-2">🔄 Software Updates</div>
      <div id="update-content" class="text-[11px] text-slate-400">Checking…</div>
      <button onclick="checkUpdate()" class="mt-2 text-[10px] px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300">Check for Updates</button>
      <button onclick="applyUpdate()" class="mt-2 ml-1 text-[10px] px-2 py-1 rounded bg-orange-600/80 hover:bg-orange-500 text-white">Apply Update</button>
    </div>
    <div class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-4">
      <div class="text-xs font-bold text-slate-200 mb-2 flex items-center gap-2">🖼️ Photo Library</div>
      <input type="file" id="photo-upload" accept="image/*" class="text-[10px] text-slate-400 mb-2 w-full" />
      <button onclick="uploadPhoto()" class="text-[10px] px-2 py-1 rounded bg-pink-600/80 hover:bg-pink-500 text-white">Upload Photo</button>
      <div id="photos-content" class="mt-2 text-[11px] text-slate-500"></div>
    </div>
  </div>

  <script>
    async function loadTelemetry() {
      try {
        const r = await fetch('/api/telemetry');
        const t = await r.json();
        document.getElementById('telemetry-content').innerHTML =
          `<div>Health: <strong class="${t.health === 'healthy' ? 'text-emerald-400' : 'text-rose-400'}">${t.health}</strong></div>` +
          `<div>Uptime: ${t.uptime_human}</div>` +
          `<div>Renders: ${t.render_count} · Errors: ${t.error_count}</div>` +
          `<div>Last render: ${t.last_render_duration_ms}ms</div>` +
          (t.last_error ? `<div class="text-rose-400">⚠ ${t.last_error}</div>` : '');
      } catch (e) { document.getElementById('telemetry-content').innerHTML = '<div>Auth required</div>'; }
    }

    async function checkUpdate() {
      try {
        const r = await fetch('/api/update/check');
        const u = await r.json();
        document.getElementById('update-content').innerHTML = u.update_available
          ? `<div class="text-emerald-400">Update available: v${u.latest_version}</div><div class="text-slate-500 mt-1">${u.changelog || ''}</div>`
          : `<div class="text-slate-300">You're on the latest version (v${u.current_version})</div>`;
      } catch (e) { document.getElementById('update-content').innerHTML = '<div>Auth required</div>'; }
    }

    async function applyUpdate() {
      document.getElementById('update-content').innerHTML = '<div>Downloading & applying…</div>';
      try {
        const r = await fetch('/api/update/apply', { method: 'POST' });
        const resp = await r.json();
        document.getElementById('update-content').innerHTML = resp.success
          ? '<div class="text-emerald-400">Update applied! Restarting…</div>'
          : `<div class="text-rose-400">Update failed: ${resp.error}</div>`;
      } catch (e) { document.getElementById('update-content').innerHTML = '<div>Auth required</div>'; }
    }

    async function uploadPhoto() {
      const fileInput = document.getElementById('photo-upload');
      if (!fileInput.files.length) return;
      const fd = new FormData();
      fd.append('file', fileInput.files[0]);
      const r = await fetch('/api/photos/upload', { method: 'POST', body: fd });
      const resp = await r.json();
      document.getElementById('photos-content').innerHTML = `<div class="text-emerald-400">${resp.path} uploaded</div>`;
    }

    loadTelemetry();
    checkUpdate();
  </script>
</body>
</html>
"""


class ProductionHandler(BaseHTTPRequestHandler):
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

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        
        # 1. Web Dashboard
        if parsed.path in ["/", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))
            return

        # 1b. Health telemetry (protected)
        if parsed.path == "/api/telemetry":
            if not self._is_authenticated():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"Authentication required"}')
                return
            from core.telemetry import TELEMETRY
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(TELEMETRY.get_status()).encode("utf-8"))
            return

        # 1c. OTA update check (protected)
        if parsed.path == "/api/update/check":
            if not self._is_authenticated():
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"Authentication required"}')
                return
            from core.updates import check_for_update
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(check_for_update()).encode("utf-8"))
            return

        # 1d. Uploaded photos list (protected)
        if parsed.path == "/api/photos":
            try:
                from widgets.photo_frame.widget import list_photos
                photos = list_photos()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"photos": photos}).encode("utf-8"))
            except Exception as e:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"photos": [], "error": str(e)}).encode("utf-8"))
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

        # 5. Live Screen Mirror
        if parsed.path == "/api/screen.png":
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

        # 1. First-Run Setup (Enforce password >= 8 characters)
        if parsed.path == "/api/setup":
            if self._has_admin_setup():
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"Admin setup has already been completed."}')
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

        # 3. Logout
        if parsed.path == "/api/auth/logout":
            token = self._get_cookie("rndrsbc_session")
            if token in ACTIVE_SESSIONS:
                del ACTIVE_SESSIONS[token]
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
                password = body.get("password", "")
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


                # Preserve admin password hash
                if os.path.exists(self.config_path):
                    with open(self.config_path, "r") as f:
                        old_cfg = json.load(f)
                        if "admin_password_hash" in old_cfg and "admin_password_hash" not in cfg_obj:
                            cfg_obj["admin_password_hash"] = old_cfg["admin_password_hash"]

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

        # 6. OTA Self-Update
        if parsed.path == "/api/update/apply":
            from core.updates import download_and_stage_update, apply_staged_update
            staged = download_and_stage_update()
            if not staged.get("success"):
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(staged).encode("utf-8"))
                return
            result = apply_staged_update(staged.get("staged_dir"))
            self.send_response(200 if result.get("success") else 500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))
            return

        if parsed.path == "/api/update/rollback":
            from core.updates import rollback_update
            result = rollback_update()
            self.send_response(200 if result.get("success") else 500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))
            return

        # 7. Photo upload (multipart/form-data)
        if parsed.path == "/api/photos/upload" and self.headers.get("Content-Type", "").startswith("multipart/form-data"):
            try:
                # Use werkzeug's MultiPartParser directly. The stdlib `cgi` module
                # was removed in Python 3.13 (PEP 594), and werkzeug is a declared
                # dependency (works on all supported versions).
                from werkzeug.formparser import MultiPartParser
                from io import BytesIO
                content_type = self.headers.get("Content-Type", "")
                boundary = None
                for part in content_type.split(";")[1:]:
                    if "boundary=" in part:
                        boundary = part.split("=", 1)[1].strip().strip('"').encode()
                length = int(self.headers.get("Content-Length", 0) or 0)
                # NOTE: do_POST already consumed the body via rfile.read(length)
                # into raw_body at the top of the method. Re-reading rfile here
                # returns empty bytes, so we must use the in-scope raw_body.
                body = raw_body
                if not boundary:
                    raise ValueError("Missing multipart boundary")
                _, files = MultiPartParser().parse(BytesIO(body), boundary, length or None)
                storage = files.get("file")
                if storage is None and files:
                    storage = list(files.values())[0]
                if storage is not None and storage.filename:
                    from widgets.photo_frame.widget import save_photo
                    filename = storage.filename or "photo.jpg"
                    storage.stream.seek(0)
                    photo_path = save_photo(storage.stream.read(), filename)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "uploaded", "path": photo_path}).encode("utf-8"))
                else:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"No file uploaded"}')
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
        server = HTTPServer(("0.0.0.0", actual_port), ProductionHandler)
    except PermissionError:
        actual_port = 8080
        server = HTTPServer(("0.0.0.0", actual_port), ProductionHandler)

    logger.info(f"Production Web Dashboard active at: http://localhost:{actual_port}")
    server.serve_forever()
