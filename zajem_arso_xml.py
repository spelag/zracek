"""Urni zajem ARSO XML feeda s podatki o kakovosti zraka (vse postaje v Sloveniji).

Vir:   https://www.arso.gov.si/xml/zrak/ones_zrak_urni_podatki_zadnji.xml
       (ARSO predlaga zajem 45 minut čez polno uro, perioda 60 min)
Izhod: zajem_arso_xml/raw/<leto>/ones_zrak_<YYYYMMDD_HHMM>.xml  (surova kopija, ime po datum_priprave)
       zajem_arso_xml/arso_live_urno.csv                (široka tabela: postaja × ura, brez podvajanj)
       zajem_arso_xml/zajem.log

Zagon: py zajem_arso_xml.py   (samo standardna knjižnica; vsako uro ob :50 prek crona ali Task Schedulerja)
"""
import csv
import datetime as dt
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

URL = "https://www.arso.gov.si/xml/zrak/ones_zrak_urni_podatki_zadnji.xml"
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "zajem_arso_xml"
RAW = OUT / "raw"
CSV = OUT / "arso_live_urno.csv"
LOG = OUT / "zajem.log"
POLLUTANTS = ["so2", "co", "o3", "no2", "nox", "pm10", "pm2.5", "benzen"]
COLUMNS = ["time_from_local", "time_to_local", "station_code", "station_name", "lat", "lon", "alt_m",
           "d96_e", "d96_n"] + [p.replace(".", "") for p in POLLUTANTS] + ["prepared_local", "fetched_utc"]


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{dt.datetime.now(dt.UTC):%Y-%m-%dT%H:%M:%SZ} arso_xml {msg}\n")


def fetch(url: str, tries: int = 3) -> bytes:
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(30 * (i + 1))
    raise RuntimeError(f"prenos ni uspel: {last}")


def main() -> None:
    fetched = dt.datetime.now(dt.UTC)
    data = fetch(URL)
    root = ET.fromstring(data)
    prepared = root.findtext("datum_priprave", "").strip()  # npr. "2026-11-13 11:35" (lokalni čas)
    stamp = dt.datetime.strptime(prepared, "%Y-%m-%d %H:%M")

    raw_path = RAW / f"{stamp:%Y}" / f"ones_zrak_{stamp:%Y%m%d_%H%M}.xml"
    if raw_path.exists():
        log(f"brez sprememb (datum_priprave {prepared})")
        return
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(data)

    existing = set()
    if CSV.exists():
        with CSV.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                existing.add((row["station_code"], row["time_from_local"]))

    new_rows = []
    for st in root.iter("postaja"):
        code = st.get("sifra")
        t_from = st.findtext("datum_od", "").strip()
        key = (code, t_from)
        if key in existing:
            continue
        existing.add(key)
        row = {
            "time_from_local": t_from.replace(" ", "T"),
            "time_to_local": st.findtext("datum_do", "").strip().replace(" ", "T"),
            "station_code": code,
            "station_name": st.findtext("merilno_mesto", "").strip(),
            "lat": st.get("wgs84_sirina", ""), "lon": st.get("wgs84_dolzina", ""),
            "alt_m": st.get("nadm_visina", ""), "d96_e": st.get("d96_e", ""), "d96_n": st.get("d96_n", ""),
            "prepared_local": prepared.replace(" ", "T"),
            "fetched_utc": f"{fetched:%Y-%m-%dT%H:%M:%SZ}",
        }
        for p in POLLUTANTS:
            row[p.replace(".", "")] = st.findtext(p, "").strip()
        new_rows.append(row)

    write_header = not CSV.exists()
    with CSV.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        if write_header:
            w.writeheader()
        w.writerows(new_rows)
    log(f"shranjeno {raw_path.name}, novih vrstic {len(new_rows)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log(f"NAPAKA {e!r}")
        sys.exit(1)
