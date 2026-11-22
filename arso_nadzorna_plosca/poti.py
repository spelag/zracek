"""Iskanje podatkovnega paketa ARSOhack.

Podatkovni paket ni del repozitorija. Skripte ga poiščejo v eni od teh lokacij
(prva, ki vsebuje mapo ``04_prostor/``, zmaga):

1. ``ARSO_PAKET`` (okoljska spremenljivka, pot do korena paketa),
2. ``arso_nadzorna_plosca/data/`` (prvotna oblika ``data/04_prostor/…``),
3. koren projekta (``04_prostor/``, ``01_meritve/`` … ob mapi ``arso_nadzorna_plosca``),
4. ``koren_projekta/data/``,
5. trenutna mapa in ``./data/``.

Paket: https://github.com/vidacex/ARSOhack
"""
from __future__ import annotations

import glob
import os
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJEKT = HERE.parent
DATA_URL = "https://github.com/vidacex/ARSOhack"
MARKER = "04_prostor"


def _kandidati():
    env = os.environ.get("ARSO_PAKET")
    if env:
        yield Path(env).expanduser()
    yield HERE / "data"
    yield PROJEKT
    yield PROJEKT / "data"
    # pogoste lokacije kloniranega paketa
    yield PROJEKT / "ARSOhack"
    yield PROJEKT.parent / "ARSOhack"
    yield Path.home() / "ARSOhack"
    yield Path.cwd()
    yield Path.cwd() / "data"


def _unikatni():
    videli, izbrani = set(), []
    for p in _kandidati():
        try:
            p = p.resolve()
        except OSError:
            pass
        if p not in videli:
            videli.add(p)
            izbrani.append(p)
    return izbrani


def poisci_paket():
    """Koren podatkovnega paketa ali ``None``."""
    for p in _unikatni():
        if (p / MARKER).is_dir():
            return p
    return None


PAKET = poisci_paket()


def navodila_za_paket() -> str:
    """Kratka, izvedljiva navodila za namestitev podatkovnega paketa."""
    lokacije = "\n".join(f"  {p}" for p in _unikatni())
    return "\n".join([
        "=" * 78,
        "Podatkovni paket ARSOhack ni najden - baza.py ne more zgraditi zrak.db.",
        "",
        "Iskal sem mapo 04_prostor/ v:",
        lokacije,
        "",
        "Namestitev podatkovnega paketa:",
        f"  git clone {DATA_URL}",
        "  cd ARSOhack",
        f"  cp -r 01_meritve 02_modeli 03_vreme_inverzija 04_prostor {PROJEKT}/",
        "",
        "Ali pa pokažite na obstoječo kopijo paketa:",
        "  export ARSO_PAKET=/pot/do/ARSOhack",
        "",
        "baza.py potrebuje 04_prostor (stavbe, OSM, četrtne skupnosti),",
        "01_meritve (postaje) in 02_modeli (CAMx, GRAL) za lastnosti celic.",
        "=" * 78,
    ])


@lru_cache(maxsize=None)
def najdi(ime: str) -> str:
    """Poišče datoteko kjerkoli pod podatkovnim paketom in vrne njeno pot."""
    if PAKET is None:
        raise FileNotFoundError(navodila_za_paket())
    zadetki = glob.glob(str(PAKET / "**" / ime), recursive=True)
    for z in sorted(zadetki):
        if Path(z).is_file():
            return z
    raise FileNotFoundError(
        f"{ime} ni v podatkovnem paketu {PAKET}.\n"
        f"Prenesite paket: {DATA_URL} (ali nastavite ARSO_PAKET)."
    )
