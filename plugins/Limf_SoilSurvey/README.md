# Limf_SoilSurvey 2.0.0

QGIS-plugin til håndtering af jordprøver.

## Funktioner

| Værktøj | Beskrivelse |
|---|---|
| Lav grid | Opretter et jordprøvetagningsgrid baseret på Markkort 2024-parceller med automatisk opdeling og sammenlægning |
| Centerpunkter | Genererer centerpunkter for hvert gridfelt |
| QField-klargøring | Forbereder lag til brug i QField |
| Rapporteksport | Eksporterer rapporter og PDF-output |

## Krav

- QGIS 3.0 eller nyere
- Markkort 2024 shapefil placeret i `Data/Markkort/Markkort2024_simpl.shp`

## Installation

Kopiér plugin-mappen til QGIS' plugin-mappe og aktiver pluginnet i QGIS' plugin-manager.

## Forfatter

Henrik Rosenskjold — henrik.rosenskjold@aalborg.dk

## Changelog

### 2.0.0
- Markkort 2024-baseret grid med automatisk opdeling og sammenlægning
- MBR-orienterede strimler (rotating calipers) for bedre celleform
- Konveksitetsvagt forhindrer sliver-artefakter i ikke-konvekse parceller
- Understøttelse af min/gns/max ha-grænser
