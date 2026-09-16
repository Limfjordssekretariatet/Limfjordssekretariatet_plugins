# -*- coding: utf-8 -*-
"""Udfyld terrænkoter og afvandingsfelter — Processing-algoritme.

Henter terrænkoter fra en valgt højdemodel og genberegner de afledte felter
på et støttepunktlag.

Algoritmens id (``afvanding:udfyld_afvanding``) er det samme som i det
selvstændige plugin, værktøjet kom fra — så en model, der allerede bruger
det, virker uændret.
"""

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsCoordinateTransform,
    QgsExpressionContextUtils,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
    QgsProject,
    QgsRaster,
)

#: Afvandingsklasse (dropdown-værdien) -> afvandingsdybde i cm.
DYBDE = {
    "Frit vandspejl": 0,
    "Sump": 25,
    "Våd eng": 50,
    "Fugtig eng": 75,
    "Tør eng": 100,
    "Mark": 125,
}

#: (klassefelt, dybdefelt, vandspejlsfelt) for nu og fremtid.
PAR = [("sommid_nuv", "dybde_nuv", "vsp_nuv"),
       ("sommid_frem", "dybde_frem", "vsp_frem")]

#: Projektvariablen, som nye punkters standardværdi for terraen læser.
VAR_DTM = "dtm_lag"


class UdfyldAfvanding(QgsProcessingAlgorithm):

    PUNKTER = "PUNKTER"
    DTM = "DTM"
    KILDE = "KILDE"
    KUN_VALGTE = "KUN_VALGTE"
    OVERSKRIV = "OVERSKRIV"

    def tr(self, s):
        return QCoreApplication.translate("UdfyldAfvanding", s)

    def createInstance(self):
        return UdfyldAfvanding()

    def name(self):
        return "udfyld_afvanding"

    def displayName(self):
        return self.tr("Udfyld terrænkoter og afvandingsfelter")

    def group(self):
        return self.tr("Støttepunkter")

    def groupId(self):
        return "stoettepunkter"

    def shortHelpString(self):
        return self.tr(
            "Sampler den valgte højdemodel i hvert støttepunkt og skriver "
            "resultatet i feltet 'terraen'. Derefter genberegnes dybde_nuv, "
            "dybde_frem, vsp_nuv og vsp_frem ud fra dropdownfelterne "
            "sommid_nuv og sommid_frem.\n\n"
            "Feltet dtm_kilde udfyldes automatisk med højdemodellens navn, "
            "medmindre du selv skriver en tekst i 'Version til feltet "
            "dtm_kilde'.\n\n"
            "Projektvariablen 'dtm_lag' sættes samtidig til den valgte "
            "højdemodel, så nye punkter, du digitaliserer bagefter, selv "
            "henter deres kote."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.PUNKTER, self.tr("Støttepunkter"),
            types=[QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DTM, self.tr("Højdemodel (DTM)")))
        self.addParameter(QgsProcessingParameterString(
            self.KILDE,
            self.tr("Version til feltet dtm_kilde (valgfri — ellers bruges "
                    "højdemodellens navn)"),
            defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.KUN_VALGTE, self.tr("Kun valgte punkter"), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.OVERSKRIV, self.tr("Overskriv eksisterende terrænkoter"),
            defaultValue=True))

    def processAlgorithm(self, parameters, context, feedback):
        lag = self.parameterAsVectorLayer(parameters, self.PUNKTER, context)
        dtm = self.parameterAsRasterLayer(parameters, self.DTM, context)
        kilde = self.parameterAsString(parameters, self.KILDE, context).strip()
        kun_valgte = self.parameterAsBool(parameters, self.KUN_VALGTE, context)
        overskriv = self.parameterAsBool(parameters, self.OVERSKRIV, context)

        felter = lag.fields()
        idx = {n: felter.indexFromName(n) for n in
               ["terraen", "dtm_kilde", "sommid_nuv", "sommid_frem",
                "dybde_nuv", "dybde_frem", "vsp_nuv", "vsp_frem"]}
        mangler = [n for n in ("terraen", "sommid_nuv", "sommid_frem")
                   if idx[n] < 0]
        if mangler:
            raise QgsProcessingException(
                self.tr("Laget er ikke et støttepunktlag — det mangler "
                        "felterne: %s") % ", ".join(mangler))

        tr = QgsCoordinateTransform(lag.crs(), dtm.crs(), QgsProject.instance())
        prov = dtm.dataProvider()

        antal = lag.selectedFeatureCount() if kun_valgte else lag.featureCount()
        if kun_valgte and antal == 0:
            raise QgsProcessingException(self.tr("Ingen punkter er valgt."))

        # Redigeringen SKAL startes, før objekterne hentes. En GeoPackage
        # genåbnes i skrivetilstand ved startEditing(), og en læser, der blev
        # åbnet forinden, giver derefter nul objekter — uden fejl. Så skrev
        # værktøjet ingenting, hver gang laget ikke allerede stod i
        # redigering, og meldte "Terrænkoter skrevet: 0".
        if not lag.isEditable():
            lag.startEditing()
        objekter = lag.selectedFeatures() if kun_valgte else lag.getFeatures()

        n_kote, n_nodata, ukendt = 0, 0, set()

        for i, f in enumerate(objekter):
            if feedback.isCanceled():
                break
            feedback.setProgress(int(100 * i / max(antal, 1)))
            aendringer = {}

            geom = f.geometry()
            har_kote = f[idx["terraen"]]
            skal_samples = (overskriv or har_kote is None
                            or str(har_kote) == "NULL")

            kote = None
            if skal_samples and geom and not geom.isEmpty():
                pt = geom.asPoint()
                try:
                    pt = tr.transform(pt)
                except Exception:
                    pass
                res = prov.identify(pt, QgsRaster.IdentifyFormatValue)
                if res.isValid():
                    v = res.results().get(1)
                    if v is not None and v == v:  # ikke NaN
                        kote = round(float(v), 2)
                if kote is None:
                    n_nodata += 1
                else:
                    n_kote += 1
                aendringer[idx["terraen"]] = kote
            else:
                kote = None if har_kote is None else float(har_kote)

            if idx["dtm_kilde"] >= 0:
                aendringer[idx["dtm_kilde"]] = kilde if kilde else dtm.name()

            for klassefelt, dybdefelt, vspfelt in PAR:
                klasse = f[idx[klassefelt]]
                dybde = None
                if klasse is not None and str(klasse) != "NULL":
                    dybde = DYBDE.get(str(klasse).strip())
                    if dybde is None:
                        ukendt.add(str(klasse))
                if idx[dybdefelt] >= 0:
                    aendringer[idx[dybdefelt]] = dybde
                if idx[vspfelt] >= 0:
                    aendringer[idx[vspfelt]] = (
                        None if (kote is None or dybde is None)
                        else round(kote - dybde / 100.0, 2))

            lag.changeAttributeValues(f.id(), aendringer)

        if not lag.commitChanges():
            raise QgsProcessingException(
                self.tr("Kunne ikke gemme: %s") % "; ".join(lag.commitErrors()))

        QgsExpressionContextUtils.setProjectVariable(
            QgsProject.instance(), VAR_DTM, dtm.name())

        feedback.pushInfo("Terrænkoter skrevet: %d" % n_kote)
        if n_nodata:
            feedback.reportError(
                "Punkter uden for højdemodellen eller på NODATA: %d" % n_nodata,
                fatalError=False)
        if ukendt:
            feedback.reportError(
                "Ukendte afvandingsklasser (stavefejl?): %s"
                % ", ".join(sorted(ukendt)), fatalError=False)
        feedback.pushInfo("Projektvariablen dtm_lag sat til: %s" % dtm.name())

        return {self.PUNKTER: lag.id()}
