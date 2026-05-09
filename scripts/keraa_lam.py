import gzip
import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta

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


def main():
    nyt_fin = datetime.now(timezone.utc) + timedelta(hours=3)
    pvm_str = nyt_fin.date().isoformat()
    tunti   = nyt_fin.hour
    print(f"Kerätään {pvm_str} klo {tunti:02d}...")

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


if __name__ == "__main__":
    main()
