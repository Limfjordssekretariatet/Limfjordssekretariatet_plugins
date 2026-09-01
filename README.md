# Limfjordssekretariatet QGIS Plugins

Fælles plugin-repository med værktøjer til QGIS, udviklet af Henrik Rosenskjold
for Limfjordsrådets sekretariat.

Værktøjerne er **frie** — GNU GPL v2 eller nyere. Enhver må bruge, ændre og
videregive dem. Se [Licens og kreditering](#licens-og-kreditering).

## Tilgængelige plugins

| Plugin (vises i QGIS) | Version | Beskrivelse |
|-----------------------|---------|-------------|
| **Vandprojekter – Jordbund** | 3.2.15 | Håndtering af jordprøver – grid (markkort-baseret), centerpunkter, QField-klargøring og rapporteksport |
| **Vandprojekter – Vådområder** | 4.0.21 | Diverse GIS-værktøjer (jordberegning, terræninterpolation m.m.) |
| **Vandprojekter – Dræn** | 0.2.18 | Find drænudløbspunkter baseret på fald og DEM |
| **Vandprojekter – Lodsejeratlas** | 0.2.11 | Genererer et lodsejer-atlas (mapbook) ud fra en layout-skabelon med auto-mapping af felter |
| **Vandprojekter – Lodsejerudtræk** | 0.1.14 | Henter matrikler og ejeroplysninger for et valgt polygon via Datafordeleren/CVR |
| **Vandprojekter – VASP** | 1.2.19 | Integration mellem VASP Access-database og QGIS – henter terræn-/profildata ind som lag |
| **Vandprojekter – N-regneark** | 2.16 | Udfylder kvælstof-regnearket (N) for et vådområdeprojekt: oplande, arealer og tilførsel |

---

## Installation i QGIS

### Trin 1 – Tilføj repository

1. Åbn QGIS
2. Gå til **Plugins → Manage and Install Plugins**
3. Vælg fanen **Settings**
4. Klik **Add** under *Plugin Repositories*
5. Udfyld felterne:
   - **Name:** `Limfjordssekretariatet`
   - **URL:** `https://limfjordssekretariatet.github.io/Limfjordssekretariatet_plugins/plugins.xml`
6. Klik **OK**

### Trin 2 – Installér plugin

1. Gå til fanen **All**
2. Søg efter det ønskede plugin (f.eks. *Vandprojekter – Jordbund*)
3. Klik **Install Plugin**

> Plugins vises fremover under fanen **Installed**, og QGIS giver besked når der er en ny version tilgængelig.

---

## Data der hentes ved første brug

Nogle plugins bruger store datasæt, der er for store til selve plugin-pakken.
De hentes automatisk fra en GitHub release **første gang værktøjet bruges** og
gemmes lokalt i QGIS-profilen (så de kun hentes én gang og overlever
plugin-opdateringer). Kræver internetadgang ved første kørsel.

| Plugin | Datasæt | Størrelse | Release |
|--------|---------|-----------|---------|
| Vandprojekter – Jordbund | Markkort (grid) | ~110 MB | `markkort-v2` |
| Vandprojekter – Lodsejeratlas | Referencekort (arealtabel 2021-2023) | ~110 MB | `referencekort-v1` |
| Vandprojekter – N-regneark | Jordbundskort 2024 | 375 MB | `grunddata-v1` |
| Vandprojekter – N-regneark | Marker 2024 | 353 MB | `grunddata-v1` |
| Vandprojekter – N-regneark | Befæstet areal | 147 MB | `grunddata-v1` |
| Vandprojekter – N-regneark | Vandløb (polygoner) | 47 MB | `grunddata-v1` |
| Vandprojekter – N-regneark | DHMLinje (tilpasningslinjer) | 14 MB | `grunddata-v1` |

**Vandprojekter – Lodsejerudtræk** henter i stedet matrikler og ejeroplysninger *live*
fra Datafordeleren ved hver forespørgsel. Det kræver en personlig
Datafordeler-API-nøgle, som hver bruger selv skal konfigurere (distribueres ikke).

**Vandprojekter – N-regneark** henter kun det datasæt et trin faktisk skal
bruge — regner man kun oplande, hentes jordbundskortet aldrig.

**Vandprojekter – VASP** læser fra en lokal GeoPackage, som pluginnet selv genopbygger ud
fra din VASP Access-database (.mdb) første gang du vælger en database (og via
"Genindlæs database"). Databasen distribueres ikke – hver bruger peger på sin egen.

---

## Opdatering

Når en ny version er udgivet, vil QGIS vise en opdateringsknap under
**Plugins → Manage and Install Plugins → Upgradeable**.

---

## For udviklere

- Hvert plugin ligger i `plugins/<navn>/` med en `metadata.txt`.
- Push til `main` udløser en GitHub Actions-workflow (`.github/workflows/deploy.yml`),
  der bygger en zip pr. plugin + `plugins.xml` og publicerer dem til GitHub Pages.
- Plugin-id i `plugins.xml` dannes fra **mappenavnet** (`Limf_*`), så
  `name=` i `metadata.txt` kun styrer det viste navn i QGIS.
- Store datasæt distribueres som **release-assets** (op til 2 GB), ikke i
  plugin-zippen (GitHub Pages-grænse er 100 MB pr. fil). Pluginnet downloader
  dem ved første brug.

---

## Licens og kreditering

### Koden er fri

Pluginnene er frit tilgængelige under **GNU General Public License v2 eller
nyere** — hele teksten står i [LICENSE](LICENSE). Kort fortalt: enhver må
bruge, kopiere, ændre og videregive dem, også i en anden organisation og til
egne projekter. Videregiver du en ændret udgave, skal den følge samme licens,
og ophavsretserklæringerne skal med. Der er ingen garanti — værktøjerne
leveres som de er.

Licensen er ikke et frit valg. Et QGIS-plugin bygger på QGIS' egne
GPL-licenserede API'er og skal derfor selv være GPL-kompatibelt.
Vådområder-pluginnet har erklæret GPL v2+ siden det blev oprettet; nu står
det udtrykkeligt for hele repositoriet.

### Hvem der har lavet dem

Værktøjerne er udviklet af **Henrik Rosenskjold**, hovedudvikler, for
**Limfjordsrådets sekretariat** — bygget til at løse og forenkle sekretariatets
egne arbejdsgange.

### Kreditering

**Bygger du videre på værktøjerne, så kreditér.** Det gælder både en ændret
udgave af et plugin, kode lånt herfra ind i et andet værktøj, og resultater
brugt i en rapport eller et projekt:

> *Vandprojekter — QGIS-plugins. Henrik Rosenskjold, Limfjordsrådets
> sekretariat.*
> https://github.com/Limfjordssekretariatet/Limfjordssekretariatet_plugins

Licensen kræver under alle omstændigheder, at ophavsretserklæringerne i
kildekoden bliver stående, når du videregiver koden — også i ændret form.
Krediteringen ovenfor er den læsbare udgave af det samme.

### Ansvar

Værktøjerne er bygget til Limfjordsrådets sekretariats egne arbejdsgange og
stilles frit til rådighed, som de er. **Anvendelse og fortolkning af
resultaterne er på eget ansvar** — der følger ingen garanti med, hverken for
at et resultat er rigtigt, eller for at et værktøj passer til dit formål.

Der kommer løbende opdateringer. QGIS giver besked under **Plugins → Manage
and Install Plugins → Upgradeable**, men det er dit eget ansvar at hente dem.
Rettelser, forslag og ønsker tages løbende — [opret en sag](https://github.com/Limfjordssekretariatet/Limfjordssekretariatet_plugins/issues).

### Data er ikke vores

Pluginnene henter og videredistribuerer data, vi ikke har ophavsret til.
**Vilkårene for de data er leverandørens — ikke pluginnets licens.**

| Data | Kommer fra | Hvordan |
|------|------------|---------|
| Højdemodel (DHM) | Klimadatastyrelsen, Dataforsyningen | hentes live via WCS med din egen token |
| Matrikler, ejeroplysninger, CVR | Datafordeleren | hentes live med din egen API-nøgle |
| Markkort og Marker 2024 (markblokke, afgrøder, grundbetaling) | Landbrugsstyrelsen | følger med som release-assets, bearbejdet af os |
| Referencekort 2021-2023 (satskort) | Landbrugsstyrelsen | følger med som release-asset |
| Jordbundskort, befæstet areal, vandløb, DHM-linjer | offentlige danske grunddata | følger med som release-assets, bearbejdet af os |
| N-beregningsarket `mst_n_beregning_jul2023.xlsx` | Miljøstyrelsen | følger med N-regneark-pluginnet |

De datasæt, vi videredistribuerer, er **bearbejdede** udgaver — klippet til
Limfjordsoplandet, forenklet, og for markkortets vedkommende renset for
sliver-polygoner. De er lavet til at virke i disse værktøjer. Skal data
bruges til andet, så hent dem hos kilden og følg kildens vilkår, også for
kreditering.

Baggrundskort (ortofoto m.m.) leveres ikke af pluginnene — de kommer fra det,
du selv har i dit QGIS-projekt.

### Kode fra andre

`plugins/Limf_WetlandTools/test/qgis_interface.py` stammer fra QGIS Plugin
Builder-skabelonen — © Ivan Mincik, German Carrillo og Tim Sutton, under GPL.

---

## Spørgsmål og fejlrapportering

Opret en sag under [Issues](https://github.com/Limfjordssekretariatet/Limfjordssekretariatet_plugins/issues).
