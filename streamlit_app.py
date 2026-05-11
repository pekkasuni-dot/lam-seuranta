"""
LAM-seuranta - Streamlit-sovellus
==========================================
Reaaliaikainen liikenteen poikkeamaseuranta.
Vertailu: trimmattu keskiarvo neljältä edelliseltä normaalilta viikolta
          (pyhäpäivät ohitetaan automaattisesti).
Kuluvan päivän tuntidata tallennetaan Supabase-tietokantaan.
"""

import streamlit as st
import folium
from streamlit_folium import st_folium
import plotly.graph_objects as go
import json
import gzip
import csv
import io
import math
import threading
import urllib.request
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
from streamlit_autorefresh import st_autorefresh

st.set_page_config(
    page_title="Liikenteen tilannekuva",
    page_icon="🚦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────
# ASETUKSET
# ─────────────────────────────────────────────────────────────────

BASE_URL        = "https://tie.digitraffic.fi"
ALUE_BBOX       = (23.5, 63.7, 29.5, 70.1)
RAJA_LIEVA      = 30
RAJA_KORKEA     = 80
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
    "NORMAALI":   "#2D9E47",
    "LASKU":      "#2E6FBF",
    "EI_DATAA":   "#C0C0C0",
}
SELITTEET = {
    "KRIITTINEN": f"Kriittinen (>{RAJA_KRIITTINEN}%)",
    "KORKEA":     f"Korkea (+{RAJA_KORKEA}–{RAJA_KRIITTINEN}%)",
    "LIEVA":      f"Lievä (+{RAJA_LIEVA}–{RAJA_KORKEA}%)",
    "NORMAALI":   f"Normaali (±{RAJA_LIEVA}%)",
    "LASKU":      f"Lasku (>{RAJA_LIEVA}%)",
    "EI_DATAA":   "Ei dataa",
}

# ─────────────────────────────────────────────────────────────────
# SUPABASE
# ─────────────────────────────────────────────────────────────────

def sb_request(method, path, body=None):
    url = st.secrets["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    headers = {
        "apikey":        st.secrets["SUPABASE_KEY"],
        "Authorization": "Bearer " + st.secrets["SUPABASE_KEY"],
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
    tulokset = []
    for r in rivit:
        t    = int(r["tunti"])
        aika = datetime(nyt_fin.year, nyt_fin.month, nyt_fin.day,
                        t, 0, 0, tzinfo=timezone.utc)
        tulokset.append({
            "tunti": t,
            "aika":  aika,
            "s1":    float(r["s1"]),
            "s2":    float(r["s2"]),
            "yht":   float(r["s1"]) + float(r["s2"]),
        })
    return tulokset

@st.cache_data(ttl=3600, show_spinner=False)
def hae_paiva_supabasesta(sid, pvm_str):
    path  = f"tuntidata?sid=eq.{sid}&pvm=eq.{pvm_str}&order=tunti.asc"
    rivit = sb_request("GET", path)
    if not rivit:
        return []
    pvm = date.fromisoformat(pvm_str)
    return [{
        "tunti": int(r["tunti"]),
        "aika":  datetime(pvm.year, pvm.month, pvm.day,
                          int(r["tunti"]), 0, 0, tzinfo=timezone.utc),
        "s1":    float(r["s1"]),
        "s2":    float(r["s2"]),
        "yht":   float(r["s1"]) + float(r["s2"]),
    } for r in rivit]

# ─────────────────────────────────────────────────────────────────
# VERKKO
# ─────────────────────────────────────────────────────────────────

def hae_bytes(url, timeout=25):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if raw[:2] == b'\x1f\x8b':
            import gzip as _gz
            raw = _gz.decompress(raw)
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
    h = (19*a+b-d-g+15) % 30; i = c // 4; k = c % 4
    l = (32+2*e+2*i-h-k) % 7; m = (a+11*h+22*l) // 451
    kk = (h+l-7*m+114) // 31; pv = ((h+l-7*m+114) % 31)+1
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
    for v in [pvm.year-1, pvm.year, pvm.year+1]:
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

@st.cache_data(ttl=300)
def hae_reaaliaikadata():
    data = hae_json(f"{BASE_URL}/api/tms/v1/stations/data")
    if not data:
        return {}
    tulos = {}
    for st_item in data.get("stations", []):
        sid = st_item.get("id")
        if sid:
            tulos[sid] = {
                s.get("name",""): s.get("value")
                for s in st_item.get("sensorValues", [])
                if s.get("name") and s.get("value") is not None
            }
    return tulos

@st.cache_data(ttl=3600)
def hae_asemat():
    data = hae_json(f"{BASE_URL}/api/tms/v1/stations")
    if not data:
        return {}
    asemat = {}
    xmin, ymin, xmax, _ = ALUE_BBOX
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

@st.cache_data(ttl=3600)
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
        j = sorted(arvot)
        return sum(j[1:-1]) / len(j[1:-1])
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
        bl = {"ok": False, "yht": None, "s1": None, "s2": None,
              "arvot_s1": "", "arvot_s2": ""}
    else:
        if b_s1 is None: b_s1 = b_s2
        if b_s2 is None: b_s2 = b_s1
        bl = {"ok": True,
              "yht": round(b_s1+b_s2, 1),
              "s1":  round(b_s1, 1),
              "s2":  round(b_s2, 1),
              "arvot_s1": ";".join(f"{v:.0f}" for v in sorted(s1v)),
              "arvot_s2": ";".join(f"{v:.0f}" for v in sorted(s2v))}
    with lukko:
        tulokset[sid] = bl

@st.cache_data(ttl=3600, show_spinner=False)
def hae_baselineet(asemat_tuple, tunti, normaalit_pvmt_tuple):
    asemat         = dict(asemat_tuple)
    normaalit_pvmt = list(normaalit_pvmt_tuple)
    tulokset = {}
    lukko    = threading.Lock()
    for i in range(0, len(asemat), 10):
        era = list(asemat.items())[i:i+10]
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

@st.cache_data(ttl=300, show_spinner=False)
def hae_eilen_csv(tms_num, eilen_str):
    eilen   = date.fromisoformat(eilen_str)
    yy      = str(eilen.year)[-2:].lstrip("0") or "0"
    doy     = eilen.timetuple().tm_yday
    url     = f"{BASE_URL}/api/tms/v1/history/raw/lamraw_{tms_num}_{yy}_{doy}.csv"
    raw     = hae_bytes(url, timeout=20)
    if raw is None:
        return []
    laskuri = {}
    try:
        for rivi in csv.reader(
                io.StringIO(raw.decode("utf-8", errors="replace")), delimiter=";"):
            if len(rivi) < 13:
                continue
            try:
                t = int(rivi[3]); su = int(rivi[9]); f = int(rivi[12])
                if f != 0:
                    continue
                if t not in laskuri:
                    laskuri[t] = {"s1": 0, "s2": 0}
                if su == 1:   laskuri[t]["s1"] += 1
                elif su == 2: laskuri[t]["s2"] += 1
            except ValueError:
                continue
    except Exception:
        return []
    tulokset = []
    for t, v in laskuri.items():
        aika = datetime(eilen.year, eilen.month, eilen.day,
                        t, 0, 0, tzinfo=timezone.utc)
        tulokset.append({
            "tunti": t, "aika": aika,
            "s1": float(v["s1"]), "s2": float(v["s2"]),
            "yht": float(v["s1"]+v["s2"]),
        })
    return sorted(tulokset, key=lambda x: x["aika"])

@st.cache_data(ttl=86400, show_spinner=False)
def csv_hae_kaikki_tunnit(tms_num, pvm_str):
    """Lataa CSV kerran ja palauttaa {tunti: (s1, s2)} kaikille 24 tunnille."""
    if tms_num is None:
        return {}
    pvm = date.fromisoformat(pvm_str)
    yy  = str(pvm.year)[-2:].lstrip("0") or "0"
    doy = pvm.timetuple().tm_yday
    url = f"{BASE_URL}/api/tms/v1/history/raw/lamraw_{tms_num}_{yy}_{doy}.csv"
    raw = hae_bytes(url, timeout=20)
    if raw is None:
        return {}
    laskuri = {t: {"s1": 0, "s2": 0} for t in range(24)}
    try:
        for rivi in csv.reader(
                io.StringIO(raw.decode("utf-8", errors="replace")), delimiter=";"):
            if len(rivi) < 13:
                continue
            try:
                t  = int(rivi[3])
                su = int(rivi[9])
                f  = int(rivi[12])
                if f != 0 or t not in laskuri:
                    continue
                if su == 1:   laskuri[t]["s1"] += 1
                elif su == 2: laskuri[t]["s2"] += 1
            except ValueError:
                continue
    except Exception:
        return {}
    return {t: (float(v["s1"]) if v["s1"] > 0 else None,
                float(v["s2"]) if v["s2"] > 0 else None)
            for t, v in laskuri.items()}

@st.cache_data(ttl=3600, show_spinner=False)
def hae_24h_baseline(tms_num, nyt_fin_str):
    nyt_fin   = datetime.fromisoformat(nyt_fin_str)
    normaalit, _ = etsi_normaalit_paivat(nyt_fin, maara=4, max_viikkoja=16)
    paivien_data = {pvm: csv_hae_kaikki_tunnit(tms_num, pvm.isoformat())
                    for pvm in normaalit}
    bl = {}
    for tunti in range(24):
        s1v, s2v = [], []
        for tunnit in paivien_data.values():
            s1, s2 = tunnit.get(tunti, (None, None))
            if s1 is not None: s1v.append(s1)
            if s2 is not None: s2v.append(s2)
        bl[tunti] = {
            "s1": trimmattu_keskiarvo(s1v) or 0,
            "s2": trimmattu_keskiarvo(s2v) or 0,
        }
    return bl

# ─────────────────────────────────────────────────────────────────
# LASKENTA
# ─────────────────────────────────────────────────────────────────

def laske_liikennedata(sdata):
    v60_s1 = sdata.get("OHITUKSET_60MIN_KIINTEA_SUUNTA1")
    v60_s2 = sdata.get("OHITUKSET_60MIN_KIINTEA_SUUNTA2")
    if v60_s1 is None and v60_s2 is None:
        return None, None, None, 0.0
    s1 = float(v60_s1) if v60_s1 is not None else 0.0
    s2 = float(v60_s2) if v60_s2 is not None else 0.0
    nopeudet = []
    for suunta in ["SUUNTA1", "SUUNTA2"]:
        nop = sdata.get(f"KESKINOPEUS_60MIN_KIINTEA_{suunta}")
        if nop and nop > 0:
            nopeudet.append(float(nop))
    nopeus = sum(nopeudet) / len(nopeudet) if nopeudet else 0.0
    return s1 + s2, s1, s2, nopeus

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
# AIKAJANA-MODAL
# ─────────────────────────────────────────────────────────────────

@st.dialog(" ", width="large")
def nayta_aikajana_modal(sid, nimi, tms_num, nyt_fin):
    st.markdown(f"### {nimi}")
    st.markdown("*Viimeiset 24 tuntia*")
    if "_Flex" in nimi:
        st.warning("⚠️ Tämä on Flex-versio asemasta. Data saattaa olla puutteellista.")

    nyt_fin_str = nyt_fin.replace(minute=0, second=0, microsecond=0).isoformat()
    eilen       = (nyt_fin - timedelta(days=1)).date()

    with st.spinner("Haetaan historiadataa..."):
        sb_eilen  = hae_paiva_supabasesta(sid, eilen.isoformat())
        csv_eilen = hae_eilen_csv(tms_num, eilen.isoformat())
        # Yhdistä: Supabase-data ensisijainen, CSV täydentää puuttuvat tunnit
        sb_tunnit = {r["tunti"] for r in sb_eilen}
        eilen_data = sorted(
            sb_eilen + [r for r in csv_eilen if r["tunti"] not in sb_tunnit],
            key=lambda x: x["aika"],
        )
        tanaan_data = hae_tanaan_supabasesta(sid, nyt_fin)

    raja    = nyt_fin - timedelta(hours=24)
    kaikki  = [{**r, "lahde": "Historia"}   for r in eilen_data  if r["aika"] >= raja]
    kaikki += [{**r, "lahde": "Reaaliaikainen"} for r in tanaan_data if r["aika"] >= raja]
    data    = sorted(kaikki, key=lambda x: x["aika"])

    with st.spinner("Lasketaan vertailuarvo..."):
        baseline = hae_24h_baseline(tms_num, nyt_fin_str)

    if not data:
        st.warning("Historiadataa ei saatavilla.")
        st.caption(f"Eilen Supabase: {len(sb_eilen)} tuntia · CSV: {len(csv_eilen)} tuntia · Tänään: {len(tanaan_data)} tuntia")
        st.caption("Tänään kerätty data ilmestyy tänne kun GitHub Actions on ajanut vähintään kerran.")
        return

    tanaan_tunnit = len(tanaan_data)
    eilen_tunnit  = len([r for r in eilen_data if r["aika"] >= raja])
    st.caption(f"Eilen: {eilen_tunnit} tuntia (SB:{len(sb_eilen)}+CSV:{len(csv_eilen)}) · Tänään: {tanaan_tunnit} tuntia")

    ajat    = [r["aika"] for r in data]
    s1_nyt  = [r["s1"]  for r in data]
    s2_nyt  = [r["s2"]  for r in data]
    yht_nyt = [r["yht"] for r in data]

    raja_aika = nyt_fin - timedelta(hours=24)
    bl_alku   = datetime(raja_aika.year, raja_aika.month, raja_aika.day,
                         raja_aika.hour, tzinfo=timezone.utc)
    bl_loppu  = datetime(nyt_fin.year, nyt_fin.month, nyt_fin.day,
                         nyt_fin.hour, tzinfo=timezone.utc)
    bl_ajat, bl_s1, bl_s2, bl_yht = [], [], [], []
    t = bl_alku
    while t <= bl_loppu:
        b = baseline.get(t.hour, {"s1": 0, "s2": 0})
        bl_ajat.append(t)
        bl_s1.append(b["s1"])
        bl_s2.append(b["s2"])
        bl_yht.append(b["s1"] + b["s2"])
        t += timedelta(hours=1)

    fig = go.Figure()

    fig.add_trace(go.Scatter(x=bl_ajat, y=bl_yht, name="Yht. vertailu",
        line=dict(color="#888", dash="dash", width=1.5), mode="lines"))
    fig.add_trace(go.Scatter(x=bl_ajat, y=bl_s1, name="S1 vertailu",
        line=dict(color="#5a8fd0", dash="dash", width=1.5), mode="lines"))
    fig.add_trace(go.Scatter(x=bl_ajat, y=bl_s2, name="S2 vertailu",
        line=dict(color="#28904a", dash="dash", width=1.5), mode="lines"))
    lahde_lista = [r["lahde"] for r in data]

    fig.add_trace(go.Scatter(x=ajat, y=yht_nyt, name="Yhteensä",
        line=dict(color="#FF8C00", width=2.5), mode="lines+markers",
        marker=dict(size=5),
        customdata=lahde_lista,
        hovertemplate="Yhteensä: %{y:.0f} · %{customdata}<extra></extra>"))
    fig.add_trace(go.Scatter(x=ajat, y=s1_nyt, name="S1",
        line=dict(color="#2E6FBF", width=2.5), mode="lines+markers",
        marker=dict(size=5),
        hovertemplate="S1: %{y:.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=ajat, y=s2_nyt, name="S2",
        line=dict(color="#2D9E47", width=2.5), mode="lines+markers",
        marker=dict(size=5),
        hovertemplate="S2: %{y:.0f}<extra></extra>"))

    fig.update_layout(
        paper_bgcolor="#0f0f1a", plot_bgcolor="#1a1a2e",
        font=dict(color="#e0e0ff"),
        xaxis=dict(title="Aika", gridcolor="#2a2a4a", tickformat="%H:%M %d.%m."),
        yaxis=dict(title="Ohituksia / tunti", gridcolor="#2a2a4a"),
        legend=dict(bgcolor="#1a1a2e", bordercolor="#3a3a5c", borderwidth=1),
        hovermode="x unified", height=420,
        margin=dict(l=60, r=20, t=20, b=60),
    )
    st.plotly_chart(fig, use_container_width=True)

    if data:
        viimeisin = data[-1]
        bl_t = baseline.get(viimeisin["tunti"], {"s1": 0, "s2": 0})
        metrics = []
        bl = bl_t["s1"] + bl_t["s2"]
        p  = (viimeisin["yht"] - bl) / bl * 100 if bl > 0 else 0
        metrics.append(("Yhteensä nyt", f"{viimeisin['yht']:.0f} ajon/h", f"{p:+.1f}%"))
        bl = bl_t["s1"]
        p  = (viimeisin["s1"] - bl) / bl * 100 if bl > 0 else 0
        metrics.append(("S1 nyt", f"{viimeisin['s1']:.0f} ajon/h", f"{p:+.1f}%"))
        bl = bl_t["s2"]
        p  = (viimeisin["s2"] - bl) / bl * 100 if bl > 0 else 0
        metrics.append(("S2 nyt", f"{viimeisin['s2']:.0f} ajon/h", f"{p:+.1f}%"))
        for col, (label, val, delta) in zip(st.columns(3), metrics):
            col.metric(label, val, delta)

# ─────────────────────────────────────────────────────────────────
# KELIKAMERAT
# ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def hae_kelikamerat():
    data = hae_json("https://tie.digitraffic.fi/api/weathercam/v1/stations")
    if not data:
        return []
    kamerat = []
    xmin, ymin, xmax, ymax = ALUE_BBOX
    for f in data.get("features", []):
        props  = f.get("properties", {})
        coords = f.get("geometry", {}).get("coordinates", [None, None])
        if not coords[0] or not coords[1]:
            continue
        lon, lat = coords[0], coords[1]
        if not (xmin <= lon <= xmax and ymin <= lat <= ymax):
            continue
        img_url = None
        for preset in props.get("presets", []):
            preset_id = preset.get("id")
            if preset_id:
                img_url = f"https://weathercam.digitraffic.fi/{preset_id}.jpg"
                break
        if img_url:
            kamerat.append({
                "id":   props.get("id", ""),
                "nimi": props.get("name", props.get("id", "")),
                "lon":  lon,
                "lat":  lat,
                "url":  img_url,
            })
    return kamerat

# ─────────────────────────────────────────────────────────────────
# KARTTA
# ─────────────────────────────────────────────────────────────────

def luo_kartta(asemat, rtdata, baselineet, kulmat, kelikamerat=None):
    kartta = folium.Map(
        location=[65.5, 26.0], zoom_start=6,
        tiles="CartoDB dark_matter", control_scale=True,
    )
    kartta.get_root().html.add_child(folium.Element(
        "<style>.leaflet-control-scale-imperial{display:none!important}</style>"
    ))
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(kartta)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satelliitti",
    ).add_to(kartta)
    from folium.plugins import Fullscreen
    Fullscreen(position="topleft", title="Koko ruutu",
               title_cancel="Poistu", force_separate_button=True).add_to(kartta)

    koot = {"KRIITTINEN":14,"KORKEA":12,"LIEVA":10,
            "NORMAALI":7,"LASKU":7,"EI_DATAA":6}
    yht_lkm = {k: 0 for k in VARIT}

    nuoli_layer = folium.FeatureGroup(name="Suuntanuolet", show=True)
    luokka_layerit = {
        luokka: folium.FeatureGroup(name=f"{e} {SELITTEET[luokka]}", show=True)
        for luokka, e in [
            ("KRIITTINEN","🔴"),("KORKEA","🟠"),("LIEVA","🟡"),
            ("NORMAALI","🟢"),("LASKU","🔵"),("EI_DATAA","🔘"),
        ]
    }

    for sid, asema in asemat.items():
        if asema.get("tila") == "REMOVED_TEMPORARILY":
            continue
        sdata = rtdata.get(sid, {})
        bl    = baselineet.get(sid, {"ok": False, "yht": None,
                                     "s1": None, "s2": None,
                                     "arvot_s1": "", "arvot_s2": ""})
        yht, s1, s2, nopeus = laske_liikennedata(sdata)
        lon, lat  = asema["lon"], asema["lat"]
        kulma_tie = kulmat.get(sid, 0.0)

        if yht is None or not bl["ok"]:
            luokka = "EI_DATAA"
            p_yht = p_s1 = p_s2 = 0.0
            bl_yht = bl_s1 = bl_s2 = 0.0
            yht = s1 = s2 = 0.0
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

        popup_html = f"""
        <div style='font-family:sans-serif;min-width:200px'>
          <b style='font-size:14px'>{asema['nimi']}</b><br>
          <span style='color:#666'>{asema.get('kunta','')}</span>
          <hr style='margin:6px 0'>
          <table style='width:100%;font-size:12px'>
            <tr><td>Liikenne nyt</td><td><b>{yht:.0f} ajon/h</b></td></tr>
            <tr><td>Vertailu</td><td>{bl_yht:.0f} ajon/h</td></tr>
            <tr><td>Poikkeama</td>
                <td><b style='color:{vari}'>{p_yht:+.1f}%</b></td></tr>
            <tr><td>Nopeus</td><td>{nopeus:.0f} km/h</td></tr>
          </table>
          <hr style='margin:6px 0'>
          <table style='width:100%;font-size:11px;color:#555'>
            <tr><td>S1: {s1:.0f} (vert. {bl_s1:.0f})</td>
                <td><b style='color:{VARIT[poikkeama_luokka(p_s1)]}'>{p_s1:+.1f}%</b></td></tr>
            <tr><td>S2: {s2:.0f} (vert. {bl_s2:.0f})</td>
                <td><b style='color:{VARIT[poikkeama_luokka(p_s2)]}'>{p_s2:+.1f}%</b></td></tr>
          </table>
          <div style='font-size:10px;color:#999;margin-top:4px'>
            {bl.get('arvot_s1','–')} | {bl.get('arvot_s2','–')}
          </div>
        </div>"""

        tooltip_teksti = f"{asema['nimi']}: {p_yht:+.0f}%"

        if bl["ok"]:
            for kulma, s_pct in [
                (kulma_tie, p_s1),
                ((kulma_tie+180)%360, p_s2),
            ]:
                lon2, lat2 = kulma_siirto(lon, lat, kulma, 0.012)
                nuoli_vari = VARIT[poikkeama_luokka(s_pct)]
                folium.PolyLine(
                    locations=[[lat, lon], [lat2, lon2]],
                    color=nuoli_vari, weight=2.5, opacity=0.85,
                ).add_to(nuoli_layer)
                folium.RegularPolygonMarker(
                    location=[lat2, lon2], number_of_sides=3, radius=5,
                    rotation=kulma-90, color=nuoli_vari,
                    fill=True, fill_color=nuoli_vari,
                    fill_opacity=1.0, weight=0,
                ).add_to(nuoli_layer)

        folium.CircleMarker(
            location=[lat, lon],
            radius=koko,
            color="#000000", weight=1,
            fill=True, fill_color=vari, fill_opacity=0.95,
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=tooltip_teksti,
        ).add_to(luokka_layerit[luokka])

    nuoli_layer.add_to(kartta)
    for layer in luokka_layerit.values():
        layer.add_to(kartta)

    kamera_layer = folium.FeatureGroup(name="📷 Kelikamerat", show=False)
    for kamera in (kelikamerat or []):
        popup_html = f"""
        <div style='font-family:sans-serif;text-align:center;min-width:200px'>
          <b style='font-size:13px'>{kamera['nimi']}</b><br>
          <img src='{kamera['url']}' width='280'
               style='margin-top:6px;border-radius:4px;'
               onerror='this.style.display="none"'>
          <div style='font-size:10px;color:#999;margin-top:4px'>
            Kuva päivittyy ~10 min välein
          </div>
        </div>"""
        folium.CircleMarker(
            location=[kamera["lat"], kamera["lon"]],
            radius=4,
            color="#ffffff",
            weight=1.5,
            fill=True,
            fill_color="#000000",
            fill_opacity=0.9,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"📷 {kamera['nimi']}",
        ).add_to(kamera_layer)
    kamera_layer.add_to(kartta)

    folium.LayerControl(collapsed=True, position="topright").add_to(kartta)
    return kartta, yht_lkm

# ─────────────────────────────────────────────────────────────────
# PÄÄOHJELMA
# ─────────────────────────────────────────────────────────────────

def main():
    st.markdown("""
    <style>
    .stApp { background: #0f0f1a; }
    .block-container { padding-top:1rem !important; padding-bottom:0.5rem !important; }
    .stSidebar { background: #1a1a2e; }
    h1, h2, h3 { color: #e0e0ff; }
    [data-testid="stMainMenu"] { display: none !important; }
    </style>""", unsafe_allow_html=True)

    with st.sidebar:
        st.markdown("""
        <div style='padding:0.8rem 0 1rem 0;border-bottom:1px solid #3a3a5c;
                    margin-bottom:1rem;'>
          <div style='font-size:1.3rem;font-weight:700;color:#e0e0ff;line-height:1.2;'>
            Liikenteen tilannekuva
          </div>
          <div style='font-size:0.75rem;color:#888;margin-top:0.3rem;'>
            Reaaliaikainen liikenteen poikkeamaseuranta
          </div>
        </div>""", unsafe_allow_html=True)

        st.markdown("## ⚙️ Asetukset")
        paivitys_min = st.slider("Automaattinen päivitys (min)", 1, 15, 5)
        if st.button("🔄 Päivitä nyt", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        st.markdown("---")
        with st.expander("ℹ️ Vertailutiedot", expanded=False):
            _pvmt   = st.session_state.get("vertailu_pvmt", [])
            _ohit   = st.session_state.get("vertailu_ohitetut", [])
            _metodi = st.session_state.get("vertailu_metodi", "")
            _kpl    = st.session_state.get("kelikamerat_maara", 0)
            if _pvmt:
                st.markdown(f"**Päivät:** {', '.join(str(p) for p in _pvmt)}")
            if _ohit:
                st.markdown(f"**Ohitettu pyhien takia:** {', '.join(str(p) for p in _ohit)}")
            if _metodi:
                st.markdown(f"**Metodi:** {_metodi}")
            if _kpl:
                st.markdown(f"**Kelikameroita alueella:** {_kpl} kpl")
        st.markdown("---")
        st.markdown("### 📊 Aikajana")
        st.markdown("*Valitse asema:*")
        _asemat_sb = st.session_state.get("asemat_cache", {})
        valittu_nimi = st.selectbox(
            "Asema", label_visibility="collapsed",
            options=["– valitse –"] + sorted(a["nimi"] for a in _asemat_sb.values()),
        )
        if valittu_nimi != "– valitse –":
            _valittu_sid = next(
                (sid for sid, a in _asemat_sb.items() if a["nimi"] == valittu_nimi), None
            )
            if _valittu_sid and st.button("📊 Näytä aikajana", use_container_width=True):
                st.session_state["aikajana_sid"] = _valittu_sid
                st.rerun()

    st_autorefresh(interval=paivitys_min * 60 * 1000, key="autorefresh")

    nyt_fin = datetime.now(ZoneInfo("Europe/Helsinki")).replace(tzinfo=timezone.utc)
    tunti   = nyt_fin.hour

    normaalit_pvmt, ohitetut = etsi_normaalit_paivat(nyt_fin, maara=4, max_viikkoja=16)
    vp_str = ["ma","ti","ke","to","pe","la","su"][nyt_fin.weekday()]
    vp_part = ["maanantaita","tiistaita","keskiviikkoa","torstaina",
               "perjantaita","lauantaita","sunnuntaita"][nyt_fin.weekday()]
    st.session_state["vertailu_pvmt"]     = normaalit_pvmt
    st.session_state["vertailu_ohitetut"] = ohitetut
    st.session_state["vertailu_metodi"]   = (
        f"Trimmattu keskiarvo (4 normaalia {vp_part}, klo {tunti:02d}:xx)"
    )

    with st.spinner("Haetaan asematietoja..."):
        asemat = hae_asemat()
    if not asemat:
        st.error("Asematietojen haku epäonnistui.")
        return
    st.session_state["asemat_cache"] = asemat

    with st.spinner("Haetaan liikennedata..."):
        rtdata = hae_reaaliaikadata()

    with st.spinner("Haetaan suuntavakiot..."):
        kulmat = hae_suuntavakiot(tuple(sorted(asemat.keys())))

    with st.spinner(f"Lasketaan vertailuarvo ({vp_str} klo {tunti:02d}:xx)..."):
        baselineet = hae_baselineet(
            tuple(sorted(asemat.items())),
            tunti,
            tuple(normaalit_pvmt),
        )

    try:
        kelikamerat = hae_kelikamerat()
    except Exception:
        kelikamerat = []
    st.session_state["kelikamerat_maara"] = len(kelikamerat)

    with st.spinner("Piirretään kartta..."):
        kartta, yht_lkm = luo_kartta(asemat, rtdata, baselineet, kulmat,
                                      kelikamerat=kelikamerat)

    st.markdown("<div style='padding-top:0.5rem'></div>", unsafe_allow_html=True)

    # Tilabaari
    varit_hex = {"KRIITTINEN":"#DC1E1E","KORKEA":"#FF7800","LIEVA":"#FFD200",
                 "NORMAALI":"#2D9E47","LASKU":"#2E6FBF","EI_DATAA":"#C0C0C0"}
    nimien = {"KRIITTINEN":"Kriittinen","KORKEA":"Korkea","LIEVA":"Lievä",
              "NORMAALI":"Normaali","LASKU":"Lasku","EI_DATAA":"Ei dataa"}
    teksti_varit = {"KRIITTINEN":"#fff","KORKEA":"#fff","LIEVA":"#333",
                    "NORMAALI":"#fff","LASKU":"#fff","EI_DATAA":"#555"}
    yhteensa = sum(yht_lkm.values()) or 1

    segmentit = ""
    for luokka, lkm in yht_lkm.items():
        if lkm == 0:
            continue
        osuus = lkm / yhteensa * 100
        segmentit += (
            f"<div style='flex:{osuus:.1f};background:{varit_hex[luokka]};"
            f"display:flex;align-items:center;justify-content:center;min-width:20px;'>"
            f"<span style='font-size:12px;font-weight:500;color:{teksti_varit[luokka]};'>{lkm}</span>"
            f"</div>"
        )

    legenda = ""
    for luokka, lkm in yht_lkm.items():
        legenda += (
            f"<span style='font-size:13px;color:var(--color-text-secondary);"
            f"display:flex;align-items:center;gap:4px;'>"
            f"<span style='width:8px;height:8px;border-radius:50%;"
            f"background:{varit_hex[luokka]};display:inline-block;'></span>"
            f"{nimien[luokka]}: {lkm}</span>"
        )

    html_baari = f"""
    <div style='padding:0.5rem 0 0.25rem 0'>
      <div style='font-size:13px;color:var(--color-text-secondary);margin-bottom:6px;'>
        Asemien tila — {yhteensa} asemaa
      </div>
      <div style='display:flex;height:28px;border-radius:8px;overflow:hidden;gap:2px;'>
        {segmentit}
      </div>
      <div style='display:flex;gap:12px;margin-top:8px;flex-wrap:wrap;'>
        {legenda}
      </div>
    </div>"""
    st.markdown(html_baari, unsafe_allow_html=True)

    if yht_lkm.get("KRIITTINEN", 0) > 0 or yht_lkm.get("KORKEA", 0) > 0:
        st.error(f"⚠️ POIKKEAVA LIIKENNE: {yht_lkm.get('KRIITTINEN',0)} kriittistä, "
                 f"{yht_lkm.get('KORKEA',0)} korkeaa")
    else:
        st.success("✅ Liikenne normaalilla tasolla")

    st.markdown("*Klikkaa pistettä popup-tiedoille. Aikajana: valitse asema sivupalkista.*")
    st_folium(kartta, width="100%", height=750, returned_objects=[])

    if "aikajana_sid" in st.session_state:
        _sid = st.session_state.pop("aikajana_sid")
        _asema = asemat.get(_sid)
        if _asema and _asema.get("tmsNum"):
            nayta_aikajana_modal(
                sid=_sid,
                nimi=_asema["nimi"],
                tms_num=_asema["tmsNum"],
                nyt_fin=nyt_fin,
            )

    st.markdown(f"*Päivitetty: {nyt_fin.strftime('%d.%m.%Y %H:%M')} (Suomen aika)*")


if __name__ == "__main__":
    main()
