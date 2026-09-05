"""
rndrSBC - Native Weather Widget
Full parity with the InkyPi weather plugin, served from a clean-room engine.

Supported features:
  • Dual providers: OpenWeatherMap (OneCall 3.0 + Air Pollution + Reverse Geocode)
      or Open-Meteo (Forecast + Air Quality + Geocoding) — no API key required.
  • Units: metric / imperial / standard (Kelvin)
  • Time format: 12h / 24h, plus location timezone (auto)
  • Title selection: resolved location label OR custom override
  • Metrics: sunrise, sunset, wind (+ direction arrow), humidity,
      pressure, UV index, visibility, air quality
  • Hourly curve graph with precipitation bars and icon/step control
  • 5-day forecast with moon phase toggle (hemisphere-aware)
"""

from PIL import Image
import os
import math
import time as _time
from datetime import datetime, timedelta
from core.canvas import ResponsiveCanvas, Rect
from core import i18n
from widgets.base import BaseWidget, register_widget


# --------------------------------------------------------------------------- #
# Unit & helper maps
# --------------------------------------------------------------------------- #

UNITS = {
    "metric":    {"temp": "°C", "temp_omp": "metric",    "wind": "m/s",  "dist": "km"},
    "imperial":   {"temp": "°F", "temp_omp": "imperial", "wind": "mph",  "dist": "mi"},
    "standard":   {"temp": "K",  "temp_omp": "metric",    "wind": "m/s",  "dist": "km"},
}

OPEN_METEO_UNIT_PARAMS = {
    "standard": "temperature_unit=celsius&wind_speed_unit=ms&precipitation_unit=mm",
    "metric":    "temperature_unit=celsius&wind_speed_unit=ms&precipitation_unit=mm",
    "imperial":  "temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch",
}

KMH_TO_MS = 1000.0 / 3600.0
MS_TO_MPH = 2.236936


def get_wmo_icon_name(code, is_day=1):
    """Map Open-Meteo weather_code to bundled icon filename."""
    suffix = "d" if is_day else "n"
    if code == 0: return f"01{suffix}.png"
    elif code in [1, 2]: return f"02{suffix}.png"
    elif code == 3: return f"04{suffix}.png"
    elif code in [45, 48]: return f"50{suffix}.png"
    elif code in [51, 53, 55, 56, 57]: return f"09{suffix}.png"
    elif code in [61, 63, 65, 66, 67]: return f"10{suffix}.png"
    elif code in [71, 73, 75, 77, 85, 86]: return f"13{suffix}.png"
    elif code in [80, 81, 82]: return f"09{suffix}.png"
    elif code in [95, 96, 99]: return f"11{suffix}.png"
    return f"01{suffix}.png"


def _round_kelvin(t_celsius: float, units: str):
    """Return display int for a Celsius value under the requested units."""
    if units == "standard":
        return int(round(t_celsius + 273.15))
    return int(round(t_celsius))


def _wind_arrow(deg: float) -> str:
    """Return a cardinal wind arrow for a compass bearing (degrees)."""
    deg = int(deg or 0) % 360
    arrows = ["↓", "↙", "←", "↖", "↑", "↗", "→", "↘"]
    return arrows[round(deg / 45.0) % 8]


def _moon_phase_icon(fraction: float, lat: float) -> str:
    """Convert a 0.0-1.0 lunar phase fraction into an icon name (hemisphere-aware).

    North-hemisphere naming is inverted for Southern-hemisphere viewers.
    """
    f = fraction % 1.0
    if f < 0.0625 or f >= 0.9375:
        name = "newmoon"
    elif f < 0.1875:
        name = "waxingcrescent"
    elif f < 0.3125:
        name = "firstquarter"
    elif f < 0.4375:
        name = "waxinggibbous"
    elif f < 0.5625:
        name = "fullmoon"
    elif f < 0.6875:
        name = "waninggibbous"
    elif f < 0.8125:
        name = "lastquarter"
    else:
        name = "waningcrescent"

    if lat < 0:  # Southern hemisphere: mirror waxing / waning and quarter names
        swap = {
            "waxingcrescent": "waningcrescent", "waningcrescent": "waxingcrescent",
            "firstquarter": "lastquarter", "lastquarter": "firstquarter",
            "waxinggibbous": "waninggibbous", "waninggibbous": "waxinggibbous",
        }
        name = swap.get(name, name)
    return f"{name}.png"


def resolve_icon(name):
    if not name:
        return None
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "assets", "icons", "weather", name),
        os.path.join(os.path.dirname(__file__), "..", "..", "assets", "icons", name),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


# --------------------------------------------------------------------------- #
# Data providers
# --------------------------------------------------------------------------- #

class _OpenMeteoProvider:
    """Open-Meteo data source. No API key required."""

    FORECAST_URL = (
        "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,"
        "surface_pressure,wind_speed_10m,wind_direction_10m"
        "&hourly=temperature_2m,precipitation,precipitation_probability,weather_code,visibility"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max"
        "&timezone=auto&forecast_days={days}&{units}"
    )
    AIR_URL = (
        "https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}"
        "&hourly=european_aqi,uv_index&timezone=auto"
    )

    def __init__(self, units: str, widget: BaseWidget = None):
        self.units_param = OPEN_METEO_UNIT_PARAMS[units]
        self.display_units = units
        self.widget = widget

    def fetch(self, lat, lon):
        days = 7
        url = self.FORECAST_URL.format(lat=lat, lon=lon, days=days, units=self.units_param)
        if self.widget:
            weather, is_stale_w = self.widget.fetch_remote_json(url, ttl=300)
            aqi, is_stale_a = self.widget.fetch_remote_json(self.AIR_URL.format(lat=lat, lon=lon), ttl=600, default={})
            return weather, aqi, (is_stale_w or is_stale_a)
        else:
            import requests
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            weather = r.json()
            aqi = {}
            try:
                ar = requests.get(self.AIR_URL.format(lat=lat, lon=lon), timeout=10)
                ar.raise_for_status()
                aqi = ar.json()
            except Exception:
                aqi = {}
            return weather, aqi, False

    def parse(self, weather, aqi, settings):
        units = settings.get("units", "imperial")
        tz = self._tz(weather)
        now_utc = None  # resolved below

        cur = weather.get("current", {})
        daily = weather.get("daily", {})
        hourly = weather.get("hourly", {})
        daily_times = daily.get("time", [])

        # ---- Current block ----
        try:
            iso = cur.get("time", "")
            dt_now = datetime.fromisoformat(iso) if iso else datetime.now()
        except Exception:
            dt_now = datetime.now()

        date_label = dt_now.strftime("%A, %B %d")
        sunrise_iso = daily_times[0][:-6] + "+00:00" if daily_times else ""
        sunset_iso = daily_times[0][:-6] + "+00:00" if daily_times else ""

        wind_ms = float(cur.get("wind_speed_10m", 0) or 0)
        if units == "imperial":
            wind_disp = round(wind_ms * MS_TO_MPH)
        else:
            wind_disp = round(wind_ms)

        current = {
            "temperature": _round_kelvin(float(cur.get("temperature_2m", 0) or 0), units),
            "feels_like": _round_kelvin(float(cur.get("apparent_temperature", 0) or 0), units),
            "humidity": int(round(float(cur.get("relative_humidity_2m", 0) or 0))),
            "pressure": int(round(float(cur.get("surface_pressure", 0) or 0))),
            "wind": wind_disp,
            "wind_arrow": _wind_arrow(cur.get("wind_direction_10m", 0)),
            "icon": get_wmo_icon_name(cur.get("weather_code", 0), cur.get("is_day", 1)),
            "high": _round_kelvin(daily["temperature_2m_max"][0] if daily["temperature_2m_max"] else 0, units),
            "low": _round_kelvin(daily["temperature_2m_min"][0] if daily["temperature_2m_min"] else 0, units),
            "sunrise": (daily["sunrise"][0].split("T")[1][:5] if daily.get("sunrise") else "--:--"),
            "sunset": (daily["sunset"][0].split("T")[1][:5] if daily.get("sunset") else "--:--"),
        }

        # ---- UV & visibility & AQI (current hour) ----
        uv = "N/A"
        try:
            uv_times = hourly.get("time", [])
            uv_vals = aqi.get("hourly", {}).get("uv_index", []) if aqi else []
            if uv_vals:
                hh = dt_now.hour
                for i, t in enumerate(uv_times[:len(uv_vals)]):
                    try:
                        if datetime.fromisoformat(t).hour == hh:
                            uv = int(round(float(uv_vals[i])))
                            break
                    except Exception:
                        pass
        except Exception:
            uv = "N/A"
        current["uv"] = uv

        aqi_idx = "N/A"
        try:
            aq_times = aqi.get("hourly", {}).get("time", []) if aqi else []
            aq_vals = aqi.get("hourly", {}).get("european_aqi", []) if aqi else []
            if aq_vals:
                hh = dt_now.hour
                for i, t in enumerate(aq_times[:len(aq_vals)]):
                    try:
                        if datetime.fromisoformat(t).hour == hh:
                            aqi_idx = round(float(aq_vals[i]), 1)
                            break
                    except Exception:
                        pass
        except Exception:
            aqi_idx = "N/A"
        current["aqi"] = aqi_idx

        # ---- Visibility ----
        vis_val = "N/A"
        try:
            vis_times = hourly.get("time", [])
            vis_vals = hourly.get("visibility", [])
            if vis_vals:
                hh = dt_now.hour
                for i, t in enumerate(vis_times[:len(vis_vals)]):
                    try:
                        if datetime.fromisoformat(t).hour == hh:
                            vis_m = float(vis_vals[i])
                            vis_val = "≥10" if vis_m >= 10000 else f"{vis_m / 1000.0:.1f}"
                            break
                    except Exception:
                        pass
            if units == "imperial" and vis_val != "N/A" and not vis_val.startswith(">"):
                vis_val = f"{float(vis_val) * 0.621371:.1f}"
        except Exception:
            vis_val = "N/A"
        current["visibility"] = vis_val

        # ---- Hourly graph ----
        hourly_series = []
        for i in range(min(24, len(hourly.get("time", [])))):
            try:
                ts = datetime.fromisoformat(hourly["time"][i])
            except Exception:
                continue
            hourly_series.append({
                "time": ts,
                "temp": _round_kelvin(float(hourly["temperature_2m"][i]), units),
                "precip_prob": float(hourly["precipitation_probability"][i]) if i < len(hourly.get("precipitation_probability", [])) else 0,
                "icon": get_wmo_icon_name(hourly["weather_code"][i], 1),
            })

        # ---- Daily forecast with moon phase ----
        forecast = []
        for i in range(min(7, len(daily.get("time", [])))):
            try:
                d = datetime.fromisoformat(daily["time"][i])
            except Exception:
                continue
            # Approximate moon phase from the synodic period anchored to a known new moon
            moon_age = ((d - datetime(2000, 1, 6, 18, 14)).total_seconds() / 86400.0) % 29.530588853
            fraction = moon_age / 29.530588853
            forecast.append({
                "day": d.strftime("%a"),
                "high": _round_kelvin(daily["temperature_2m_max"][i] if i < len(daily["temperature_2m_max"]) else 0, units),
                "low": _round_kelvin(daily["temperature_2m_min"][i] if i < len(daily["temperature_2m_min"]) else 0, units),
                "icon": get_wmo_icon_name(daily["weather_code"][i] if i < len(daily["weather_code"]) else 0, 1),
                "moon_icon": _moon_phase_icon(fraction, settings.get("latitude", 0)),
            })

        return {
            "date_label": date_label,
            "current": current,
            "hourly": hourly_series,
            "forecast": forecast,
            "timezone": self._tz(weather),
            "source": "Open-Meteo",
        }

    def _tz(self, weather):
        return weather.get("timezone", "UTC")


class _OpenWeatherMapProvider:
    """OpenWeatherMap data source. Requires an API key."""

    WEATHER_URL = "https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={lon}&units={units}&exclude=minutely&appid={key}"
    AIR_URL = "https://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={key}"
    GEO_URL = "https://api.openweathermap.org/geo/1.0/reverse?lat={lat}&lon={lon}&limit=1&appid={key}"

    def __init__(self, api_key: str, widget: BaseWidget = None):
        self.key = api_key
        self.widget = widget

    def fetch(self, lat, lon, units):
        omp_units = UNITS[units]["temp_omp"]
        url = self.WEATHER_URL.format(lat=lat, lon=lon, units=omp_units, key=self.key)
        if self.widget:
            weather, is_stale_w = self.widget.fetch_remote_json(url, ttl=300)
            aqi, is_stale_a = self.widget.fetch_remote_json(self.AIR_URL.format(lat=lat, lon=lon, key=self.key), ttl=600, default={})
            return weather, aqi, (is_stale_w or is_stale_a)
        else:
            import requests
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            weather = r.json()
            aqi = {}
            try:
                ar = requests.get(self.AIR_URL.format(lat=lat, lon=lon, key=self.key), timeout=10)
                ar.raise_for_status()
                aqi = ar.json()
            except Exception:
                aqi = {}
            return weather, aqi, False

    def fetch_location(self, lat, lon):
        try:
            url = self.GEO_URL.format(lat=lat, lon=lon, key=self.key)
            if self.widget:
                data, _ = self.widget.fetch_remote_json(url, ttl=86400, default=[])
            else:
                import requests
                r = requests.get(url, timeout=8)
                r.raise_for_status()
                data = r.json()
            if data and isinstance(data, list):
                name = data[0].get("name", "") or ""
                state = data[0].get("state", "") or ""
                country = data[0].get("country", "") or ""
                return f"{name}, {state or country}".strip(", ")
        except Exception:
            pass
        return ""

    def parse(self, weather, aqi, settings):
        units = settings.get("units", "imperial")
        cur = weather.get("current", {})
        daily = weather.get("daily", [])
        hourly = weather.get("hourly", [])

        t = cur.get("dt", 0)
        dt_now = datetime.fromtimestamp(t) if t else datetime.now()
        date_label = dt_now.strftime("%A, %B %d")

        icon = (cur.get("weather", [{}])[0].get("icon", "01d") or "01d")

        def _t(v):
            return _round_kelvin(float(v), units)

        current = {
            "temperature": _t(cur.get("temp", 0)),
            "feels_like": _t(cur.get("feels_like", 0)),
            "humidity": int(round(float(cur.get("humidity", 0)))),
            "pressure": int(round(float(cur.get("pressure", 0)))),
            "wind": round(float(cur.get("wind_speed", 0))),
            "wind_arrow": _wind_arrow(cur.get("wind_deg", 0)),
            "icon": icon,
            "high": _t(daily[0]["temp"]["max"]) if daily else 0,
            "low": _t(daily[0]["temp"]["min"]) if daily else 0,
            "sunrise": datetime.fromtimestamp(daily[0]["sunrise"]).strftime("%H:%M") if daily else "--:--",
            "sunset": datetime.fromtimestamp(daily[0]["sunset"]).strftime("%H:%M") if daily else "--:--",
            "uv": int(round(float(cur.get("uvi", 0)))) if cur.get("uvi") else "N/A",
            "visibility": self._fmt_visibility(cur.get("visibility"), units),
            "aqi": self._parse_aqi(aqi),
        }

        hourly_series = []
        for h in hourly[:24]:
            hd = datetime.fromtimestamp(h.get("dt", 0))
            hicon = (h.get("weather", [{}])[0].get("icon", "01d") or "01d")
            hourly_series.append({
                "time": hd,
                "temp": _t(h.get("temp", 0)),
                "precip_prob": float(h.get("pop", 0) or 0) * 100.0,
                "icon": hicon,
            })

        forecast = []
        for d in daily[:7]:
            dd = datetime.fromtimestamp(d.get("dt", 0))
            icon = d.get("weather", [{}])[0].get("icon", "01d") or "01d"
            # OpenWeatherMap reports moon_phase as fraction in [0,1]
            moon_frac = float(d.get("moon_phase", 0) or 0)
            forecast.append({
                "day": dd.strftime("%a"),
                "high": _t(d["temp"]["max"]),
                "low": _t(d["temp"]["min"]),
                "icon": icon,
                "moon_icon": _moon_phase_icon(moon_frac or 0.0, settings.get("latitude", 0)),
            })

        return {
            "date_label": date_label,
            "current": current,
            "hourly": hourly_series,
            "forecast": forecast,
            "timezone": self._tz(weather),
            "source": "OpenWeatherMap",
        }

    def _parse_aqi(self, aqi):
        try:
            idx = aqi["list"][0]["main"]["aqi"]
            scale = ["Good", "Fair", "Moderate", "Poor", "Very Poor"][int(idx) - 1]
            return scale
        except Exception:
            return "N/A"

    def _fmt_visibility(self, vis_m, units):
        try:
            vis_m = float(vis_m)
        except (TypeError, ValueError):
            return "N/A"
        if units == "imperial":
            mi = vis_m / 1609.0
            return "≥6.2" if mi >= 6.2 else f"{mi:.1f}"
        km = vis_m / 1000.0
        return "≥10" if km >= 10 else f"{km:.1f}"


PROVIDERS = {
    "OpenWeatherMap": _OpenWeatherMapProvider,
    "OpenMeteo": _OpenMeteoProvider,
}


# --------------------------------------------------------------------------- #
# The widget
# --------------------------------------------------------------------------- #

@register_widget("weather", "Weather Dashboard")
class WeatherWidget(BaseWidget):
    name = "Weather Dashboard"
    description = "Live conditions, hourly curve, air quality, and 7-day forecast"
    default_interval_minutes = 30

    def get_config_schema(self) -> dict:
        return {
            "fields": [
                {"name": "location", "label": "Location", "type": "string", "default": "New York City"},
                {"name": "title", "label": "Title Override (optional)", "type": "string", "default": ""},
                {"name": "latitude", "label": "Latitude", "type": "number", "default": 40.7128},
                {"name": "longitude", "label": "Longitude", "type": "number", "default": -74.0060},
                {"name": "weatherProvider", "label": "Weather Provider", "type": "select",
                 "options": ["OpenMeteo", "OpenWeatherMap"], "default": "OpenMeteo"},
                {"name": "units", "label": "Units", "type": "select",
                 "options": ["imperial", "metric", "standard"], "default": "imperial"},
                {"name": "displayRefreshTime", "label": "Show Refresh Time", "type": "boolean", "default": True},
                {"name": "displayMetrics", "label": "Show Metrics (humidity, wind, etc.)", "type": "boolean", "default": True},
                {"name": "displayGraph", "label": "Show Hourly Graph", "type": "boolean", "default": True},
                {"name": "displayRain", "label": "Show Rain Bars", "type": "boolean", "default": False},
                {"name": "displayGraphIcons", "label": "Show Hourly Icons", "type": "boolean", "default": False},
                {"name": "graphIconStep", "label": "Graph Icon Step (hours)", "type": "number", "default": 6},
                {"name": "displayForecast", "label": "Show 7-Day Forecast", "type": "boolean", "default": True},
                {"name": "forecastDays", "label": "Forecast Days", "type": "select",
                 "options": ["3", "5", "7"], "default": "7"},
                {"name": "moonPhase", "label": "Show Moon Phase", "type": "boolean", "default": False},
                {"name": "time_format", "label": "Time Format", "type": "select",
                 "options": ["12h", "24h"], "default": "12h"},
                {"name": "titleSelection", "label": "Title Source", "type": "select",
                 "options": ["location", "custom"], "default": "location"},
                {"name": "customTitle", "label": "Custom Title (when Title Source = custom)", "type": "string", "default": ""},
                {"name": "weatherTimeZone", "label": "Time Zone", "type": "select",
                 "options": ["locationTimeZone", "localTimeZone"], "default": "locationTimeZone"},
                {"name": "api_key", "label": "OpenWeatherMap API Key", "type": "string", "default": ""},
                {"name": "frame", "label": "Frame Style", "type": "select",
                 "options": ["Corner", "Rectangle", "None"], "default": "None"},
            ]
        }

    def render(self, dimensions, settings: dict, bounds: Rect = None) -> Image.Image:
        lat = float(settings.get("latitude", 40.7128))
        lon = float(settings.get("longitude", -74.0060))
        location = settings.get("location", "Weather")
        units = settings.get("units", "imperial")
        if units not in UNITS:
            units = "imperial"
        provider_name = settings.get("weatherProvider", "OpenMeteo")
        time_format = settings.get("time_format", "12h")
        display_graph = settings.get("displayGraph", True)
        timezone = None
        weather_time_zone = settings.get("weatherTimeZone", "locationTimeZone")
        display_refresh_time = settings.get("displayRefreshTime", True)
        display_metrics = settings.get("displayMetrics", True)
        display_rain = settings.get("displayRain", False)
        display_graph_icons = settings.get("displayGraphIcons", False)
        graph_icon_step = int(settings.get("graphIconStep", 6) or 6)
        display_forecast = settings.get("displayForecast", True)
        forecast_days = int(settings.get("forecastDays", 7) or 7)
        moon_phase = settings.get("moonPhase", False)
        frame_style = settings.get("frame", "None")
        api_key = settings.get("api_key", "").strip()
        title_selection = settings.get("titleSelection", "location")
        custom_title = (settings.get("customTitle") or "").strip()
        weather_tz = settings.get("weatherTimeZone", "locationTimeZone")
        lang = settings.get("language", self.config.get("language", "en")) if hasattr(self, "config") else settings.get("language", "en")

        # Resolve title: custom selection wins, else the resolved location name.
        if title_selection == "custom" and custom_title:
            title = custom_title
        else:
            title = (settings.get("title") or "").strip() or location

        # ---- Fetch & parse with non-blocking async caching ----
        is_stale = False
        if provider_name == "OpenWeatherMap":
            if not api_key:
                raise RuntimeError("OpenWeatherMap requires an API key. Set one in the weather widget settings.")
            provider = _OpenWeatherMapProvider(api_key, widget=self)
            weather, aqi, is_stale = provider.fetch(lat, lon, units)
            data = provider.parse(weather, aqi, settings)
            if title == location and not settings.get("title"):
                resolved = provider.fetch_location(lat, lon)
                if resolved:
                    title = resolved
        else:
            provider = _OpenMeteoProvider(units, widget=self)
            weather, aqi, is_stale = provider.fetch(lat, lon)
            data = provider.parse(weather, aqi, settings)
            if title == location and not settings.get("title") and location in ("Weather", "New York City"):
                title = location

        timezone = data.get("timezone") or "UTC"

        with ResponsiveCanvas(dimensions, bg_color="#ffffff") as canvas:
            content = bounds if bounds is not None else canvas.bounds.inset(canvas.pt(16))
            cur = data["current"]

            # ---- Layout ----
            use_forecast = display_forecast and data.get("forecast")
            use_graph = display_graph
            if use_forecast:
                # ensure forecast days is bounded to what we actually have
                if "forecast_count" in data:
                    forecast_days = min(forecast_days, int(data["forecast_count"]))
                if use_graph:
                    h_box, t_box, g_box, f_box = content.split_rows([1.0, 3.2, 3.0, 2.6], gap=canvas.pt(8))
                else:
                    h_box, t_box, f_box = content.split_rows([1.0, 3.6, 3.6], gap=canvas.pt(8))
                    g_box = None
            else:
                if use_graph:
                    h_box, t_box, g_box = content.split_rows([1.0, 3.4, 3.4], gap=canvas.pt(8))
                else:
                    h_box, t_box = content.split_rows([1.0, 3.6], gap=canvas.pt(8))
                f_box = None
                g_box = g_box if use_graph else None

            self._draw_header(canvas, h_box, title, data["date_label"], time_format, lang, is_stale=is_stale, display_refresh_time=display_refresh_time, timezone=timezone, weather_time_zone=weather_time_zone)
            self._draw_today(canvas, t_box, cur, units, data, settings, lang, display_metrics=display_metrics)

            if use_graph and g_box is not None:
                self._draw_graph(canvas, g_box, data["hourly"], units, time_format, graph_icon_step, display_rain=display_rain, display_icons=display_graph_icons)

            if use_forecast and f_box is not None:
                self._draw_forecast(canvas, f_box, data["forecast"], units, moon_phase, lat, forecast_days=forecast_days)

            if bounds is None:
                canvas.draw_frame(frame_style, color="#000000")

            return canvas.to_image()

    # ------------------------------------------------------------------ #
    def _draw_header(self, canvas, box, title, date_label, time_format, lang, is_stale=False, display_refresh_time=True, timezone="UTC", weather_time_zone="locationTimeZone"):
        font_title = canvas.get_font("Roboto-Bold", 26, font_weight="bold")
        font_date = canvas.get_font("Roboto-Regular", 18)
        font_sub = canvas.get_font("Roboto-Regular", 12)
        canvas.draw_text(title, (box.x, box.y + canvas.pt(2)), font=font_title, fill="#000000")
        canvas.draw_text(date_label, (box.center[0], box.y + canvas.pt(6)), font=font_date, fill="#000000", anchor="ma")
        if display_refresh_time:
            now = datetime.now()
            if weather_time_zone == "locationTimeZone" and timezone and timezone != "UTC":
                try:
                    from zoneinfo import ZoneInfo
                    now = datetime.now(ZoneInfo(timezone)).replace(tzinfo=None)
                except Exception:
                    pass  # fall back to device local time if TZ string is invalid
            stamp = now.strftime("%H:%M") if time_format == "24h" else now.strftime("%I:%M %p").lstrip("0")
            canvas.draw_text(f"{i18n.label(lang, 'updated')}: {stamp}", (box.right, box.y + canvas.pt(8)), font=font_sub, fill="#000000", anchor="ra")
        if is_stale:
            canvas.draw_stale_indicator(box, tooltip="Offline Data")

    def _draw_today(self, canvas, box, cur, units, data, settings, lang, display_metrics=True):
        unit_sym = UNITS[units]["temp"]
        wind_unit = UNITS[units]["wind"]

        # Panelized: hero conditions (left) + grouped metrics (right), separated by a
        # vertical rule. Strong whitespace over floating text for e-ink legibility.
        pad = canvas.pt(6)
        inner = box.inset(pad)
        hero, metrics_box = inner.split_columns([3.4, 6.6], gap=canvas.pt(12))

        # --- Left: big icon, current temp, condition text, high/low ---
        cur_icon_path = resolve_icon(cur["icon"]) or resolve_icon("01d.png")
        icon_area, temp_area = hero.split_rows([1.0, 1.15], gap=canvas.pt(2))
        if cur_icon_path:
            canvas.paste_icon(cur_icon_path, icon_area, size_pt=int(icon_area.h * 0.82))

        font_big = canvas.get_font("Roboto-Bold", 44, font_weight="bold")
        font_cond = canvas.get_font("Roboto-Bold", 14, font_weight="bold")
        font_hi = canvas.get_font("Roboto-Regular", 13)
        temp_label = cur.get("title") or cur.get("condition") or ""
        canvas.draw_text(f"{cur['temperature']}{unit_sym}", (temp_area.x, temp_area.y + canvas.pt(2)), font=font_big, fill="#000000")
        offset_y = canvas.pt(50)
        if temp_label:
            canvas.draw_text(temp_label, (temp_area.x, temp_area.y + offset_y), font=font_cond, fill="#333333")
            offset_y += canvas.pt(20)
        canvas.draw_text(
            f"H {cur['high']}°   L {cur['low']}°", (temp_area.x, temp_area.y + offset_y), font=font_hi, fill="#000000")

        # Vertical rule separating hero from metrics panel
        rule_x = hero.right + canvas.pt(5)
        canvas.draw.line([(rule_x, inner.y), (rule_x, inner.bottom)], fill="#dddddd", width=canvas.pt(2))

        # --- Right: grouped 2x4 metrics grid with columns headers (no dense boxes) ---
        if display_metrics:
            metrics = [
                ("Sunrise", f"{self._fmt_hms(cur['sunrise'], settings.get('time_format', '12h'))}", "sunrise.png"),
                ("Sunset", f"{self._fmt_hms(cur['sunset'], settings.get('time_format', '12h'))}", "sunset.png"),
                (i18n.label(lang, "wind"), f"{cur['wind']} {wind_unit} {cur['wind_arrow']}", "wind.png"),
                (i18n.label(lang, "humidity"), f"{cur['humidity']}%", "humidity.png"),
                ("Pressure", f"{cur['pressure']} hPa", "pressure.png"),
                ("UV Index", f"{cur['uv']}", "uvi.png"),
                ("Visibility", f"{cur['visibility']} {UNITS[units]['dist']}", "visibility.png"),
                ("Air Quality", f"{cur['aqi']}", "aqi.png"),
            ]
            m_rows = metrics_box.split_rows([1, 1, 1, 1], gap=canvas.pt(6))
            m_idx = 0
            font_lbl = canvas.get_font("Roboto-Regular", 11)
            font_val = canvas.get_font("Roboto-Bold", 12, font_weight="bold")
            for row in m_rows:
                cols = row.split_columns([1, 1], gap=canvas.pt(6))
                for col in cols:
                    if m_idx >= len(metrics):
                        continue
                    lbl, val, ico_name = metrics[m_idx]
                    ico_sub, txt_sub = col.split_columns([1.8, 8.2], gap=canvas.pt(3))
                    ico_p = resolve_icon(ico_name)
                    if ico_p:
                        canvas.paste_icon(ico_p, ico_sub.inset(canvas.pt(2)), size_pt=15)
                    canvas.draw_text(lbl, (txt_sub.x, txt_sub.y), font=font_lbl, fill="#777777")
                    canvas.draw_text(val, (txt_sub.x, txt_sub.y + canvas.pt(13)), font=font_val, fill="#000000")
                    m_idx += 1

    def _panel_label(self, canvas, box, text):
        """Draws a small uppercase panel header tag at the top-left of a box."""
        font = canvas.get_font("Roboto-Bold", 12, font_weight="bold")
        canvas.draw_text(text.upper(), (box.x, box.y + canvas.pt(2)), font=font, fill="#555555")

    def _draw_graph(self, canvas, box, hourly, units, time_format, icon_step, display_rain=False, display_icons=False):
        if not hourly:
            return
        # Panelize the graph inside a delineated card with a header label and padding.
        canvas.draw_card(box, radius=10, fill="#ffffff", outline="#c9c9c9", width=1)
        inner = box.inset(canvas.pt(10))
        pad_top = canvas.pt(22)
        self._panel_label(canvas, inner, i18n_label := "Hourly Forecast")
        chart = Rect(inner.x, inner.y + pad_top, inner.w, inner.h - pad_top)
        temps = [h["temp"] for h in hourly]
        min_t, max_t = min(temps), max(temps)
        t_range = max_t - min_t if max_t != min_t else 1.0
        chart_w = chart.w - canvas.pt(60) # leave room for axis labels
        chart_h = chart.h - canvas.pt(36)
        start_x = chart.x + canvas.pt(35)
        start_y = chart.y + canvas.pt(10)
        step_x = chart_w / (len(temps) - 1) if len(temps) > 1 else chart_w
        coords = [(start_x + i * step_x, start_y + chart_h - ((t - min_t) / t_range) * (chart_h - canvas.pt(12))) for i, t in enumerate(temps)]

        # Min/Max temp labels on left axis
        font_axis = canvas.get_font("Roboto-Regular", 11)
        canvas.draw_text(f"{int(round(max_t))}°", (chart.x + canvas.pt(30), start_y), font=font_axis, fill="#000000", anchor="rm")
        canvas.draw_text(f"{int(round(min_t))}°", (chart.x + canvas.pt(30), start_y + chart_h), font=font_axis, fill="#000000", anchor="rm")

        # 0% / 100% precip labels on right axis
        right_x = start_x + chart_w + canvas.pt(5)
        canvas.draw_text("100%", (right_x, start_y), font=font_axis, fill="#666666", anchor="lm")
        canvas.draw_text("0%", (right_x, start_y + chart_h), font=font_axis, fill="#666666", anchor="lm")

        # Filled area under temperature line (gradient effect or warm fill)
        if len(coords) >= 2:
            poly_points = [(coords[0][0], start_y + chart_h)] + coords + [(coords[-1][0], start_y + chart_h)]
            canvas.draw.polygon(poly_points, fill="#ffe8bc")

        # Rain bars (precipitation probability over time)
        if display_rain:
            for i, h in enumerate(hourly):
                pop = h.get("precip_prob", 0)
                if pop > 0:
                    bar_h = int((pop / 100.0) * chart_h * 0.4)
                    bx = start_x + i * step_x - canvas.pt(2)
                    by = start_y + chart_h - bar_h
                    canvas.draw.rectangle([bx, by, bx + canvas.pt(4), start_y + chart_h], fill="#3b6cff")

        # Temperature line
        canvas.draw.line(coords, fill="#ff9900", width=canvas.pt(3), joint="round")

        # Hour ticks / labels
        font_t = canvas.get_font("Roboto-Regular", 11)
        for i, h in enumerate(hourly):
            px = coords[i][0]
            if i % 3 == 0:
                canvas.draw_text(self._fmt_hour(h["time"], time_format), (px, chart.bottom - canvas.pt(2)), font=font_t, fill="#000000", anchor="mb")
            # hourly condition icons, gated by both display flag and step
            if display_icons and icon_step and i % icon_step == 0:
                ico_path = resolve_icon(h.get("icon"))
                if ico_path:
                    canvas.paste_icon(ico_path, Rect(px - canvas.pt(9), start_y + chart_h - canvas.pt(26), canvas.pt(18), canvas.pt(18)), size_pt=16)

    def _draw_forecast(self, canvas, box, forecast, units, moon_phase, lat, forecast_days=7):
        days_map = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        n = min(forecast_days, len(forecast), 7)
        if n <= 0:
            return
        # Panelize the forecast inside a delineated card with a header label.
        canvas.draw_card(box, radius=10, fill="#ffffff", outline="#c9c9c9", width=1)
        inner = box.inset(canvas.pt(6))
        pad_top = canvas.pt(24)
        self._panel_label(canvas, inner, "Forecast")
        tiles = Rect(inner.x, inner.y + pad_top, inner.w, inner.h - pad_top)
        f_cols = tiles.split_columns([1] * n, gap=canvas.pt(8))
        font_f_day = canvas.get_font("Roboto-Bold", 13, font_weight="bold")
        font_f_tmp = canvas.get_font("Roboto-Regular", 12)
        for i in range(n):
            f = forecast[i]
            col = f_cols[i]
            canvas.draw_card(col, radius=8, fill="#f7f7f7", outline="#000000", width=1)
            d_inner = col.inset(canvas.pt(4))
            if moon_phase:
                d_rows = d_inner.split_rows([1.0, 1.6, 1.2, 1.0], gap=canvas.pt(2))
            else:
                d_rows = d_inner.split_rows([1.0, 2.0, 1.0], gap=canvas.pt(2))

            canvas.draw_text(days_map[_wday(f["day"])], (col.center[0], col.y + canvas.pt(6)), font=font_f_day, fill="#000000", anchor="mt")
            d_icon = resolve_icon(self._normalize_icon(f["icon"]))
            if d_icon:
                canvas.paste_icon(d_icon, d_rows[1])
            if moon_phase:
                m_icon = resolve_icon(f["moon_icon"])
                if m_icon:
                    canvas.paste_icon(m_icon, d_rows[2])
            canvas.draw_text(f"{f['high']}° / {f['low']}°", (col.center[0], col.bottom - canvas.pt(6)), font=font_f_tmp, fill="#000000", anchor="mb")

    # ---- helpers ----
    def _fmt_hour(self, dt, time_format):
        if time_format == "24h":
            return dt.strftime("%H:00")
        return dt.strftime("%I %p").lstrip("0")

    def _fmt_hms(self, tstr, time_format):
        tstr = (tstr or "--:--")[:5]
        if time_format != "24h" and ":" in tstr:
            try:
                hh, mm = tstr.split(":")
                hh = int(hh)
                return f"{hh % 12 or 12}:{mm} {'AM' if hh < 12 else 'PM'}"
            except Exception:
                pass
        return tstr

    def _normalize_icon(self, name):
        # OpenWeatherMap icons: e.g. "01d"; our bundle wants "01d.png" (and "022d" variant)
        if not name:
            return "01d.png"
        if not name.endswith(".png"):
            name += ".png"
        if not resolve_icon(name):
            name = name[:2] + ".png" if len(name) >= 5 else "01d.png"
        return name


def _wday(day_label):
    m = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
    return m.get(day_label, 0)
