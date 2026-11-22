"""
ocena.py - ocena upravičenosti nove merilne postaje po H3 celicah (OCENA, ne meritev).

Vhod:  agg_<ura>h.csv   (iz agregacija.py: time_utc, cell, res, n, n_ranljivi)
Izhod: ocena_<ura>h.csv (celica, vrednosti členov, prispevki členov, skupna ocena)

Členi (vsi normalizirani na 0-1):
  users : gostota uporabnikov (na km2)           -> izpostavljenost (SINTETIČNI podatki)
  gral  : prispevek malih kurišč k PM10 (GRAL)    -> MODEL ARSO, 15 m, leto 2021, brez prometa
  pm    : zimsko povprečje PM2.5 iz CAMx          -> MODEL, 4,4 km, precenjuje
  dist  : razdalja do najbližje uradne postaje    -> višja = bolj upravičena lokacija
  sens  : gostota občutljivih lokacij (OSM)       -> vrtci, šole, bolnišnice, domovi
"""
import glob
import numpy as np
import pandas as pd
import geopandas as gpd
import h3
import rasterio
from pyproj import Transformer
from scipy.spatial import cKDTree
from rasterio.mask import mask as rio_mask
from shapely.geometry import Polygon
from shapely.ops import transform as shp_transform

DATA = "data"
UTEZI = dict(users=0.25, gral=0.25, pm=0.05, dist=0.25, sens=0.20)


def najdi(ime):
    """Poišče datoteko kjerkoli pod data/ (da ni treba ugibati podmap)."""
    p = glob.glob(f"{DATA}/**/{ime}", recursive=True)
    if not p:
        raise FileNotFoundError(f"{ime} ni pod {DATA}/")
    return p[0]


def norm(s, lo=5, hi=95):
    """Obreže ekstreme (percentila lo, hi) in normalizira na 0-1."""
    a, b = np.nanpercentile(s, [lo, hi])
    return ((s.clip(a, b) - a) / (b - a + 1e-9)).fillna(0)


# ---------------------------------------------------------------- obogatitev
def dodaj_geometrijo(df):
    ll = [h3.cell_to_latlng(c) for c in df.cell]
    df["lat"] = [p[0] for p in ll]
    df["lon"] = [p[1] for p in ll]
    df["km2"] = [h3.cell_area(c, unit="km^2") for c in df.cell]
    return df


def dodaj_pm(df, band=1):
    """Vzorči CAMx zimsko povprečje (pas 1 = PM2.5, pas 2 = PM10; preverite v README)."""
    with rasterio.open(najdi("camx_zimsko_povprecje_slo.tif")) as r:
        tf = Transformer.from_crs(4326, r.crs, always_xy=True)
        xy = [tf.transform(lo, la) for lo, la in zip(df.lon, df.lat)]
        vals = np.array([v[0] for v in r.sample(xy, indexes=band)], dtype=float)
        if r.nodata is not None:
            vals[vals == r.nodata] = np.nan
    df["pm"] = vals
    return df


def dodaj_gral(df, ime_pasu="povprecje_kurilna_sezona_okt_maj"):
    """Povprečje GRAL prispevka malih kurišč k PM10 (µg/m3) znotraj vsake H3 celice.
    MODEL (ARSO GRAL, 15 m, leto 2021). Celice zunaj mreže ali zabrisane (brez podatka) = NaN."""
    # Mreža GRAL je v UTM 33N (EPSG:32633), NE v D96/TM (razlika 1,5 km): pretvorba iz lat/lon.
    tf = Transformer.from_crs(4326, 32633, always_xy=True)
    vrednosti = []
    with rasterio.open(najdi("gral_kurisca_statistika_15m.tif")) as r:
        opisi = list(r.descriptions or [])
        pas = opisi.index(ime_pasu) + 1 if ime_pasu in opisi else 2   # pas 2 = kurilna sezona
        for c in df.cell:
            rob = [(lng, lat) for lat, lng in h3.cell_to_boundary(c)]
            g = shp_transform(tf.transform, Polygon(rob))
            try:
                a, _ = rio_mask(r, [g], crop=True, filled=False, all_touched=True, indexes=pas)
            except ValueError:                      # celica povsem zunaj mreže
                vrednosti.append(np.nan)
                continue
            a = np.ma.masked_invalid(a.astype(float))
            if r.nodata is not None and not np.isnan(r.nodata):
                a = np.ma.masked_equal(a, r.nodata)
            vrednosti.append(float(a.mean()) if a.count() > 0 else np.nan)
    df["gral_kurisca"] = vrednosti
    return df


def dodaj_razdaljo(df):
    """Razdalja (m) do najbližje uradne postaje ARSO ali EEA."""
    st = pd.concat([
        gpd.read_file(najdi("arso_lj_postaje.geojson"))[["geometry"]],
        gpd.read_file(najdi("eea_postaje.geojson"))[["geometry"]],
    ]).set_crs(4326, allow_override=True).to_crs(3794)
    tree = cKDTree(np.c_[st.geometry.x, st.geometry.y])

    c = gpd.GeoSeries(gpd.points_from_xy(df.lon, df.lat), crs=4326).to_crs(3794)
    df["dist_postaja_m"], _ = tree.query(np.c_[c.x, c.y])
    return df


def dodaj_obcutljive(df):
    """Št. občutljivih lokacij znotraj celice (na ločljivosti vsake celice)."""
    ob = gpd.read_file(najdi("osm_obcutljive_lokacije.geojson")).to_crs(4326)
    lat, lon = ob.geometry.y.to_numpy(), ob.geometry.x.to_numpy()
    df["obcutljive"] = 0
    for res in df.res.unique():
        s = pd.Series([h3.latlng_to_cell(a, b, int(res)) for a, b in zip(lat, lon)])
        cnt = s.value_counts()
        m = df.res == res
        df.loc[m, "obcutljive"] = df.loc[m, "cell"].map(cnt).fillna(0).to_numpy()
    return df


# ---------------------------------------------------------------- ocena
def ocena(df, w=UTEZI):
    """Doda prispevke členov (c_*) in skupno oceno. Uteži se normalizirajo na vsoto 1."""
    w = {"users": 0, "gral": 0, "pm": 0, "dist": 0, "sens": 0, **w}   # manjkajoči členi = 0
    s = sum(w.values())
    w = {k: v / s for k, v in w.items()}
    df = df.copy()
    df["c_users"] = w["users"] * norm(df.n / df.km2)
    # celice brez GRAL (zunaj mreže, zabrisane) dobijo mediano: nevtralno, ne najslabše
    g = df["gral_kurisca"] if "gral_kurisca" in df else pd.Series(np.nan, index=df.index)
    df["c_gral"] = w["gral"] * norm(g.fillna(g.median()))
    df["c_pm"] = w["pm"] * norm(df.pm)
    df["c_dist"] = w["dist"] * norm(df.dist_postaja_m.clip(upper=3000))  # >3 km: brez dodatne koristi
    df["c_sens"] = w["sens"] * norm(df.obcutljive / df.km2)
    df["ocena"] = df[["c_users", "c_gral", "c_pm", "c_dist", "c_sens"]].sum(axis=1)
    return df.sort_values("ocena", ascending=False).reset_index(drop=True)


def pripravi(agg):
    """Obogati agregirane celice z vsemi členi (počasen del, naredi se enkrat)."""
    df = dodaj_geometrijo(agg.copy())
    df = dodaj_pm(df)
    df = dodaj_gral(df)
    df = dodaj_razdaljo(df)
    df = dodaj_obcutljive(df)
    return df


if __name__ == "__main__":
    ura = 21
    agg = pd.read_csv(f"agg_{ura}h.csv")
    df = pripravi(agg)
    df.to_csv(f"obogaten_{ura}h.csv", index=False)       # surovi členi
    res = ocena(df)
    res.to_csv(f"ocena_{ura}h.csv", index=False)

    print(res[["cell", "res", "n", "pm", "gral_kurisca", "dist_postaja_m", "obcutljive", "ocena"]]
          .head(10).round(2).to_string())
    print("\nPM (CAMx) min/max:", df.pm.min().round(1), df.pm.max().round(1),
          "| manjkajoči PM:", int(df.pm.isna().sum()))