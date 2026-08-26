import os
import math

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterVectorLayer,
    QgsProcessingOutputNumber,
    QgsProcessingException,
    QgsProject,
)


EXCEL_ARK_TILFOERSEL  = "1. Tilførsel"
EXCEL_ARK_OMSAETNING  = "2. Omsætning"
TN_BELASTNING_CELLE   = "C30"   # kg N/ha/år  — Tilførsel, vandoplandet
OMSAETNINGSRATE_CELLE = "D32"   # kg N/ha/døgn — Omsætning, Oversvømmelse


class BeregnOmsaetningsrate(QgsProcessingAlgorithm):

    PROJEKTOMRAADE = "PROJEKTOMRAADE"
    OUTPUT_RATE    = "OUTPUT_RATE"

    def name(self):
        return "beregn_omsaetningsrate"

    def displayName(self):
        return "Beregn Omsætningsrate (Oversvømmelse)"

    def group(self):
        return "Oversvoemmelse"

    def groupId(self):
        return "oversvoemmelse"

    def createInstance(self):
        return BeregnOmsaetningsrate()

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.PROJEKTOMRAADE,
                "Projektområde",
            )
        )
        self.addOutput(
            QgsProcessingOutputNumber(
                self.OUTPUT_RATE,
                "Omsætningsrate (kg N/ha/døgn)",
            )
        )

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        try:
            import openpyxl
        except ImportError:
            raise QgsProcessingException(
                "openpyxl er ikke installeret. Installer via OSGeo4W Shell: pip install openpyxl"
            )

        lag = self.parameterAsVectorLayer(parameters, self.PROJEKTOMRAADE, context)
        if not lag:
            raise QgsProcessingException("Projektområde-laget kunne ikke indlæses.")

        lag_navn = lag.name()

        projekt_mappe = QgsProject.instance().homePath()
        if not projekt_mappe:
            raise QgsProcessingException(
                "QGIS-projektet er ikke gemt. Gem projektet og prøv igen."
            )

        output_mappe = os.path.join(projekt_mappe, f"Outputfiler_{lag_navn}")
        if not os.path.isdir(output_mappe):
            raise QgsProcessingException(
                f'Mappen "Outputfiler_{lag_navn}" blev ikke fundet i: {projekt_mappe}'
            )

        excel_filnavn = f"Resultat_{lag_navn}.xlsx"
        excel_sti = os.path.join(output_mappe, excel_filnavn)
        if not os.path.isfile(excel_sti):
            raise QgsProcessingException(
                f'Excel-filen "{excel_filnavn}" blev ikke fundet i: {output_mappe}'
            )

        feedback.pushInfo(f"Læser Excel-fil: {excel_sti}")

        # data_only=True for at få beregnede formelværdier (ikke formlerne selv)
        wb_read = openpyxl.load_workbook(excel_sti, data_only=True)

        if EXCEL_ARK_TILFOERSEL not in wb_read.sheetnames:
            raise QgsProcessingException(
                f'Arket "{EXCEL_ARK_TILFOERSEL}" blev ikke fundet i {excel_filnavn}.'
            )

        tn_raa = wb_read[EXCEL_ARK_TILFOERSEL][TN_BELASTNING_CELLE].value
        if tn_raa is None:
            raise QgsProcessingException(
                f"Cellen {EXCEL_ARK_TILFOERSEL}!{TN_BELASTNING_CELLE} er tom. "
                f"Kør vandoplandet-beregningen først."
            )

        try:
            tn_belastning = float(tn_raa)
        except (TypeError, ValueError):
            raise QgsProcessingException(
                f'Ugyldig værdi i {EXCEL_ARK_TILFOERSEL}!{TN_BELASTNING_CELLE}: "{tn_raa}"'
            )

        feedback.pushInfo(f"TN belastning ({EXCEL_ARK_TILFOERSEL}!{TN_BELASTNING_CELLE}): {tn_belastning} kg N/ha/år")

        # Empirisk formel (Overvågning af vådområder 2018-2021, s. 74):
        #   TN fjernelse (%) = 27,5 * exp(-6e-4 * x)
        #   x = TN belastning (kg N/ha/år)
        tn_fjernelse_pct = 27.5 * math.exp(-6e-4 * tn_belastning)
        feedback.pushInfo(f"TN fjernelse (%): {tn_fjernelse_pct:.4f}")

        # Omregn til kg N/ha/døgn
        omsaetningsrate = (tn_fjernelse_pct / 100.0 * tn_belastning) / 365.0
        feedback.pushInfo(f"Omsætningsrate: {round(omsaetningsrate, 6)} kg N/ha/døgn")

        # Skriv til Omsætning!D32
        wb_write = openpyxl.load_workbook(excel_sti)
        if EXCEL_ARK_OMSAETNING not in wb_write.sheetnames:
            raise QgsProcessingException(
                f'Arket "{EXCEL_ARK_OMSAETNING}" blev ikke fundet i {excel_filnavn}.'
            )

        wb_write[EXCEL_ARK_OMSAETNING][OMSAETNINGSRATE_CELLE] = round(omsaetningsrate, 6)
        wb_write.save(excel_sti)

        feedback.pushInfo(
            f"Omsætningsrate skrevet til {EXCEL_ARK_OMSAETNING}!{OMSAETNINGSRATE_CELLE}: "
            f"{round(omsaetningsrate, 6)} kg N/ha/døgn"
        )

        return {self.OUTPUT_RATE: omsaetningsrate}
