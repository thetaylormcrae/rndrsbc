"""
rndrSBC - Native Calendar & Agenda Widget
Resolution-independent monthly calendar grid and synchronized iCal agenda feed.
"""

from PIL import Image
import os
import re
import time
import datetime
import calendar
import urllib.request
import logging
from core.canvas import ResponsiveCanvas, Rect
from core import i18n
from widgets.base import BaseWidget, register_widget

logger = logging.getLogger("rndrSBC.calendar")

def parse_ics_feed(ics_content: str, max_events=6) -> list[dict]:
    """Lightweight native ICS/iCal parser with date sorting."""
    events = []
    vevents = re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', ics_content, re.DOTALL)
    
    now = datetime.datetime.now()
    today_start = datetime.datetime(now.year, now.month, now.day)

    for ev in vevents:
        summary_m = re.search(r'SUMMARY.*?:(.*)', ev)
        summary = summary_m.group(1).strip() if summary_m else "Event"
        summary = summary.replace('\\,', ',').replace('\\;', ';').replace('\\n', ' ')

        dtstart_m = re.search(r'DTSTART.*?:([0-9TZ]+)', ev)
        if not dtstart_m:
            continue

        dt_raw = dtstart_m.group(1)
        try:
            if 'T' in dt_raw:
                dt_clean = dt_raw.replace('Z', '')[:15]
                dt = datetime.datetime.strptime(dt_clean, "%Y%m%dT%H%M%S")
                is_all_day = False
            else:
                dt = datetime.datetime.strptime(dt_raw[:8], "%Y%m%d")
                is_all_day = True

            if dt >= today_start:
                events.append({
                    "summary": summary,
                    "datetime": dt,
                    "is_all_day": is_all_day
                })
        except Exception:
            continue

    events.sort(key=lambda x: x["datetime"])
    return events[:max_events]

@register_widget("calendar", "Calendar & Agenda")
class CalendarWidget(BaseWidget):
    name = "Calendar & Agenda"
    description = "Monthly calendar overview and live iCal event schedule"
    default_interval_minutes = 60

    def get_config_schema(self) -> dict:
        return {
            "fields": [
                {"name": "title", "label": "Calendar Title", "type": "string", "default": "My Schedule"},
                {"name": "ics_url", "label": "iCal / ICS Feed URL", "type": "string", "default": ""},
                {"name": "caldav_url", "label": "CalDAV URL (optional)", "type": "string", "default": ""},
                {"name": "caldav_user", "label": "CalDAV Username", "type": "string", "default": ""},
                {"name": "caldav_pass", "label": "CalDAV Password", "type": "password", "default": ""},
                {"name": "first_day_sunday", "label": "Week Starts On", "type": "select", "options": ["Sunday", "Monday"], "default": "Sunday"},
                {"name": "frame", "label": "Frame Style", "type": "select", "options": ["Corner", "Rectangle", "None"], "default": "Corner"}
            ]
        }

    def _fetch_caldav(self, url: str, user: str = "", password: str = "") -> list[dict]:
        """Fetch calendar object collection via CalDAV REPORT, return parsed events."""
        if not url or not url.strip():
            return []
        from requests.auth import HTTPBasicAuth
        import requests as _req
        body = """<?xml version="1.0"?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav">
  <C:filter>
    <C:comp-filter name="VCALENDAR">
      <C:comp-filter name="VEVENT">
        <C:time-range start="%s" end="%s"/>
      </C:comp-filter>
    </C:comp-filter>
  </C:filter>
</C:calendar-query>"""
        now = datetime.datetime.now()
        start = now.strftime("%Y%m%dT000000Z")
        end = (now + datetime.timedelta(days=365)).strftime("%Y%m%dT000000Z")
        try:
            resp = _req.request(
                "REPORT", url,
                data=body % (start, end),
                auth=HTTPBasicAuth(user, password) if user else None,
                headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
                timeout=10,
            )
            if resp.status_code not in (200, 207):
                logger.error(f"CalDAV request failed: HTTP {resp.status_code}")
                return []
            # Extract every embedded <calendar-data> VEVENT block
            import re as _re
            blocks = _re.findall(r"<C:calendar-data[^>]*>(.*?)</C:calendar-data>", resp.text, _re.S)
            events = []
            for blk in blocks:
                evs = parse_ics_feed(blk, max_events=200)
                events.extend(evs)
            events.sort(key=lambda x: x["datetime"])
            return events[:200]
        except Exception as e:
            logger.error(f"CalDAV fetch failed: {e}")
            return []

    def _fetch_events(self, ics_url: str, caldav_url: str = "", caldav_user: str = "", caldav_pass: str = "") -> list[dict]:
        # Prefer CalDAV (structured multi-calendar fetch) when configured.
        if caldav_url and caldav_url.strip():
            evts = self._fetch_caldav(caldav_url, caldav_user, caldav_pass)
            if evts:
                return evts[:8]
        now = datetime.datetime.now()
        if not ics_url or not ics_url.strip():
            # Demo default calendar events if no URL provided
            return [
                {"summary": "Team Sync & Standup", "datetime": now.replace(hour=9, minute=30), "is_all_day": False},
                {"summary": "Product Design Review", "datetime": now.replace(hour=14, minute=0), "is_all_day": False},
                {"summary": "Family Dinner", "datetime": (now + datetime.timedelta(days=1)).replace(hour=18, minute=30), "is_all_day": False},
                {"summary": "Quarterly Planning", "datetime": (now + datetime.timedelta(days=2)).replace(hour=10, minute=0), "is_all_day": False},
                {"summary": "Dentist Appointment", "datetime": (now + datetime.timedelta(days=4)).replace(hour=11, minute=15), "is_all_day": False},
            ]
        try:
            req = urllib.request.Request(ics_url, headers={"User-Agent": "rndrSBC/1.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read().decode("utf-8", errors="ignore")
                return parse_ics_feed(content)
        except Exception as e:
            logger.error(f"Failed to fetch ICS feed: {e}")
            return [{"summary": "Unable to load iCal feed", "datetime": datetime.datetime.now(), "is_all_day": True}]

    def render(self, dimensions: tuple[int, int], settings: dict, bounds: Rect = None) -> Image.Image:
        title = settings.get("title", "My Schedule")
        ics_url = settings.get("ics_url", "")
        caldav_url = settings.get("caldav_url", "")
        caldav_user = settings.get("caldav_user", "")
        caldav_pass = settings.get("caldav_pass", "")
        frame_style = settings.get("frame", "Corner")
        start_sunday = settings.get("first_day_sunday", True)

        lang = settings.get("language", self.config.get("language", "en"))

        events = self._fetch_events(ics_url, caldav_url, caldav_user, caldav_pass)
        now = self.get_local_now(settings=settings)

        with ResponsiveCanvas(dimensions, bg_color="#ffffff") as canvas:
            content = bounds if bounds is not None else canvas.bounds.inset(canvas.pt(16))
            
            # Header + Body Split
            h_box, b_box = content.split_rows([1.0, 8.2], gap=canvas.pt(10))

            # 1. Header
            font_title = canvas.get_token_font("title")
            font_date = canvas.get_token_font("headline")
            font_sub = canvas.get_token_font("caption")
            
            canvas.draw_text(title, (h_box.x, h_box.y + canvas.pt(2)), font=font_title, fill="#000000")
            canvas.draw_text(now.strftime("%B %Y"), (h_box.center[0], h_box.y + canvas.pt(6)), font=font_date, fill="#000000", anchor="ma")
            canvas.draw_text(f"Updated: {now.strftime('%I:%M %p').lstrip('0')}", (h_box.right, h_box.y + canvas.pt(8)), font=font_sub, fill="#000000", anchor="ra")

            # 2. Main Columns: Left = Month Grid, Right = Upcoming Agenda
            cal_col, agenda_col = b_box.split_columns([4.8, 5.2], gap=canvas.pt(14))

            # --- LEFT: MONTH CALENDAR CARD ---
            canvas.draw_card(cal_col, radius=10, fill="#ffffff", outline="#000000", width=1)
            cal_inner = cal_col.inset(canvas.pt(12))
            
            # Month Header
            m_head_box, grid_box = cal_inner.split_rows([1.0, 6.5], gap=canvas.pt(8))
            font_cal_month = canvas.get_token_font("headline")
            canvas.draw_text(now.strftime("%A, %b %d"), (m_head_box.x, m_head_box.y + canvas.pt(2)), font=font_cal_month, fill="#000000")

            # Day Headers (S M T W T F S)
            days_letters = i18n.weekday_names(lang, start_sunday)
            day_header_box, days_matrix_box = grid_box.split_rows([1.0, 6.0], gap=canvas.pt(4))
            
            font_dh = canvas.get_token_font("metric")
            dh_cols = day_header_box.split_columns([1] * 7)
            for i, letter in enumerate(days_letters):
                fill_c = "#e65c00" if (start_sunday and i in [0, 6]) or (not start_sunday and i >= 5) else "#000000"
                canvas.draw_text(letter, dh_cols[i].center, font=font_dh, fill=fill_c, anchor="mm")

            # Calendar Matrix
            cal = calendar.Calendar(firstweekday=6 if start_sunday else 0)
            month_days = cal.monthdayscalendar(now.year, now.month)
            
            while len(month_days) < 6:
                month_days.append([0] * 7)

            row_boxes = days_matrix_box.split_rows([1] * 6, gap=canvas.pt(2))
            font_dnum = canvas.get_token_font("small")
            font_dnum_bold = canvas.get_token_font("metric")

            for r_idx, week in enumerate(month_days):
                cell_cols = row_boxes[r_idx].split_columns([1] * 7)
                event_days = {ev["datetime"].day for ev in events
                              if ev["datetime"].year == now.year and ev["datetime"].month == now.month}
                for c_idx, d_num in enumerate(week):
                    if d_num == 0:
                        continue
                    cell = cell_cols[c_idx]
                    if d_num == now.day:
                        badge_r = min(cell.w, cell.h) // 2 - canvas.pt(2)
                        canvas.draw.ellipse(
                            [cell.center[0] - badge_r, cell.center[1] - badge_r, cell.center[0] + badge_r, cell.center[1] + badge_r],
                            fill="#000000"
                        )
                        canvas.draw_text(str(d_num), cell.center, font=font_dnum_bold, fill="#ffffff", anchor="mm")
                    else:
                        canvas.draw_text(str(d_num), cell.center, font=font_dnum, fill="#000000", anchor="mm")
                    # event-day dot below the number
                    if d_num in event_days:
                        dot_cy = (cell.y + cell.h - canvas.pt(4) if d_num == now.day
                                  else cell.y + cell.h - canvas.pt(6))
                        canvas.draw.ellipse(
                            [cell.center[0] - canvas.pt(2), dot_cy - canvas.pt(2),
                             cell.center[0] + canvas.pt(2), dot_cy + canvas.pt(2)],
                            fill="#e65c00"
                        )

            # --- RIGHT: UPCOMING AGENDA CARD ---
            canvas.draw_card(agenda_col, radius=10, fill="#ffffff", outline="#000000", width=1)
            ag_inner = agenda_col.inset(canvas.pt(12))
            
            ag_head_box, ag_list_box = ag_inner.split_rows([1.0, 6.5], gap=canvas.pt(8))
            font_ag_t = canvas.get_token_font("headline")
            canvas.draw_text(i18n.label(lang, "upcoming_events"), (ag_head_box.x, ag_head_box.y + canvas.pt(2)), font=font_ag_t, fill="#000000")

            # Events List (Up to 5 items)
            display_events = events[:5]
            if not display_events:
                font_empty = canvas.get_token_font("body")
                canvas.draw_text(i18n.label(lang, "no_events"), ag_list_box.center, font=font_empty, fill="#666666", anchor="mm")
            else:
                ev_rows = ag_list_box.split_rows([1] * len(display_events), gap=canvas.pt(6))
                font_ev_title = canvas.get_token_font("body_bold")
                font_ev_date = canvas.get_token_font("small")
                font_ev_time = canvas.get_token_font("caption_bold")

                for i, ev in enumerate(display_events):
                    r_box = ev_rows[i]
                    canvas.draw_card(r_box, radius=6, fill="#ffffff", outline="#000000", width=1)
                    
                    # Split event row into Date Badge (Left) and Details (Right)
                    badge_col, detail_col = r_box.split_columns([2.5, 7.5], gap=canvas.pt(6))
                    
                    # Date / Time Badge
                    ev_dt = ev["datetime"]
                    day_str = ev_dt.strftime("%a %d").upper()
                    time_str = "ALL DAY" if ev["is_all_day"] else ev_dt.strftime("%I:%M %p").lstrip('0')

                    b_top, b_bot = badge_col.inset(canvas.pt(3)).split_rows([1, 1])
                    canvas.draw_text(day_str, b_top.center, font=font_ev_time, fill="#e65c00", anchor="mm")
                    canvas.draw_text(time_str, b_bot.center, font=font_ev_date, fill="#000000", anchor="mm")

                    # Vertical separator
                    canvas.draw.line([(detail_col.x - canvas.pt(3), r_box.y + canvas.pt(6)), (detail_col.x - canvas.pt(3), r_box.bottom - canvas.pt(6))], fill="#000000", width=1)

                    # Event Title
                    sum_text = ev["summary"]
                    if len(sum_text) > 28:
                        sum_text = sum_text[:26] + "..."
                    canvas.draw_text(sum_text, (detail_col.x + canvas.pt(4), detail_col.center[1]), font=font_ev_title, fill="#000000", anchor="lm")

            if bounds is None:
                canvas.draw_frame(frame_style, color="#000000")
            return canvas.to_image()
