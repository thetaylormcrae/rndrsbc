"""
rndrSBC - Lightweight i18n / localization
Zero-dependency translation map for widget labels, calendar weekdays, and units.
The active language comes from config (top-level "language", e.g. "es", "fr", "de")
and is overridable per-widget via its "language" setting.
"""

import logging

logger = logging.getLogger("rndrSBC.i18n")

# Short month names keyed by iOS-style locale tag -> ISO month 1..12
MONTHS = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "es": ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"],
    "fr": ["Janv", "Févr", "Mars", "Avr", "Mai", "Juin", "Juil", "Août", "Sept", "Oct", "Nov", "Déc"],
    "de": ["Jan", "Feb", "März", "Apr", "Mai", "Juni", "Juli", "Aug", "Sep", "Okt", "Nov", "Dez"],
    "it": ["Gen", "Feb", "Mar", "Apr", "Mag", "Giu", "Lug", "Ago", "Set", "Ott", "Nov", "Dic"],
    "pt": ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"],
    "nl": ["Jan", "Feb", "Mrt", "Apr", "Mei", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"],
    "pl": ["Sty", "Lut", "Mar", "Kwi", "Maj", "Cze", "Lip", "Sie", "Wrz", "Paź", "Lis", "Gru"],
    "tr": ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"],
}

# Short weekday names, index 0 = Monday
WEEKDAYS_MON = {
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "es": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
    "fr": ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"],
    "de": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    "it": ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"],
    "pt": ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"],
    "nl": ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"],
    "tr": ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"],
}

# Sunday-start weekday labels
WEEKDAYS_SUN = {
    "en": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
    "es": ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"],
    "fr": ["Dim", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"],
    "de": ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"],
    "it": ["Dom", "Lun", "Mar", "Mer", "Gio", "Ven", "Sab"],
    "pt": ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"],
    "nl": ["Zo", "Ma", "Di", "Wo", "Do", "Vr", "Za"],
    "tr": ["Paz", "Pzt", "Sal", "Çar", "Per", "Cum", "Cmt"],
}

# Generic widget-label translations.
LABELS = {
    "en": {
        "upcoming_events": "Upcoming Events", "no_events": "No upcoming events scheduled",
        "updated": "Updated", "all_day": "ALL DAY", "today": "Today", "tomorrow": "Tomorrow",
        "weather_now": "Now", "feels_like": "feels like", "humidity": "Humidity",
        "wind": "Wind", "pressure": "Pressure", "forecast": "Forecast",
        "unable_ical": "Unable to load calendar feed", "system": "System",
        "up": "Up", "cpu": "CPU", "mem": "Memory", "disk": "Disk",
        "network": "Network", "signal": "Signal", "ip": "IP", "dns": "DNS",
        "width": "Width", "height": "Height",
    },
    "es": {
        "upcoming_events": "Próximos eventos", "no_events": "No hay eventos próximos",
        "updated": "Actualizado", "all_day": "TODO EL DÍA", "today": "Hoy", "tomorrow": "Mañana",
        "weather_now": "Ahora", "feels_like": "sensación", "humidity": "Humedad",
        "wind": "Viento", "pressure": "Presión", "forecast": "Pronóstico",
        "unable_ical": "No se pudo cargar la agenda", "system": "Sistema",
        "up": "Encendido", "cpu": "CPU", "mem": "Memoria", "disk": "Disco",
        "network": "Red", "signal": "Señal", "ip": "IP", "dns": "DNS",
        "width": "Ancho", "height": "Alto",
    },
    "fr": {
        "upcoming_events": "Événements à venir", "no_events": "Aucun événement à venir",
        "updated": "Mis à jour", "all_day": "TOUTE LA JOURNÉE", "today": "Aujourd'hui", "tomorrow": "Demain",
        "weather_now": "Maintenant", "feels_like": "ressenti", "humidity": "Humidité",
        "wind": "Vent", "pressure": "Pression", "forecast": "Prévisions",
        "unable_ical": "Impossible de charger l'agenda", "system": "Système",
        "up": "Allumé", "cpu": "CPU", "mem": "Mémoire", "disk": "Disque",
        "network": "Réseau", "signal": "Signal", "ip": "IP", "dns": "DNS",
        "width": "Largeur", "height": "Hauteur",
    },
    "de": {
        "upcoming_events": "Kommende Termine", "no_events": "Keine kommenden Termine",
        "updated": "Aktualisiert", "all_day": "GANZTÄGIG", "today": "Heute", "tomorrow": "Morgen",
        "weather_now": "Jetzt", "feels_like": "gefühlt", "humidity": "Luftfeuchte",
        "wind": "Wind", "pressure": "Druck", "forecast": "Vorhersage",
        "unable_ical": "Kalender konnte nicht geladen werden", "system": "System",
        "up": "Betrieb", "cpu": "CPU", "mem": "Speicher", "disk": "Festplatte",
        "network": "Netzwerk", "signal": "Signal", "ip": "IP", "dns": "DNS",
        "width": "Breite", "height": "Höhe",
    },
    "tr": {
        "upcoming_events": "Yaklaşan Etkinlikler", "no_events": "Yaklaşan etkinlik yok",
        "updated": "Güncellendi", "all_day": "TÜM GÜN", "today": "Bugün", "tomorrow": "Yarın",
        "weather_now": "Şimdi", "feels_like": "hissedilen", "humidity": "Nem",
        "wind": "Rüzgar", "pressure": "Basınç", "forecast": "Tahmin",
        "unable_ical": "Takvim yüklenemedi", "system": "Sistem",
        "up": "Açık", "cpu": "CPU", "mem": "Bellek", "disk": "Disk",
        "network": "Ağ", "signal": "Sinyal", "ip": "IP", "dns": "DNS",
        "width": "Genişlik", "height": "Yükseklik",
    },
}

SUPPORTED = sorted(set(MONTHS) & set(WEEKDAYS_MON) & set(WEEKDAYS_SUN) & set(LABELS))


def normalize(lang: str) -> str:
    code = (lang or "en").split("-")[0].split("_")[0].lower()
    return code if code in SUPPORTED else "en"


def month_name(lang: str, month_idx: int) -> str:
    """month_idx is 1..12 (ISO). Returns localized short month."""
    code = normalize(lang)
    table = MONTHS.get(code, MONTHS["en"])
    return table[(month_idx - 1) % 12]


def weekday_names(lang: str, start_sunday: bool) -> list[str]:
    code = normalize(lang)
    if start_sunday:
        return WEEKDAYS_SUN.get(code, WEEKDAYS_SUN["en"])
    return WEEKDAYS_MON.get(code, WEEKDAYS_MON["en"])


def label(lang: str, key: str, fallback: str = None) -> str:
    code = normalize(lang)
    table = LABELS.get(code, LABELS["en"])
    val = table.get(key)
    if val is None:
        return fallback or LABELS["en"].get(key, key)
    return val
