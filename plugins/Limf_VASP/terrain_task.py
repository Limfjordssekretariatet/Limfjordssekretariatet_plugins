"""Baggrundsopgave til DHM-terrænhentning.

Henter terrænkoter fra DHM i en QgsTask, så QGIS' UI forbliver responsiv og
viser en fremgangslinje under hentningen (der kan tage tid for lange forløb).

Kun selve DHM-hentningen (netværk + raster-opslag) kører i baggrunden — den
er ren Python/GDAL og trådsikker. Forskydningen (som bruger QGIS processing)
og lagopbygningen sker i hovedtråden, før/efter tasken.
"""

from qgis.core import QgsTask, QgsMessageLog, Qgis

from .geo import dhm


class TerrainTask(QgsTask):
    """Henter terrænkote (Z) for forskudte punkter i baggrunden.

    points:      forskudte punkt-dicts (med 'x', 'y', 'station').
    on_done:     callback(points_med_z) der køres i hovedtråden ved succes.
    on_error:    callback(besked) der køres i hovedtråden ved fejl.
    """

    def __init__(self, description, points, on_done, on_error):
        super().__init__(description, QgsTask.CanCancel)
        self._points = points
        self._on_done = on_done
        self._on_error = on_error
        self._error = None

    def run(self):
        """Kører i worker-tråden. Må ikke røre QGIS-GUI eller -lag."""
        try:
            dhm.add_terrain_z(
                self._points,
                progress=lambda done, total:
                    self.setProgress(100.0 * done / total),
                is_canceled=self.isCanceled)
        except dhm.DhmError as exc:
            self._error = str(exc)
            return False
        except Exception as exc:  # uventet — log og fejl pænt
            self._error = "Uventet fejl under DHM-hentning: %s" % exc
            QgsMessageLog.logMessage(
                self._error, "VASP", Qgis.Critical)
            return False
        return not self.isCanceled()

    def finished(self, result):
        """Kører i hovedtråden, når run() er færdig."""
        if self.isCanceled():
            return
        if not result:
            if self._error and self._on_error:
                self._on_error(self._error)
            return
        if self._on_done:
            self._on_done(self._points)
