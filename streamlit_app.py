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
import time
import urllib.request
import urllib.error
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

def tarkista_salasana():
    """Yksinkertainen salasanasuojaus Streamlit session_state:n avulla."""
    if "kirjautunut" not in st.session_state:
        st.session_state.kirjautunut = False

    if not st.session_state.kirjautunut:
        # Piilotetaan Streamlitin oletussisältö kirjautumisen ajaksi
        st.markdown("""
        <style>
        [data-testid="stSidebar"] {display: none;}
        [data-testid="stHeader"] {display: none;}
        </style>
        """, unsafe_allow_html=True)

        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown("<br><br>", unsafe_allow_html=True)
            st.markdown("## 🚦 LAM-liikenneseuranta")
            st.markdown("#### Kirjaudu sisään")
            salasana = st.text_input("Salasana", type="password", key="pw_input")
            if st.button("Kirjaudu", use_container_width=True):
                oikea = st.secrets.get("PASSWORD", "demo2026")
                if salasana == oikea:
                    st.session_state.kirjautunut = True
                    st.rerun()
                else:
                    st.error("Väärä salasana")
        st.stop()  # Pysaytetaan renderöinti tahan - paasivu ei nayta lainkaan
        return False
    return True

# ─────────────────────────────────────────────────────────────────
# ASETUKSET
# ─────────────────────────────────────────────────────────────────

BASE_URL       = "https://tie.digitraffic.fi"
ALUE_BBOX      = (23.5, 63.7, 29.5, 70.1)
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
    "LASKU":      f"Lasku (>{RAJA_LIEVA}% alle)",
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

def luo_kartta(asemat, rtdata, baselineet, kulmat):
    """Luo Folium-kartan LAM-pisteistä ja suuntanuolista."""
    kartta = folium.Map(
        location=[65.5, 26.0],
        zoom_start=6,
        tiles="CartoDB positron",
        control_scale=True,
    )

    # Pisteiden koko luokan mukaan
    koot = {"KRIITTINEN": 14, "KORKEA": 12, "LIEVA": 10,
            "NORMAALI": 7, "LASKU": 9, "EI_DATAA": 6}

    yht_lkm = {k: 0 for k in VARIT}

    # Kaksi erillistä layeria: nuolet ensin (alle), pisteet jälkeen (päälle)
    nuoli_layer = folium.FeatureGroup(name="Suuntanuolet", show=True)
    piste_layer = folium.FeatureGroup(name="Asemat", show=True)

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

        # Piste piste_layeriin (piirtyy nuolten päälle)
        folium.CircleMarker(
            location=[lat, lon],
            radius=koko,
            color="white",
            weight=1.5,
            fill=True,
            fill_color=vari,
            fill_opacity=0.9,
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=f"{asema['nimi']}: {p_yht:+.0f}%",
        ).add_to(piste_layer)

    # Lisää layerit kartalle: nuolet ensin, pisteet päälle
    nuoli_layer.add_to(kartta)
    piste_layer.add_to(kartta)
    folium.LayerControl().add_to(kartta)

    return kartta, yht_lkm

# ─────────────────────────────────────────────────────────────────
# PÄÄOHJELMA
# ─────────────────────────────────────────────────────────────────

def main():
    if not tarkista_salasana():
        return

    # CSS
    st.markdown("""
    <style>
    .stApp { background: #0f0f1a; }
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

    # Otsikko
    col_t, col_logo = st.columns([4, 1])
    with col_t:
        st.markdown("# 🚦 LAM-liikenneseuranta")
        st.markdown("Reaaliaikainen liikenteen poikkeamaseuranta")

    # Sivupalkki
    with st.sidebar:
        st.markdown("## ⚙️ Asetukset")

        paivitys_min = st.slider(
            "Automaattinen päivitys (min)", 1, 15, 5
        )
        st.markdown("---")
        st.markdown("### 📊 Luokitusrajat")
        st.markdown(f"""
        - 🔴 Kriittinen: >{RAJA_KRIITTINEN}%
        - 🟠 Korkea: +{RAJA_KORKEA}–{RAJA_KRIITTINEN}%
        - 🟡 Lievä: +{RAJA_LIEVA}–{RAJA_KORKEA}%
        - 🟢 Normaali: ±{RAJA_LIEVA}%
        - 🔵 Lasku: >{RAJA_LIEVA}% alle
        """)
        st.markdown("---")
        if st.button("🔄 Päivitä nyt", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        if st.button("🚪 Kirjaudu ulos", use_container_width=True):
            st.session_state.kirjautunut = False
            st.rerun()

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
        kartta, yht_lkm = luo_kartta(asemat, rtdata, baselineet, kulmat)

    # Tilastot yläreunaan
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
        st.markdown(f"**Metodi:** Trimmattu keskiarvo (4 normaalia {vp_str}ta, "
                    f"klo {tunti:02d}:xx)")

    # Kartta
    st.markdown("### 🗺️ Liikennekartta")
    st.markdown("*Klikkaa pistettä tarkempiin tietoihin. Nuolet näkyvät zoomaamalla lähemmäksi.*")
    st_folium(kartta, width="100%", height=650, returned_objects=[])

    # Viimeinen päivitys + automaattinen uudelleenlataus
    st.markdown(f"*Päivitetty: {nyt_fin.strftime('%d.%m.%Y %H:%M')} (Suomen aika)*")

    # Automaattinen päivitys
    time.sleep(paivitys_min * 60)
    st.rerun()


if __name__ == "__main__":
    main()
