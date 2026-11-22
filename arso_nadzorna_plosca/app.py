"""
app.py - nadzorna plošča za ARSO: kje postaviti novo merilno mesto.

Plošča bere SAMO iz baze zrak.db (ustvari jo: python baza.py). Posameznih uporabnikov
in njihovih lokacij v bazi ni, zato jih plošča ne more videti.

Zagon (iz mape projekta):
    streamlit run app.py

OPOZORILO: uporabniki so SINTETIČNI, PM je MODEL (CAMx), ocena je OCENA.
"""
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

from ocena import ocena

st.set_page_config(page_title="Zrak, ki ga dihamo · ARSO", layout="wide")

# Same database as baza.py and the API bridge (arsodata/zrak_baza.py).
DB = os.environ.get("ZRAK_DB") or str(Path(__file__).with_name("zrak.db"))
VIR_TXT = {"sinteticno": "sintetični", "dejansko": "dejanski"}
CLENI = {
    "ocena": "Skupna ocena",
    "c_users": "Prispevek: gostota uporabnikov",
    "c_gral": "Prispevek: kurišča (model GRAL)",
    "c_pm": "Prispevek: PM2.5 (model CAMx)",
    "c_dist": "Prispevek: oddaljenost od postaj",
    "c_sens": "Prispevek: občutljive lokacije",
    "n": "Število uporabnikov",
}

if not os.path.exists(DB):
    st.error("Baze zrak.db ni. Najprej zaženite: python baza.py")
    st.stop()
MTIME = os.path.getmtime(DB)      # ob ponovni gradnji baze se predpomnilnik osveži


# ------------------------------------------------------------------ branje iz baze
def _q(sql, params=()):
    uri = f"file:{Path(DB).as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as con:   # samo branje
        return pd.read_sql(sql, con, params=params)


@st.cache_data
def celice_ob_uri(ura_utc, mtime):
    return _q("SELECT * FROM v_celica_ura WHERE substr(ura_utc, 12, 2) = ?", (f"{ura_utc:02d}",))


@st.cache_data
def povzetek_ob_uri(ura_utc, mtime):
    d = _q("SELECT * FROM zasedenost_povzetek WHERE substr(ura_utc, 12, 2) = ?",
           (f"{ura_utc:02d}",))
    if d.empty:          # ura še brez objavljenih celic (npr. sveža baza)
        return pd.Series({"objavljenih": 0, "izpuscenih": 0})
    return d.iloc[0]


@st.cache_data
def postaje(mtime):
    return _q("SELECT lon, lat FROM postaja")


@st.cache_data
def obcutljive(mtime):
    return _q("SELECT lon, lat FROM obcutljiva_lokacija")


@st.cache_data
def konfiguracija(mtime):
    d = _q("SELECT kljuc, vrednost FROM konfiguracija")
    return dict(zip(d.kljuc, d.vrednost))


def razprseni_top(df, n=10, min_km=1.5):
    """Top n lokacij, brez sosednjih podvojitev (vsaka vsaj min_km od že izbranih)."""
    izbrani = []
    for i, r in df.iterrows():
        ok = True
        for j in izbrani:
            dx = (r.lon - df.at[j, "lon"]) * np.cos(np.radians(46.05)) * 111.32
            dy = (r.lat - df.at[j, "lat"]) * 111.32
            if np.hypot(dx, dy) < min_km:
                ok = False
                break
        if ok:
            izbrani.append(i)
        if len(izbrani) == n:
            break
    return df.loc[izbrani]


conf = konfiguracija(MTIME)
K_DB = int(conf["k_prag"])

# ------------------------------------------------------------------ stranska vrstica
st.sidebar.header("Nastavitve")
ura_lok = st.sidebar.slider("Ura (lokalni čas)", 0, 23, 22,
                            help="Čez dan (8–15 h) so otroci v vrtcih in šolah, 60 % odraslih pa v službi, "
                                 "zato se gostota uporabnikov spremeni. Večerni vrh PM2.5 je ob 22–24 h.")
ura_utc = (ura_lok - 1) % 24          # pozimi je Slovenija UTC+1

k = st.sidebar.slider("Prag zasebnosti k", K_DB, 30, K_DB,
                      help=f"Baza vsebuje samo celice z vsaj {K_DB} uporabniki. "
                           "Prag lahko samo povišate, nižanje ni mogoče, ker manjših celic baza ne hrani.")

st.sidebar.subheader("Uteži členov")
w = dict(
    users=st.sidebar.slider("Gostota uporabnikov", 0.0, 1.0, 0.25, 0.05),
    gral=st.sidebar.slider("Prispevek kurišč (model GRAL)", 0.0, 1.0, 0.25, 0.05,
                           help="Koliko PM10 po modelu ARSO GRAL (15 m) prispevajo mala kurišča. "
                                "Prostorsko natančnejše od CAMx; brez prometa, leto 2021."),
    pm=st.sidebar.slider("PM2.5 (model CAMx)", 0.0, 1.0, 0.05, 0.05,
                         help="Grob model 4,4 km, v MOL skoraj brez razlik med celicami."),
    dist=st.sidebar.slider("Oddaljenost od postaj", 0.0, 1.0, 0.25, 0.05),
    sens=st.sidebar.slider("Občutljive lokacije", 0.0, 1.0, 0.20, 0.05),
)
barva_po = st.sidebar.selectbox("Barvanje karte", list(CLENI), format_func=CLENI.get)
pokazi_ob = st.sidebar.checkbox("Pokaži občutljive lokacije (vrtci, šole, domovi ...)", value=False)

if sum(w.values()) == 0:
    st.warning("Vsaj ena utež mora biti večja od 0.")
    st.stop()

# ------------------------------------------------------------------ izračun
vse = celice_ob_uri(ura_utc, MTIME)
if "gral_kurisca" not in vse.columns:
    st.error("Baza je stara (brez GRAL). Zaženite znova: python baza.py")
    st.stop()
pov = povzetek_ob_uri(ura_utc, MTIME)
df0 = vse[vse.n >= k].copy()
if df0.empty:
    st.warning("Pri tem pragu k ni nobene objavljene celice. Znižajte k.")
    st.stop()
izpuscenih = int(pov.izpuscenih) + int(vse[vse.n < k].n.sum())

df = ocena(df0, w)
df["mesto"] = range(1, len(df) + 1)
top = razprseni_top(df)

# ------------------------------------------------------------------ glava
st.title("Zrak, ki ga dihamo: kje postaviti novo merilno mesto")
_viri = sorted(df.vir.dropna().unique())
_vir_txt = ("mešani (sintetični + dejanski)" if len(_viri) > 1
            else VIR_TXT.get(_viri[0], _viri[0]) if _viri else "neznani")
st.caption("ARSO ne vidi ljudi, vidi, kje je zrak slab in kje ga največ ljudi diha. "
           f"Podatki o uporabnikih so **{_vir_txt}**, PM2.5 je **model (CAMx), ne meritev**, "
           "ocena je **ocena**, ne meritev. Plošča bere samo agregirano bazo, brez posameznih lokacij.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Ura (lokalno / UTC)", f"{ura_lok}:00 / {ura_utc}:00")
c2.metric("Celic, ki jih ARSO vidi", len(df))
c3.metric("Uporabnikov v objavljenih celicah", f"{int(df.n.sum()):,}".replace(",", "."))
c4.metric("Uporabnikov izpuščenih (pod k)", f"{izpuscenih:,}".replace(",", "."))

# ------------------------------------------------------------------ karta
v = df[barva_po]
t = (v - v.min()) / (v.max() - v.min() + 1e-9)
df["r"] = 255
df["g"] = (230 * (1 - t)).astype(int)
df["b"] = 0
df["ocena_txt"] = df.ocena.round(2)
df["pm_txt"] = df.pm.round(1)
df["dist_km"] = (df.dist_postaja_m / 1000).round(2)
df["gral_txt"] = df.gral_kurisca.round(1).astype(object).where(df.gral_kurisca.notna(), "ni podatka")
df = df.drop(columns=["gral_kurisca"])     # NaN ne gre v karto; besedilo je v gral_txt
top = df.loc[top.index]    # top je bil izbran pred dodanimi stolpci, zato ga osvežimo

tooltip = {"text": "Mesto #{mesto}  (ocena {ocena_txt})\nuporabnikov: {n}\n"
                   "kurišča (model GRAL, µg/m3 PM10): {gral_txt}\nPM2.5 (model CAMx): {pm_txt}\n"
                   "do postaje: {dist_km} km\n"
                   "občutljivih lokacij: {obcutljive}"}

sloji = [
    pdk.Layer("H3HexagonLayer", df, get_hexagon="cell",
              get_fill_color="[r, g, b, 150]", get_line_color=[80, 80, 80],
              line_width_min_pixels=1, pickable=True, extruded=False),
    pdk.Layer("H3HexagonLayer", df[df.cell.isin(top.cell)], get_hexagon="cell",
              filled=False, stroked=True, get_line_color=[200, 0, 120],
              line_width_min_pixels=4, pickable=True),
]
if pokazi_ob:
    sloji.append(pdk.Layer("ScatterplotLayer", obcutljive(MTIME), get_position="[lon, lat]",
                           get_radius=90, get_fill_color=[0, 150, 70, 220]))
sloji.append(pdk.Layer("ScatterplotLayer", postaje(MTIME), get_position="[lon, lat]",
                       get_radius=150, get_fill_color=[0, 90, 255, 230]))

st.pydeck_chart(pdk.Deck(
    layers=sloji,
    initial_view_state=pdk.ViewState(latitude=46.056, longitude=14.505, zoom=10.5),
    map_style="light", tooltip=tooltip,
), height=560)
st.caption("Modre pike: obstoječe uradne postaje (ARSO, EEA). Zelene pike: občutljive lokacije (OSM). "
           "Vijoličen obris: top 10 predlaganih lokacij (vsaka vsaj 1,5 km od druge). "
           "Barva: " + CLENI[barva_po] + ".")

# ------------------------------------------------------------------ top 10
st.subheader("Top 10 predlaganih lokacij in razlaga »zakaj ta«")
tab = top.assign(
    **{"Mesto": top.mesto, "Uporabnikov": top.n, "Občutljivih lok.": top.obcutljive,
       "Do postaje (km)": top.dist_km, "Kurišča GRAL": top.gral_txt, "PM2.5 CAMx": top.pm_txt, "Ocena": top.ocena_txt,
       "Četrtna skupnost": top.cs_ime, "lat": top.lat.round(4), "lon": top.lon.round(4)}
)[["Mesto", "Četrtna skupnost", "Uporabnikov", "Občutljivih lok.", "Do postaje (km)",
   "Kurišča GRAL", "PM2.5 CAMx", "Ocena", "lat", "lon"]]
st.dataframe(tab, hide_index=True, use_container_width=True)

pr = top.set_index(top.mesto.astype(str))[["c_users", "c_gral", "c_pm", "c_dist", "c_sens"]]
pr.columns = ["Uporabniki", "Kurišča (GRAL)", "PM2.5 (CAMx)", "Oddaljenost od postaj", "Občutljive lokacije"]
st.bar_chart(pr)
st.caption("Višina stolpca: koliko posamezen člen prispeva k oceni. Tako je razvidno, zakaj je lokacija "
           "predlagana.")

with st.expander("Omejitve in poštena raba podatkov"):
    st.markdown(
        "- **Uporabniki so sintetični**: razporejeni po stanovanjskih stavbah (GURS KN, uteži = število "
        "stanovanj). Delež uporabnikov, profili (odrasel, otrok, starejši) in dnevni premik (otroci v "
        "najbližji vrtec ali šolo, 60 % odraslih v stavbe s poslovnimi prostori v MOL) so predpostavke.\n"
        f"- **Zasebnost**: v bazi so samo števila uporabnikov po šestkotnikih H3 (res {conf['h3_res']}) in "
        f"uri. Celice pod pragom k = {K_DB} baza ne sprejme. Brez ID-jev in posameznih lokacij.\n"
        "- **Kurišča (GRAL)** so model ARSO za PM10 iz malih kurišč, 15 m, leto 2021, scenarij M2. "
        "Brez prometa in ozadja; absolutnih vrednosti ne primerjajte z meritvami. Celice zunaj mreže "
        "(vzhod MOL) in zabrisane celice dobijo mediano.\n"
        "- **PM2.5 je model CAMx** (4,4 km, zimsko povprečje 2023–2026). Ne loči sosesk in zadnja leta "
        "precenjuje; zato ima privzeto majhno težo.\n"
        "- **Občutljive lokacije** so iz OpenStreetMap (ni popolno, zasebni vrtci pogosto manjkajo).\n"
        "- **Ocena** je utežena vsota členov; uteži so nastavljive in jih je treba uskladiti z ARSO.\n"
        "- Vir: ARSO, GURS, MOL, © OpenStreetMap contributors, Copernicus."
    )