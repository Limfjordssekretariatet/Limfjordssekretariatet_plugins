"""VASP-plugin: indgang og orkestrering.

Opretter menuen/toolbar-knappen og binder databasekode (dbaccess) sammen
med geometri-/lagkode (processing) og GUI (profile_dialog). Indeholder
ingen SQL eller geometri-logik selv.
"""

import os
import subprocess

from qgis.PyQt.QtWidgets import (
    QAction, QMessageBox, QApplication, QFileDialog)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProject, QgsApplication

from . import config
from . import dbaccess
from . import writeback
from .profile_dialog import ProfileDialog
from .gisline_dialog import GisLineDialog
from .main_dialog import MainDialog
from .terrain_task import TerrainTask
from .geo import layer_builder
from .geo import offset


class VaspPlugin:
    """Hovedklassen QGIS instantierer via classFactory."""

    def __init__(self, iface):
        self.iface = iface
        self.menu = "&VASP"
        self.actions = []

    # --- QGIS plugin-livscyklus ------------------------------------------

    def initGui(self):
        """Opret VASP-hovedknappen. Kaldes når pluginnet indlæses.

        Én knap "VASP-integration" åbner en dialog med de enkelte
        handlinger som knapper.
        """
        icon_path = os.path.join(config.PLUGIN_DIR, "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        action = QAction(icon, "VASP-integration", self.iface.mainWindow())
        action.triggered.connect(self.open_main_dialog)
        self.iface.addPluginToMenu(self.menu, action)
        self.iface.addToolBarIcon(action)
        self.actions.append(action)

    def unload(self):
        """Fjern menu og toolbar-knap. Kaldes når pluginnet afregistreres."""
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
        self.actions = []

    # --- Handlinger -------------------------------------------------------

    def open_main_dialog(self):
        """Åbn VASP-integration-dialogen med handlingsknapperne."""
        dialog = MainDialog(
            on_terraen=self.run_terraen_paa_profil,
            on_importer=self.run_importer_laengdeprofil,
            on_importer_linje=self.run_importer_vandloebslinje,
            on_opdater=self.run_opdater_data,
            on_vaelg_database=self.vaelg_database,
            get_db_path=config.db_path,
            data_ready=self._data_ready,
            parent=self.iface.mainWindow())
        dialog.exec_()

    def _data_ready(self):
        """True hvis datafilen (GeoPackagen) findes – dvs. en database er valgt
        og bygget. Styrer om handlings-knapperne er aktive."""
        return os.path.exists(config.DEFAULT_GPKG_PATH)

    def vaelg_database(self):
        """Lad brugeren vælge en VASP-database og husk valget.

        Returnerer True hvis databasen blev ændret. Tilbyder at genopbygge
        GeoPackagen (læsedata) fra den nye database, så profillisten passer.
        """
        win = self.iface.mainWindow()
        current = config.db_path()
        start_dir = os.path.dirname(current) if current else ""
        path, _ = QFileDialog.getOpenFileName(
            win, "Vælg VASP-database", start_dir,
            "Access-database (*.mdb *.accdb);;Alle filer (*.*)")
        if not path:
            return False
        if os.path.normcase(os.path.abspath(path)) == \
                os.path.normcase(os.path.abspath(current)):
            return False  # samme database; intet ændret

        config.set_db_path(path)
        self.iface.messageBar().pushInfo(
            "VASP", "Aktiv database: %s" % path)

        # Datafilen (GeoPackagen) bygges ALTID automatisk fra den valgte
        # database – uden den kan ingen handlinger køre. Ingen frivillig
        # ja/nej: at vælge database = bygge datafilen.
        self._rebuild_gpkg(db_path=path)
        return True

    def _profiles_or_warn(self):
        """Hent profil-listen; vis fejl/tom-besked og returnér None hvis ingen."""
        win = self.iface.mainWindow()
        try:
            profiles = dbaccess.list_profiles()
        except dbaccess.VaspDbError as exc:
            QMessageBox.critical(win, "VASP — databasefejl", str(exc))
            return None
        if not profiles:
            QMessageBox.information(
                win, "VASP",
                "Der blev ikke fundet nogen profiler med geokodede "
                "terrænpunkter i databasen.")
            return None
        return profiles

    def run_terraen_paa_profil(self):
        """Terræn på profil: forskudte stationeringspunkter med DHM-kote.

        Bruger interval + side + distance fra dialogen. Terræn fra DHM er
        altid slået til, og resultatet skrives tilbage til VASP.
        """
        profiles = self._profiles_or_warn()
        if profiles is None:
            return
        dialog = ProfileDialog(
            profiles, mode=ProfileDialog.MODE_TERRAIN,
            parent=self.iface.mainWindow())
        if dialog.exec_() != ProfileDialog.Accepted:
            return
        prof = dialog.selected_profile()
        if not prof:
            return
        self._load_terrain(
            prof, self._centerline_for(prof),
            dialog.terrain_side(), dialog.selected_interval(),
            distance=dialog.selected_distance())

    def run_importer_laengdeprofil(self):
        """Importer længdeprofil til GIS: alle terrænpunkter som de er."""
        profiles = self._profiles_or_warn()
        if profiles is None:
            return
        dialog = ProfileDialog(
            profiles, mode=ProfileDialog.MODE_PROFILE,
            parent=self.iface.mainWindow())
        if dialog.exec_() != ProfileDialog.Accepted:
            return
        prof = dialog.selected_profile()
        if not prof:
            return
        self._load_profile(prof, interval=None, side=None)

    def run_importer_vandloebslinje(self):
        """Importer en vandløbslinje (VANDLØBGIS) til GIS som LineString."""
        win = self.iface.mainWindow()
        try:
            linjer = dbaccess.list_gislinjer()
        except dbaccess.VaspDbError as exc:
            QMessageBox.critical(win, "VASP — databasefejl", str(exc))
            return
        if not linjer:
            QMessageBox.information(
                win, "VASP", "Der blev ikke fundet nogen vandløbslinjer.")
            return

        dialog = GisLineDialog(linjer, win)
        if dialog.exec_() != GisLineDialog.Accepted:
            return
        linje = dialog.selected_line()
        if not linje:
            return

        try:
            points = dbaccess.read_gisline_points(linje["gisdataid"])
        except dbaccess.VaspDbError as exc:
            QMessageBox.critical(win, "VASP — databasefejl", str(exc))
            return
        if len(points) < 2:
            QMessageBox.information(
                win, "VASP",
                "Vandløbslinjen '%s' har ikke nok punkter." % linje["navn"])
            return

        navn = linje["vlbnavn"] or linje["navn"]
        layer_name = "VASP vandløbslinje: %s" % navn
        layer = layer_builder.build_gisline_layer(
            layer_name, points, linje["koordsysid"])
        if not layer.isValid():
            QMessageBox.critical(
                win, "VASP", "Kunne ikke oprette linjelaget i QGIS.")
            return

        QgsProject.instance().addMapLayer(layer)
        self.iface.setActiveLayer(layer)
        self.iface.zoomToActiveLayer()
        self.iface.messageBar().pushSuccess(
            "VASP", "Indlæste vandløbslinje '%s' (%d punkter)."
            % (navn, len(points)))

    def run_opdater_data(self):
        """Genopbyg GeoPackagen fra den aktive Access-database.

        Bruges når der er skrevet nye profiler ind i VASP. Pluginnet læser
        ellers kun det øjebliksbillede der allerede ligger i vasp_data.gpkg.
        """
        win = self.iface.mainWindow()
        svar = QMessageBox.question(
            win, "VASP — opdater data",
            "Genopbyg datafilen fra VASP-databasen nu?\n\n%s\n\n"
            "Det henter alle profiler (også nye) ind igen og kan tage et "
            "øjeblik. QGIS er optaget imens." % config.db_path(),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if svar != QMessageBox.Yes:
            return
        if self._rebuild_gpkg(config.db_path()):
            QMessageBox.information(
                win, "VASP", "Succes! Databasen er genindlæst")

    def _rebuild_gpkg(self, db_path):
        """Genopbyg GeoPackagen fra db_path via rebuild_gpkg.ps1.

        Returnerer True ved succes. Viser fejl-dialoger selv.
        """
        win = self.iface.mainWindow()
        script = os.path.join(config.PLUGIN_DIR, "tools", "rebuild_gpkg.ps1")
        if not os.path.exists(script):
            QMessageBox.critical(
                win, "VASP",
                "Kunne ikke finde opdaterings-scriptet:\n%s" % script)
            return False

        # Indlæste VASP-lag låser GeoPackage-filen, så genopbygningen (der
        # sletter og genskaber .gpkg) ville fejle. Fjern dem først.
        self._remove_gpkg_layers()

        powershell = self._find_powershell()
        if powershell is None:
            QMessageBox.critical(
                win, "VASP",
                "Kunne ikke finde powershell.exe på systemet.")
            return False

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", script, "-Mdb", db_path],
                capture_output=True, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(
                win, "VASP", "Kunne ikke starte opdateringen:\n%s" % exc)
            return False
        finally:
            QApplication.restoreOverrideCursor()

        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "").strip()[-1500:]
            QMessageBox.critical(
                win, "VASP — opdatering fejlede",
                "Genopbygningen fejlede.\n\n%s" % tail)
            return False

        self.iface.messageBar().pushSuccess(
            "VASP", "Data opdateret fra VASP-databasen.")
        return True

    def _find_powershell(self):
        """Find powershell.exe via fuld sti.

        QGIS-processen har ikke nødvendigvis System32 på PATH, så vi kan ikke
        regne med at 'powershell.exe' alene kan findes. Returnerer fuld sti
        eller None.
        """
        windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
        if windir:
            full = os.path.join(
                windir, "System32", "WindowsPowerShell", "v1.0",
                "powershell.exe")
            if os.path.exists(full):
                return full
        # Fald tilbage til PATH-opslag, hvis WINDIR ikke gav resultat.
        from shutil import which
        return which("powershell.exe") or which("powershell")

    def _remove_gpkg_layers(self):
        """Fjern lag fra projektet hvis de peger på vasp_data.gpkg.

        Returnerer antallet af fjernede lag. Frigør filen så den kan
        genopbygges.
        """
        project = QgsProject.instance()
        gpkg = os.path.normcase(os.path.abspath(config.DEFAULT_GPKG_PATH))
        to_remove = []
        for layer_id, layer in project.mapLayers().items():
            src = layer.source() or ""
            # Kilden ser typisk ud som '<sti>.gpkg|layername=...'
            path = os.path.normcase(os.path.abspath(src.split("|", 1)[0]))
            if path == gpkg:
                to_remove.append(layer_id)
        if to_remove:
            project.removeMapLayers(to_remove)
        return len(to_remove)

    def _centerline_for(self, prof):
        """Vælg den linje stationeringspunkterne skal følge.

        Foretrækker den VANDLØBGIS-linje profilen er geokodet på
        (geocodegdsid) — det er den linje brugeren ser på kortet og
        forventer punkterne følger. Falder tilbage til mellempunkterne,
        hvis profilen ikke har en geokodet linje, eller linjen er tom.
        """
        gid = prof.get("geocodegdsid")
        if gid is not None:
            line = dbaccess.read_geocoded_line(gid)
            if len(line) >= 2:
                return line
        return dbaccess.read_profile_centerline(prof["lgdid"])

    def _load_profile(self, prof, interval=None, side=None):
        """Importer længdeprofilen til GIS — alle terrænpunkter som de er."""
        win = self.iface.mainWindow()
        try:
            points = dbaccess.read_profile_points(prof["lgdid"])
        except dbaccess.VaspDbError as exc:
            QMessageBox.critical(win, "VASP — databasefejl", str(exc))
            return

        if not points:
            QMessageBox.information(
                win, "VASP",
                "Profilet '%s' har ingen geokodede punkter." % prof["navn"])
            return

        layer_name = "VASP længdeprofil: %s" % prof["navn"]
        layer = layer_builder.build_profile_layer(
            layer_name, points, prof["koordsysid"])

        if not layer.isValid():
            QMessageBox.critical(
                win, "VASP", "Kunne ikke oprette laget i QGIS.")
            return

        QgsProject.instance().addMapLayer(layer)
        self.iface.setActiveLayer(layer)
        self.iface.zoomToActiveLayer()
        self.iface.messageBar().pushSuccess(
            "VASP",
            "Indlæste %d terrænpunkter for '%s'." % (
                len(points), prof["navn"]))

    def _load_terrain(self, prof, centerline, side, interval, distance):
        """Forskyd linjen til siden og hent Z fra DHM i baggrunden.

        Stationeringen følger det oprindelige profil (interval måles på
        originallinjen), mens geometrien tages fra den parallelforskudte
        linje. Selve DHM-hentningen (som kan tage tid) køres i en QgsTask
        med fremgangslinje, så QGIS ikke fryser.
        """
        win = self.iface.mainWindow()
        if len(centerline) < 2:
            QMessageBox.information(
                win, "VASP",
                "Profilet '%s' har ikke nok linjepunkter til at lave "
                "stationeringspunkter (kræver mindst 2)." % prof["navn"])
            return
        # Forskydningen bruger QGIS processing og skal ske i hovedtråden.
        epsg = layer_builder.epsg_for(prof["koordsysid"])
        start_station = centerline[0]["station"] or 0.0
        shifted = offset.offset_line_points(
            centerline, distance, side, interval, start_station, epsg)
        if not shifted:
            QMessageBox.information(
                win, "VASP",
                "Kunne ikke forskyde linjen for '%s'." % prof["navn"])
            return

        # DHM-hentning i baggrunden; laget bygges i on_done (hovedtråden).
        task = TerrainTask(
            "VASP: henter terræn fra DHM for '%s'" % prof["navn"],
            shifted,
            on_done=lambda pts: self._terrain_done(
                prof, pts, side, interval),
            on_error=lambda msg: QMessageBox.critical(
                win, "VASP — DHM-fejl", msg))
        # Hold en reference, så tasken ikke bliver garbage-collected.
        self._task = task
        QgsApplication.taskManager().addTask(task)
        self.iface.messageBar().pushInfo(
            "VASP", "Henter terræn fra DHM … (se fremgang nederst)")

    def _terrain_done(self, prof, shifted, side, interval):
        """Byg PointZ-laget, når DHM-hentningen er færdig (hovedtråden)."""
        win = self.iface.mainWindow()
        med_z = [p for p in shifted if p.get("z") is not None]
        if not med_z:
            QMessageBox.information(
                win, "VASP",
                "Ingen af punkterne fik en terrænkote fra DHM "
                "(uden for dækning?).")
            return

        side_navn = "venstre" if side == offset.SIDE_LEFT else "højre"
        layer_name = "VASP terræn (%s): %s" % (side_navn, prof["navn"])
        layer = layer_builder.build_terrain_layer(
            layer_name, shifted, prof["koordsysid"])

        if not layer.isValid():
            QMessageBox.critical(
                win, "VASP", "Kunne ikke oprette terræn-laget i QGIS.")
            return

        QgsProject.instance().addMapLayer(layer)
        self.iface.setActiveLayer(layer)
        self.iface.zoomToActiveLayer()
        self.iface.messageBar().pushSuccess(
            "VASP",
            "Indlæste %d terrænpunkter (%g m, %s side, Z fra DHM) for '%s'."
            % (len(med_z), interval, side_navn, prof["navn"]))

        # Skriv automatisk det nye terræn-datalag tilbage til VASP.
        self._writeback_terrain(prof, med_z, side)

    def _writeback_terrain(self, prof, points, side):
        """Skriv terræn-datalaget tilbage til VASP som ny profil + punkter."""
        win = self.iface.mainWindow()
        navn = writeback.terrain_layer_name(
            prof["navn"], side == offset.SIDE_LEFT)

        # Bekræft før der skrives direkte i databasen.
        svar = QMessageBox.question(
            win, "VASP — skriv til database",
            "Skriv %d terrænpunkter til VASP-databasen som ny profil?\n\n"
            "Navn:      %s\n"
            "Database:  %s"
            % (len(points), navn, config.db_path()),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if svar != QMessageBox.Yes:
            self.iface.messageBar().pushInfo(
                "VASP", "Terrænlaget blev ikke skrevet til VASP.")
            return

        try:
            new_lgdid = writeback.write_terrain(
                prof["lgdid"], navn, points)
        except writeback.WritebackError as exc:
            QMessageBox.warning(
                win, "VASP — tilbageskrivning fejlede",
                "Terrænlaget er i QGIS, men kunne ikke skrives til VASP:\n\n"
                "%s" % exc)
            return
        self.iface.messageBar().pushSuccess(
            "VASP",
            "Skrev '%s' til VASP (ny profil LGDID %s, %d punkter)."
            % (navn, new_lgdid, len(points)))
