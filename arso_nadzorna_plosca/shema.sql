-- shema.sql - baza za ARSO nadzorno ploščo "Zrak, ki ga dihamo"
-- SQLite. Za PostgreSQL: TEXT časi -> TIMESTAMPTZ, lat/lon -> geometry(Point,4326).
--
-- NAČELO: baza NE vsebuje uporabnikov. Brez ID-jev, brez surovih lokacij, brez profilov
-- posameznikov. Agregacija (H3 + prag k) se zgodi PRED vpisom; v zasedenost pridejo
-- samo celice z vsaj k uporabniki.

PRAGMA foreign_keys = ON;

-- Metapodatki zagona: ločljivost H3, datum, vir podatkov, privzete uteži ...
CREATE TABLE konfiguracija (
    kljuc    TEXT PRIMARY KEY,          -- npr. 'h3_res', 'datum_generiranja', 'vir_uporabnikov'
    vrednost TEXT NOT NULL
);

-- Statične lastnosti celic (ne spreminjajo se z uro). Samo celice, ki so kdaj objavljene.
CREATE TABLE celica (
    cell_id         TEXT PRIMARY KEY,   -- H3 ID, npr. '881e12ab05fffff'
    res             INTEGER NOT NULL,   -- H3 ločljivost
    lat             REAL NOT NULL,      -- središče celice
    lon             REAL NOT NULL,
    km2             REAL NOT NULL,      -- površina (za gostote)
    pm25_zima       REAL,               -- CAMx zimsko povprečje PM2.5 (µg/m3): MODEL, ne meritev
    gral_kurisca    REAL,               -- GRAL: prispevek malih kurišč k PM10, kurilna sezona (µg/m3): MODEL, 2021
    dist_postaja_m  REAL,               -- razdalja do najbližje uradne postaje ARSO/EEA
    obcutljive      INTEGER NOT NULL DEFAULT 0,   -- št. vrtcev, šol, bolnišnic, domov (OSM)
    cs_ime          TEXT                -- četrtna skupnost (neobvezno, za poročila)
);

-- Zasedenost po celici in uri: to je ENO, kar ARSO dobi o ljudeh.
CREATE TABLE zasedenost (
    cell_id  TEXT    NOT NULL REFERENCES celica(cell_id),
    ura_utc  TEXT    NOT NULL,          -- začetek ure, ISO 8601 (kot time_utc v paketu)
    n        INTEGER NOT NULL,          -- št. uporabnikov v celici
    k_prag   INTEGER NOT NULL,          -- prag, uporabljen pri agregaciji
    vir      TEXT    NOT NULL CHECK (vir IN ('sinteticno', 'dejansko')),
    PRIMARY KEY (cell_id, ura_utc),
    CHECK (n >= k_prag)                 -- baza fizično ne sprejme celice pod pragom
);
CREATE INDEX idx_zasedenost_ura ON zasedenost(ura_utc);

-- Kolikšen del je bil zaradi praga izpuščen (za prikaz "zasebnost deluje")
CREATE TABLE zasedenost_povzetek (
    ura_utc      TEXT PRIMARY KEY,
    objavljenih  INTEGER NOT NULL,      -- uporabniki v objavljenih celicah
    izpuscenih   INTEGER NOT NULL,      -- uporabniki v celicah pod pragom (št., brez lokacij)
    k_prag       INTEGER NOT NULL,
    vir          TEXT NOT NULL CHECK (vir IN ('sinteticno', 'dejansko'))
);

-- Obstoječe uradne postaje (sloj na karti, izračun razdalj)
CREATE TABLE postaja (
    postaja_id TEXT PRIMARY KEY,        -- koda ARSO (E403) ali EEA (SI0003A)
    ime        TEXT NOT NULL,
    vir        TEXT NOT NULL CHECK (vir IN ('ARSO', 'EEA')),
    tip        TEXT,                    -- mestno ozadje / promet / ...
    lat        REAL NOT NULL,
    lon        REAL NOT NULL
);

-- Občutljive lokacije (OSM): sloj na karti in vir za člen "obcutljive"
CREATE TABLE obcutljiva_lokacija (
    osm_id     TEXT PRIMARY KEY,
    kategorija TEXT NOT NULL CHECK (kategorija IN
               ('vrtec', 'sola', 'bolnisnica', 'zdravstveni_dom', 'dom_za_starejse')),
    ime        TEXT,
    naslov     TEXT,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL
);

-- Pogled, iz katerega bere plošča: eno vrstico na celico in uro, že z vsemi členi
CREATE VIEW v_celica_ura AS
SELECT z.ura_utc, z.cell_id AS cell, z.n, z.k_prag, z.vir,
       c.res, c.lat, c.lon, c.km2,
       c.pm25_zima AS pm, c.gral_kurisca, c.dist_postaja_m, c.obcutljive, c.cs_ime
FROM zasedenost z
JOIN celica c USING (cell_id);

-- ---------------------------------------------------------------------------
-- NEOBVEZNO (2. faza, plošča tega ne potrebuje): meritve uradnih postaj za preverjanje
-- modela in učinka ukrepov (smer 6 in 8 v listu za ekipe).
-- CREATE TABLE meritev_postaje (
--     postaja_id TEXT NOT NULL REFERENCES postaja(postaja_id),
--     time_utc   TEXT NOT NULL,
--     pm25       REAL,
--     pm10       REAL,
--     PRIMARY KEY (postaja_id, time_utc)
-- );