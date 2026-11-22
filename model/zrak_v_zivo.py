"""Zrak v Ljubljani v živo: zadnje ure meritev ARSO + napoved vremena in CAMS (Open-Meteo) v enem DataFrame-u.

Primer za ekipe ADA Hack 2026. Brez ključev, brez registracije. Potrebuje: pandas, requests.

    py zrak_v_zivo.py                 # izpiše zadnjo uro za Ljubljano in napoved za naslednjih 24 ur
    from zrak_v_zivo import *         # uporabi funkcije v svoji kodi

Viri in zamiki:
- ARSO XML feed (uradne meritve, vse postaje v Sloveniji, samo zadnja ura; ARSO ga obnovi ~35 min po polni uri)
- zajem v oblaku (ADA Hack): zgodovina istega feeda od 13. 11. 2026 naprej, osvežena vsako uro ob :50
- Open-Meteo: napoved vremena (ICON/GFS mešanica) in kakovosti zraka (CAMS Europe 0,1°), brez ključa
"""
import datetime as dt
import io
import xml.etree.ElementTree as ET

import pandas as pd
import requests

ARSO_XML = "https://www.arso.gov.si/xml/zrak/ones_zrak_urni_podatki_zadnji.xml"
ZAJEM = "https://ada-hack-zrak.live-event-recap-spike.workers.dev"
OM_WEATHER = "https://api.open-meteo.com/v1/forecast"
OM_AQ = "https://air-quality-api.open-meteo.com/v1/air-quality"
LJ_STATIONS = {"E403": "LJ Bežigrad", "E405": "LJ Celovška", "E404": "LJ Vič"}
LOCAL_OFFSET = dt.timedelta(hours=1)  # ARSO: UTC+1 brez poletnega premika
POLLUTANTS = ["so2", "co", "o3", "no2", "nox", "pm10", "pm2.5", "benzen"]


def _num(s: str):
    """ARSO zapiše vrednosti pod mejo zaznave kot '<2'; vrnemo polovico meje in oznako."""
    if s is None or (isinstance(s, float) and s != s):  # None ali NaN
        return None, ""
    s = str(s).strip()
    if not s:
        return None, ""
    if s.startswith("<"):
        return float(s[1:].replace(",", ".")) / 2, "pod_mejo"
    return float(s.replace(",", ".")), ""


def arso_zadnja_ura() -> pd.DataFrame:
    """Zadnja ura meritev za vse postaje ARSO (ena vrstica na postajo)."""
    r = requests.get(ARSO_XML, timeout=60)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    rows = []
    for st in root.iter("postaja"):
        t_local = dt.datetime.strptime(st.findtext("datum_od"), "%Y-%m-%d %H:%M")
        row = {
            "time_utc": (t_local - LOCAL_OFFSET).replace(tzinfo=dt.UTC),
            "time_local": t_local,
            "station_code": st.get("sifra"),
            "station_name": st.findtext("merilno_mesto"),
            "lat": float(st.get("wgs84_sirina")) if st.get("wgs84_sirina") else None,
            "lon": float(st.get("wgs84_dolzina")) if st.get("wgs84_dolzina") else None,
        }
        for p in POLLUTANTS:
            v, flag = _num(st.findtext(p))
            row[p.replace(".", "")] = v
            if flag:
                row[p.replace(".", "") + "_flag"] = flag
        rows.append(row)
    return pd.DataFrame(rows)


def arso_zgodovina(dni: int = 5) -> pd.DataFrame:
    """Zajete ure iz zajema v oblaku (od 13. 11. 2026), zadnjih `dni` dni, vse postaje."""
    r = requests.get(f"{ZAJEM}/arso/history.csv", timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), dtype=str)
    df = df.drop_duplicates(["station_code", "time_from_local"], keep="last")
    df["time_local"] = pd.to_datetime(df["time_from_local"])
    df["time_utc"] = (df["time_local"] - LOCAL_OFFSET).dt.tz_localize("UTC")
    for p in ["so2", "co", "o3", "no2", "nox", "pm10", "pm25", "benzen"]:
        vals = df[p].map(lambda s: _num(s)[0])
        df[p] = vals
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    od = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=dni)
    return df[df["time_utc"] >= od].sort_values(["station_code", "time_utc"]).reset_index(drop=True)


def napoved(lat: float = 46.0655, lon: float = 14.5127, ure: int = 48) -> pd.DataFrame:
    """Urna napoved vremena in kakovosti zraka (CAMS) za točko, naslednjih `ure` ur. Model, ne meritev."""
    w = requests.get(OM_WEATHER, params={
        "latitude": lat, "longitude": lon, "timezone": "UTC", "forecast_days": 3,
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation,cloud_cover,boundary_layer_height",
    }, timeout=60).json()["hourly"]
    a = requests.get(OM_AQ, params={
        "latitude": lat, "longitude": lon, "timezone": "UTC", "forecast_days": 3, "domains": "cams_europe",
        "hourly": "pm10,pm2_5,nitrogen_dioxide,ozone,european_aqi",
    }, timeout=60).json()["hourly"]
    df = pd.DataFrame(w).merge(pd.DataFrame(a), on="time")
    df["time_utc"] = pd.to_datetime(df.pop("time")).dt.tz_localize("UTC")
    now = pd.Timestamp.now(tz="UTC").floor("h")
    return df[(df.time_utc >= now) & (df.time_utc < now + pd.Timedelta(hours=ure))].reset_index(drop=True)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 200)
    zadnja = arso_zadnja_ura()
    lj = zadnja[zadnja.station_code.isin(LJ_STATIONS)]
    print("Zadnja ura ARSO, Ljubljana:")
    print(lj[["time_local", "station_name", "pm10", "pm25", "no2", "o3"]].to_string(index=False))
    zg = arso_zgodovina(5)
    print(f"\nZgodovina iz zajema: {len(zg)} vrstic, {zg.time_utc.min()} – {zg.time_utc.max()}, postaj {zg.station_code.nunique()}")
    bez = zg[zg.station_code == "E403"].set_index("time_utc")["pm25"]
    print("PM2.5 Bežigrad, zadnjih 12 ur:", bez.tail(12).round(1).tolist())
    nap = napoved()
    print("\nNapoved Bežigrad, naslednjih 24 ur (CAMS PM2.5, T, veter, višina mejne plasti):")
    print(nap.head(24)[["time_utc", "pm2_5", "temperature_2m", "wind_speed_10m", "boundary_layer_height"]].to_string(index=False))
