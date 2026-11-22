"""
agregacija.py - zasebnost: ARSO vidi samo celice z vsaj K uporabniki.

Ena ločljivost H3 (RES). Celice z manj kot K uporabniki se IZPUSTIJO (niso objavljene).
Celice se ne prekrivajo in se ne združujejo.

V produkciji bi ta korak tekel na strežniku takoj ob prejemu lokacije, surove točke
pa se ne bi nikoli shranile.

Vhod:  sim_uporabniki_<ura>h.csv  (time_utc, lat, lon, profil)
Izhod: agg_<ura>h.csv             (time_utc, cell, res, n, n_ranljivi)
"""
import pandas as pd
import h3

K = 10
RES = 8        # res 8 ~ 0,7 km2; res 9 ~ 0,1 km2 (pri 30.000 uporabnikih bi večina padla pod K)


def agregiraj(df, k=K, res=RES):
    df = df.copy()
    df["cell"] = [h3.latlng_to_cell(a, b, res) for a, b in zip(df.lat, df.lon)]
    df["ranljiv"] = df.profil.isin(["otrok", "starejsi"])
    g = df.groupby("cell").agg(n=("cell", "size"), n_ranljivi=("ranljiv", "sum")).reset_index()
    ok = g[g.n >= k].copy()
    ok["res"] = res
    izpuscenih = int(g[g.n < k].n.sum())     # uporabniki v celicah pod pragom
    return ok, izpuscenih


if __name__ == "__main__":
    for ura in (8, 13, 21):
        u = pd.read_csv(f"sim_uporabniki_{ura}h.csv", parse_dates=["time_utc"])
        agg, izp = agregiraj(u)
        agg["time_utc"] = u.time_utc.iloc[0]
        agg = agg[["time_utc", "cell", "res", "n", "n_ranljivi"]]
        agg.to_csv(f"agg_{ura}h.csv", index=False)
        assert (agg.n >= K).all()            # nobena objavljena celica pod pragom
        print(f"{ura}h: {len(agg)} celic, {agg.n.sum()} od {len(u)} uporabnikov objavljenih, "
              f"izpuščenih {izp}")