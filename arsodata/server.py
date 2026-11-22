"""REST API for the Ljubljana 12-hour air quality forecast.

Wraps air_12.air_quality_forecast as an HTTP endpoint that returns a compact
array of air-quality classes 1..5 (1=good … 5=very bad), one per hour.

Setup
-----
    pip install flask pandas numpy requests h3

Run
---
    python server.py                                   # http://127.0.0.1:9161
    # production: gunicorn -w 1 -b 0.0.0.0:5000 server:app

Data files (optional)
---------------------
Live mode is the default and needs only internet access: no local data files
are required.  Offline mode (``live=0`` or historic ``at`` values) uses the
ARSOhack data package.  If that package is missing, startup prints full
installation instructions and offline requests return 503
``data_files_missing``.  Get it from https://github.com/vidacex/ARSOhack and
place ``01_meritve/``, ``02_modeli/`` and ``03_vreme_inverzija/`` at the
project root (next to ``arsodata/``); ``04_prostor/`` is only needed by the
dashboard's ``baza.py``.

Dashboard database
------------------
Every valid request to /air-quality is also aggregated into the ARSO dashboard
database (``arso_nadzorna_plosca/zrak.db``) by ``zrak_baza.zabelezi_zahtevo``:
the snapped lat/lon becomes an H3 cell and its occupancy for the current UTC
hour is incremented and committed.  Raw coordinates are never stored.

``ZRAK_DB`` selects the dashboard DB (default: ../arso_nadzorna_plosca/zrak.db)
and ``ZRAK_STAGING_DB`` the private sub-threshold counter DB (default:
zrak_zahteve.db next to it).  Recording is best-effort: if the DB is missing,
locked or h3 is not installed, the forecast is still answered normally.

Endpoints
---------
GET /health
    -> {"status": "ok"}

GET /companion
GET /companion/<path:filename>
    -> Static files from ./companion (defaults to index.html)

GET /air-quality?lat=46.045&lon=14.480
    -> [2, 3, 5, 4, 2, 3, 4, 3, 2, 1, 2, 3]          # 12 ints, 1..5 (or null)

GET /air-quality?lat=46.045&lon=14.480&meta=1
    -> {"lat": 46.045, "lon": 14.48,
        "station": "LJ Bežigrad", "station_km": 0.6,
        "measured_at": "2026-01-20T17:00:00+00:00",
        "inversion": true, "live": true,
        "cams_trend": true, "fallback": false,
        "hours": 12, "start_offset": 0,
        "classes": [2, 3, 5, 4, 2, ...],
        "times": ["2026-01-20T18:00:00+00:00", ...]}

Query parameters
----------------
lat, lon         required. WGS84 degrees.
hours            1..13 (default 12). Number of classes returned.
include_current  1 (default) -> element 0 is the latest measurement.
                 0          -> element 0 is the first forecast hour.
                 So include_current=1, hours=12  ==  "now + next 11 h";
                    include_current=0, hours=12  ==  "next 12 h".
live             1 (default) -> newest ARSO + Open-Meteo CAMS (needs internet);
                   also used when only 'at' is supplied.
                 0 -> local data files only (offline/archive). Requires the
                   ARSOhack data package; returns 503 if it is missing.
                 If 'at' is omitted (i.e. "now"), 'live=1' is required.
at               ISO timestamp, e.g. 2026-01-20T18:00 (naive = UTC).
                 Omitted -> "now" (implies live=1).
meta             1 -> return metadata object instead of the plain array.
compact          legacy alias: 1 -> array only (default). Overrides meta.

Errors
------
400  missing / invalid lat, lon, hours, at (body: {"error", "code"})
404  no station with a recent PM2.5 measurement near (lat, lon)
503  live=0 requested but the optional ARSOhack data package is missing
500  unexpected failure

Notes
-----
- air_12 caches live downloads for 10 min, so repeated live calls are cheap.
- Values are 1 (good) … 5 (very bad); null means no data for that hour.
- The Flask dev server is single-threaded. For production, run with a single
  worker or add a lock around air_12._live_cache.
- A live request can take up to a few seconds (Open-Meteo round trip); it can
  take ~1 min if the network is flaky (internal requests have timeout=60s).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
COMPANION_DIR = BASE_DIR / "companion"

# Make sibling modules importable regardless of where this file is launched from.
sys.path.insert(0, str(BASE_DIR))

try:
    import pandas as pd
    from air_12 import air_quality_forecast
    from paket import manjkajoce_datoteke, navodila_za_podatke
except ImportError as _e:  # pragma: no cover
    sys.stderr.write(
        f"ERROR: could not import air_12 / pandas / flask: {_e}\n"
        "Place this file next to air_12.py and paket.py (and optionally v_zivo/).\n"
        "Install: pip install flask pandas numpy requests h3\n"
    )
    raise

try:
    from zrak_baza import zabelezi_zahtevo
except Exception as _e:  # pragma: no cover - recording must never break the API
    zabelezi_zahtevo = None
    sys.stderr.write(f"WARNING: dashboard bridge unavailable ({_e}); requests will not be stored.\n")

app = Flask(__name__)


def _opozori_o_podatkih() -> None:
    """Tell the operator on startup how to install the optional data package."""
    try:
        if manjkajoce_datoteke():
            sys.stderr.write("\n" + navodila_za_podatke() + "\n\n")
    except Exception:  # noqa: BLE001 - a startup notice must never prevent serving
        pass


_opozori_o_podatkih()


# --------------------------------------------------------------------------- helpers

def _as_bool(v, default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _int_or_none(v):
    """pandas nullable Int64 / numpy int / None -> plain int or None."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return int(v)


# --------------------------------------------------------------------------- routes

@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/companion", defaults={"filename": "index.html"})
@app.get("/companion/", defaults={"filename": "index.html"})
@app.get("/companion/<path:filename>")
def companion(filename: str):
    return send_from_directory(COMPANION_DIR, filename)


@app.get("/air-quality")
def air_quality():
    # ---- lat/lon --------------------------------------------------------
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Query parameters 'lat' and 'lon' are required and must be numbers.",
                       code="bad_request"), 400
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return jsonify(error="lat must be in [-90, 90] and lon in [-180, 180].",
                       code="bad_request"), 400

    # ---- dashboard: commit this request as aggregated occupancy --------
    # Best-effort: a database problem must not break the forecast.
    if zabelezi_zahtevo is not None:
        try:
            zapis = zabelezi_zahtevo(lat, lon)
            if zapis:
                app.logger.debug("recorded request in zrak.db: %s", zapis)
        except Exception:  # noqa: BLE001
            app.logger.exception("could not record air-data request in zrak.db")

    # ---- time / live ----------------------------------------------------
    at = request.args.get("at")            # None -> "now"
    live = _as_bool(request.args.get("live"), default=True)   # live data is the default
    if at is None:
        if "live" in request.args and not live:
            return jsonify(error="If 'at' is omitted (i.e. \"now\"), 'live=1' is required.",
                           code="bad_request"), 400
        live = True                        # "now" only makes sense with fresh data
    else:
        try:
            pd.Timestamp(at)
        except (TypeError, ValueError):
            return jsonify(error="'at' must be an ISO timestamp, e.g. 2026-01-20T18:00.",
                           code="bad_request"), 400

    # ---- slice options --------------------------------------------------
    include_current = _as_bool(request.args.get("include_current"), default=True)
    max_hours = 13 if include_current else 12
    try:
        hours = int(request.args.get("hours", 12))
    except ValueError:
        return jsonify(error="'hours' must be an integer.", code="bad_request"), 400
    if not (1 <= hours <= max_hours):
        return jsonify(
            error=f"'hours' must be between 1 and {max_hours} "
                  f"for include_current={'1' if include_current else '0'}.",
            code="bad_request"), 400

    want_meta = _as_bool(request.args.get("meta"), default=False)
    if _as_bool(request.args.get("compact"), default=False):
        want_meta = False

    # ---- forecast -------------------------------------------------------
    try:
        df = air_quality_forecast(lat, lon, t=at, live=live)
    except ValueError as e:
        # air_12 raises ValueError when no station had a recent PM2.5
        return jsonify(error=str(e), code="no_data_nearby"), 404
    except FileNotFoundError as e:
        # live=False was requested but the optional ARSOhack data package is absent
        app.logger.warning("offline forecast requested but data package is incomplete: %s", e)
        return jsonify(
            error="Offline mode (live=0) needs the ARSOhack data package, which is not "
                  "installed. Live mode is the default and works without it. "
                  "See the server startup log or https://github.com/vidacex/ARSOhack.",
            code="data_files_missing"), 503
    except Exception as e:  # noqa: BLE001
        app.logger.exception("air_quality_forecast failed")
        return jsonify(error=f"internal error: {e}", code="internal"), 500

    start = 0 if include_current else 1
    rows = df.iloc[start:start + hours]
    classes = [_int_or_none(c) for c in rows["aq_class"]]

    # ---- plain array (default) -----------------------------------------
    if not want_meta:
        return jsonify(classes)

    # ---- full metadata --------------------------------------------------
    times = [t.isoformat() for t in rows["time_utc"]]
    measured_at = df.attrs.get("measured_at")
    return jsonify(
        lat=lat, lon=lon,
        station=df.attrs.get("station"),
        station_km=df.attrs.get("station_km"),
        measured_at=measured_at.isoformat() if measured_at is not None else None,
        inversion=df.attrs.get("inversion"),
        live=df.attrs.get("live"),
        cams_trend=df.attrs.get("cams_trend"),
        fallback=df.attrs.get("fallback"),
        hours=len(classes),
        start_offset=int(rows.index[0]) if len(rows) else None,
        classes=classes,
        times=times,
    )


# --------------------------------------------------------------------------- main

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "9161")),
        debug=_as_bool(os.environ.get("DEBUG"), default=False),
    )