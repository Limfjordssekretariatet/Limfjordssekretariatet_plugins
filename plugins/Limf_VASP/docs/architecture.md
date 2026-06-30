# VASP QGIS-plugin — Arkitektur og Plan

> Status: **Planlægning**. Dette dokument beskriver den planlagte arkitektur.
> Der er endnu ikke skrevet plugin-kode (projektet indeholder kun `CLAUDE.md`
> og `VASPdatabase_Dummykopi.mdb`).

## 1. Formål og scope

Pluginnet skal:

1. **Læse** data fra VASP Access-databasen (`.mdb`).
2. **Mappe** vandløbsgeometri og attributter til QGIS-lag.
3. **Opdatere** attributter (og på sigt geometri) tilbage til databasen.

Reglerne fra `CLAUDE.md` er bindende:

- Brug PyQGIS API.
- Ingen koordinatbaserede museklik.
- Access via `pyodbc`.
- Al databasekode placeres i `dbaccess.py`.
- Al geometri-behandling placeres i `processing`.

---

## 2. Analyse af den faktiske database

Undersøgt via 32-bit ODBC mod `VASPdatabase_Dummykopi.mdb`.

### 2.1 Relevante tabeller

| Tabel          | Rækker | Rolle |
|----------------|-------:|-------|
| `VANDLØB`      | 552    | Vandløbsstamdata (én række pr. vandløb) |
| `VANDLØBGIS`   | 1559   | Geometri + GIS-metadata (flere versioner pr. vandløb) |
| `PROJEKTER`    | 706    | Projekter knyttet til vandløb |
| `KOORDSYSKODER`| 3      | Opslagstabel for koordinatsystem |
| `REGIONSKODER1/2` | —   | Opslag for regioner |
| `PROJEKTTYPEKODER`, `PROJEKTSTATUSKODER` | — | Opslag for projekter |

Nøglerelation: `VANDLØB.VLBSYSID` ⇐ `VANDLØBGIS.VLBSYSID` (1-til-mange)
og `VANDLØB.VLBSYSID` ⇐ `PROJEKTER.VLBSYSID`.

### 2.2 `VANDLØB` (stamdata)

```
VLBSYSID (COUNTER, PK)   LEVEL3ID (int)        NAVN (varchar)
VLBNR1, VLBNR2 (varchar) REGIONSID1/2 (int)    BEMÆRKNING (longchar)
GLOBID (GUID)            CREDATE/UPDDATE (date) CREINIT/UPDINIT (varchar)
FASTDNNADDENT (double)   DNNMETODE (int)
```

### 2.3 `VANDLØBGIS` (geometri)

```
GISDATAID (COUNTER, PK)  VLBSYSID (int, FK)    NAVN (varchar)
DATAGRUNDLAG (varchar)   GISDATA (LONGBINARY)  STATDATA (LONGBINARY)
DDHKOORDSYSID (int, FK)  GYLDIGFRADATO/TILDATO (date)
DIGILÆNGDE (double)      DNNADDENTMAX/MIN/MID (double)
GLOBID, CRE*/UPD*
```

> **Vigtigt:** Geometri ligger **ikke** som WKT eller X/Y-kolonner, men som en
> proprietær binær BLOB i `GISDATA`. Formatet er dekodet (se §2.5).

### 2.4 Koordinatsystem (`KOORDSYSKODER`)

| `DDHKOORDSYSID` | Navn | Antaget EPSG |
|---:|---|---|
| 0 | Ikke oplyst | (ukendt — kræver fallback) |
| 1 | UTM Zone 32 (ED50) | 23032 |
| 2 | UTM Zone 32 (EUREF89) | 25832 |

`DDHKOORDSYSID` læses pr. række i `VANDLØBGIS` og oversættes til en EPSG-kode.

### 2.5 BLOB-format for `GISDATA` (dekodet)

Verificeret mod faktiske data (eksempel: 381 punkter, 12196 bytes):

```
offset 0 : int32 (little-endian)  = antal punkter N
offset 4 : N gentagelser af 32 bytes, hver = 4 × float64 (little-endian):
             [0] X  (Easting)
             [1] Y  (Northing)
             [2] stationering / kote langs linjen (kumulativ afstand)
             [3] M  (måleværdi)
```

Sanity check: `bytes == 4 + N*32`. For eksemplet: `4 + 381*32 = 12196` ✓.
Første punkter dekodet til gyldige UTM32-koordinater i Danmark
(X≈547.608, Y≈6.318.818), og felt [2] stiger monotont (0 → 3,545 → 20,90),
hvilket bekræfter stationering.

`STATDATA` var NULL i den undersøgte række — formodes at være en separat
station-/profil-BLOB. **Skal undersøges nærmere** før det bruges; behandles
indtil videre som ukendt/valgfrit.

→ Hver `VANDLØBGIS`-række mapper til **én LineString-feature** (X/Y), evt. med
Z fra felt [2] hvis ønsket.

---

## 3. Kritisk infrastruktur-constraint (bitness)

**Den vigtigste tekniske risiko.** Konstateret på denne maskine:

- QGIS 3.40.15 kører **64-bit Python 3.12**.
- De installerede Access-drivere (`Microsoft Access Driver (*.mdb, *.accdb)`)
  er **kun 32-bit**.
- Der findes **ingen 64-bit ACE/Jet OLEDB-provider** installeret.

Konsekvens: `pyodbc` i QGIS-processen (64-bit) kan **ikke** åbne `.mdb`-filen
direkte med de drivere, der findes nu. Schema-aflæsningen i denne analyse
lykkedes kun via en separat **32-bit** proces.

### Løsningsmuligheder (skal besluttes med brugeren)

| # | Løsning | Fordele | Ulemper |
|---|---------|---------|---------|
| A | Installér **64-bit Microsoft Access Database Engine 2016 (ACE)** | `pyodbc` virker direkte i QGIS som krævet | Kræver admin/installation; kan konflikte med 32-bit Office |
| B | **32-bit sidecar-proces**: lille hjælpeprogram (32-bit Python) tilgår DB, QGIS taler med den via IPC/fil | Ingen ændring af QGIS | Bryder "Access via pyodbc"-reglen i QGIS-processen; mere kompleks |
| C | Migrér data til **GeoPackage/SQLite** ved import, skriv tilbage til `.mdb` via sidecar | Hurtig GIS-performance | To datakilder at holde synkroniserede |

**Anbefaling: A** (64-bit ACE). Den overholder `CLAUDE.md`-reglen om `pyodbc`
direkte og er enklest arkitektonisk. Plan nedenfor antager A, men `dbaccess.py`
isoleres så B/C kan bygges ovenpå uden at røre processing-/UI-lag.

---

## 4. Foreslået modulstruktur

```
VASP_v2/
├─ __init__.py            # classFactory → instantierer pluginnet
├─ metadata.txt           # QGIS plugin-metadata
├─ vasp_plugin.py         # UI-indgang: menu/toolbar, orkestrering (ingen DB/geometri)
├─ dbaccess.py            # ALT databasekode (pyodbc) — læsning + skrivning
├─ processing/
│  ├─ __init__.py
│  ├─ geometry_codec.py   # decode/encode af GISDATA-BLOB ⇄ punktlister
│  └─ layer_builder.py    # punktlister ⇄ QgsVectorLayer/QgsFeature
├─ model.py               # dataclasses: Vandløb, VandløbGis, Projekt
├─ config.py              # EPSG-mapping, tabel-/feltnavne, konstanter
└─ docs/architecture.md
```

Ansvarsadskillelse (jf. reglerne):

- `dbaccess.py` kender SQL og `pyodbc`, men **intet** til QgsGeometry.
  Returnerer rå BLOBs + attributter som Python-objekter.
- `processing/` kender geometri (BLOB ↔ koordinater ↔ QgsGeometry),
  men **intet** til SQL eller `pyodbc`.
- `vasp_plugin.py` binder dem sammen og styrer QGIS-UI.

---

## 5. Plan — Trin 1: Læsning af VASP Access-database

**Mål:** Hent vandløb + tilhørende GIS-rækker robust og uafhængigt af UI.

1. **Forbindelse** (`dbaccess.py`)
   - Byg connection string:
     `Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=<sti>;`
   - Åbn via `pyodbc.connect(...)`. Håndtér eksplicit fejlen
     "Data source name not found / no default driver" → vis brugervenlig
     besked om bitness/ACE-installation (jf. §3).
   - DB-sti hentes relativt til pluginnet eller fra plugin-indstilling.

2. **Læsefunktioner** (`dbaccess.py`)
   - `read_vandloeb()` → liste af `Vandløb`.
   - `read_vandloebgis(vlbsysid=None)` → liste af `VandløbGis`
     (inkl. rå `GISDATA`-bytes og `DDHKOORDSYSID`).
   - `read_projekter(vlbsysid=None)` → liste af `Projekt`.
   - `read_lookup(table)` → dict for opslagstabeller (regioner, koordsys, status).
   - Brug parametriserede queries (`?`) — ingen streng-konkatenering.
   - Vær opmærksom på Æ/Ø/Å i tabelnavne → quoting med `[ ]`.

3. **Encoding/typer**
   - Map Access-typer: `COUNTER/INTEGER→int`, `DOUBLE→float`,
     `DATETIME→datetime`, `GUID→str`, `LONGBINARY→bytes`.
   - Læs BLOB som `bytes` (ingen geometri-logik her).

4. **Validering (off-QGIS smoke test)**
   - Tæl rækker (forventet: VANDLØB=552, VANDLØBGIS=1559, PROJEKTER=706).
   - Bekræft at mindst én `GISDATA`-BLOB dekoder korrekt
     (`len == 4 + N*32`).

**Deliverables:** `dbaccess.py` (læsedel), `model.py`, `config.py`.

---

## 6. Plan — Trin 2: Mapping til QGIS-lag

**Mål:** Lav et linjelag i hukommelsen ud fra DB-data, klar til visning/redigering.

1. **BLOB-dekoder** (`processing/geometry_codec.py`)
   - `decode_gisdata(blob: bytes) -> list[tuple[x,y,z,m]]`
     - Læs `N = int32` fra offset 0.
     - Iterér N × 4 × float64 (struct `'<dddd'`).
     - Valider `len(blob) == 4 + N*32`; ellers rejs tydelig fejl.
   - `encode_gisdata(points) -> bytes` (til Trin 3).

2. **CRS-opslag** (`config.py`)
   - `DDHKOORDSYSID → EPSG`: `1→23032`, `2→25832`, `0→fallback (afklares)`.

3. **Lagopbygning** (`processing/layer_builder.py`)
   - Opret `QgsVectorLayer("LineString?crs=EPSG:25832", "VASP Vandløb", "memory")`.
     - Beslut: ét lag pr. CRS, eller reprojicér alt til 25832. **Anbefaling:**
       saml i 25832 og reprojicér ED50-rækker via `QgsCoordinateTransform`.
   - Definér felter fra `VANDLØB`/`VANDLØBGIS` (NAVN, VLBNR1, DIGILÆNGDE, …).
   - Pr. `VandløbGis`-række:
     - `QgsGeometry.fromPolylineXY([QgsPointXY(x,y), …])` (Z valgfrit fra felt [2]).
     - Sæt attributter; gem **`GISDATAID` og `VLBSYSID` som skjulte nøglefelter**
       (kritisk for tilbageskrivning i Trin 3).
   - Tilføj laget til projektet (`QgsProject.instance().addMapLayer`).

4. **Identitet/sporbarhed**
   - Hver feature bærer `GISDATAID` (primærnøgle) → 1:1 tilbage til DB-rækken.
   - Ingen koordinatbaserede museklik: udvælgelse sker via attribut/feature-id.

**Deliverables:** `processing/geometry_codec.py`, `processing/layer_builder.py`.

---

## 7. Plan — Trin 3: Opdatering af attributter tilbage til databasen

**Mål:** Persistér ændringer fra QGIS-laget til Access — sikkert og sporbart.

1. **Scope-beslutning (afklar med bruger)**
   - **Fase 3a — kun attributter** (NAVN, BEMÆRKNING, m.fl.): lav risiko, start her.
   - **Fase 3b — geometri** (re-encode `GISDATA` + opdater `DIGILÆNGDE`,
     `DNNADDENT*`, stationering): højere risiko, kræver `encode_gisdata`.

2. **Indsamling af ændringer**
   - Læs ændrede features fra memory-laget; identificér via `GISDATAID`/`VLBSYSID`.
   - Map QGIS-felter → DB-kolonner (samme mapping som Trin 2, omvendt).

3. **Skrivning** (`dbaccess.py`, skrivedel)
   - Parametriserede `UPDATE [VANDLØB] SET … WHERE VLBSYSID = ?`
     og `UPDATE [VANDLØBGIS] SET … WHERE GISDATAID = ?`.
   - Sæt revisionsfelter: `UPDDATE = now()`, `UPDINIT = <bruger>`.
   - Geometri (3b): `encode_gisdata(points)` → skriv til `GISDATA` som BLOB-param;
     genberegn afledte længdefelter i `processing` (ikke i SQL).

4. **Transaktion & sikkerhed**
   - `autocommit=False`; saml relaterede UPDATEs i én transaktion; commit/rollback.
   - **Tag backup af `.mdb` før første skrivning** (filen er ~190 MB; kopiér).
   - Optimistisk samtidighed: sammenlign `UPDDATE` før skriv (advar ved konflikt).
   - Valider geometri (lukkede/2-punkts linjer) før encode.

5. **Test**
   - Skriv mod **dummy-kopien**, ikke produktion.
   - Round-trip-test: læs → decode → encode → skriv → læs igen, og bekræft
     byte-identisk `GISDATA` for uændrede features.

**Deliverables:** skrivedel i `dbaccess.py`, `encode_gisdata`, UI-knap "Gem til VASP".

---

## 8. Åbne spørgsmål (kræver afklaring)

1. **Bitness/driver (§3):** Installeres 64-bit ACE (A), eller bygges sidecar (B)?
   Blokerer al kørsel i QGIS.
2. **`STATDATA`-BLOB:** Format og rolle (stationsprofiler?) — skal reverse-engineeres
   før den bruges.
3. **`DDHKOORDSYSID = 0` ("Ikke oplyst"):** Hvilket CRS antages som fallback?
4. **Felt [2]/[3] i BLOB:** Bekræft semantik (stationering vs. kote vs. måleværdi)
   mod VASP-dokumentation.
5. **Geometri-skrivning (3b):** I scope nu, eller kun attributter (3a) først?
6. **Flere GIS-versioner pr. vandløb** (1559 GIS vs. 552 vandløb): vises alle,
   eller kun gyldig version (`GYLDIGFRADATO`/`TILDATO`)?

---

## 9. Anbefalet rækkefølge

1. Afklar §8.1 (driver) og §8.5 (scope).
2. Byg `config.py` + `model.py` + `dbaccess.py` læsedel → smoke-test (§5.4).
3. Byg `geometry_codec.decode` + `layer_builder` → vis vandløb i QGIS (Trin 2).
4. Byg attribut-skrivning (Fase 3a) med backup + transaktion.
5. (Valgfrit) `geometry_codec.encode` + geometri-skrivning (Fase 3b) med round-trip-test.
