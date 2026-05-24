"""
liikennetiedotteet.py
=====================
Hakee Digitrafficista aktiiviset liikennetiedotteet (TRAFFIC_ANNOUNCEMENT)
ja piirtää ne Folium-kartalle omana layerinään.

Datalähde: https://tie.digitraffic.fi/api/traffic-message/v1/messages
Lisenssi: CC BY 4.0 — Liikenteenohjausyhtiö Fintraffic / Digitraffic
"""

import json
import urllib.request
import urllib.parse
import gzip
from datetime import datetime, timezone
from html import escape

import folium
import streamlit as st

# ─────────────────────────────────────────────────────────────────
# ASETUKSET
# ─────────────────────────────────────────────────────────────────

API_URL = "https://tie.digitraffic.fi/api/traffic-message/v1/messages"

HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
    "Digitraffic-User": "LAM-seuranta/1.0",
}

# Tiedotteen vakavuusluokat → väri (sopii VARIT-paletille)
VARIT_VAKAVUUS = {
    "HIGHEST": "#DC1E1E",   # esim. tieosuus suljettu
    "HIGH":    "#FF7800",   # onnettomuus, este
    "NORMAL":  "#FFD200",
    "LOW":     "#2D9E47",
    "UNKNOWN": "#888888",
}

# ─────────────────────────────────────────────────────────────────
# HAKU
# ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=120, show_spinner=False)
def hae_tiedotteet():
    """Hae kaikki aktiiviset TRAFFIC_ANNOUNCEMENT-tiedotteet."""
    params = {
        "situationType": "TRAFFIC_ANNOUNCEMENT",
        "inactiveHours": 0,
        "includeAreaGeometry": "false",
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8")).get("features", [])
    except Exception as e:
        st.warning(f"Liikennetiedotteiden haku epäonnistui: {e}")
        return []


# ─────────────────────────────────────────────────────────────────
# SUODATUS & PARSINTA
# ─────────────────────────────────────────────────────────────────

def _piste_bboxissa(lon, lat, bbox):
    lon_min, lat_min, lon_max, lat_max = bbox
    return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max


def _geometrian_keskipiste(geom):
    """Palauttaa (lon, lat) tai None."""
    if not geom:
        return None
    coords = geom.get("coordinates")
    if not coords:
        return None
    gtype = geom.get("type")
    if gtype == "Point":
        return coords[0], coords[1]
    if gtype == "LineString":
        return coords[0][0], coords[0][1]
    if gtype == "MultiLineString" and coords and coords[0]:
        return coords[0][0][0], coords[0][0][1]
    if gtype == "Polygon" and coords and coords[0]:
        return coords[0][0][0], coords[0][0][1]
    return None


def _bboxissa(feature, bbox):
    p = _geometrian_keskipiste(feature.get("geometry"))
    return p is not None and _piste_bboxissa(p[0], p[1], bbox)


def _parsi_tiedote(feature):
    """Poimi olennainen yhteenveto yhdestä tiedotteesta."""
    props = feature.get("properties", {}) or {}
    announcements = props.get("announcements") or [{}]
    ann = announcements[0]

    location = ann.get("location") or {}
    location_details = ann.get("locationDetails") or {}
    road_address = (
        location_details.get("roadAddressLocation", {}) or {}
    ).get("primaryPoint", {}) or {}

    # Ajankohta
    time_period = ann.get("timeAndDuration") or {}
    start = time_period.get("startTime")

    # Vakavuus
    features_list = ann.get("features") or []
    severity = "UNKNOWN"
    feature_names = []
    for ft in features_list:
        if isinstance(ft, dict):
            name = ft.get("name", "")
            sev = ft.get("severity", "UNKNOWN")
            if name:
                feature_names.append(name)
            # Pidä korkein vakavuus
            order = ["LOW", "NORMAL", "HIGH", "HIGHEST"]
            if (sev in order and
                (severity not in order or order.index(sev) > order.index(severity))):
                severity = sev

    point = _geometrian_keskipiste(feature.get("geometry"))

    return {
        "id":          props.get("situationId", ""),
        "title":       (ann.get("title") or "").strip(),
        "description": (location.get("description") or "").strip(),
        "road":        road_address.get("roadName") or "",
        "road_number": road_address.get("roadNumber"),
        "municipality": road_address.get("municipality") or "",
        "province":    road_address.get("province") or "",
        "severity":    severity,
        "features":    feature_names,
        "start_time":  start,
        "release_time": props.get("releaseTime"),
        "point":       point,  # (lon, lat) tai None
        "geometry":    feature.get("geometry"),
    }


def hae_alueen_tiedotteet(bbox):
    """Hae ja suodata tiedotteet annetulle bboxille."""
    kaikki = hae_tiedotteet()
    osumat = [f for f in kaikki if _bboxissa(f, bbox)]
    return [_parsi_tiedote(f) for f in osumat]


# ─────────────────────────────────────────────────────────────────
# KARTTALAYER
# ─────────────────────────────────────────────────────────────────

def _aika_fi(iso_str):
    """ISO-aika → 'dd.mm. HH:MM' Suomen aikaa."""
    if not iso_str:
        return ""
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        dt = dt.astimezone(ZoneInfo("Europe/Helsinki"))
        return dt.strftime("%d.%m. %H:%M")
    except Exception:
        return iso_str


def _popup_html(t):
    """Rakenna HTML-popup yhdelle tiedotteelle."""
    vari = VARIT_VAKAVUUS.get(t["severity"], "#888")
    rivit = []
    if t["title"]:
        rivit.append(
            f"<b style='font-size:13px;color:{vari}'>"
            f"{escape(t['title'])}</b>"
        )
    if t["description"] and t["description"] != t["title"]:
        rivit.append(
            f"<div style='font-size:12px;margin-top:4px'>"
            f"{escape(t['description'])}</div>"
        )
    meta = []
    if t["road"]:
        tienro = f" (tie {t['road_number']})" if t["road_number"] else ""
        meta.append(f"<b>Sijainti:</b> {escape(t['road'])}{tienro}")
    if t["municipality"]:
        meta.append(f"<b>Kunta:</b> {escape(t['municipality'])}")
    if t["start_time"]:
        meta.append(f"<b>Alkoi:</b> {_aika_fi(t['start_time'])}")
    if t["release_time"]:
        meta.append(f"<b>Päivitetty:</b> {_aika_fi(t['release_time'])}")
    if meta:
        rivit.append(
            "<hr style='margin:6px 0;border-color:#ddd'>"
            "<div style='font-size:11px;color:#555;line-height:1.6'>"
            + "<br>".join(meta)
            + "</div>"
        )
    return (
        f"<div style='font-family:sans-serif;min-width:220px;max-width:320px'>"
        + "".join(rivit)
        + "</div>"
    )


def _tooltip(t):
    osat = [t["title"] or "Liikennetiedote"]
    if t["road"]:
        osat.append(t["road"])
    return " · ".join(osat)


def lisaa_tiedotteet_kartalle(kartta, tiedotteet, show=True):
    """
    Lisää tiedotteet Folium-kartalle omana FeatureGroupina.
    Palauttaa lukumäärän vakavuusluokittain.
    """
    layer = folium.FeatureGroup(name="⚠️ Liikennetiedotteet", show=show)
    lkm_vakavuus = {k: 0 for k in VARIT_VAKAVUUS}

    for t in tiedotteet:
        if not t["point"]:
            continue
        lon, lat = t["point"]
        vari = VARIT_VAKAVUUS.get(t["severity"], "#888")
        lkm_vakavuus[t["severity"]] = lkm_vakavuus.get(t["severity"], 0) + 1

        # Piirrä reitti/alue jos LineString
        geom = t["geometry"]
        if geom and geom.get("type") == "LineString":
            coords = [[c[1], c[0]] for c in geom["coordinates"]]
            folium.PolyLine(
                locations=coords, color=vari, weight=4, opacity=0.7,
            ).add_to(layer)
        elif geom and geom.get("type") == "MultiLineString":
            for line in geom["coordinates"]:
                coords = [[c[1], c[0]] for c in line]
                folium.PolyLine(
                    locations=coords, color=vari, weight=4, opacity=0.7,
                ).add_to(layer)

        # Varoituskolmio-merkki (Folium DivIcon)
        ikoni_html = (
            f"<div style='font-size:20px;color:{vari};"
            f"text-shadow:0 0 3px #000,0 0 3px #000;line-height:1'>⚠</div>"
        )
        folium.Marker(
            location=[lat, lon],
            icon=folium.DivIcon(
                html=ikoni_html,
                icon_size=(24, 24),
                icon_anchor=(12, 12),
            ),
            popup=folium.Popup(_popup_html(t), max_width=340),
            tooltip=_tooltip(t),
        ).add_to(layer)

    layer.add_to(kartta)
    return lkm_vakavuus
