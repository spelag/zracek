"""Skupne pomožne funkcije za zvezke s primeri (podatki/paket/primeri/).

Uporaba v zvezku:  import sys; sys.path.insert(0, "."); from paket import *
Vse poti so relativne na koren paketa (podatki/paket/), ki je privzeto ".." glede na mapo primeri/.
"""
import math
from pathlib import Path

import numpy as np
import pandas as pd

P = Path(__file__).resolve().parent.parent  # koren paketa

# Postaje ARSO (meritev)
POSTAJE = {
    "E403": dict(ime="LJ Bežigrad", lat=46.0655449, lon=14.5127203, tip="mestno ozadje"),
    "E405": dict(ime="LJ Celovška", lat=46.0720916, lon=14.4871183, tip="promet"),
    "E404": dict(ime="LJ Vič", lat=46.037487, lon=14.4892717, tip="mestno ozadje"),
}
ZIMSKI_MESECI = [11, 12, 1, 2, 3]

# Mejne in ciljne vrednosti (EU direktiva 2008/50 in 2024/2881, WHO 2021)
MEJA_PM10_DAN_EU = 50      # µg/m³, dnevno povprečje, dovoljenih 35 preseganj na leto
MEJA_PM25_DAN_WHO = 15     # µg/m³, WHO smernica za dnevno povprečje
MEJA_PM25_LETO_EU_2030 = 10


def haversine_km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12742 * math.asin(math.sqrt(a))


def najblizja_postaja(lat, lon):
    """Vrne (koda, razdalja_km) najbližje postaje ARSO."""
    return min(((k, haversine_km(lat, lon, v["lat"], v["lon"])) for k, v in POSTAJE.items()), key=lambda x: x[1])


def nalozi_arso(postaje=None, z_dopolnitvijo=True):
    """Urne meritve ARSO PM10/PM2.5 (meritev). Vrne stolpce time_utc, station_code, pm10, pm25."""
    df = pd.read_csv(P / "01_meritve" / "arso_lj_pm_urno_2016_2026.csv.gz", parse_dates=["time_utc"],
                     usecols=["time_utc", "station_code", "pm10", "pm25"], dtype={"pm10_flag": str})
    if z_dopolnitvijo:
        d = P / "01_meritve" / "arso_lj_pm_urno_2026_11_dopolnitev.csv"
        if d.exists():
            df = pd.concat([df, pd.read_csv(d, parse_dates=["time_utc"], usecols=["time_utc", "station_code", "pm10", "pm25"])], ignore_index=True)
    if postaje:
        df = df[df.station_code.isin(postaje)]
    return df.sort_values(["station_code", "time_utc"]).reset_index(drop=True)


def nalozi_inverzijo():
    """Urna tabela inverzije iz meritev ARSO (izpeljano iz meritev)."""
    return pd.read_csv(P / "03_vreme_inverzija" / "inverzija_urno_2016_2026.csv", parse_dates=["time_utc"])


def nalozi_era5(point="E403", stolpci=None):
    """ERA5 prek Open-Meteo za točko (model). Združi obe datoteki."""
    use = ["time_utc", "point_id"] + (stolpci or ["temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m",
                                                 "wind_direction_10m", "cloud_cover", "shortwave_radiation", "boundary_layer_height"])
    frames = [pd.read_csv(P / "03_vreme_inverzija" / f, parse_dates=["time_utc"], usecols=use)
              for f in ("era5_openmeteo_urno_2016_2020.csv.gz", "era5_openmeteo_urno_2021_2026.csv.gz")]
    df = pd.concat(frames, ignore_index=True)
    return df[df.point_id == point].drop(columns="point_id").sort_values("time_utc").reset_index(drop=True)


def nalozi_cams(point="E403"):
    """CAMS Europe prek Open-Meteo za točko (model). Stolpci cams_pm10, cams_pm25, cams_no2, cams_o3."""
    df = pd.read_csv(P / "02_modeli" / "cams_openmeteo_urno_2016_2026.csv.gz", parse_dates=["time_utc"],
                     usecols=["time_utc", "point_id", "pm10", "pm2_5", "nitrogen_dioxide", "ozone"])
    df = df[df.point_id == point].drop(columns="point_id")
    return df.rename(columns={"pm10": "cams_pm10", "pm2_5": "cams_pm25", "nitrogen_dioxide": "cams_no2", "ozone": "cams_o3"}).reset_index(drop=True)


def nalozi_camx(point="E403", samo_isti_dan=True):
    """Model CAMx ARSO (4,4 km) v celici točke. Stolpci camx_pm25, camx_pm10, camx_o3, run_utc, lead_h.
    samo_isti_dan=True vrne ure napovedi +1 … +24 (vsaka ura iz svežega zagona, brez podvajanj po time_utc);
    False vrne tudi ure +25 … +48 (za isto time_utc dve vrstici: današnji in včerajšnji zagon), glej nalozi_camx_jutri."""
    df = pd.read_csv(P / "02_modeli" / "camx" / "camx_tocke_urno_2023_2026.csv.gz", parse_dates=["time_utc", "run_utc"])
    df = df[df.point_id == point].drop(columns="point_id")
    if samo_isti_dan:
        df = df[df.lead_h <= 24]
    return df.reset_index(drop=True)


def nalozi_camx_jutri(point="E403"):
    """Model CAMx ARSO: samo ure napovedi +25 … +48 (napoved za jutri, izdana ob 00 UTC dan prej; 2023–2025)."""
    df = nalozi_camx(point, samo_isti_dan=False)
    return df[df.lead_h.between(25, 48)].reset_index(drop=True)


def nalozi_camx_polje(leto):
    """Izrez polja CAMx okoli Ljubljane (21 × 21 celic, urno) kot xarray.Dataset; potrebuje xarray in netCDF4."""
    import xarray as xr
    return xr.open_dataset(P / "02_modeli" / "camx" / f"camx_kotlina_urno_{leto}.nc")


def nalozi_radiosondazo():
    """Dnevne metrike inverzije iz radiosondaže Ljubljana (izpeljano iz meritev)."""
    return pd.read_csv(P / "03_vreme_inverzija" / "igra2_lj_inverzija_dnevno.csv", parse_dates=["time_utc"])


def nalozi_senzorje_umerjene(samo_zanesljive=True):
    """Umerjene urne vrednosti občanskih senzorjev (OCENA)."""
    df = pd.read_csv(P / "01_meritve" / "senzorji_umerjeno_urno.csv.gz", parse_dates=["time_utc"],
                     dtype={"pm25_cal_note": str, "pm10_cal_note": str})
    if samo_zanesljive:
        df = df[df.pm25_cal_note.isna() | (df.pm25_cal_note == "")]
    return df.reset_index(drop=True)


def dnevno(df, cas="time_utc", min_ur=18, agg="mean"):
    """Dnevna povprečja (UTC dan) numeričnih stolpcev; dan velja, če ima vsaj min_ur ur s podatkom (po stolpcu)."""
    d = df.set_index(cas)
    num = d.select_dtypes("number")
    g = num.groupby(num.index.floor("D"))
    out = g.agg(agg)
    n = g.count()
    out = out.where(n >= min_ur)
    out.index.name = "dan"
    return out.reset_index()


def zima(df, cas="time_utc", meseci=ZIMSKI_MESECI):
    """Samo zimski meseci (privzeto nov–mar)."""
    return df[df[cas].dt.month.isin(meseci)]


def sezona(ts):
    """Oznaka kurilne sezone, npr. 2025-12-03 -> '2025/26'. Vektorizirano za Series."""
    if isinstance(ts, pd.Series):
        y = ts.dt.year.where(ts.dt.month >= 7, ts.dt.year - 1)
        return y.astype(str) + "/" + ((y + 1) % 100).astype(str).str.zfill(2)
    y = ts.year if ts.month >= 7 else ts.year - 1
    return f"{y}/{(y + 1) % 100:02d}"


def razred_iqz(pm25=None, pm10=None):
    """Razred kakovosti zraka po urnih vrednostih (približek lestvice ARSO/EEA za PM): 1 dobro … 5 zelo slabo.
    Mejne vrednosti (µg/m³): PM2.5 10/20/25/50; PM10 20/40/50/100."""
    def r(v, meje):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return np.nan
        return 1 + sum(v > m for m in meje)
    c = [x for x in (r(pm25, [10, 20, 25, 50]), r(pm10, [20, 40, 50, 100])) if not (isinstance(x, float) and np.isnan(x))]
    return max(c) if c else np.nan


RAZRED_IME = {1: "dobro", 2: "zadovoljivo", 3: "zmerno", 4: "slabo", 5: "zelo slabo"}
