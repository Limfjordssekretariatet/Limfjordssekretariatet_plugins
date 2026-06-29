# -*- coding: utf-8 -*-
"""Referencekort-data: downloades fra en GitHub release og caches lokalt.

Referencekortet (arealtabel 2021-2023, ~230 MB) er for stort til at distribuere
i selve plugin-zippen (GitHub Pages-grænse 100 MB). Det ligger i stedet som et
release-asset og hentes første gang atlasset bygges. Caches i QGIS-profilen, så
det overlever plugin-opdateringer og kun hentes igen når REFERENCE_VERSION bumpes.
"""

import os
import shutil
import tempfile
import urllib.request
import zipfile

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QMessageBox, QApplication, QProgressDialog)
from qgis.core import QgsApplication, QgsMessageLog

REFERENCE_VERSION = 'referencekort-v1'   # = release-tag; bump for ny download
REFERENCE_ZIP_NAME = 'Referencekort_2021-2023.zip'
REFERENCE_SHP_NAME = 'Referencekort_2021-2023.shp'
REFERENCE_URL = (
    'https://github.com/Limfjordssekretariatet/Limfjordssekretariatet_plugins'
    f'/releases/download/{REFERENCE_VERSION}/{REFERENCE_ZIP_NAME}'
)


def reference_cache_dir():
    return os.path.join(QgsApplication.qgisSettingsDirPath(),
                        'Limf_Atlas', REFERENCE_VERSION)


def reference_path():
    """Stien til den cachede referencekort-shapefil."""
    return os.path.join(reference_cache_dir(), REFERENCE_SHP_NAME)


def ensure_reference(parent=None):
    """Sørg for at referencekortet findes i cachen. Hentes fra GitHub release og
    pakkes ud første gang (eller når REFERENCE_VERSION bumpes). Returnerer stien
    til .shp hvis klar, ellers None (og viser fejl/spørger brugeren)."""
    path = reference_path()
    if os.path.exists(path):
        return path

    cache_dir = reference_cache_dir()
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Question)
    msg.setWindowTitle('Hent referencekort')
    msg.setText(
        'Referencekortet (arealtabel) skal hentes første gang (ca. 110 MB).\n'
        'Det gemmes lokalt, så det kun hentes én gang.\n\nHent nu?')
    msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    if msg.exec_() != QMessageBox.Yes:
        return None

    progress = QProgressDialog('Henter referencekort…', 'Annullér', 0, 100, parent)
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

        urllib.request.urlretrieve(REFERENCE_URL, tmp_zip, _hook)

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
                f'{REFERENCE_SHP_NAME}')
            return None
        QgsMessageLog.logMessage(
            f'Atlas: referencekort hentet og udpakket til {cache_dir}', 'Atlas')
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
            f'Kunne ikke hente referencekortet:\n{e}\n\nURL:\n{REFERENCE_URL}')
        return None
    finally:
        if tmp_zip and os.path.exists(tmp_zip):
            try:
                os.remove(tmp_zip)
            except OSError:
                pass
