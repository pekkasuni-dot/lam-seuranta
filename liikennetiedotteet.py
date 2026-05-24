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

# Luokitellaan tiedote sen sisällön perusteella → ikoni, väri, otsikko
# Avainsanat tarkistetaan announcements[0].features[*].name -listasta
# JA otsikosta/kuvauksesta varmuuden vuoksi (suomenkieliset osumat).
LUOKAT = {
    "ACCIDENT": {
        "nimi":  "Onnettomuus",
        "vari":  "#DC1E1E",     # punainen
        "ikoni": "🚨",
        "avainsanat": [
            "onnettomuus", "kolari", "törmäys", "ulosajo", "ojaanajo",
            "accident", "crash", "collision",
        ],
    },
    "OBSTACLE": {
        "nimi":  "Este tiellä",
        "vari":  "#FF7800",     # oranssi
        "ikoni": "⚠",
        "avainsanat": [
            "este", "eläin", "hirvi", "poro", "esine", "ajoneuvo tiellä",
            "obstacle", "animal", "object on road",
        ],
    },
    "ROAD_CLOSED": {
        "nimi":  "Tie suljettu",
        "vari":  "#8B0000",     # tummanpunainen
        "ikoni": "⛔",
        "avainsanat": [
            "suljettu", "tie poikki", "closed", "blocked",
        ],
    },
    "WEATHER": {
        "nimi":  "Sää- tai keliolosuhde",
        "vari":  "#2E6FBF",     # sininen
        "ikoni": "❄",
        "avainsanat": [
            "liukas", "lumi", "jää", "sade", "tuuli", "sumu", "myrsky",
            "weather", "slippery", "snow", "ice", "fog",
        ],
    },
    "TRAFFIC_JAM": {
        "nimi":  "Ruuhka",
        "vari":  "#FFD200",     # keltainen
        "ikoni": "🚗",
        "avainsanat": [
            "ruuhka", "jono", "hidas", "congestion", "queue", "slow traffic",
        ],
    },
    "OTHER": {
        "nimi":  "Muu tiedote",
        "vari":  "#888888",     # harmaa
        "ikoni": "ℹ",
        "avainsanat": [],
    },
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


def _luokittele(announcement_type, feature_names, title, description):
    """
    Päättele luokka useammasta kentästä:
    1) trafficAnnouncementType ("accident_report", "general", "preliminary_accident_report")
    2) features[].name -lista
    3) otsikko ja kuvaus tekstihakuna
    """
    # 1) trafficAnnouncementType
    atype = (announcement_type or "").lower()
    if "accident" in atype:
        return "ACCIDENT"

    # 2 + 3) Tekstihaku — kerätään kaikki tekstit yhteen ja etsitään avainsanat
    teksti = " ".join([
        " ".join(feature_names or []),
        title or "",
        description or "",
    ]).lower()

    # Käydään luokat priorisoidussa järjestyksessä:
    # vakavimmat ensin, jotta esim. "onnettomuus + ruuhka" → ACCIDENT
    for luokka in ["ROAD_CLOSED", "ACCIDENT", "OBSTACLE", "WEATHER", "TRAFFIC_JAM"]:
        for avainsana in LUOKAT[luokka]["avainsanat"]:
            if avainsana in teksti:
                return luokka

    return "OTHER"


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

    # Features ja vakavuus
    features_list = ann.get("features") or []
    severity = "UNKNOWN"
    feature_names = []
    for ft in features_list:
        if isinstance(ft, dict):
            name = ft.get("name", "")
            sev = ft.get("severity", "UNKNOWN")
            if name:
                feature_names.append(name)
            order = ["LOW", "NORMAL", "HIGH", "HIGHEST"]
            if (sev in order and
                (severity not in order or order.index(sev) > order.index(severity))):
                severity = sev

    title = (ann.get("title") or "").strip()
    description = (location.get("description") or "").strip()
    announcement_type = props.get("trafficAnnouncementType", "")

    luokka = _luokittele(announcement_type, feature_names, title, description)

    point = _geometrian_keskipiste(feature.get("geometry"))

    return {
        "id":          props.get("situationId", ""),
        "title":       title,
        "description": description,
        "road":        road_address.get("roadName") or "",
        "road_number": road_address.get("roadNumber"),
        "municipality": road_address.get("municipality") or "",
        "province":    road_address.get("province") or "",
        "severity":    severity,
        "luokka":      luokka,
        "ann_type":    announcement_type,
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
    luokka = LUOKAT.get(t["luokka"], LUOKAT["OTHER"])
    vari = luokka["vari"]

    rivit = []
    rivit.append(
        f"<div style='font-size:11px;color:{vari};font-weight:600;"
        f"text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px'>"
        f"{luokka['ikoni']} {luokka['nimi']}</div>"
    )
    if t["title"]:
        rivit.append(
            f"<b style='font-size:13px;color:#222'>{escape(t['title'])}</b>"
        )
    if t["description"] and t["description"] != t["title"]:
        rivit.append(
            f"<div style='font-size:12px;margin-top:4px;color:#444'>"
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
        f"<div style='font-family:sans-serif;min-width:240px;max-width:340px'>"
        + "".join(rivit)
        + "</div>"
    )


def _tooltip(t):
    luokka = LUOKAT.get(t["luokka"], LUOKAT["OTHER"])
    osat = [f"{luokka['ikoni']} {luokka['nimi']}"]
    if t["title"]:
        osat.append(t["title"])
    if t["road"]:
        osat.append(t["road"])
    return " · ".join(osat)


def _ikoni_div_html(luokka_info):
    """
    Rakenna selkeästi erottuva ikoni-DIV:
    valkoinen tausta + värillinen reuna + ikoni keskellä.
    """
    return (
        f"<div style='"
        f"display:flex;align-items:center;justify-content:center;"
        f"width:30px;height:30px;border-radius:50%;"
        f"background:#ffffff;"
        f"border:3px solid {luokka_info['vari']};"
        f"box-shadow:0 0 4px rgba(0,0,0,0.6);"
        f"font-size:16px;line-height:1;'>"
        f"{luokka_info['ikoni']}"
        f"</div>"
    )


def lisaa_tiedotteet_kartalle(kartta, tiedotteet, show=True):
    """
    Lisää tiedotteet Folium-kartalle omana FeatureGroupina.
    Palauttaa lukumäärän luokittain.
    """
    layer = folium.FeatureGroup(name="⚠️ Liikennetiedotteet", show=show)
    lkm_luokka = {k: 0 for k in LUOKAT}

    for t in tiedotteet:
        if not t["point"]:
            continue
        lon, lat = t["point"]
        luokka_info = LUOKAT.get(t["luokka"], LUOKAT["OTHER"])
        lkm_luokka[t["luokka"]] = lkm_luokka.get(t["luokka"], 0) + 1

        # Piirrä reitti/alue jos LineString
        geom = t["geometry"]
        if geom and geom.get("type") == "LineString":
            coords = [[c[1], c[0]] for c in geom["coordinates"]]
            folium.PolyLine(
                locations=coords, color=luokka_info["vari"],
                weight=5, opacity=0.75,
            ).add_to(layer)
        elif geom and geom.get("type") == "MultiLineString":
            for line in geom["coordinates"]:
                coords = [[c[1], c[0]] for c in line]
                folium.PolyLine(
                    locations=coords, color=luokka_info["vari"],
                    weight=5, opacity=0.75,
                ).add_to(layer)

        # Pyöreä, valkoinen ja värireunainen ikoni
        folium.Marker(
            location=[lat, lon],
            icon=folium.DivIcon(
                html=_ikoni_div_html(luokka_info),
                icon_size=(30, 30),
                icon_anchor=(15, 15),
            ),
            popup=folium.Popup(_popup_html(t), max_width=360),
            tooltip=_tooltip(t),
        ).add_to(layer)

    layer.add_to(kartta)
    return lkm_luokka
