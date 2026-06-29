# Limfjordssekretariatet QGIS Plugins

Fælles plugin-repository med værktøjer udviklet af Limfjordssekretariatet til brug i QGIS.

## Tilgængelige plugins

| Plugin (vises i QGIS) | Version | Beskrivelse |
|-----------------------|---------|-------------|
| **LIMF - Jordbund** | 3.2.7 | Håndtering af jordprøver – grid (markkort-baseret), centerpunkter, QField-klargøring og rapporteksport |
| **LIMF - Vådområdeværktøjer** | 4.0.11 | Diverse GIS-værktøjer (jordberegning, terræninterpolation m.m.) |
| **LIMF - Dræn** | 0.2.9 | Find drænudløbspunkter baseret på fald og DEM |
| **LIMF - Lodsejer Atlas Mapbook** | 0.2.1 | Genererer et lodsejer-atlas (mapbook) ud fra en layout-skabelon med auto-mapping af felter |
| **LIMF - Lodsejerudtræk** | 0.1 | Henter matrikler og ejeroplysninger for et valgt polygon via Datafordeleren/CVR |

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
2. Søg efter det ønskede plugin (f.eks. *LIMF - Jordbund*)
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
| LIMF - Jordbund | Markkort (grid) | ~110 MB | `markkort-v2` |
| LIMF - Lodsejer Atlas Mapbook | Referencekort (arealtabel 2021-2023) | ~110 MB | `referencekort-v1` |

**LIMF - Lodsejerudtræk** henter i stedet matrikler og ejeroplysninger *live*
fra Datafordeleren ved hver forespørgsel. Det kræver en personlig
Datafordeler-API-nøgle, som hver bruger selv skal konfigurere (distribueres ikke).

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

## Spørgsmål og fejlrapportering

Opret en sag under [Issues](https://github.com/Limfjordssekretariatet/Limfjordssekretariatet_plugins/issues).
