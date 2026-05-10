import csv
import gzip
import io
import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo

BASE_URL  = "https://tie.digitraffic.fi"
ALUE_BBOX = (23.5, 63.7, 29.5, 70.1)
HEADERS   = {
    "Accept":          "*/*",
    "Accept-Encoding": "gzip",
    "Digitraffic-User": "LAM-seuranta/1.0",
}


def hae_bytes(url, timeout=25):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return raw
    except Exception as e:
        print(f"Virhe haussa {url}: {e}")
        return None


def hae_json(url):
    raw = hae_bytes(url)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


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
            lon, lat = float(coords[0]), float(coords[1])
            if xmin <= lon <= xmax and lat >= ymin:
                asemat[sid] = {
                    "tmsNum": tnum,
                    "tila":   props.get("collectionStatus", ""),
                }
    return asemat


def hae_rtdata():
    data = hae_json(f"{BASE_URL}/api/tms/v1/stations/data")
    if not data:
        return {}
    tulos = {}
    for item in data.get("stations", []):
        sid = item.get("id")
        if sid:
            tulos[sid] = {
                s["name"]: s["value"]
                for s in item.get("sensorValues", [])
                if s.get("name") and s.get("value") is not None
            }
    return tulos


def sb_upsert(rows):
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/tuntidata?on_conflict=sid,pvm,tunti"
    key = os.environ["SUPABASE_KEY"]
    req = urllib.request.Request(
        url,
        data=json.dumps(rows).encode("utf-8"),
        headers={
            "apikey":        key,
            "Authorization": f"Bearer {key}",
            "Content-Type":  "application/json",
            "Prefer":        "resolution=merge-duplicates",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()
    print(f"OK: {len(rows)} riviä tallennettu")


def csv_aggregoi_paiva(tms_num, pvm):
    """Lataa päivän CSV ja palauttaa {tunti: (s1, s2)}."""
    yy  = str(pvm.year)[-2:].lstrip("0") or "0"
    doy = pvm.timetuple().tm_yday
    url = f"{BASE_URL}/api/tms/v1/history/raw/lamraw_{tms_num}_{yy}_{doy}.csv"
    raw = hae_bytes(url)
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


def tallenna_eilinen(asemat):
    """Aggregoi eilisen CSV-data Supabaseen. Ajetaan klo 01 Suomen aikaa."""
    eilen   = (datetime.now(ZoneInfo("Europe/Helsinki")) - timedelta(days=1)).date()
    pvm_str = eilen.isoformat()

    # Tarkista onko jo tallennettu
    key = os.environ["SUPABASE_KEY"]
    tarkistus_url = (os.environ["SUPABASE_URL"].rstrip("/") +
                     f"/rest/v1/tuntidata?pvm=eq.{pvm_str}&select=sid&limit=1")
    tark_req = urllib.request.Request(tarkistus_url, headers={
        "apikey": key, "Authorization": f"Bearer {key}",
    })
    try:
        with urllib.request.urlopen(tark_req, timeout=10) as resp:
            if json.loads(resp.read()):
                print(f"Eilinen {pvm_str} on jo tallennettu, ohitetaan.")
                return
    except Exception as e:
        print(f"Tarkistusvirhe: {e}")
        return

    print(f"Tallennetaan eilinen {pvm_str}...")
    rivit = []
    for sid, asema in asemat.items():
        if asema["tila"] == "REMOVED_TEMPORARILY" or not asema["tmsNum"]:
            continue
        tunnit = csv_aggregoi_paiva(asema["tmsNum"], eilen)
        for tunti, (s1, s2) in tunnit.items():
            if s1 is None and s2 is None:
                continue
            rivit.append({
                "sid":     sid,
                "tms_num": asema["tmsNum"],
                "pvm":     pvm_str,
                "tunti":   tunti,
                "s1":      s1 or 0.0,
                "s2":      s2 or 0.0,
            })

    if not rivit:
        print("Ei eilistä dataa tallennettavaksi.")
        return

    for i in range(0, len(rivit), 500):
        sb_upsert(rivit[i:i+500])
    print(f"Eilinen tallennettu: {len(rivit)} riviä.")


def main():
    nyt_fin = datetime.now(ZoneInfo("Europe/Helsinki")).replace(tzinfo=timezone.utc)
    # OHITUKSET_60MIN_KIINTEA edustaa juuri päättynyttä tuntia (esim. klo 17 → tunti 16)
    kohde   = nyt_fin - timedelta(hours=1)
    pvm_str = kohde.date().isoformat()
    tunti   = kohde.hour
    print(f"Kerätään {pvm_str} klo {tunti:02d} (ajo klo {nyt_fin.hour:02d})...")

    asemat = hae_asemat()
    if not asemat:
        print("VIRHE: asematiedot puuttuvat")
        raise SystemExit(1)

    rtdata = hae_rtdata()
    if not rtdata:
        print("VIRHE: RT-data puuttuu")
        raise SystemExit(1)

    rivit = []
    for sid, asema in asemat.items():
        if asema["tila"] == "REMOVED_TEMPORARILY":
            continue
        sdata = rtdata.get(sid, {})
        s1 = sdata.get("OHITUKSET_60MIN_KIINTEA_SUUNTA1")
        s2 = sdata.get("OHITUKSET_60MIN_KIINTEA_SUUNTA2")
        if s1 is None and s2 is None:
            continue
        rivit.append({
            "sid":     sid,
            "tms_num": asema["tmsNum"] or 0,
            "pvm":     pvm_str,
            "tunti":   tunti,
            "s1":      float(s1 or 0),
            "s2":      float(s2 or 0),
        })

    if rivit:
        sb_upsert(rivit)
    else:
        print("Ei tallennettavaa dataa")

    # Klo 09 Suomen aikaa: tallenna eilinen CSV-data Supabaseen
    # (raakadata saatavilla tyypillisesti klo 08-09)
    if tunti == 9:
        tallenna_eilinen(asemat)


if __name__ == "__main__":
    main()
