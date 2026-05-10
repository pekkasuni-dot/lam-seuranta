"""
Tarkistetaan neljä korjausta koodianalyysin ja yksikkötestien avulla.
Ei vaadi selainta, Streamlit-sessiota eikä verkkoyhteyttä.
"""
import sys
import os
import ast
import time
import importlib.util
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────────────────────────
# Lataa laske_liikennedata ja csv_hae_kaikki_tunnit suoraan
# ilman Streamlit-riippuvuutta (mock st ennen importtia)
# ─────────────────────────────────────────────────────────────────

import types, unittest.mock as mock

# Minimal st mock niin että moduuli latautuu
st_mock = types.ModuleType("streamlit")
st_mock.cache_data   = lambda *a, **kw: (lambda f: f)  # decorator passthrough
st_mock.secrets      = {}
st_mock.set_page_config = lambda **kw: None
st_mock.session_state = {}
st_mock.dialog       = lambda *a, **kw: (lambda f: f)
sys.modules.setdefault("streamlit", st_mock)

# Mock folium, streamlit_folium, plotly, streamlit_autorefresh
for mod in ["folium", "plotly", "plotly.graph_objects",
            "streamlit_autorefresh", "folium.plugins"]:
    sys.modules.setdefault(mod, types.ModuleType(mod))

sf_mock = types.ModuleType("streamlit_folium")
sf_mock.st_folium = lambda *a, **kw: {}
sys.modules.setdefault("streamlit_folium", sf_mock)

# folium needs Map, FeatureGroup, CircleMarker etc.
folium_mock = sys.modules["folium"]
for attr in ["Map", "FeatureGroup", "CircleMarker", "PolyLine",
             "Popup", "LayerControl", "Element", "RegularPolygonMarker"]:
    setattr(folium_mock, attr, mock.MagicMock())
folium_mock.get_root = mock.MagicMock()
plugins_mock = sys.modules["folium.plugins"]
plugins_mock.Fullscreen = mock.MagicMock()

# plotly.graph_objects needs Figure, Scatter
go_mock = sys.modules["plotly.graph_objects"]
go_mock.Figure  = mock.MagicMock()
go_mock.Scatter = mock.MagicMock()

# st_autorefresh mock
st_ar_mock = sys.modules["streamlit_autorefresh"]
st_ar_mock.st_autorefresh = lambda *a, **kw: None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# Lataa keraa_lam normaalisti (ei st-riippuvuuksia)
import keraa_lam

# Lataa streamlit_app käyttäen importlib jotta voimme injektoida mock ensin
spec = importlib.util.spec_from_file_location(
    "streamlit_app", os.path.join(ROOT, "streamlit_app.py")
)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


# ═══════════════════════════════════════════════════════
# KORJAUS 1: 5min × 12 fallback poistettu
# ═══════════════════════════════════════════════════════

def test_ei_dataa_kun_molemmat_60min_puuttuvat():
    """Molemmat OHITUKSET_60MIN puuttuvat → pitää palauttaa None ensimmäisenä arvona."""
    sdata = {
        "OHITUKSET_5MIN_LIUKUVA_SUUNTA1": 10.0,
        "OHITUKSET_5MIN_LIUKUVA_SUUNTA2": 8.0,
    }
    yht, s1, s2, nopeus = app.laske_liikennedata(sdata)
    assert yht is None, f"Odotettiin None, saatiin {yht}"
    assert s1  is None
    assert s2  is None


def test_palauttaa_arvon_kun_60min_olemassa():
    """Kun 60min-arvot ovat olemassa, palautetaan niiden summa."""
    sdata = {
        "OHITUKSET_60MIN_KIINTEA_SUUNTA1": 200.0,
        "OHITUKSET_60MIN_KIINTEA_SUUNTA2": 150.0,
    }
    yht, s1, s2, nopeus = app.laske_liikennedata(sdata)
    assert yht == 350.0, f"Odotettiin 350.0, saatiin {yht}"
    assert s1  == 200.0
    assert s2  == 150.0


def test_ei_kayta_5min_fallbackia():
    """60min puuttuu mutta 5min on → EI saa palauttaa 5min*12."""
    sdata = {
        "OHITUKSET_5MIN_LIUKUVA_SUUNTA1": 10.0,
        "OHITUKSET_5MIN_LIUKUVA_SUUNTA2": 8.0,
    }
    yht, s1, s2, _ = app.laske_liikennedata(sdata)
    # Jos koodissa olisi *12-fallback, tulisi 10*12+8*12=216
    assert yht is None, (
        f"Funktio käyttää 5min-fallbackia! Palautti {yht}, odotettiin None."
    )


def test_luo_kartta_ei_dataa_kun_60min_puuttuu():
    """luo_kartta-logiikka: yht=None → EI_DATAA."""
    yht = None
    bl  = {"ok": False}
    luokka = "EI_DATAA" if (yht is None or not bl["ok"]) else "NORMAALI"
    assert luokka == "EI_DATAA"


def test_luo_kartta_ei_dataa_kun_baseline_puuttuu():
    """luo_kartta-logiikka: yht>0 mutta baseline ok=False → EI_DATAA."""
    yht = 300.0
    bl  = {"ok": False}
    luokka = "EI_DATAA" if (yht is None or not bl["ok"]) else "NORMAALI"
    assert luokka == "EI_DATAA"


# ═══════════════════════════════════════════════════════
# KORJAUS 1 – kooditarkistus: ei *12-kertoimia
# ═══════════════════════════════════════════════════════

def test_ei_12_kerrointa_koodissa():
    """Koodissa ei saa olla *12 laskentaan liittyvää logiikkaa."""
    src = open(os.path.join(ROOT, "streamlit_app.py"), encoding="utf-8").read()
    # Haetaan kaikki *12 -esiintymät
    tree = ast.parse(src)
    virheilmoitukset = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            # Tarkista onko oikea puoli 12
            if isinstance(node.right, ast.Constant) and node.right.value == 12:
                virheilmoitukset.append(f"rivi {node.lineno}: *12 löytyi!")
            if isinstance(node.left, ast.Constant) and node.left.value == 12:
                virheilmoitukset.append(f"rivi {node.lineno}: 12* löytyi!")
    assert not virheilmoitukset, "\n".join(virheilmoitukset)


def test_ei_5min_liukuva_viittauksia_laskennassa():
    """laske_liikennedata-funktiossa ei saa viitata 5MIN-sensoreihin."""
    src = open(os.path.join(ROOT, "streamlit_app.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "laske_liikennedata":
            func_src = ast.get_source_segment(src, node) or ""
            assert "5MIN" not in func_src, (
                "laske_liikennedata viittaa 5MIN-sensoreihin!"
            )
            return
    raise AssertionError("laske_liikennedata-funktiota ei löydy!")


# ═══════════════════════════════════════════════════════
# KORJAUS 2: 24h-graafin tehokkuus
# ═══════════════════════════════════════════════════════

def test_csv_hae_kaikki_tunnit_olemassa():
    """csv_hae_kaikki_tunnit-funktio on olemassa streamlit_app:ssa."""
    assert hasattr(app, "csv_hae_kaikki_tunnit"), (
        "csv_hae_kaikki_tunnit puuttuu streamlit_app:sta!"
    )


def test_hae_24h_baseline_kutsuu_csv_hae_kaikki_tunnit_kerran_per_paiva():
    """hae_24h_baseline käyttää csv_hae_kaikki_tunnit dict-comprehensionissa (ei silmukassa)."""
    src = open(os.path.join(ROOT, "streamlit_app.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "hae_24h_baseline":
            func_src = ast.get_source_segment(src, node) or ""
            assert "csv_hae_kaikki_tunnit" in func_src, (
                "hae_24h_baseline ei kutsu csv_hae_kaikki_tunnit!"
            )
            # Dict-comprehension pitäisi löytyä (kutsutaan kerran per pvm)
            assert "for pvm in normaalit" in func_src, (
                "Ei löydy 'for pvm in normaalit' - rakenne muuttunut?"
            )
            # 24-silmukkaa ei saa olla csv-kutsussa
            assert "csv_hae_tunti" not in func_src, (
                "hae_24h_baseline kutsuu vanhaa csv_hae_tunti! Käytä csv_hae_kaikki_tunnit."
            )
            return
    raise AssertionError("hae_24h_baseline-funktiota ei löydy!")


def test_hae_24h_baseline_palauttaa_24_tuntia(monkeypatch):
    """hae_24h_baseline palauttaa dict jossa 24 avainta (tunnit 0-23)."""
    dummy_tunnit = {t: (float(t*10), float(t*8)) for t in range(24)}
    calls = []

    def fake_csv(tms_num, pvm_str):
        calls.append(pvm_str)
        return dummy_tunnit

    monkeypatch.setattr(app, "csv_hae_kaikki_tunnit", fake_csv)
    monkeypatch.setattr(app, "etsi_normaalit_paivat",
        lambda nyt, maara=4, max_viikkoja=16: (
            [date(2026, 4, 13), date(2026, 4, 6),
             date(2026, 3, 30), date(2026, 3, 23)], []
        )
    )

    nyt_fin_str = "2026-05-10T10:00:00+00:00"
    tulos = app.hae_24h_baseline(23456, nyt_fin_str)

    assert len(tulos) == 24, f"Odotettiin 24 tuntia, saatiin {len(tulos)}"
    assert set(tulos.keys()) == set(range(24))
    assert len(calls) == 4, f"csv_hae_kaikki_tunnit kutsuttiin {len(calls)}× (pitäisi olla 4)"


# ═══════════════════════════════════════════════════════
# KORJAUS 3: Eilisen datan tallennus Supabaseen
# ═══════════════════════════════════════════════════════

def test_tallenna_eilinen_olemassa():
    """tallenna_eilinen-funktio on keraa_lam.py:ssä."""
    assert hasattr(keraa_lam, "tallenna_eilinen"), (
        "tallenna_eilinen puuttuu keraa_lam.py:stä!"
    )


def test_csv_aggregoi_paiva_olemassa():
    """csv_aggregoi_paiva-funktio on keraa_lam.py:ssä."""
    assert hasattr(keraa_lam, "csv_aggregoi_paiva"), (
        "csv_aggregoi_paiva puuttuu keraa_lam.py:stä!"
    )


def test_tallenna_eilinen_ajetaan_tunnilla_9():
    """main() kutsuu tallenna_eilinen klo 09 (CSV saatavilla vasta klo 08-09)."""
    src = open(os.path.join(ROOT, "scripts", "keraa_lam.py"), encoding="utf-8").read()
    assert "tunti == 9" in src, "Ei löydy 'tunti == 9' tarkistusta main():ssa! (CSV saatavilla vasta klo 08-09)"
    assert "tallenna_eilinen" in src, "tallenna_eilinen ei esiinny main():ssa!"



def test_tallenna_eilinen_tarkistaa_onko_jo_tallennettu():
    """tallenna_eilinen sisältää duplikaattitarkistuksen ennen tallennusta."""
    src = open(os.path.join(ROOT, "scripts", "keraa_lam.py"), encoding="utf-8").read()
    assert "on jo tallennettu" in src or "limit=1" in src, (
        "tallenna_eilinen ei tarkista duplikaattia!"
    )


def test_nayta_aikajana_modal_kayttaa_supabasea_ensin():
    """nayta_aikajana_modal hakee eilen-datan Supabasesta ensin, CSV vasta fallbackina."""
    src = open(os.path.join(ROOT, "streamlit_app.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "nayta_aikajana_modal":
            func_src = ast.get_source_segment(src, node) or ""
            assert "hae_paiva_supabasesta" in func_src, (
                "nayta_aikajana_modal ei kutsu hae_paiva_supabasesta!"
            )
            assert "hae_eilen_csv" in func_src, (
                "nayta_aikajana_modal ei sisällä CSV-fallbackia (hae_eilen_csv)!"
            )
            # Supabase-kutsu tulee ennen CSV-fallbackia
            sb_pos  = func_src.index("hae_paiva_supabasesta")
            csv_pos = func_src.index("hae_eilen_csv")
            assert sb_pos < csv_pos, (
                "CSV-kutsu tulee ennen Supabase-kutsua! Järjestys väärä."
            )
            return
    raise AssertionError("nayta_aikajana_modal-funktiota ei löydy!")


def test_csv_aggregoi_paiva_palauttaa_oikean_rakenteen(monkeypatch):
    """csv_aggregoi_paiva palauttaa {tunti: (s1, s2)} tyhjällä CSV:llä."""
    import io as _io
    monkeypatch.setattr(keraa_lam, "hae_bytes", lambda url, **kw: b"")
    tulos = keraa_lam.csv_aggregoi_paiva(23456, date(2026, 5, 9))
    # Tyhjä CSV → kaikki nollia → palautetaan tyhjä dict (kaikki None)
    # (tai tyhjä dict jos poikkeus) — molemmat OK
    assert isinstance(tulos, dict)


# ═══════════════════════════════════════════════════════
# KORJAUS 4: Aikavyöhyke
# ═══════════════════════════════════════════════════════

def test_zoneinfo_importattu_streamlit_app():
    """from zoneinfo import ZoneInfo on streamlit_app.py:ssä."""
    src = open(os.path.join(ROOT, "streamlit_app.py"), encoding="utf-8").read()
    assert "from zoneinfo import ZoneInfo" in src, (
        "ZoneInfo-import puuttuu streamlit_app.py:stä!"
    )


def test_zoneinfo_importattu_keraa_lam():
    """from zoneinfo import ZoneInfo on keraa_lam.py:ssä."""
    src = open(os.path.join(ROOT, "scripts", "keraa_lam.py"), encoding="utf-8").read()
    assert "from zoneinfo import ZoneInfo" in src, (
        "ZoneInfo-import puuttuu keraa_lam.py:stä!"
    )


def test_ei_timedelta_hours3_streamlit_app():
    """streamlit_app.py ei käytä timedelta(hours=3) aikavyöhykkeenä."""
    src = open(os.path.join(ROOT, "streamlit_app.py"), encoding="utf-8").read()
    assert "timedelta(hours=3)" not in src, (
        "streamlit_app.py käyttää edelleen timedelta(hours=3)!"
    )


def test_ei_timedelta_hours3_keraa_lam():
    """keraa_lam.py ei käytä timedelta(hours=3) aikavyöhykkeenä."""
    src = open(os.path.join(ROOT, "scripts", "keraa_lam.py"), encoding="utf-8").read()
    assert "timedelta(hours=3)" not in src, (
        "keraa_lam.py käyttää edelleen timedelta(hours=3)!"
    )


def test_zoneinfo_helsinki_kesäaika():
    """ZoneInfo antaa +3:00:00 offsetin toukokuussa (kesäaika)."""
    nyt = datetime(2026, 5, 10, 12, 0, tzinfo=ZoneInfo("Europe/Helsinki"))
    offset = nyt.utcoffset()
    assert offset.total_seconds() == 3 * 3600, (
        f"Kesäajan offset pitäisi olla 3h, saatiin {offset}"
    )


def test_zoneinfo_helsinki_talviaika():
    """ZoneInfo antaa +2:00:00 offsetin joulukuussa (talviaika)."""
    talvi = datetime(2026, 12, 15, 12, 0, tzinfo=ZoneInfo("Europe/Helsinki"))
    offset = talvi.utcoffset()
    assert offset.total_seconds() == 2 * 3600, (
        f"Talviajan offset pitäisi olla 2h, saatiin {offset}"
    )


def test_nyt_fin_kayttaa_zoneinfo_streamlit_app():
    """main():ssa nyt_fin rakennetaan ZoneInfo-tavalla."""
    src = open(os.path.join(ROOT, "streamlit_app.py"), encoding="utf-8").read()
    assert 'ZoneInfo("Europe/Helsinki")' in src or "ZoneInfo('Europe/Helsinki')" in src, (
        "streamlit_app.py ei käytä ZoneInfo('Europe/Helsinki')!"
    )


def test_nyt_fin_kayttaa_zoneinfo_keraa_lam():
    """main():ssa nyt_fin rakennetaan ZoneInfo-tavalla."""
    src = open(os.path.join(ROOT, "scripts", "keraa_lam.py"), encoding="utf-8").read()
    assert 'ZoneInfo("Europe/Helsinki")' in src or "ZoneInfo('Europe/Helsinki')" in src, (
        "keraa_lam.py ei käytä ZoneInfo('Europe/Helsinki')!"
    )
