# -*- coding: utf-8 -*-
"""Referencekort-data: downloades fra en GitHub release og caches lokalt.

Referencekortene (arealtabel: Omdrift/Permanent græs) er for store til at
ligge i selve plugin-zippen (GitHub Pages-grænse 100 MB). De ligger i stedet
som release-assets og hentes første gang atlasset bygges. Caches i QGIS-
profilen, så de overlever plugin-opdateringer og kun hentes igen når periodens
``version`` bumpes. Hver periode har sin egen cache, så et skift frem og
tilbage ikke henter forfra.

Der er to referenceperioder at vælge imellem, og kortene er ikke ens:

* **2021-2023** har arealanvendelsen i feltet ``Satskort`` med værdierne
  "Omdrift" og "Permanent græs".
* **2017-2021** har den i feltet ``Arealtype`` – og staver kategorien
  "Permenent græs". Stavefejlen står i kilden hos Landbrugsstyrelsen;
  atlas_builder matcher på "græs" og rammer derfor begge.

Kortene kommer fra https://landbrugsgeodata.fvm.dk (Engangskompensation) og er
forberedt ens inden upload: alt andet end kategorifeltet skåret væk, og
geometrien forenklet med 1 m tolerance (ogr2ogr -simplify 1.0). Det bringer
downloaden fra 341-431 MB ned omkring 120-140 MB og flytter arealerne under
0,1 %.
"""

import os
import shutil
import tempfile
import urllib.request
import zipfile

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QMessageBox, QApplication, QProgressDialog)
from qgis.core import QgsApplication, QgsMessageLog

_RELEASE_BASE = ('https://github.com/Limfjordssekretariatet'
                 '/Limfjordssekretariatet_plugins/releases/download')

#: Referenceperioderne brugeren kan vælge imellem. Nøglen vises i dialogen og
#: skrives i atlassets overskrift. 'version' er release-tagget – bump det hvis
#: en periodes fil udskiftes, så cachen hentes forfra.
PERIODER = {
    '2021-2023': {
        'version': 'referencekort-v1',
        'zip': 'Referencekort_2021-2023.zip',
        'fil': 'Referencekort_2021-2023.shp',
        'felt': 'Satskort',
        'mb': 110,
    },
    '2017-2021': {
        'version': 'referencekort-2017-2021-v1',
        'zip': 'Referencekort_2017-2021.zip',
        'fil': 'Referencekort_2017-2021.shp',
        'felt': 'Arealtype',
        'mb': 138,
    },
}

#: Perioden der er valgt når dialogen åbner.
STANDARD_PERIODE = '2021-2023'


def perioder():
    """Perioderne i den rækkefølge de skal vises – nyeste først."""
    return sorted(PERIODER, reverse=True)


def _periode(periode):
    if periode not in PERIODER:
        raise ValueError('Ukendt referenceperiode: {!r}'.format(periode))
    return PERIODER[periode]


def kategorifelt(periode=STANDARD_PERIODE):
    """Navnet på feltet med arealanvendelsen i den valgte periodes kort."""
    return _periode(periode)['felt']


def reference_cache_dir(periode=STANDARD_PERIODE):
    return os.path.join(QgsApplication.qgisSettingsDirPath(),
                        'Limf_Atlas', _periode(periode)['version'])


def reference_path(periode=STANDARD_PERIODE):
    """Stien til den cachede referencekort-shapefil."""
    return os.path.join(reference_cache_dir(periode), _periode(periode)['fil'])


def reference_url(periode=STANDARD_PERIODE):
    p = _periode(periode)
    return '{}/{}/{}'.format(_RELEASE_BASE, p['version'], p['zip'])


def ensure_reference(parent=None, periode=STANDARD_PERIODE):
    """Sørg for at periodens referencekort findes i cachen.

    Hentes fra GitHub release og pakkes ud første gang (eller når periodens
    version bumpes). Returnerer stien til .shp hvis klar, ellers None (og
    viser fejl/spørger brugeren).
    """
    p = _periode(periode)
    path = reference_path(periode)
    if os.path.exists(path):
        return path

    cache_dir = reference_cache_dir(periode)
    url = reference_url(periode)
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Question)
    msg.setWindowTitle('Hent referencekort')
    msg.setText(
        'Referencekortet for {} (arealtabel) skal hentes første gang '
        '(ca. {} MB).\nDet gemmes lokalt, så hver periode kun hentes én '
        'gang.\n\nHent nu?'.format(periode, p['mb']))
    msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    if msg.exec_() != QMessageBox.Yes:
        return None

    progress = QProgressDialog('Henter referencekort {}…'.format(periode),
                               'Annullér', 0, 100, parent)
    progress.setWindowModality(Qt.WindowModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)
    QApplication.processEvents()

    tmp_zip = None
    try:
        os.makedirs(cache_dir, exist_ok=True)
        fd, tmp_zip = tempfile.mkstemp(suffix='.zip')
        os.close(fd)

        def _hook(block_num, block_size, total_size):
            if progress.wasCanceled():
                raise InterruptedError('Download annulleret')
            if total_size > 0:
                pct = min(100, int(block_num * block_size * 100 / total_size))
                progress.setValue(pct)
                QApplication.processEvents()

        urllib.request.urlretrieve(url, tmp_zip, _hook)

        progress.setLabelText('Pakker referencekort ud…')
        progress.setValue(100)
        QApplication.processEvents()
        with zipfile.ZipFile(tmp_zip) as zf:
            zf.extractall(cache_dir)

        progress.close()
        if not os.path.exists(path):
            QMessageBox.warning(
                parent, 'Fejl',
                'Referencekortet blev hentet, men forventede fil mangler:\n'
                '{}'.format(p['fil']))
            return None
        QgsMessageLog.logMessage(
            'Atlas: referencekort {} hentet og udpakket til {}'.format(
                periode, cache_dir), 'Atlas')
        return path

    except InterruptedError:
        progress.close()
        shutil.rmtree(cache_dir, ignore_errors=True)
        return None
    except Exception as e:
        progress.close()
        shutil.rmtree(cache_dir, ignore_errors=True)
        QMessageBox.critical(
            parent, 'Fejl ved download',
            'Kunne ikke hente referencekortet for {}:\n{}\n\nURL:\n{}'.format(
                periode, e, url))
        return None
    finally:
        if tmp_zip and os.path.exists(tmp_zip):
            try:
                os.remove(tmp_zip)
            except OSError:
                pass
