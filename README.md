LAM-evakuointiseuranta – Streamlit-demo
Paikallinen käynnistys
```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```
Oletussalasana: `demo2026`
Julkaisu Streamlit Cloudiin (ilmainen)
Luo GitHub-tili jos sinulla ei ole: https://github.com
Luo uusi repositorio (esim. `lam-seuranta`)
Lisää tiedostot:
`streamlit_app.py`
`requirements.txt`
Mene https://share.streamlit.io
Kirjaudu GitHub-tunnuksilla
Klikkaa "New app" → valitse repositorio ja tiedosto
Lisää salasana: App settings → Secrets:
```toml
   PASSWORD = "oma_salasanasi"
   ```
Klikkaa "Deploy" → saat julkisen URL:n
Tiedostorakenne
```
lam-seuranta/
├── streamlit_app.py    # Pääohjelma
├── requirements.txt    # Riippuvuudet
└── README.md           # Tämä tiedosto
```
Ominaisuudet
Reaaliaikainen liikennedata Digitrafficin LAM-asemilta
Baseline: trimmattu keskiarvo 4 normaalilta viikolta
(pyhäpäivät ohitetaan automaattisesti)
Interaktiivinen kartta (zoom, pan, klikkaus)
Suuntanuolet tien suunnan mukaan
Värikoodaus: vihreä=normaali, keltainen=lievä, oranssi=korkea, punainen=kriittinen
Salasanasuojaus
Automaattinen päivitys (oletuksena 5 min)
