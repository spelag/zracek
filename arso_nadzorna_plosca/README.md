# ARSO nadzorna plošča: datoteke

Tehnični opis skript, ki sestavljajo ploščo za izbiro lokacije nove merilne postaje. Vse skripte so Python (razen `shema.sql`) in berejo podatkovni paket organizatorja, razpakiran v mapo `data/`.

## 1. Pregled

| Datoteka | Naloga |
|---|---|
| `sim.py` | Sintetični uporabniki in njihov dnevni premik |
| `agregacija.py` | Točke v celice H3 s pragom k |
| `ocena.py` | Obogatitev celic z lastnostmi in ocena upravičenosti |
| `shema.sql` | Shema baze SQLite |
| `baza.py` | Zgradi `zrak.db` |
| `app.py` | Streamlit plošča (bere samo `zrak.db`) |
| `karta.py` | Stari hitri pregled celic v HTML, za ploščo ni potreben |

## 2. Zagon

Potrebujete Python 3.10+.

```
python -m venv venv
venv\Scripts\activate                 # PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt       # iz paketa organizatorja
pip install -U h3 scipy streamlit pydeck
```

`h3` mora biti različica 4 (`h3.latlng_to_cell`).

```
python baza.py            # zgradi zrak.db
streamlit run app.py      # odpre ploščo
```

- Pred ponovnim zagonom `baza.py` ustavite Streamlit (Ctrl+C), sicer Windows ne dovoli prepisa `zrak.db`.
- Plošča bere samo bazo, zato je po vsaki spremembi `sim.py`, `agregacija.py` ali `ocena.py` treba znova zagnati `baza.py`.
- Posamezne faze lahko poženete ločeno: `python sim.py`, `python agregacija.py`, `python ocena.py`.

## 3. Pretok podatkov

```
sim.py ──► agregacija.py ──► baza.py ──► zrak.db ──► app.py
 točke        H3 + prag k        ▲
                           ocena.py (GRAL, CAMx, postaje, OSM)
```

Surove točke obstajajo samo v pomnilniku znotraj `baza.py`. V bazo gredo izključno števila po celicah in urah.

## 4. Datoteke podrobno

### sim.py
Ob uvozu ustvari sintetične uporabnike.

- **Bere:** `kn_stavbe_atributi.csv.gz`, `osm_obcutljive_lokacije.geojson`, `mol_cetrtne_skupnosti.geojson` (samo pri zagonu kot skripta).
- **Pot do podatkov** je konstanta `P = "data/04_prostor/"` na vrhu datoteke. Popravite jo, če je paket drugje.
- **Nastavitve (vrh datoteke):** `N` (30.000), `SEED` (42), `DELEZI_PROFILOV` (70/15/15), `SUM_M` (šum 15 m), `ŠOLA_OD_UTC`/`ŠOLA_DO_UTC` (7 do 14), `DELEZ_DELOVNIH` (0,6).
- **Uvoz vrne:** `users` (lat, lon, profil, šola, delovno mesto) in `sola`.
- **`posnetek(users, ura_utc)`** vrne anonimne točke `time_utc, lat, lon, profil` za uro. Otroci so med 7 in 14 UTC v eni od treh najbližjih šol ali vrtcev, zaposleni odrasli v stavbi s poslovnimi prostori (uteži = število prostorov, samo MOL).
- **`python sim.py`** zapiše `sim_uporabniki_8h.csv`, `_13h.csv`, `_21h.csv` in izpiše število uporabnikov po četrtnih skupnostih.

### agregacija.py
- **Nastavitve:** `K = 10`, `RES = 8` (~0,7 km²).
- **`agregiraj(df, k, res)`** vrne `(celice, izpuščeni)`. Celice so `cell, n, n_ranljivi, res`; celice pod pragom se izpustijo, ne združujejo. Uporabniki v teh celicah se samo preštejejo.
- **`python agregacija.py`** prebere `sim_uporabniki_*h.csv` in zapiše `agg_*h.csv`.
- `n_ranljivi` se računa, a se ne shranjuje v bazo (plošča ga ne uporablja, pri majhnih celicah bi lahko razkril posameznike).

### ocena.py
- **`najdi(ime)`** poišče datoteko kjerkoli pod `data/` (`DATA = "data"`).
- **`pripravi(agg)`** doda celicam lastnosti:
  - `lat`, `lon`, `km2`: središče in površina,
  - `pm`: zimsko povprečje PM2.5 iz `camx_zimsko_povprecje_slo.tif` (vzorec v središču; pas 1 predpostavljen kot PM2.5),
  - `gral_kurisca`: povprečje po površini celice iz `gral_kurisca_statistika_15m.tif` (pas »kurilna sezona«, pretvorba lat/lon v EPSG:32633, ker je mreža GRAL v UTM 33N in ne v D96),
  - `dist_postaja_m`: razdalja do najbližje postaje ARSO ali EEA,
  - `obcutljive`: število občutljivih lokacij znotraj celice.
- **`ocena(df, w)`** vrne celice, razvrščene po oceni, s prispevki členov `c_users, c_gral, c_pm, c_dist, c_sens` in skupno `ocena`.
- **Privzete uteži** (`UTEZI`): uporabniki 0,25, GRAL 0,25, razdalja 0,25, občutljive lokacije 0,20, CAMx 0,05. Uteži se normalizirajo na vsoto 1; manjkajoči člen šteje kot 0.
- **Normalizacija:** obrez 5. in 95. percentila, nato 0 do 1. Gostote so na km². Razdalja je omejena na 3 km. Celice brez GRAL (zunaj mreže ali zabrisane) dobijo mediano.
- **`python ocena.py`** prebere `agg_21h.csv` in zapiše `obogaten_21h.csv` ter `ocena_21h.csv`.

### shema.sql
Shema baze SQLite. Ključno: `zasedenost` ima pogoj `CHECK (n >= k_prag)`, torej baza celic pod pragom ne sprejme. Tabele:

| Tabela | Vsebina |
|---|---|
| `celica` | Statične lastnosti celic (PM CAMx, GRAL, razdalja, občutljive lokacije, četrtna skupnost) |
| `zasedenost` | Število uporabnikov po celici in uri |
| `zasedenost_povzetek` | Število izpuščenih uporabnikov po uri |
| `postaja`, `obcutljiva_lokacija` | Sloji za karto |
| `konfiguracija` | Ločljivost H3, prag k, datumi, viri |
| `v_celica_ura` | Pogled za ploščo: celica + zasedenost + lastnosti |

Vsak zapis zasedenosti ima stolpec `vir` (`sinteticno` ali `dejansko`).

### baza.py
Zbriše in znova zgradi `zrak.db` v štirih korakih: (1) agregati za vseh 24 UTC ur, (2) lastnosti celic prek `ocena.pripravi()` ter četrtna skupnost, (3) postaje in občutljive lokacije, (4) zapis v bazo.

- Postaje EEA se vpišejo brez podvojitev s postajami ARSO. Imena stolpcev v `eea_postaje.geojson` skripta poskuša več možnih, ker README paketa ne navaja vseh.

### app.py
Streamlit plošča, bere `zrak.db` v načinu samo za branje (`mode=ro`). Uvaža `ocena.py` (funkcija `ocena`).

- **Stranska vrstica:** ura (lokalni čas), prag k (samo navzgor od praga v bazi), pet uteži (uporabniki, GRAL, CAMx, razdalja, občutljive lokacije), barvanje karte, sloj občutljivih lokacij.
- **Karta** celic po oceni, vijoličen obris top 10 (vsaka vsaj 1,5 km od druge).
- **Tabela top 10** (z vrednostjo GRAL in četrtno skupnostjo) in graf prispevkov členov (»zakaj ta«).
- Če baza nima stolpca `gral_kurisca`, plošča izpiše »Baza je stara« in zahteva ponoven zagon `baza.py`.

### karta.py
Samostojen hiter pregled: prebere `ocena_21h.csv` in naredi `karta.html` (pydeck). Namenjen razhroščevanju, plošča ga ne uporablja.

## 5. Vmesne datoteke

Nastanejo ob samostojnem zagonu posameznih skript in za ploščo niso potrebne: `sim_uporabniki_*h.csv`, `agg_*h.csv`, `obogaten_21h.csv`, `ocena_21h.csv`, `karta.html`. Datoteke `sim_uporabniki_*h.csv` vsebujejo sintetične surove točke, zato jih ne obravnavajte kot podatke ARSO.

## 6. Podatki iz paketa, ki jih datoteke berejo

| Datoteka paketa | Bere |
|---|---|
| `kn_stavbe_atributi.csv.gz` | `sim.py` |
| `osm_obcutljive_lokacije.geojson` | `sim.py`, `ocena.py`, `baza.py` |
| `mol_cetrtne_skupnosti.geojson` | `sim.py` (preverjanje), `baza.py` |
| `arso_lj_postaje.geojson`, `eea_postaje.geojson` | `ocena.py`, `baza.py` |
| `camx_zimsko_povprecje_slo.tif` | `ocena.py` |
| `gral_kurisca_statistika_15m.tif` | `ocena.py` |

## 7. Pomembne predpostavke in omejitve

- Uporabniki so **sintetični**; deleži profilov, delež zaposlenih in časovno okno so predpostavke.
- CAMx ima celice po 4,4 km in po README precenjuje, zato ima privzeto majhno težo.
- GRAL je samo prispevek malih kurišč za leto 2021 (brez prometa in ozadja), absolutnih vrednosti ne primerjajte z meritvami.
- Dnevni premik zajema samo otroke in zaposlene odrasle znotraj MOL.
- Čas je v UTC, lokalni čas pozimi je UTC+1.

## 8. Kaj preveriti pri prvem zagonu

1. **Pas CAMx rastra:** `ocena.py` predpostavlja, da je pas 1 PM2.5. Vrednosti `pm` morajo biti približno 15 do 30 µg/m³. Če so višje, je to verjetno PM10 (`band=2` v `dodaj_pm`).
2. **Pot do podatkov v `sim.py`:** `P = "data/04_prostor/"` je trdo zapisana.
3. **Izpis `baza.py`:** na koncu mora našteti vrstice po tabelah (`celica`, `zasedenost`, `zasedenost_povzetek`, `postaja`, `obcutljiva_lokacija`).
4. **Postaje EEA:** če nimajo imen, so se uporabila nadomestna imena (ni napaka).

## 9. Povezava s strežnikom API (`arsodata/server.py`)

Strežnik `arsodata/server.py` odgovarja na `GET /air-quality?lat=&lon=` z 12 urnimi razredi kakovosti zraka. Vsaka veljavna zahteva se hkrati zabeleži v to bazo (`zrak.db`), zato plošča poleg sintetičnega dneva prikazuje tudi dejansko uporabo.

- Točka iz zahteve se preslika v celico H3 z ločljivostjo `h3_res` in pragom `k_prag` iz tabele `konfiguracija`. Surove koordinate se ne shranijo nikoli.
- Zahteve se štejejo po celici in trenutni UTC uri. Ko celica doseže prag `k`, se vpiše v `zasedenost` z `vir = 'dejansko'`; če za isto celico in uro že obstaja sintetična vrstica, jo nadomesti. Druge celice iste ure ostanejo sintetične.
- Celice pod pragom se hranijo samo v ločeni, neobjavljeni bazi `zrak_zahteve.db` (samo števci, brez uporabnikov). V `zrak.db` se ne zapiše nobena celica pod pragom, zato `CHECK (n >= k_prag)` ostane nespremenjen.
- `zasedenost_povzetek` se ob vsaki zahtevi znova izračuna iz objavljenih vrstic in zadržanih števcev.
- Vsaka HTTP zahteva šteje kot en uporabnik v tisti uri; ponovno pošiljanje iste naprave zvišuje števec, ker API nima identitete naprave.
- Če baza še ne obstaja, jo strežnik ustvari po `shema.sql`; `baza.py` jo ob ponovni gradnji zamenja in zapiše nov `zagon_id`, zato strežnik stare zasebne števce zavrže.

Nastavitve okolja:

| Spremenljivka | Privzeto | Pomen |
|---|---|---|
| `ZRAK_DB` | `arso_nadzorna_plosca/zrak.db` | baza, ki jo bere plošča |
| `ZRAK_STAGING_DB` | `zrak_zahteve.db` ob `zrak.db` | zasebni števci pod pragom |

```
pip install flask pandas numpy requests h3
python arsodata/server.py              # http://127.0.0.1:9161
streamlit run arso_nadzorna_plosca/app.py
```

Plošča, `baza.py` in strežnik morajo gledati isto bazo. Pred ponovnim zagonom `baza.py` ustavite strežnik; na Windows datoteke ne bo mogoče prepisati.