"""Bridge between the REST API and the ARSO dashboard database (``zrak.db``).

Every ``GET /air-quality`` request with valid coordinates carries a (snapped)
latitude and longitude.  Instead of storing that point, this module aggregates
it into an H3
cell and increments the cell's occupancy for the current UTC hour in the same
SQLite database that ``arso_nadzorna_plosca/app.py`` reads.

This is the production step described in ``arso_nadzorna_plosca/agregacija.py``:
aggregation happens on the server at the moment the location arrives, and the
raw point is never written anywhere.

Privacy model
-------------
* ``zrak.db`` only receives cells that reached the privacy threshold ``k``
  (``konfiguracija.k_prag``), exactly like ``baza.py``.  The schema's
  ``CHECK (n >= k_prag)`` is therefore never bypassed.
* Sub-threshold counters live in a separate, unpublished sidecar database
  (``zrak_zahteve.db`` by default).  They are aggregate counters, not users,
  and they never enter ``zrak.db``.
* Each request counts as one user for the current hour; the API has no device
  identity, so repeated polls from one device inflate the count.  This is
  documented in ``arso_nadzorna_plosca/README.md``.

Coexistence with the synthetic baseline
---------------------------------------
``baza.py`` builds a synthetic day in ``zrak.db``.  Real requests are written
with ``vir='dejansko'``:

* a cell/hour that already has a synthetic row is left alone until the real
  count reaches ``k``; then the synthetic row is replaced by the real one.
* other cells of the same hour keep their synthetic values, so the dashboard
  still has a baseline.
* ``zasedenost_povzetek`` is recomputed for the hour from the published rows
  plus the withheld real counters (and the synthetic withheld count of that
  hour), so displayed totals do not double-count.

Environment
-----------
``ZRAK_DB``          path to the dashboard DB (default: ../arso_nadzorna_plosca/zrak.db)
``ZRAK_STAGING_DB``  path to the private counter DB (default: zrak_zahteve.db next to ZRAK_DB)
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:  # the dashboard requires h3 v4; keep the API importable without it
    import h3
except ImportError:  # pragma: no cover - depends on the deployment
    h3 = None

BASE_DIR = Path(__file__).resolve().parent
PLOSCA_DIR = BASE_DIR.parent / "arso_nadzorna_plosca"
DB_PATH = Path(os.environ.get("ZRAK_DB", PLOSCA_DIR / "zrak.db")).expanduser().resolve()
STAGING_PATH = Path(
    os.environ.get("ZRAK_STAGING_DB", DB_PATH.with_name("zrak_zahteve.db"))
).expanduser().resolve()
SHEMA_PATH = PLOSCA_DIR / "shema.sql"

DEFAULT_RES = 8
DEFAULT_K = 10
VIR = "dejansko"
HOUR_FMT = "%Y-%m-%dT%H:00:00Z"
KEEP_HOURS = 72                    # prune private counters older than this
_LOCK = threading.Lock()
_warned_no_h3 = False

_STAGING_DDL = """
CREATE TABLE IF NOT EXISTS staging.osnutek (
    cell_id TEXT    NOT NULL,
    ura_utc TEXT    NOT NULL,
    n       INTEGER NOT NULL,
    res     INTEGER NOT NULL,
    PRIMARY KEY (cell_id, ura_utc)
);
CREATE TABLE IF NOT EXISTS staging.izhodisce (
    ura_utc          TEXT PRIMARY KEY,
    izpuscenih_sint  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS staging.meta (
    kljuc    TEXT PRIMARY KEY,
    vrednost TEXT NOT NULL
);
"""


# --------------------------------------------------------------------------- helpers

def _utc_hour(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime(HOUR_FMT)


def _open() -> sqlite3.Connection:
    """Connection to zrak.db with the private counter DB attached.

    Autocommit mode is used so the caller controls the transaction explicitly.
    ``BEGIN IMMEDIATE`` serialises writers across Flask threads/workers.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    STAGING_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=15)
    con.isolation_level = None
    con.execute("PRAGMA busy_timeout = 15000")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("ATTACH DATABASE ? AS staging", (str(STAGING_PATH),))
    return con


def _ensure_schema(con: sqlite3.Connection) -> None:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='zasedenost'"
    ).fetchone()
    if row is None:
        if not SHEMA_PATH.exists():
            raise FileNotFoundError(
                f"Dashboard schema not found: {SHEMA_PATH}. "
                "Point ZRAK_DB at an existing zrak.db or restore shema.sql."
            )
        con.executescript(SHEMA_PATH.read_text(encoding="utf-8"))
        # Defaults so the dashboard can open a DB that baza.py has not built yet.
        con.executemany(
            "INSERT OR IGNORE INTO konfiguracija (kljuc, vrednost) VALUES (?, ?)",
            [
                ("h3_res", str(DEFAULT_RES)),
                ("k_prag", str(DEFAULT_K)),
                ("vir_uporabnikov", VIR),
                ("datum_generiranja", datetime.now(timezone.utc).date().isoformat()),
            ],
        )
    con.executescript(_STAGING_DDL)


def _config(con: sqlite3.Connection) -> tuple[int, int]:
    conf = dict(con.execute("SELECT kljuc, vrednost FROM konfiguracija").fetchall())
    try:
        res = int(conf.get("h3_res", DEFAULT_RES))
    except (TypeError, ValueError):
        res = DEFAULT_RES
    try:
        k = int(conf.get("k_prag", DEFAULT_K))
    except (TypeError, ValueError):
        k = DEFAULT_K
    return res, k


def _reset_if_rebuilt(con: sqlite3.Connection) -> None:
    """Drop private counters when baza.py rebuilt zrak.db.

    baza.py writes a fresh ``zagon_id`` on every rebuild; staging stores the id
    it was accumulated against.  A mismatch means the published DB is new, so
    old counters would no longer correspond to it.
    """
    row = con.execute(
        "SELECT vrednost FROM konfiguracija WHERE kljuc='zagon_id'"
    ).fetchone()
    zagon = row[0] if row else ""
    stored = con.execute(
        "SELECT vrednost FROM staging.meta WHERE kljuc='zagon_id'"
    ).fetchone()
    if stored is None or stored[0] != zagon:
        con.execute("DELETE FROM staging.osnutek")
        con.execute("DELETE FROM staging.izhodisce")
        con.execute(
            "INSERT INTO staging.meta (kljuc, vrednost) VALUES ('zagon_id', ?) "
            "ON CONFLICT(kljuc) DO UPDATE SET vrednost=excluded.vrednost",
            (zagon,),
        )


def _objavi_celico(con: sqlite3.Connection, cell: str, res: int) -> None:
    """Insert the static cell row if baza.py has not published this cell yet."""
    lat, lon = h3.cell_to_latlng(cell)
    km2 = h3.cell_area(cell, unit="km^2")
    con.execute(
        "INSERT INTO celica (cell_id, res, lat, lon, km2, pm25_zima, gral_kurisca, "
        "dist_postaja_m, obcutljive, cs_ime) VALUES (?,?,?,?,?,NULL,NULL,NULL,0,NULL) "
        "ON CONFLICT(cell_id) DO NOTHING",
        (cell, int(res), float(lat), float(lon), float(km2)),
    )


def _posodobi_povzetek(con: sqlite3.Connection, ura: str, k: int) -> None:
    """Recompute the hour summary from published rows + withheld real counters."""
    objavljenih = con.execute(
        "SELECT COALESCE(SUM(n), 0) FROM zasedenost WHERE ura_utc = ?", (ura,)
    ).fetchone()[0]
    zadrzanih = con.execute(
        "SELECT COALESCE(SUM(n), 0) FROM staging.osnutek WHERE ura_utc = ? AND n < ?",
        (ura, k),
    ).fetchone()[0]
    osnova = con.execute(
        "SELECT COALESCE(izpuscenih_sint, 0) FROM staging.izhodisce WHERE ura_utc = ?",
        (ura,),
    ).fetchone()[0]
    izpuscenih = int(zadrzanih) + int(osnova)
    con.execute(
        "INSERT INTO zasedenost_povzetek (ura_utc, objavljenih, izpuscenih, k_prag, vir) "
        "VALUES (?,?,?,?,?) "
        "ON CONFLICT(ura_utc) DO UPDATE SET objavljenih=excluded.objavljenih, "
        "izpuscenih=excluded.izpuscenih, k_prag=excluded.k_prag, vir=excluded.vir",
        (ura, int(objavljenih), izpuscenih, int(k), VIR),
    )


def _pocisti_staro(con: sqlite3.Connection, ura: str) -> None:
    try:
        cutoff_dt = datetime.strptime(ura, HOUR_FMT).replace(tzinfo=timezone.utc) - timedelta(hours=KEEP_HOURS)
    except ValueError:
        return
    cutoff = cutoff_dt.strftime(HOUR_FMT)
    con.execute("DELETE FROM staging.osnutek WHERE ura_utc < ?", (cutoff,))
    con.execute("DELETE FROM staging.izhodisce WHERE ura_utc < ?", (cutoff,))


# --------------------------------------------------------------------------- public

def zabelezi_zahtevo(lat: float, lon: float, now: datetime | None = None) -> dict | None:
    """Aggregate one air-data request into zrak.db and commit.

    Returns a small diagnostic dict (``cell``, ``n``, ``published``, ``hour``),
    or ``None`` when h3 is unavailable.  Raises on database errors; the caller
    (the API route) treats recording as best-effort.
    """
    global _warned_no_h3
    if h3 is None:
        if not _warned_no_h3:
            import sys
            sys.stderr.write(
                "WARNING: h3 is not installed; /air-quality requests will not be "
                "recorded in zrak.db. Install h3 (v4) to enable the dashboard bridge.\n"
            )
            _warned_no_h3 = True
        return None

    ura = _utc_hour(now)
    with _LOCK:
        con = _open()
        try:
            _ensure_schema(con)
            con.execute("BEGIN IMMEDIATE")
            res, k = _config(con)
            cell = h3.latlng_to_cell(float(lat), float(lon), res)

            _reset_if_rebuilt(con)
            con.execute(
                "INSERT INTO staging.osnutek (cell_id, ura_utc, n, res) VALUES (?,?,1,?) "
                "ON CONFLICT(cell_id, ura_utc) DO UPDATE SET n = n + 1",
                (cell, ura, res),
            )
            n = con.execute(
                "SELECT n FROM staging.osnutek WHERE cell_id = ? AND ura_utc = ?",
                (cell, ura),
            ).fetchone()[0]

            # Remember the synthetic withheld count the first time we touch this hour,
            # so every later summary update keeps the baseline in the total.
            con.execute(
                "INSERT INTO staging.izhodisce (ura_utc, izpuscenih_sint) "
                "SELECT ?, COALESCE((SELECT izpuscenih FROM zasedenost_povzetek "
                "                    WHERE ura_utc = ? AND vir = 'sinteticno'), 0) "
                "WHERE NOT EXISTS (SELECT 1 FROM staging.izhodisce WHERE ura_utc = ?)",
                (ura, ura, ura),
            )

            published = n >= k
            if published:
                _objavi_celico(con, cell, res)
                con.execute(
                    "INSERT INTO zasedenost (cell_id, ura_utc, n, k_prag, vir) "
                    "VALUES (?,?,?,?,?) "
                    "ON CONFLICT(cell_id, ura_utc) DO UPDATE SET n=excluded.n, "
                    "k_prag=excluded.k_prag, vir=excluded.vir",
                    (cell, ura, int(n), int(k), VIR),
                )

            _posodobi_povzetek(con, ura, k)
            _pocisti_staro(con, ura)
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    return {"cell": cell, "n": int(n), "published": bool(published), "hour": ura}
