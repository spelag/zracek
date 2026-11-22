"""
baza.py - zgradi zrak.db (SQLite) po shema.sql za vseh 24 ur.

Zagon: python baza.py     (shema.sql mora biti v isti mapi)
Pred zagonom ustavite Streamlit, sicer Windows ne dovoli prepisa datoteke.

Ta skripta simulira STREŽNIK: surove točke uporabnikov obstajajo samo v
pomnilniku, v bazo se zapišejo izključno agregati s pragom k. Plošča (app.py) bere samo
zrak.db in nikoli ne vidi posameznih točk.
"""
import os
import sqlite3
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd

from sim import users, posnetek, DATUM, N, DELEZ_DELOVNIH
from agregacija import agregiraj, K, RES
from ocena import pripravi, najdi

# Same database as app.py and the API bridge (arsodata/zrak_baza.py).
DB = os.environ.get("ZRAK_DB") or str(Path(__file__).with_name("zrak.db"))
SHEMA = Path(__file__).with_name("shema.sql")


# ------------------------------------------------------------------ pomožno
def _py(v):
    """numpy/pandas vrednosti -> čisti Python (SQLite ne zna numpy), NaN -> None."""
    if v is None or (not isinstance(v, (str, bytes)) and pd.isna(v)):
        return None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    return v


def _vrstice(df, stolpci):
    return [[_py(v) for v in r] for r in df[stolpci].itertuples(index=False, name=None)]


def stolpec(g, *imena, privzeto=None):
    """Prvi obstoječi stolpec izmed imen; sicer stolpec s privzeto vrednostjo."""
    for i in imena:
        if i in g.columns:
            return g[i]
    return pd.Series([privzeto] * len(g), index=g.index)


# ------------------------------------------------------------------ 1) agregati po urah
def agregati_po_urah():
    deli, povzetki = [], []
    for h in range(24):
        snap = posnetek(users, h)                      # surove točke samo v pomnilniku
        agg, izp = agregiraj(snap)                     # H3 + prag k
        ura = snap.time_utc.iloc[0].strftime("%Y-%m-%dT%H:%M:%SZ")
        deli.append(agg.assign(ura_utc=ura))
        povzetki.append((ura, int(agg.n.sum()), int(izp), K, "sinteticno"))
        print(f"  {h:02d} UTC: {len(agg)} celic, {int(agg.n.sum())} objavljenih, {izp} izpuščenih")
    return pd.concat(deli, ignore_index=True), povzetki


# ------------------------------------------------------------------ 2) statične lastnosti celic
def celice_z_lastnostmi(zased):
    c = zased[["cell", "res"]].drop_duplicates("cell").reset_index(drop=True)
    c = pripravi(c)                                    # lat, lon, km2, pm, dist_postaja_m, obcutljive

    cs = gpd.read_file(najdi("mol_cetrtne_skupnosti.geojson")).to_crs(4326)[["cs_ime", "geometry"]]
    pts = gpd.GeoDataFrame(c[["cell"]], geometry=gpd.points_from_xy(c.lon, c.lat), crs=4326)
    j = gpd.sjoin(pts, cs, how="left", predicate="within")
    j = j[~j.index.duplicated()]
    c["cs_ime"] = j["cs_ime"].reindex(c.index)
    return c


# ------------------------------------------------------------------ 3) postaje in občutljive lokacije
def tabela_postaj():
    arso = gpd.read_file(najdi("arso_lj_postaje.geojson")).to_crs(4326)
    a = pd.DataFrame({
        "postaja_id": stolpec(arso, "station_code").astype(str),
        "ime": stolpec(arso, "station_name"),
        "vir": "ARSO",
        "tip": stolpec(arso, "station_type"),
        "lat": arso.geometry.y, "lon": arso.geometry.x,
    })
    eea_kode = set(stolpec(arso, "eea_code").dropna().astype(str))   # isti postaji, brez podvojitve

    eea = gpd.read_file(najdi("eea_postaje.geojson")).to_crs(4326)
    ids = [x if x is not None and not pd.isna(x) else f"EEA_{i}"
           for i, x in enumerate(stolpec(eea, "station_code", "eoi_code", "EoICode"))]
    e = pd.DataFrame({
        "postaja_id": [str(i) for i in ids],
        "ime": stolpec(eea, "station_name", "name").to_numpy(),
        "vir": "EEA",
        "tip": stolpec(eea, "station_type", "type_of_station", "area_classification").to_numpy(),
        "lat": eea.geometry.y.to_numpy(), "lon": eea.geometry.x.to_numpy(),
    })
    e = e[~e.postaja_id.isin(eea_kode)]
    out = pd.concat([a, e], ignore_index=True)
    out["ime"] = out["ime"].where(out["ime"].notna(), out["postaja_id"])
    return out.drop_duplicates("postaja_id")


def tabela_obcutljivih():
    ob = gpd.read_file(najdi("osm_obcutljive_lokacije.geojson")).to_crs(4326)
    o = pd.DataFrame({
        "osm_id": stolpec(ob, "osm_id").astype(str),
        "kategorija": stolpec(ob, "kategorija"),
        "ime": stolpec(ob, "ime"),
        "naslov": stolpec(ob, "naslov"),
        "lat": ob.geometry.y, "lon": ob.geometry.x,
    })
    dovoljene = {"vrtec", "sola", "bolnisnica", "zdravstveni_dom", "dom_za_starejse"}
    return o[o.kategorija.isin(dovoljene)].drop_duplicates("osm_id")


# ------------------------------------------------------------------ glavni tok
def main():
    p = Path(DB)
    if p.exists():
        p.unlink()                                     # ponovna gradnja iz nič

    print("1/4 agregacija po urah ...")
    zased, povzetki = agregati_po_urah()
    assert (zased.n >= K).all()

    print("2/4 lastnosti celic (raster CAMx, razdalje, občutljive lokacije) ...")
    celice = celice_z_lastnostmi(zased)

    print("3/4 postaje in občutljive lokacije ...")
    postaje, obc = tabela_postaj(), tabela_obcutljivih()

    print("4/4 pisanje v", DB)
    con = sqlite3.connect(DB)
    con.executescript(SHEMA.read_text(encoding="utf-8"))
    cur = con.cursor()

    cur.executemany(
        "INSERT INTO celica (cell_id, res, lat, lon, km2, pm25_zima, gral_kurisca, "
        "dist_postaja_m, obcutljive, cs_ime) VALUES (?,?,?,?,?,?,?,?,?,?)",
        _vrstice(celice.rename(columns={"cell": "cell_id", "pm": "pm25_zima"}),
                 ["cell_id", "res", "lat", "lon", "km2", "pm25_zima", "gral_kurisca",
                  "dist_postaja_m", "obcutljive", "cs_ime"]))

    z = zased.rename(columns={"cell": "cell_id"}).assign(k_prag=K, vir="sinteticno")
    cur.executemany("INSERT INTO zasedenost VALUES (?,?,?,?,?)",
                    _vrstice(z, ["cell_id", "ura_utc", "n", "k_prag", "vir"]))
    cur.executemany("INSERT INTO zasedenost_povzetek VALUES (?,?,?,?,?)", povzetki)

    cur.executemany("INSERT INTO postaja VALUES (?,?,?,?,?,?)",
                    _vrstice(postaje, ["postaja_id", "ime", "vir", "tip", "lat", "lon"]))
    cur.executemany("INSERT INTO obcutljiva_lokacija VALUES (?,?,?,?,?,?)",
                    _vrstice(obc, ["osm_id", "kategorija", "ime", "naslov", "lat", "lon"]))

    cur.executemany("INSERT INTO konfiguracija VALUES (?,?)", [
        ("h3_res", str(RES)), ("k_prag", str(K)),
        ("datum_generiranja", str(pd.Timestamp.now().date())),
        ("datum_simulacije", str(DATUM.date())),
        ("vir_uporabnikov", "sinteticno"),
        ("gral_vir", "ARSO GRAL, kurišča, PM10, 2021, kurilna sezona (model)"),
        ("n_uporabnikov", str(N)), ("delez_delovnih", str(DELEZ_DELOVNIH)),
        # New id on every rebuild: the API bridge (arsodata/zrak_baza.py) uses it
        # to discard private request counters that belonged to the old DB.
        ("zagon_id", uuid.uuid4().hex),
    ])
    con.commit()

    for t in ("celica", "zasedenost", "zasedenost_povzetek", "postaja", "obcutljiva_lokacija"):
        print(f"  {t}: {cur.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]} vrstic")
    con.close()
    print("Končano:", DB)


if __name__ == "__main__":
    main()