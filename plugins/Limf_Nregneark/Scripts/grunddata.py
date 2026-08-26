# -*- coding: utf-8 -*-
"""Referencedata der er for store til plugin-pakken.

Fem datasæt fylder tilsammen 4 GB — jordbundskortet alene 2,5 GB. GitHub Pages
tager højst 100 MB pr. fil, så de kan ikke ligge i selve pluginnet. De ligger i
stedet som release-assets og hentes første gang et trin skal bruge dem.

Ét arkiv pr. datasæt, ikke ét stort: en bruger der kun udpeger oplande, skal
ikke hente jordbundskortet. Cachen ligger i QGIS-profilen, så den overlever en
plugin-opdatering og kun hentes igen når VERSION hæves.

De små datasæt (vandløbsnetværket, grids, oplande) følger med pluginnet som før
— de fylder tilsammen under 30 MB, og at hente dem ville koste mere i besvær end
i plads.
"""
import os
import shutil
import tempfile
import zipfile

VERSION = 'grunddata-v1'     # = release-tag; hæv for at tvinge ny download
REPO = 'Limfjordssekretariatet/Limfjordssekretariatet_plugins'
PLUGIN_ID = 'Limf_Nregneark'

# Datasæt der hentes: mappenavn -> (nøglefil der skal findes, ca. MB pakket).
# Nøglefilen afgør om cachen er komplet — en afbrudt download efterlader en
# halv mappe, og den skal ikke tages for en færdig.
STORE = {
    'Jordbundskort_2024': ('Jordbundskort_2024.shp', 375),
    'Marker_2024':        ('Marker_2024.shp',        353),
    'Befaestet_Areal':    ('Samlet.shp',             147),
    'Vandloeb':           ('Vandloeb_DK.shp',         47),
    'DHMLinje':           ('DHMLinje.shp',            14),
}

_PLUGIN_ROD = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
_LOKAL = os.path.join(_PLUGIN_ROD, 'Grunddata')


def url(navn):
    return (f'https://github.com/{REPO}/releases/download/{VERSION}/'
            f'grunddata-{navn.lower()}.zip')


def cache_rod():
    """Cachen i QGIS-profilen, uden for pluginnets egen mappe.

    Pluginmappen ryddes ved opdatering; profilen gør ikke. Version i stien, så
    et nyt datasæt ikke bliver forvekslet med et gammelt.
    """
    from qgis.core import QgsApplication

    return os.path.join(QgsApplication.qgisSettingsDirPath(), PLUGIN_ID, VERSION)


def mappe(navn):
    """Mappen med datasættet, eller None hvis det ikke er hentet endnu.

    Ligger datasættet i pluginnets egen Grunddata-mappe, bruges det — så virker
    en udviklerkopi med det hele liggende, uden at hente noget.
    """
    noeglefil = STORE.get(navn, (None, 0))[0]
    lokal = os.path.join(_LOKAL, navn)
    if noeglefil and os.path.isfile(os.path.join(lokal, noeglefil)):
        return lokal
    if navn not in STORE:
        # Et af de små datasæt der følger med pluginnet.
        return lokal if os.path.isdir(lokal) else None
    hentet = os.path.join(cache_rod(), navn)
    if os.path.isfile(os.path.join(hentet, noeglefil)):
        return hentet
    return None


def sti(navn, filnavn=None):
    """Fuld sti til datasættets nøglefil (eller en anden fil i det)."""
    m = mappe(navn)
    if m is None:
        return None
    if filnavn is None:
        filnavn = STORE.get(navn, (None, 0))[0]
    if filnavn is None:
        return m
    fuld = os.path.join(m, filnavn)
    return fuld if os.path.isfile(fuld) else None


def mangler(*navne):
    """De af datasættene der endnu ikke er hentet."""
    return [n for n in navne if mappe(n) is None]


def _hent(navn, meld, afbrudt=None, fremdrift=None):
    """Henter og pakker ét datasæt ud. Sandt ved held."""
    import urllib.request

    noeglefil, mb = STORE[navn]
    maal = os.path.join(cache_rod(), navn)
    adresse = url(navn)
    meld(f'Henter {navn} (~{mb} MB) — det sker kun én gang.')

    tmp = None
    try:
        os.makedirs(maal, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix='.zip')
        os.close(fd)

        def _hook(blok, blokstoerrelse, i_alt):
            if afbrudt is not None and afbrudt():
                raise InterruptedError('afbrudt')
            if i_alt > 0 and fremdrift is not None:
                fremdrift(min(100, int(blok * blokstoerrelse * 100 / i_alt)))

        urllib.request.urlretrieve(adresse, tmp, _hook)
        meld(f'Pakker {navn} ud …')
        with zipfile.ZipFile(tmp) as z:
            z.extractall(maal)
        if not os.path.isfile(os.path.join(maal, noeglefil)):
            shutil.rmtree(maal, ignore_errors=True)
            meld(f'{navn} blev hentet, men {noeglefil} manglede i arkivet.')
            return False
        meld(f'{navn} ligger nu i {maal}')
        return True
    except InterruptedError:
        shutil.rmtree(maal, ignore_errors=True)
        return False
    except Exception as e:
        # En halv mappe er værre end ingen: den ville blive taget for komplet.
        shutil.rmtree(maal, ignore_errors=True)
        meld(f'{navn} kunne ikke hentes: {e}\n{adresse}')
        return False
    finally:
        if tmp and os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def sikr(navne, feedback=None, forael=None):
    """Sørger for at datasættene er hentet. Sandt hvis alle er klar.

    `feedback` er et Processing-feedback (skriver i loggen). `forael` er en
    dialog — er den givet, spørges brugeren først og der vises en fremdriftslinje,
    for en download på flere hundrede MB skal ikke starte af sig selv.
    """
    if isinstance(navne, str):
        navne = [navne]
    savnede = mangler(*navne)
    if not savnede:
        return True

    def meld(t):
        if feedback is not None:
            feedback.pushInfo(t)

    if forael is None:
        for navn in savnede:
            if not _hent(navn, meld,
                         afbrudt=(feedback.isCanceled if feedback else None)):
                return False
        return True

    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import (QApplication, QMessageBox, QProgressDialog)

    i_alt_mb = sum(STORE[n][1] for n in savnede)
    liste = ', '.join(savnede)
    svar = QMessageBox.question(
        forael, 'Hent referencedata',
        f'Følgende data mangler og skal hentes: {liste}.\n\n'
        f'Det fylder ca. {i_alt_mb} MB og hentes kun én gang — det gemmes i '
        'QGIS-profilen og overlever en plugin-opdatering.\n\nHent nu?',
        QMessageBox.Yes | QMessageBox.No)
    if svar != QMessageBox.Yes:
        return False

    linje = QProgressDialog('Henter referencedata …', 'Annullér', 0, 100, forael)
    linje.setWindowModality(Qt.WindowModal)
    linje.setMinimumDuration(0)
    try:
        for navn in savnede:
            linje.setLabelText(f'Henter {navn} …')
            linje.setValue(0)
            QApplication.processEvents()

            def _vis(pct):
                linje.setValue(pct)
                QApplication.processEvents()

            def _meld(t):
                linje.setLabelText(t)
                QApplication.processEvents()
                meld(t)

            if not _hent(navn, _meld, afbrudt=linje.wasCanceled,
                         fremdrift=_vis):
                linje.close()
                if not linje.wasCanceled():
                    QMessageBox.critical(
                        forael, 'Data kunne ikke hentes',
                        f'{navn} kunne ikke hentes. Se QGIS\' logpanel for '
                        'detaljer.')
                return False
    finally:
        linje.close()
    return True
