"""
LAM-evakuointiseuranta - Streamlit-sovellus
============================================
Reaaliaikainen liikenteen poikkeamaseuranta Pohteen alueella.
Baseline: trimmattu keskiarvo neljältä edelliseltä normaalilta viikolta
          (pyhäpäivät ohitetaan automaattisesti).

Vaatimukset (requirements.txt):
  streamlit
  folium
  streamlit-folium
  requests
"""

import streamlit as st
import folium
from streamlit_folium import st_folium
import json
import gzip
import csv
import io
import math
import threading
import urllib.request
import urllib.error
from streamlit_autorefresh import st_autorefresh
from datetime import datetime, timezone, timedelta, date

# ─────────────────────────────────────────────────────────────────
# SIVUN ASETUKSET
# ─────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="LAM-liikenneseuranta",
    page_icon="🚦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────
# SALASANASUOJAUS
# ─────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────
# ASETUKSET
# ─────────────────────────────────────────────────────────────────

BASE_URL       = "https://tie.digitraffic.fi"
ALUE_BBOX      = (23.5, 63.7, 29.5, 70.1)

# Aluerajaukset: (bbox, kartan_keskipiste, zoom)
ALUEET = {
    "Koko alue": (ALUE_BBOX,               [65.5, 26.0], 6),
    "Pohde":     ((23.5, 63.7, 29.5, 66.5), [65.0, 25.5], 7),
    "Lappi":     ((23.5, 66.5, 29.5, 70.1), [68.0, 26.5], 7),
}

RAJA_LIEVA     = 30
RAJA_KORKEA    = 80
RAJA_KRIITTINEN = 150
HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip",
    "Digitraffic-User": "LAM-seuranta/1.0",
}

VARIT = {
    "KRIITTINEN": "#DC1E1E",
    "KORKEA":     "#FF7800",
    "LIEVA":      "#FFD200",
    "NORMAALI":   "#32B450",
    "LASKU":      "#3C82DC",
    "EI_DATAA":   "#A0A0A0",
}
SELITTEET = {
    "KRIITTINEN": f"Kriittinen (>{RAJA_KRIITTINEN}%)",
    "KORKEA":     f"Korkea (+{RAJA_KORKEA}–{RAJA_KRIITTINEN}%)",
    "LIEVA":      f"Lievä (+{RAJA_LIEVA}–{RAJA_KORKEA}%)",
    "NORMAALI":   f"Normaali (±{RAJA_LIEVA}%)",
    "LASKU":      f"Lasku (>{RAJA_LIEVA}%)",
    "EI_DATAA":   "Ei historiadataa",
}

# ─────────────────────────────────────────────────────────────────
# VERKKO
# ─────────────────────────────────────────────────────────────────

def hae_bytes(url, timeout=25):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if raw[:2] == b'\x1f\x8b':
            raw = gzip.decompress(raw)
        return raw
    except Exception:
        return None

def hae_json(url, timeout=30):
    raw = hae_bytes(url, timeout)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────
# SUPABASE
# ─────────────────────────────────────────────────────────────────

def sb_request(method, path, body=None):
    url = st.secrets["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    key = st.secrets["SUPABASE_KEY"]
    headers = {
        "apikey":        key,
        "Authorization": "Bearer " + key,
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates",
    }
    data = json.dumps(body).encode("utf-8") if body else None
    req  = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else []
    except Exception:
        return None

def hae_tanaan_supabasesta(sid, nyt_fin):
    pvm_str = nyt_fin.date().isoformat()
    path    = f"tuntidata?sid=eq.{sid}&pvm=eq.{pvm_str}&order=tunti.asc"
    rivit   = sb_request("GET", path)
    if not rivit:
        return []
    pvm = nyt_fin.date()
    tulokset = []
    for r in rivit:
        t    = int(r["tunti"])
        aika = datetime(pvm.year, pvm.month, pvm.day, t, 0, 0, tzinfo=timezone.utc)
        tulokset.append({
            "pvm":   pvm,
            "tunti": t,
            "aika":  aika,
            "s1":    float(r["s1"]),
            "s2":    float(r["s2"]),
            "yht":   float(r["s1"]) + float(r["s2"]),
        })
    return tulokset

# ─────────────────────────────────────────────────────────────────
# PYHÄPÄIVÄT
# ─────────────────────────────────────────────────────────────────

def laske_paasiainen(vuosi):
    a = vuosi % 19; b = vuosi // 100; c = vuosi % 100
    d = b // 4; e = b % 4; f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30; i = c // 4; k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 22 * l) // 451
    kk = (h + l - 7 * m + 114) // 31; pv = ((h + l - 7 * m + 114) % 31) + 1
    return date(vuosi, kk, pv)

def laske_pyhat(vuosi):
    pyhat = set()
    for kk, pv in [(1,1),(1,6),(5,1),(12,6),(12,24),(12,25),(12,26)]:
        pyhat.add(date(vuosi, kk, pv))
    p = laske_paasiainen(vuosi)
    for d in [-2, 0, 1, 39, 49]:
        pyhat.add(p + timedelta(days=d))
    juhannus = date(vuosi, 6, 20)
    while juhannus.weekday() != 5:
        juhannus += timedelta(days=1)
    pyhat.add(juhannus - timedelta(days=1))
    pyhat.add(juhannus)
    return pyhat

def on_poikkeava_paiva(pvm):
    if pvm.weekday() >= 5:
        return False
    pyhat = set()
    for v in [pvm.year - 1, pvm.year, pvm.year + 1]:
        pyhat |= laske_pyhat(v)
    vappu = date(pvm.year, 5, 1)
    if vappu.weekday() >= 5:
        pyhat.discard(vappu)
    if pvm in pyhat:
        return True
    ed = pvm - timedelta(days=1)
    while ed.weekday() >= 5: ed -= timedelta(days=1)
    seu = pvm + timedelta(days=1)
    while seu.weekday() >= 5: seu += timedelta(days=1)
    return ed in pyhat or seu in pyhat

def etsi_normaalit_paivat(nyt_fin, maara=4, max_viikkoja=16):
    normaalit, ohitetut = [], []
    viikko = 1
    while len(normaalit) < maara and viikko <= max_viikkoja:
        kand = (nyt_fin - timedelta(weeks=viikko)).date()
        if on_poikkeava_paiva(kand):
            ohitetut.append(kand)
        else:
            normaalit.append(kand)
        viikko += 1
    return normaalit, ohitetut

# ─────────────────────────────────────────────────────────────────
# DATA-HAKU
# ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)  # 5 min välimuisti reaaliaikadatalle
def hae_reaaliaikadata():
    data = hae_json(f"{BASE_URL}/api/tms/v1/stations/data")
    if not data:
        return {}
    tulos = {}
    for st_item in data.get("stations", []):
        sid = st_item.get("id")
        if sid:
            tulos[sid] = {
                s.get("name", ""): s.get("value")
                for s in st_item.get("sensorValues", [])
                if s.get("name") and s.get("value") is not None
            }
    return tulos

@st.cache_data(ttl=3600)  # 1h välimuisti asematiedoille
def hae_asemat():
    data = hae_json(f"{BASE_URL}/api/tms/v1/stations")
    if not data:
        return {}
    asemat = {}
    xmin, ymin, xmax, ymax = ALUE_BBOX
    for f in data.get("features", []):
        props  = f.get("properties", {})
        sid    = props.get("id")
        tnum   = props.get("tmsNumber")
        coords = f.get("geometry", {}).get("coordinates", [None, None])
        if sid and coords[0] and coords[1]:
            lon, lat = coords[0], coords[1]
            if xmin <= lon <= xmax and lat >= ymin:
                asemat[sid] = {
                    "id": sid, "tmsNum": tnum,
                    "nimi": props.get("name", f"Asema {sid}"),
                    "tie": props.get("roadNumber"),
                    "lon": lon, "lat": lat,
                    "kunta": props.get("municipality", ""),
                    "tila": props.get("collectionStatus", ""),
                }
    return asemat

@st.cache_data(ttl=3600)  # 1h välimuisti suuntavakioille
def hae_suuntavakiot(asemat_ids_tuple):
    asemat_ids = set(asemat_ids_tuple)
    data = hae_json(f"{BASE_URL}/api/tms/v1/stations/sensor-constants")
    if not data:
        return {}
    kulmat = {}
    asema_lista = data.get("stations", []) if isinstance(data, dict) else data
    for asema in asema_lista:
        if not isinstance(asema, dict):
            continue
        sid = asema.get("id")
        if sid not in asemat_ids:
            continue
        for vakio in asema.get("sensorConstantValues", []):
            if vakio.get("name") == "Tien_suunta":
                try:
                    kulmat[sid] = float(vakio.get("value", 0))
                except (TypeError, ValueError):
                    pass
                break
    return kulmat

def csv_hae_tunti(tms_num, pvm, tunti):
    if tms_num is None:
        return None, None
    yy  = str(pvm.year)[-2:].lstrip("0") or "0"
    doy = pvm.timetuple().tm_yday
    url = f"{BASE_URL}/api/tms/v1/history/raw/lamraw_{tms_num}_{yy}_{doy}.csv"
    raw = hae_bytes(url, timeout=20)
    if raw is None:
        return None, None
    try:
        s1 = s2 = 0
        for rivi in csv.reader(
                io.StringIO(raw.decode("utf-8", errors="replace")), delimiter=";"):
            if len(rivi) < 13:
                continue
            try:
                if int(rivi[3]) == tunti and int(rivi[12]) == 0:
                    if int(rivi[9]) == 1:   s1 += 1
                    elif int(rivi[9]) == 2: s2 += 1
            except ValueError:
                continue
        return (float(s1) if s1 > 0 else None,
                float(s2) if s2 > 0 else None)
    except Exception:
        return None, None

def trimmattu_keskiarvo(arvot):
    arvot = [a for a in arvot if a is not None]
    if len(arvot) >= 4:
        arvot_j = sorted(arvot)
        return sum(arvot_j[1:-1]) / len(arvot_j[1:-1])
    elif len(arvot) == 3:
        return sum(arvot) / 3
    return None

def _hae_aseman_baseline_thread(sid, tms_num, normaalit_pvmt, tunti, tulokset, lukko):
    s1v, s2v = [], []
    for pvm in normaalit_pvmt:
        s1, s2 = csv_hae_tunti(tms_num, pvm, tunti)
        if s1 is not None: s1v.append(s1)
        if s2 is not None: s2v.append(s2)
    b_s1 = trimmattu_keskiarvo(s1v)
    b_s2 = trimmattu_keskiarvo(s2v)
    if b_s1 is None and b_s2 is None:
        bl = {"yht": None, "s1": None, "s2": None, "ok": False,
              "arvot_s1": "", "arvot_s2": ""}
    else:
        if b_s1 is None: b_s1 = b_s2
        if b_s2 is None: b_s2 = b_s1
        bl = {"yht": round(b_s1+b_s2,1), "s1": round(b_s1,1), "s2": round(b_s2,1),
              "ok": True,
              "arvot_s1": ";".join(f"{v:.0f}" for v in sorted(s1v)),
              "arvot_s2": ";".join(f"{v:.0f}" for v in sorted(s2v))}
    with lukko:
        tulokset[sid] = bl

@st.cache_data(ttl=3600, show_spinner=False)
def hae_baselineet(asemat_tuple, tunti, normaalit_pvmt_tuple):
    """Baseline välimuistitetaan tunnin ja päivien yhdistelmällä."""
    asemat        = dict(asemat_tuple)
    normaalit_pvmt = list(normaalit_pvmt_tuple)
    tulokset = {}
    lukko    = threading.Lock()
    MAX_SAIK = 10
    asema_lista = list(asemat.items())
    for i in range(0, len(asema_lista), MAX_SAIK):
        era = asema_lista[i:i+MAX_SAIK]
        saikeet = []
        for sid, asema in era:
            t = threading.Thread(
                target=_hae_aseman_baseline_thread,
                args=(sid, asema.get("tmsNum"), normaalit_pvmt, tunti, tulokset, lukko),
                daemon=True
            )
            saikeet.append(t)
            t.start()
        for t in saikeet:
            t.join()
    return tulokset

# ─────────────────────────────────────────────────────────────────
# LASKENTA
# ─────────────────────────────────────────────────────────────────

def laske_liikennedata(sdata):
    s1 = s2 = 0.0
    nopeudet = []
    for suunta in ["SUUNTA1", "SUUNTA2"]:
        v60 = sdata.get(f"OHITUKSET_60MIN_KIINTEA_{suunta}")
        v5  = sdata.get(f"OHITUKSET_5MIN_LIUKUVA_{suunta}")
        val = float(v60) if v60 is not None else (float(v5)*12 if v5 is not None else 0.0)
        if suunta == "SUUNTA1": s1 = val
        else: s2 = val
        nop = sdata.get(f"KESKINOPEUS_60MIN_KIINTEA_{suunta}")
        if nop and nop > 0: nopeudet.append(float(nop))
    nopeus = sum(nopeudet)/len(nopeudet) if nopeudet else 0.0
    return s1+s2, s1, s2, nopeus

def pct(nykyinen, baseline):
    if baseline and baseline > 0:
        return round((nykyinen - baseline) / baseline * 100, 1)
    return 0.0

def poikkeama_luokka(p):
    if p >= RAJA_KRIITTINEN: return "KRIITTINEN"
    elif p >= RAJA_KORKEA:   return "KORKEA"
    elif p >= RAJA_LIEVA:    return "LIEVA"
    elif p <= -RAJA_LIEVA:   return "LASKU"
    return "NORMAALI"

def kulma_siirto(lon, lat, kulma_asteet, pituus):
    rad = math.radians(kulma_asteet)
    dlon = pituus * math.sin(rad) / math.cos(math.radians(lat))
    dlat = pituus * math.cos(rad)
    return lon + dlon, lat + dlat

# ─────────────────────────────────────────────────────────────────
# KARTTA
# ─────────────────────────────────────────────────────────────────

def luo_kartta(asemat, rtdata, baselineet, kulmat,
               keskipiste=None, zoom=6):
    """Luo Folium-kartan LAM-pisteistä, suuntanuolista ja luokkasuodattimesta."""
    kartta = folium.Map(
        location=keskipiste or [65.5, 26.0],
        zoom_start=zoom,
        tiles="CartoDB positron",
        control_scale=True,
    )
    from folium.plugins import Fullscreen
    Fullscreen(
        position="topleft",
        title="Koko ruutu",
        title_cancel="Poistu koko ruudusta",
        force_separate_button=True,
    ).add_to(kartta)

    koot = {"KRIITTINEN": 14, "KORKEA": 12, "LIEVA": 10,
            "NORMAALI": 7, "LASKU": 9, "EI_DATAA": 6}

    yht_lkm = {k: 0 for k in VARIT}

    # Nuolilayer (yksi, kaikkien alla)
    nuoli_layer = folium.FeatureGroup(name="Suuntanuolet", show=True)

    # Luokkakohtaiset layerit pisteitä varten – voi kytkeä päälle/pois kartalla
    luokka_layerit = {
        luokka: folium.FeatureGroup(
            name=f"{emoji} {SELITTEET[luokka]}",
            show=True
        )
        for luokka, emoji in [
            ("KRIITTINEN", "🔴"),
            ("KORKEA",     "🟠"),
            ("LIEVA",      "🟡"),
            ("NORMAALI",   "🟢"),
            ("LASKU",      "🔵"),
            ("EI_DATAA",   "⚫"),
        ]
    }
    # piste_layer ei enää käytössä – korvattu luokkakohtaisilla

    for sid, asema in asemat.items():
        if asema.get("tila") == "REMOVED_TEMPORARILY":
            continue

        sdata = rtdata.get(sid, {})
        bl    = baselineet.get(sid, {"ok": False, "yht": None,
                                     "s1": None, "s2": None,
                                     "arvot_s1": "", "arvot_s2": ""})
        yht, s1, s2, nopeus = laske_liikennedata(sdata)
        lon, lat = asema["lon"], asema["lat"]
        kulma_tie = kulmat.get(sid, 0.0)

        if not bl["ok"]:
            luokka = "EI_DATAA"
            p_yht = p_s1 = p_s2 = 0.0
            bl_yht = bl_s1 = bl_s2 = 0.0
        else:
            p_yht  = pct(yht, bl["yht"])
            p_s1   = pct(s1,  bl["s1"])
            p_s2   = pct(s2,  bl["s2"])
            luokka = poikkeama_luokka(p_yht)
            bl_yht = bl["yht"] or 0.0
            bl_s1  = bl["s1"]  or 0.0
            bl_s2  = bl["s2"]  or 0.0

        yht_lkm[luokka] = yht_lkm.get(luokka, 0) + 1
        vari = VARIT[luokka]
        koko = koot[luokka]

        # Popup-sisältö
        popup_html = f"""
        <div style='font-family:sans-serif;min-width:220px'>
          <b style='font-size:14px'>{asema['nimi']}</b><br>
          <span style='color:#666'>{asema.get('kunta','')}</span>
          <hr style='margin:6px 0'>
          <table style='width:100%;font-size:12px'>
            <tr><td>Liikenne nyt</td><td><b>{yht:.0f} ajon/h</b></td></tr>
            <tr><td>Baseline</td><td>{bl_yht:.0f} ajon/h</td></tr>
            <tr><td>Poikkeama</td>
              <td><b style='color:{vari}'>{p_yht:+.1f}%</b></td></tr>
            <tr><td>Nopeus</td><td>{nopeus:.0f} km/h</td></tr>
          </table>
          <hr style='margin:6px 0'>
          <table style='width:100%;font-size:11px;color:#555'>
            <tr>
              <td>S1: {s1:.0f} ajon/h (base {bl_s1:.0f})</td>
              <td><b style='color:{VARIT[poikkeama_luokka(p_s1)]}'>{p_s1:+.1f}%</b></td>
            </tr>
            <tr>
              <td>S2: {s2:.0f} ajon/h (base {bl_s2:.0f})</td>
              <td><b style='color:{VARIT[poikkeama_luokka(p_s2)]}'>{p_s2:+.1f}%</b></td>
            </tr>
          </table>
          <div style='font-size:10px;color:#999;margin-top:4px'>
            Baseline: {bl.get("arvot_s1","–")} | {bl.get("arvot_s2","–")}
          </div>
          <a href='?aikajana_sid={sid}' target='_self'
             style='display:block;margin-top:8px;padding:5px 10px;
                    background:#2a2a5a;color:#a0a0ff;
                    border:1px solid #4a4a9a;border-radius:5px;
                    text-align:center;font-size:12px;text-decoration:none;'>
            📊 Näytä aikajana
          </a>
        </div>
        """

        # Suuntanuolet nuoli_layeriin (piirtyy pisteiden alle)
        if bl["ok"]:
            nuoli_pituus = 0.012
            for kulma, s_pct in [
                (kulma_tie,           p_s1),
                ((kulma_tie+180)%360, p_s2),
            ]:
                lon2, lat2 = kulma_siirto(lon, lat, kulma, nuoli_pituus)
                nuoli_vari = VARIT[poikkeama_luokka(s_pct)]
                folium.PolyLine(
                    locations=[[lat, lon], [lat2, lon2]],
                    color=nuoli_vari,
                    weight=2.5,
                    opacity=0.85,
                ).add_to(nuoli_layer)
                folium.RegularPolygonMarker(
                    location=[lat2, lon2],
                    number_of_sides=3,
                    radius=5,
                    rotation=kulma - 90,
                    color=nuoli_vari,
                    fill=True,
                    fill_color=nuoli_vari,
                    fill_opacity=1.0,
                    weight=0,
                ).add_to(nuoli_layer)

        # Piste luokkakohtaiseen layeriin
        folium.CircleMarker(
            location=[lat, lon],
            radius=koko,
            color="white",
            weight=1.5,
            fill=True,
            fill_color=vari,
            fill_opacity=0.9,
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=f"{asema['nimi']}: {p_yht:+.0f}% | Klikkaa tiedot",
        ).add_to(luokka_layerit[luokka])

    # Lisää layerit kartalle järjestyksessä
    nuoli_layer.add_to(kartta)
    for layer in luokka_layerit.values():
        layer.add_to(kartta)
    folium.LayerControl(collapsed=True, position="topright").add_to(kartta)

    return kartta, yht_lkm

# ─────────────────────────────────────────────────────────────────
# PÄÄOHJELMA
# ─────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────
# 24H AIKAJANA
# ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def hae_24h_data(tms_num, nyt_fin):
    """
    Hakee CSV-historiadata aikajanaa varten (eilen varmasti, tänään jos saatavilla).
    Tänään CSV julkaistaan vasta seuraavana päivänä — puuttuva data täydennetään
    session-snapshooteilla funktiossa nayta_aikajana.
    """
    tulokset = []
    nyt_pvm  = nyt_fin.date()
    eilen    = (nyt_fin - timedelta(days=1)).date()
    raja     = nyt_fin - timedelta(hours=24)

    for pvm in [eilen, nyt_pvm]:
        yy  = str(pvm.year)[-2:].lstrip("0") or "0"
        doy = pvm.timetuple().tm_yday
        url = f"{BASE_URL}/api/tms/v1/history/raw/lamraw_{tms_num}_{yy}_{doy}.csv"
        raw = hae_bytes(url, timeout=20)
        if raw is None:
            continue
        tunti_laskuri = {}
        try:
            for rivi in csv.reader(
                    io.StringIO(raw.decode("utf-8", errors="replace")),
                    delimiter=";"):
                if len(rivi) < 13:
                    continue
                try:
                    t  = int(rivi[3])
                    su = int(rivi[9])
                    f  = int(rivi[12])
                    if f != 0:
                        continue
                    if t not in tunti_laskuri:
                        tunti_laskuri[t] = {"s1": 0, "s2": 0}
                    if su == 1:   tunti_laskuri[t]["s1"] += 1
                    elif su == 2: tunti_laskuri[t]["s2"] += 1
                except ValueError:
                    continue
        except Exception:
            continue

        for t, v in tunti_laskuri.items():
            aika = datetime(pvm.year, pvm.month, pvm.day, t, 0, 0).replace(tzinfo=timezone.utc)
            if aika < raja:
                continue
            tulokset.append({
                "pvm": pvm, "tunti": t,
                "s1": float(v["s1"]), "s2": float(v["s2"]),
                "yht": float(v["s1"] + v["s2"]),
                "aika": aika,
            })

    return sorted(tulokset, key=lambda x: x["aika"])


@st.cache_data(ttl=3600, show_spinner=False)
def hae_24h_baseline(tms_num, nyt_fin):
    """
    Hakee tuntikohtaisen baselinen 24 tunnille.
    Jokainen tunti lasketaan erikseen normaaleista vertailupäivistä.
    """
    from datetime import timedelta
    baseline_per_tunti = {}
    nyt_tunti = nyt_fin.hour

    # Keraa kaikki tarvittavat tunnit (viimeiset 24)
    tunnit = [(nyt_tunti - i) % 24 for i in range(24)]

    for tunti in tunnit:
        normaalit, _ = etsi_normaalit_paivat(nyt_fin, maara=4, max_viikkoja=16)
        s1v, s2v = [], []
        for pvm in normaalit:
            s1, s2 = csv_hae_tunti(tms_num, pvm, tunti)
            if s1 is not None: s1v.append(s1)
            if s2 is not None: s2v.append(s2)
        baseline_per_tunti[tunti] = {
            "s1": trimmattu_keskiarvo(s1v) or 0,
            "s2": trimmattu_keskiarvo(s2v) or 0,
        }

    return baseline_per_tunti



def nayta_aikajana(sid, nimi, tms_num, nyt_fin):
    """Renderöi 24h aikajana inline kartan alle."""
    import plotly.graph_objects as go

    st.markdown("---")
    col_otsikko, col_sulje = st.columns([5, 1])
    with col_otsikko:
        st.markdown(f"### 📊 {nimi} — Liikenteen aikajana")
        st.markdown("*Viimeiset 24 tuntia*")
    with col_sulje:
        st.markdown("<div style='margin-top:1.5rem'>", unsafe_allow_html=True)
        if st.button("✕ Sulje", key=f"sulje_{sid}", use_container_width=True):
            del st.session_state["aikajana_sid"]
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    with st.spinner("Haetaan historiadataa..."):
        csv_data = hae_24h_data(tms_num, nyt_fin)

    # Täydennä tämän päivän puuttuvat tunnit Supabasesta
    raja = nyt_fin - timedelta(hours=24)
    csv_tunnit = {(r["pvm"], r["tunti"]) for r in csv_data}
    sb_data = hae_tanaan_supabasesta(sid, nyt_fin)
    sb_data = [r for r in sb_data if r["aika"] >= raja and (r["pvm"], r["tunti"]) not in csv_tunnit]
    data_24h = sorted(csv_data + sb_data, key=lambda x: x["aika"])

    if not data_24h:
        st.warning("Historiadataa ei saatavilla tälle asemalle.")
        return

    with st.spinner("Lasketaan tuntikohtainen baseline..."):
        baseline = hae_24h_baseline(tms_num, nyt_fin)

    # Valintatyökalu (toggle-napit, oletuksena Kokonaismäärä)
    valitut = st.pills(
        "Näytä",
        options=["Kokonaismäärä", "S1", "S2"],
        selection_mode="multi",
        default=["Kokonaismäärä"],
        key=f"aikajana_pills_{sid}",
    ) or ["Kokonaismäärä"]
    nayta_yht = "Kokonaismäärä" in valitut
    nayta_s1  = "S1" in valitut
    nayta_s2  = "S2" in valitut

    # Valmistele toteutunut data kaaviota varten
    ajat    = [r["aika"] for r in data_24h]
    s1_nyt  = [r["s1"]  for r in data_24h]
    s2_nyt  = [r["s2"]  for r in data_24h]
    yht_nyt = [r["yht"] for r in data_24h]

    # Luo täysi 24h tuntisarja baselinelle (riippumaton todellisesta datasta)
    bl_raja = nyt_fin - timedelta(hours=24)
    bl_alku = datetime(bl_raja.year, bl_raja.month, bl_raja.day,
                       bl_raja.hour, 0, 0, tzinfo=timezone.utc)
    bl_loppu = datetime(nyt_fin.year, nyt_fin.month, nyt_fin.day,
                        nyt_fin.hour, 0, 0, tzinfo=timezone.utc)
    bl_ajat, bl_s1_full, bl_s2_full, bl_yht_full = [], [], [], []
    t = bl_alku
    while t <= bl_loppu:
        s1v = baseline.get(t.hour, {}).get("s1", 0)
        s2v = baseline.get(t.hour, {}).get("s2", 0)
        bl_ajat.append(t)
        bl_s1_full.append(s1v)
        bl_s2_full.append(s2v)
        bl_yht_full.append(s1v + s2v)
        t += timedelta(hours=1)

    fig = go.Figure()

    # Baseline-viivat (koko 24h)
    if nayta_yht:
        fig.add_trace(go.Scatter(
            x=bl_ajat, y=bl_yht_full,
            name="Yht. baseline",
            line=dict(color="#888888", dash="dash", width=1.5),
            mode="lines",
        ))
    if nayta_s1:
        fig.add_trace(go.Scatter(
            x=bl_ajat, y=bl_s1_full,
            name="S1 baseline",
            line=dict(color="#5a8fd0", dash="dash", width=1.5),
            mode="lines",
        ))
    if nayta_s2:
        fig.add_trace(go.Scatter(
            x=bl_ajat, y=bl_s2_full,
            name="S2 baseline",
            line=dict(color="#28904a", dash="dash", width=1.5),
            mode="lines",
        ))

    # Toteutunut liikenne
    if nayta_yht:
        fig.add_trace(go.Scatter(
            x=ajat, y=yht_nyt,
            name="Yhteensä",
            line=dict(color="#FF8C00", width=2.5),
            mode="lines+markers",
            marker=dict(size=5),
        ))
    if nayta_s1:
        fig.add_trace(go.Scatter(
            x=ajat, y=s1_nyt,
            name="S1 (kasvava suunta)",
            line=dict(color="#3C82DC", width=2.5),
            mode="lines+markers",
            marker=dict(size=5),
        ))
    if nayta_s2:
        fig.add_trace(go.Scatter(
            x=ajat, y=s2_nyt,
            name="S2 (laskeva suunta)",
            line=dict(color="#32B450", width=2.5),
            mode="lines+markers",
            marker=dict(size=5),
        ))

    fig.update_layout(
        paper_bgcolor="#0f0f1a",
        plot_bgcolor="#1a1a2e",
        font=dict(color="#e0e0ff"),
        xaxis=dict(
            title="Aika",
            gridcolor="#2a2a4a",
            tickformat="%H:%M %d.%m.",
        ),
        yaxis=dict(
            title="Ohituksia / tunti",
            gridcolor="#2a2a4a",
        ),
        legend=dict(
            bgcolor="#1a1a2e",
            bordercolor="#3a3a5c",
            borderwidth=1,
        ),
        hovermode="x unified",
        height=400,
        margin=dict(l=60, r=20, t=20, b=60),
    )

    st.plotly_chart(fig, use_container_width=True)

    # Yhteenveto
    if data_24h:
        viimeisin = data_24h[-1]
        # Hae baseline viimeisimmälle tunnille
        bl_tunti = baseline.get(viimeisin["tunti"], {})
        metrics = []
        if nayta_yht:
            bl = (bl_tunti.get("s1", 0) or 0) + (bl_tunti.get("s2", 0) or 0)
            p = (viimeisin["yht"] - bl) / bl * 100 if bl > 0 else 0
            metrics.append(("Yhteensä juuri nyt", f"{viimeisin['yht']:.0f} ajon/h", f"{p:+.1f}% vs baseline"))
        if nayta_s1:
            bl = bl_tunti.get("s1", 0) or 0
            p = (viimeisin["s1"] - bl) / bl * 100 if bl > 0 else 0
            metrics.append(("S1 juuri nyt", f"{viimeisin['s1']:.0f} ajon/h", f"{p:+.1f}% vs baseline"))
        if nayta_s2:
            bl = bl_tunti.get("s2", 0) or 0
            p = (viimeisin["s2"] - bl) / bl * 100 if bl > 0 else 0
            metrics.append(("S2 juuri nyt", f"{viimeisin['s2']:.0f} ajon/h", f"{p:+.1f}% vs baseline"))
        if metrics:
            for col, (label, val, delta) in zip(st.columns(len(metrics)), metrics):
                with col:
                    st.metric(label, val, delta)



def main():
    # CSS
    st.markdown("""
    <style>
    .stApp { background: #0f0f1a; }
    /* Tiivistetään yläreunan padding */
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 0.5rem !important;
    }
    .metric-card {
        background: #1a1a2e;
        border-radius: 8px;
        padding: 12px 16px;
        border-left: 4px solid;
        margin-bottom: 8px;
    }
    .stSidebar { background: #1a1a2e; }
    h1, h2, h3 { color: #e0e0ff; }
    </style>
    """, unsafe_allow_html=True)

    # Otsikko poistettu pääalueelta - on nyt sivupalkissa

    # Sivupalkki
    with st.sidebar:
        # Otsikko sivupalkissa
        st.markdown("""
        <div style='
            padding: 0.8rem 0 1rem 0;
            border-bottom: 1px solid #3a3a5c;
            margin-bottom: 1rem;
        '>
            <div style='font-size:1.3rem;font-weight:700;color:#e0e0ff;
                        line-height:1.2;'>
                🚦 LAM-liikenne&shy;seuranta
            </div>
            <div style='font-size:0.75rem;color:#888;margin-top:0.3rem;'>
                Reaaliaikainen liikenteen poikkeamaseuranta
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("## ⚙️ Asetukset")

        paivitys_min = st.slider(
            "Automaattinen päivitys (min)", 1, 15, 5
        )
        if st.button("🔄 Päivitä nyt", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.markdown("---")
        st.markdown("### 🗺️ Alue")
        valittu_alue = st.radio(
            "Alue",
            options=list(ALUEET.keys()),
            label_visibility="collapsed",
        )
        st.markdown("---")
        st.markdown("### 📊 Luokitusrajat")
        st.markdown(f"""
        - 🔴 Kriittinen: >{RAJA_KRIITTINEN}%
        - 🟠 Korkea: +{RAJA_KORKEA}–{RAJA_KRIITTINEN}%
        - 🟡 Lievä: +{RAJA_LIEVA}–{RAJA_KORKEA}%
        - 🟢 Normaali: ±{RAJA_LIEVA}%
        - 🔵 Lasku: >{RAJA_LIEVA}%
        """)

    st_autorefresh(interval=paivitys_min * 60 * 1000, key="autorefresh")

    # Tarkista aikajana-pyynto URL-parametreista (popup-nappi)
    qp = st.query_params
    if "aikajana_sid" in qp:
        sid_str = qp["aikajana_sid"]
        st.query_params.clear()
        try:
            sid_val = int(sid_str)
            if sid_val not in st.session_state.get("asemat_cache", {}):
                # Asemia ei viela ladattu - tallennetaan pyynto
                st.session_state["aikajana_sid"] = sid_val
            else:
                st.session_state["aikajana_sid"] = sid_val
        except ValueError:
            pass

    # Aikaleimat
    nyt_utc = datetime.now(timezone.utc)
    nyt_fin = nyt_utc + timedelta(hours=3)
    tunti   = nyt_fin.hour

    # Etsi normaalit vertailupäivät
    normaalit_pvmt, ohitetut = etsi_normaalit_paivat(nyt_fin, maara=4, max_viikkoja=16)
    vp_str = ["ma","ti","ke","to","pe","la","su"][nyt_fin.weekday()]

    # Hae data
    with st.spinner("Haetaan asematietoja..."):
        asemat = hae_asemat()

    if not asemat:
        st.error("Asematietojen haku epäonnistui. Tarkista verkkoyhteys.")
        return

    # Suodata asemat valitun alueen mukaan
    alue_bbox, kartta_keskipiste, kartta_zoom = ALUEET[valittu_alue]
    xmin, ymin, xmax, ymax = alue_bbox
    asemat = {
        sid: a for sid, a in asemat.items()
        if xmin <= a["lon"] <= xmax and ymin <= a["lat"] <= ymax
    }

    # Tallenna asemat sessioon aikajana-hakua varten
    st.session_state["asemat_cache"] = asemat

    with st.spinner("Haetaan reaaliaikainen liikennedata..."):
        rtdata = hae_reaaliaikadata()

    with st.spinner("Haetaan suuntavakiot..."):
        kulmat = hae_suuntavakiot(tuple(sorted(asemat.keys())))

    with st.spinner(f"Lasketaan baseline ({vp_str} klo {tunti:02d}:xx, pyhät ohitettu)..."):
        baselineet = hae_baselineet(
            tuple(sorted(asemat.items())),
            tunti,
            tuple(normaalit_pvmt),
        )

    # Luo kartta
    with st.spinner("Piirretään kartta..."):
        kartta, yht_lkm = luo_kartta(
            asemat, rtdata, baselineet, kulmat,
            keskipiste=kartta_keskipiste,
            zoom=kartta_zoom,
        )

    # Tilastot yläreunaan
    st.markdown("<div style='padding-top:0.5rem'></div>", unsafe_allow_html=True)
    st.markdown("#### Asemien tila")
    cols = st.columns(6)
    emojit = {"KRIITTINEN":"🔴","KORKEA":"🟠","LIEVA":"🟡",
               "NORMAALI":"🟢","LASKU":"🔵","EI_DATAA":"⚫"}
    for i, (luokka, lkm) in enumerate(yht_lkm.items()):
        with cols[i]:
            st.metric(
                label=f"{emojit[luokka]} {luokka.capitalize()}",
                value=str(lkm),
            )

    # Varoitus jos poikkeavia
    if yht_lkm.get("KRIITTINEN", 0) > 0 or yht_lkm.get("KORKEA", 0) > 0:
        st.error(f"⚠️ POIKKEAVA LIIKENNE: {yht_lkm.get('KRIITTINEN',0)} kriittistä, "
                 f"{yht_lkm.get('KORKEA',0)} korkeaa asemaa")
    else:
        st.success("✅ Liikenne normaalilla tasolla")

    # Baseline-info
    with st.expander("ℹ️ Baseline-tiedot", expanded=False):
        st.markdown(f"**Vertailupäivät:** {', '.join(str(p) for p in normaalit_pvmt)}")
        if ohitetut:
            st.markdown(f"**Ohitettu pyhien takia:** {', '.join(str(p) for p in ohitetut)}")
        vp_partitiivi = ["maanantaita","tiistaita","keskiviikkoa",
                          "torstaina","perjantaita","lauantaita","sunnuntaita"][nyt_fin.weekday()]
        st.markdown(f"**Metodi:** Trimmattu keskiarvo (4 normaalia {vp_partitiivi}, "
                    f"klo {tunti:02d}:xx)")

    # Kartta
    st.markdown("*Klikkaa asemaa kartalla nähdäksesi tiedot ja aikajananapin.*")
    st_folium(kartta, width="100%", height=750, returned_objects=[])

    # Aikajana inline kartan alla
    if "aikajana_sid" in st.session_state:
        _sid = st.session_state["aikajana_sid"]
        _asema = asemat.get(_sid)
        if _asema and _asema.get("tmsNum"):
            nayta_aikajana(
                sid=_sid,
                nimi=_asema["nimi"],
                tms_num=_asema["tmsNum"],
                nyt_fin=nyt_fin,
            )

    # Viimeinen päivitys
    st.markdown(f"*Päivitetty: {nyt_fin.strftime('%d.%m.%Y %H:%M')} (Suomen aika)*")



if __name__ == "__main__":
    main()


