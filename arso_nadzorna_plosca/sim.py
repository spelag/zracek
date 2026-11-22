"""
sim.py - simulator SINTETIČNIH uporabnikov (ni pravih podatkov o ljudeh).

Uporabniki so porazdeljeni po stanovanjskih stavbah v MOL (utež = število stanovanj).
Profili (odrasel / otrok / starejsi) in delež uporabnikov so PREDPOSTAVKE, ne podatki.
Izhod: anonimne točke (time_utc, lat, lon, profil), brez ID-jev.

Uporaba:
    python sim.py                 # ustvari sim_uporabniki_<ura>h.csv
    from sim import users, posnetek
"""
from poti import PAKET, najdi, navodila_za_paket

if PAKET is None:           # jasno sporočilo namesto tracebacka
    raise SystemExit(navodila_za_paket())

import numpy as np
import pandas as pd
import geopandas as gpd
from scipy.spatial import cKDTree

# ---------------------------------------------------------------- nastavitve
N = 30000                   # št. simuliranih uporabnikov (~7 % prebivalcev MOL, predpostavka)
SEED = 42
DELEZI_PROFILOV = {"odrasel": 0.70, "otrok": 0.15, "starejsi": 0.15}   # predpostavka
SUM_M = 15                  # šum v metrih, da točke niso natanko na centroidih stavb
ŠOLA_OD_UTC, ŠOLA_DO_UTC = 7, 14      # otroci v vrtcu/šoli (8-15 po lokalnem času pozimi)
DELEZ_DELOVNIH = 0.60       # delež odraslih, ki čez dan hodijo v službo (PREDPOSTAVKA)
DELO_OD_UTC, DELO_DO_UTC = ŠOLA_OD_UTC, ŠOLA_DO_UTC   # isto okno kot šola (app.py ga predpostavlja)
DATUM = pd.Timestamp.now().normalize()  # dan posnetka

rng = np.random.default_rng(SEED)


# ------------------------------------------------- 1) stanovanjske stavbe MOL
def nalozi_stavbe():
    st = pd.read_csv(najdi("kn_stavbe_atributi.csv.gz"))
    st = st[st["obcina_sifra"].astype(str).str.zfill(3) == "061"]
    st = st[st["stanovanjska"].astype(str).str.lower() == "true"]
    st = st.dropna(subset=["x_d96", "y_d96"])
    return st.reset_index(drop=True)


# ------------------------------------------------- 2) občutljive lokacije (OSM)
def nalozi_sole():
    # uporabimo geojson, ker ima zagotovo geometrijo (CSV morda nima koordinat)
    ob = gpd.read_file(najdi("osm_obcutljive_lokacije.geojson")).to_crs(4326)
    ob = ob[ob["kategorija"].isin(["vrtec", "sola"])].copy()
    ob["lat"] = ob.geometry.y
    ob["lon"] = ob.geometry.x
    return ob[["lat", "lon"]].reset_index(drop=True)


# ------------------------------------------------- 3) generiranje uporabnikov
def ustvari_uporabnike(st, n=N):
    w = np.maximum(st["n_stanovanj_deli"].fillna(0).to_numpy(), 1).astype(float)
    w /= w.sum()

    idx = rng.choice(len(st), size=n, p=w)
    x = st["x_d96"].to_numpy()[idx] + rng.normal(0, SUM_M, n)
    y = st["y_d96"].to_numpy()[idx] + rng.normal(0, SUM_M, n)

    dom = gpd.GeoSeries(gpd.points_from_xy(x, y), crs=3794).to_crs(4326)
    df = pd.DataFrame({"lat": dom.y.to_numpy(), "lon": dom.x.to_numpy()})
    df["profil"] = rng.choice(
        list(DELEZI_PROFILOV), size=n, p=list(DELEZI_PROFILOV.values())
    )
    return df


# priprava podatkov ob uvozu
_st = nalozi_stavbe()
sola = nalozi_sole()
users = ustvari_uporabnike(_st)

# vsakemu otroku dodelimo FIKSNO šolo/vrtec: eno od 3 najbližjih domu (da ostane ista čez dan)
_otroci = (users["profil"] == "otrok").to_numpy()
_dom = gpd.GeoSeries(
    gpd.points_from_xy(users.lon[_otroci], users.lat[_otroci]), crs=4326).to_crs(3794)
_sol = gpd.GeoSeries(gpd.points_from_xy(sola.lon, sola.lat), crs=4326).to_crs(3794)
_tree = cKDTree(np.c_[_sol.x, _sol.y])
_, _nn = _tree.query(np.c_[_dom.x, _dom.y], k=3)
_pick = _nn[np.arange(len(_nn)), rng.integers(0, 3, len(_nn))]
users["sola_lat"] = np.nan
users["sola_lon"] = np.nan
users.loc[_otroci, "sola_lat"] = sola["lat"].to_numpy()[_pick]
users.loc[_otroci, "sola_lon"] = sola["lon"].to_numpy()[_pick]


# zaposleni odrasli: FIKSNO delovno mesto v stavbi s poslovnimi prostori (utež = število prostorov)
def nalozi_delovna_mesta():
    df = pd.read_csv(najdi("kn_stavbe_atributi.csv.gz"))
    df = df[df["obcina_sifra"].astype(str).str.zfill(3) == "061"]
    df = df.dropna(subset=["x_d96", "y_d96"])
    df = df[df["stevilo_poslovnih_prostorov"].fillna(0) > 0]
    return df.reset_index(drop=True)


_dm = nalozi_delovna_mesta()
_wd = _dm["stevilo_poslovnih_prostorov"].to_numpy(float).copy()
_wd = _wd / _wd.sum()
_delavci = ((users["profil"] == "odrasel") & (rng.random(len(users)) < DELEZ_DELOVNIH)).to_numpy()
_m = rng.choice(len(_dm), size=int(_delavci.sum()), p=_wd)
_xd = _dm["x_d96"].to_numpy()[_m] + rng.normal(0, SUM_M, len(_m))
_yd = _dm["y_d96"].to_numpy()[_m] + rng.normal(0, SUM_M, len(_m))
_pd = gpd.GeoSeries(gpd.points_from_xy(_xd, _yd), crs=3794).to_crs(4326)
users["delo_lat"] = np.nan
users["delo_lon"] = np.nan
users.loc[_delavci, "delo_lat"] = _pd.y.to_numpy()
users.loc[_delavci, "delo_lon"] = _pd.x.to_numpy()


# ------------------------------------------------- 4) posnetek ob določeni uri
def posnetek(users, ura_utc, datum=DATUM):
    """Anonimne točke za dano uro (UTC). Brez ID: samo čas, lokacija, profil."""
    df = users.copy()
    if ŠOLA_OD_UTC <= ura_utc <= ŠOLA_DO_UTC:
        m = df["profil"] == "otrok"
        df.loc[m, "lat"] = df.loc[m, "sola_lat"]
        df.loc[m, "lon"] = df.loc[m, "sola_lon"]
    if DELO_OD_UTC <= ura_utc <= DELO_DO_UTC:
        m = df["delo_lat"].notna()              # zaposleni odrasli so v službi
        df.loc[m, "lat"] = df.loc[m, "delo_lat"]
        df.loc[m, "lon"] = df.loc[m, "delo_lon"]
    df["time_utc"] = datum + pd.Timedelta(hours=ura_utc)
    return df[["time_utc", "lat", "lon", "profil"]]


# ------------------------------------------------- 5) izpis in preverjanje
if __name__ == "__main__":
    for ura in (8, 13, 21):     # jutro, čez dan, večerni vrh (UTC)
        posnetek(users, ura).to_csv(f"sim_uporabniki_{ura}h.csv", index=False)
    print(f"Ustvarjeno: {len(users)} uporabnikov, datoteke sim_uporabniki_*h.csv")

    # preverjanje: uporabniki po četrtnih skupnostih
    cs = gpd.read_file(najdi("mol_cetrtne_skupnosti.geojson")).to_crs(4326)
    g = gpd.GeoDataFrame(
        users, geometry=gpd.points_from_xy(users.lon, users.lat), crs=4326
    )
    print(gpd.sjoin(g, cs[["cs_ime", "geometry"]]).cs_ime.value_counts())