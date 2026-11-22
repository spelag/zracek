"""Current + next-12-hour air quality for a point in Ljubljana.

Same idea as section 2 of the notebook 05_obcan_zdaj (zgradi_05.py), rewritten as a standalone
module (the notebook's functions are not importable). Put this file next to paket.py, then:

    from air_quality_12h import air_quality_forecast
    df = air_quality_forecast(46.045, 14.480, "2026-01-20 18:00")   # offline: from the data files
    df = air_quality_forecast(46.045, 14.480)                       # live: now, newest data (needs internet)

Row 0 is the latest MEASUREMENT of the nearest ARSO station; rows 1..12 are ESTIMATES: the typical
winter daily profile anchored on that measurement and weighted half-and-half with the CAMS trend.

Live mode (t=None or live=True) uses the same data files plus the newest data from
v_zivo/zrak_v_zivo.py: ARSO last hour, cloud-capture history and the Open-Meteo CAMS forecast.
"""
from __future__ import annotations

import math
import sys
import warnings
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from paket import (POSTAJE, najblizja_postaja, nalozi_arso, nalozi_cams,
                   nalozi_inverzijo, zima)

# Class limits in ug/m3 (table in section 1 of the notebook): class k if value <= limit[k-1],
# above the last limit -> class 5. The class of a point is the worse of PM2.5 and PM10.
_LIMITS = {"pm25": (10, 20, 25, 50), "pm10": (20, 40, 50, 100)}
_POLLUTANTS = ("pm25", "pm10")
LIVE_TTL_MIN = 10          # live data is re-downloaded at most this often
FALLBACK_MAX_KM = 5.0      # a fallback station (not the nearest) is used only if it is LESS than this far away
LIVE_HISTORY_DAYS = 365    # cloud capture only exists since 13 Nov 2026; the whole CSV is downloaded anyway,
                           # this just filters it, so a long window costs nothing and leaves no gap after the files end
_live_cache: dict = {}
_warned_no_coords = False


def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _stations_by_distance(lat, lon):
    """[(station_code, km), ...] nearest first.

    The nearest comes from najblizja_postaja (exactly as before). The others are ranked by great-circle distance.
    ASSUMPTION (paket.py not seen): every POSTAJE[code] has 'lat' and 'lon' keys. If not, only the nearest
    station is returned (old behaviour, no fallback) and a warning is shown once.
    """
    global _warned_no_coords
    code0, km0 = najblizja_postaja(lat, lon)
    rest = []
    for code, info in POSTAJE.items():
        if code == code0:
            continue
        try:
            rest.append((code, _haversine_km(lat, lon, float(info["lat"]), float(info["lon"]))))
        except (KeyError, TypeError, ValueError):
            if not _warned_no_coords:
                warnings.warn("POSTAJE entries have no 'lat'/'lon': cannot rank fallback stations, using the nearest only")
                _warned_no_coords = True
            return [(code0, km0)]
    return [(code0, km0)] + sorted(rest, key=lambda x: x[1])


def _class_of(value, limits):
    if value is None or np.isnan(value):
        return None
    for k, lim in enumerate(limits, start=1):
        if value <= lim:
            return k
    return 5


def aq_class(pm25, pm10):
    """Air quality class 1 (good) .. 5 (very bad); None if both values are missing."""
    cs = [c for c in (_class_of(pm25, _LIMITS["pm25"]), _class_of(pm10, _LIMITS["pm10"])) if c is not None]
    return max(cs) if cs else None


def _as_utc(s: pd.Series) -> pd.Series:
    s = pd.to_datetime(s)
    return s.dt.tz_localize("UTC") if s.dt.tz is None else s.dt.tz_convert("UTC")


@lru_cache(maxsize=1)
def _load():
    """Load the data files once: ARSO measurements, inversion flag, CAMS series, typical daily profiles."""
    arso = nalozi_arso()
    inv = nalozi_inverzijo()[["time_utc", "inv_pasja_ravan"]]
    cams = nalozi_cams("E403")           # model; all three stations share one grid cell
    for df in (arso, inv, cams):
        df["time_utc"] = _as_utc(df["time_utc"])
    arso = arso.sort_values("time_utc")
    inv = inv.sort_values("time_utc")

    # CAMS series per pollutant (falls back to the PM2.5 trend if the file has no PM10 column)
    cams = cams.drop_duplicates("time_utc").set_index("time_utc").sort_index()
    cams_s = {"pm25": cams["cams_pm25"],
              "pm10": cams["cams_pm10"] if "cams_pm10" in cams.columns else cams["cams_pm25"]}

    # typical winter profile by (station, inversion yes/no, weekend, UTC hour), as in the notebook
    w = zima(arso[arso.time_utc >= "2023-11-01"]).merge(inv, on="time_utc", how="left")
    w = w.dropna(subset=["inv_pasja_ravan"])
    w["inv"] = w["inv_pasja_ravan"].astype(float).astype(bool)
    w["hour"] = w.time_utc.dt.hour
    w["weekend"] = w.time_utc.dt.dayofweek >= 5
    profiles = {}
    for p in _POLLUTANTS:
        g = w.dropna(subset=[p])
        profiles[p] = (g.groupby(["station_code", "inv", "weekend", "hour"])[p].mean(),
                       g.groupby(["station_code", "weekend", "hour"])[p].mean())   # used if inversion unknown
    return arso, inv, cams_s, profiles


def _zv():
    """Import v_zivo/zrak_v_zivo.py (same as the notebook: ../v_zivo relative to this folder)."""
    try:
        import zrak_v_zivo as zv
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v_zivo"))
        import zrak_v_zivo as zv
    return zv


def _load_live():
    """Same tuple as _load(), but with the newest measurements and CAMS forecast added.

    The typical profiles stay computed from the historical files (they are climatology, not news).
    The inversion flag has no live source in v_zivo, so it stays as in the files (-> None for new hours,
    and the profile without the inversion split is used).
    """
    now = pd.Timestamp.now(tz="UTC")
    if _live_cache and now - _live_cache["at"] < pd.Timedelta(minutes=LIVE_TTL_MIN):
        return _live_cache["data"]
    arso, inv, cams_s, profiles = _load()
    zv = _zv()

    # measurements: files, then cloud-capture history (since 13 Nov 2026), then ARSO last hour (newest wins)
    cols = ["station_code", "time_utc", "pm25", "pm10"]
    parts = [arso[cols]]
    for name, fn in (("cloud history", lambda: zv.arso_zgodovina(LIVE_HISTORY_DAYS)), ("ARSO last hour", zv.arso_zadnja_ura)):
        try:
            parts.append(fn()[cols])
        except Exception as e:  # noqa: BLE001
            warnings.warn(f"live source '{name}' failed: {e!r}")
    live_arso = pd.concat(parts, ignore_index=True)
    live_arso["time_utc"] = _as_utc(live_arso["time_utc"])
    for c in ("pm25", "pm10"):
        live_arso[c] = pd.to_numeric(live_arso[c], errors="coerce")
    live_arso = (live_arso.dropna(subset=["pm25", "pm10"], how="all")
                 .drop_duplicates(["station_code", "time_utc"], keep="last").sort_values("time_utc"))

    # CAMS forecast for the Ljubljana cell (napoved() defaults to Bezigrad = the same cell as all three stations)
    try:
        nap = zv.napoved().set_index("time_utc")
        has_pm10 = cams_s["pm10"] is not cams_s["pm25"]           # file has its own PM10 series
        src = {"pm25": "pm2_5", "pm10": "pm10" if has_pm10 else "pm2_5"}   # keep PM10 trend consistent with the file
        cams_s = {p: pd.concat([cams_s[p], nap[src[p]].dropna()]).pipe(lambda s: s[~s.index.duplicated(keep="last")]).sort_index()
                  for p in _POLLUTANTS}
    except Exception as e:  # noqa: BLE001
        warnings.warn(f"live source 'Open-Meteo CAMS forecast' failed: {e!r}; CAMS trend not available for new hours")

    _live_cache.update(at=now, data=(live_arso, inv, cams_s, profiles))
    return _live_cache["data"]


def _typical(profile, code, inv_flag, ts):
    """Typical value for each timestamp in ts (UTC)."""
    by_inv, by_any = profile
    out = []
    for th in ts:
        wk = bool(th.dayofweek >= 5)
        v = by_inv.get((code, inv_flag, wk, th.hour), np.nan) if inv_flag is not None else np.nan
        if np.isnan(v):
            v = by_any.get((code, wk, th.hour), np.nan)
        out.append(v)
    return np.array(out, dtype=float)


def air_quality_forecast(lat: float, lon: float, t=None, max_lag_h: int = 3, horizon: int = 12,
                         live: bool = False, fallback: bool = True, fallback_max_km: float = FALLBACK_MAX_KM,
                         max_station_km: float | None = None) -> pd.DataFrame:
    """Current air quality and estimate for the next `horizon` hours at (lat, lon).

    Parameters
    ----------
    lat, lon   : location; the nearest ARSO station is used.
    t          : time of the query. Naive timestamps are treated as UTC (as in the notebook).
                 None = right now, in live mode.
    max_lag_h  : the latest measurement must be at most this many hours older than t.
                 (ARSO publishes ~35 min after the hour, so live "now" is usually 1.5-2.6 h old.)
    horizon    : number of hours ahead (default 12 -> 13 rows).
    live       : also use the newest data from zrak_v_zivo (needs internet). Implied by t=None.
    fallback   : if the nearest station has no PM2.5 measurement in the last max_lag_h hours, try the next
                 closest station, then the next (the forecast then uses THAT station's typical profile).
    fallback_max_km : a fallback station is used only if it is LESS than this many km away (default 5).
                 The nearest station is not subject to this limit.
    max_station_km : hard limit for every station including the nearest (None = no limit).

    Returns
    -------
    DataFrame indexed by hour_offset 0..horizon with columns
        time_utc  : hour (UTC) of the row, t floored to the hour + offset
        pm25,pm10 : ug/m3 (row 0 measured, other rows estimated)
        aq_class  : 1 good .. 5 very bad (worse of PM2.5 and PM10)
        kind      : "measurement" (row 0) or "estimate"
    df.attrs: station, station_km, measured_at (UTC time of the measurement in row 0), inversion
              (None = unknown), live, cams_trend (False = no CAMS trend available, typical profile only),
              fallback (True = the nearest station had no data and a farther one was used; station/station_km
              describe the station actually used).

    Raises ValueError if no allowed station has a PM2.5 measurement in the max_lag_h before t.
    """
    if t is None:
        t, live = pd.Timestamp.now(tz="UTC"), True
    t = pd.Timestamp(t)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    lag = pd.Timedelta(hours=max_lag_h)
    arso, inv, cams_s, profiles = _load_live() if live else _load()

    # --- row 0: latest measurement at the nearest station that has one (nearest first, then next closest)
    stations = _stations_by_distance(lat, lon)
    nearest = stations[0][0]
    if max_station_km is not None:
        stations = [s for s in stations if s[1] <= max_station_km]
    too_far = [c for c, km in stations if c != nearest and km >= fallback_max_km] if fallback else []
    stations = [s for s in stations if s[0] == nearest or (fallback and s[1] < fallback_max_km)]
    m = None
    for code, dist_km in stations:                       # after the loop, code/dist_km = the station used
        cand = arso[(arso.station_code == code) & (arso.time_utc <= t) & (arso.time_utc > t - lag)] \
            .dropna(subset=["pm25"]).tail(1)
        if not cand.empty:
            m = cand.iloc[0]
            break
    if m is None:
        tried = ", ".join(c for c, _ in stations) or "none (no station within max_station_km)"
        raise ValueError(f"No PM2.5 measurement at station(s) {tried} in the {max_lag_h} h before "
                         f"{t:%Y-%m-%d %H:%M} UTC (newest ARSO data: {arso.time_utc.max():%Y-%m-%d %H:%M} UTC"
                         f"{'' if live else '; try live=True or t=None'})"
                         + (f"; not tried, farther than {fallback_max_km:g} km: {', '.join(too_far)}." if too_far else "."))
    now = {"pm25": float(m.pm25), "pm10": float(m.pm10) if pd.notna(m.pm10) else np.nan}

    # inversion state at t (held constant over the 12 h, like in the notebook)
    i = inv[(inv.time_utc <= t) & (inv.time_utc > t - lag)].dropna(subset=["inv_pasja_ravan"]).tail(1)
    inv_flag = bool(i.inv_pasja_ravan.iloc[0]) if len(i) else None

    # --- rows 1..horizon: typical profile anchored on the measurement, scaled by CAMS trend
    t0 = t.floor("h")
    grid = pd.date_range(t0, periods=horizon + 1, freq="h")
    est, cams_used = {}, {}
    for p in _POLLUTANTS:
        if np.isnan(now[p]):
            est[p] = np.full(horizon + 1, np.nan)
            continue
        ref = _typical(profiles[p], code, inv_flag, [m.time_utc])[0]
        factor = float(np.clip(now[p] / ref, 0.3, 3.0)) if ref > 0 else 1.0      # anchor, but not too much
        typ = _typical(profiles[p], code, inv_flag, grid) * factor
        c = cams_s[p]
        base = c.get(t0, np.nan)
        ratio = np.array([c.get(th, np.nan) / base if base > 0 else np.nan for th in grid])
        cams_used[p] = bool(np.isfinite(ratio[1:]).any())
        est[p] = typ * np.where(np.isnan(ratio), 1.0, (1 + ratio) / 2)            # half weight to CAMS trend

    df = pd.DataFrame({"time_utc": grid, "pm25": est["pm25"], "pm10": est["pm10"]})
    df.loc[0, ["pm25", "pm10"]] = [now["pm25"], now["pm10"]]
    df["aq_class"] = pd.array([aq_class(a, b) for a, b in zip(df.pm25, df.pm10)], dtype="Int64")
    df["kind"] = ["measurement"] + ["estimate"] * horizon
    df[["pm25", "pm10"]] = df[["pm25", "pm10"]].round(1)
    df.index.name = "hour_offset"
    df.attrs.update(station=POSTAJE[code]["ime"], station_km=round(dist_km, 1), measured_at=m.time_utc,
                    inversion=inv_flag, live=live, cams_trend=cams_used.get("pm25", False),
                    fallback=(code != nearest))
    return df


if __name__ == "__main__":
    out = air_quality_forecast(46.045, 14.480, sys.argv[1] if len(sys.argv) > 1 else None)
    print(out.attrs)
    print(out.to_string())