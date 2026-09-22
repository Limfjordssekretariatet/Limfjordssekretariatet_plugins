"""VASP-plugin: indgang og orkestrering.

Opretter menuen/toolbar-knappen og binder databasekode (dbaccess) sammen
med geometri-/lagkode (processing) og GUI (profile_dialog). Indeholder
ingen SQL eller geometri-logik selv.
"""

import os
import subprocess

from qgis.PyQt.QtWidgets import (
    QAction, QMessageBox, QFileDialog, QProgressDialog)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QEventLoop
from qgis.PyQt.QtGui import QIcon
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProcessingUtils,
    QgsProject,
    QgsRectangle,
    QgsReferencedRectangle,
    QgsVectorFileWriter,
)

from . import config
from . import dbaccess
from . import faelles_gui
from . import writeback
from .profile_dialog import ProfileDialog
from .gisline_dialog import GisLineDialog
from .vsp_dialog import VspDialog, ScenarieDialog
from .afvanding_kilder_dialog import AfvandingKilderDialog
from .tvp_dialog import TvpDialog
from .main_dialog import MainDialog
from .oplande_dialog import OplandeDialog
from .terrain_task import TerrainTask
from .geo import layer_builder
from .geo import offset
from .geo import ber
from .geo import hds


class _BuildWorker(QThread):
    """Kører genopbygnings-scriptet i en baggrundstråd, så UI'en (og
    'arbejder'-dialogens animation) forbliver responsiv under den lange build."""

    done = pyqtSignal(int, str)   # (returncode, output-hale)

    def __init__(self, cmd):
        super().__init__()
        self._cmd = cmd

    def run(self):
        try:
            result = subprocess.run(
                self._cmd, capture_output=True, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            # Begge strømme med: fremdriften skrives til stdout, mens selve
            # årsagen (fx en manglende Access-driver) kommer på stderr. Vises
            # kun den ene, står brugeren med en overskrift uden forklaring.
            dele = [(result.stdout or "").strip(), (result.stderr or "").strip()]
            tail = "\n".join(d for d in dele if d)[-2500:]
            self.done.emit(result.returncode, tail)
        except OSError as exc:
            self.done.emit(-1, str(exc))


class VaspPlugin:
    """Hovedklassen QGIS instantierer via classFactory."""

    def __init__(self, iface):
        self.iface = iface
        self.actions = []

    # --- QGIS plugin-livscyklus ------------------------------------------

    def initGui(self):
        """Opret VASP-knappen i Vandprojekter-menuen og -værktøjslinjen.

        Én knap "VASP" åbner en dialog med de enkelte handlinger som knapper.
        """
        icon_path = os.path.join(config.PLUGIN_DIR, "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        action = QAction(icon, "VASP …", self.iface.mainWindow())
        action.setStatusTip(
            "Hent profil-, terræn- og vandspejlsdata fra VASP, og regn på dem")
        action.triggered.connect(self.open_main_dialog)
        faelles_gui.tilfoej(self.iface, action)
        self.actions.append(action)

    def unload(self):
        """Fjern knappen igen. Kaldes når pluginnet afregistreres."""
        for action in self.actions:
            faelles_gui.fjern(self.iface, action)
        self.actions = []

    # --- Handlinger -------------------------------------------------------

    def open_main_dialog(self):
        """Åbn VASP-integration-dialogen med handlingsknapperne."""
        dialog = MainDialog(
            on_terraen=self.run_terraen_paa_profil,
            on_importer=self.run_importer_laengdeprofil,
            on_importer_linje=self.run_importer_vandloebslinje,
            on_importer_vsp=self.run_importer_vandspejl,
            on_opdater=self.run_opdater_data,
            on_vaelg_database=self.vaelg_database,
            get_db_path=config.db_path,
            data_ready=self._data_ready,
            on_braend_vandloeb=self.run_braend_vandloeb,
            on_afvandingsanalyse=self.run_afvandingsanalyse,
            on_oplande_til_vasp=self.run_oplande_til_vasp,
            parent=self.iface.mainWindow())
        dialog.exec_()

    def run_braend_vandloeb(self):
        """Brænd tværprofiler ned i terrænmodellen.

        Brugeren vælger et profil-datalag i VASP; vandløbslinjen hentes fra
        den linje profilet er geokodet på, og begge dele sendes videre til
        Processing-dialogen, hvor terrænmodel og output vælges.
        """
        win = self.iface.mainWindow()
        try:
            profiler = dbaccess.list_tvp_profiles()
        except dbaccess.VaspDbError:
            QMessageBox.information(
                win, "VASP — brænd vandløb i terræn",
                "Datafilen indeholder ikke tværprofiler endnu.\n\n"
                "Tryk \"Genindlæs database\", så bygges den forfra med "
                "tværprofilerne. Det tager nogle minutter.")
            return
        if not profiler:
            QMessageBox.information(
                win, "VASP",
                "Der blev ikke fundet nogen profiler med tværprofiler i "
                "databasen.")
            return

        dialog = TvpDialog(profiler, win)
        if dialog.exec_() != TvpDialog.Accepted:
            return

        prof = dialog.selected_profile()
        if not prof:
            return
        if not prof["geocodegdsid"]:
            QMessageBox.warning(
                win, "VASP — brænd vandløb i terræn",
                "Profilet '%s' er ikke geokodet til en vandløbslinje, så "
                "linjen kan ikke hentes automatisk.\n\nVælg et andet profil."
                % prof["navn"])
            return

        try:
            points = dbaccess.read_gisline_points(prof["geocodegdsid"])
        except dbaccess.VaspDbError as exc:
            QMessageBox.critical(win, "VASP — databasefejl", str(exc))
            return
        if len(points) < 2:
            QMessageBox.warning(
                win, "VASP",
                "Vandløbslinjen for '%s' har ikke nok punkter." % prof["navn"])
            return

        navn = "VASP centerlinje: %s" % (prof["vlbnavn"] or prof["navn"])
        linje = layer_builder.build_gisline_layer(
            navn, points, prof["koordsysid"])
        if not linje.isValid():
            QMessageBox.critical(
                win, "VASP", "Kunne ikke oprette centerlinjen i QGIS.")
            return
        QgsProject.instance().addMapLayer(linje)

        self._braend_dialog({"VASP_LGDID": prof["lgdid"], "CENTERLINE": linje})

    def _braend_dialog(self, parameters):
        """Åbn Processing-dialogen for nedbrændingen med givne parametre."""
        def lav():
            from .framike_til_dhm import BraendVandloebITerraenAlgorithm
            return BraendVandloebITerraenAlgorithm()

        self._processing_dialog(
            lav, parameters, "VASP — brænd vandløb i terræn",
            "VaspBraendVandloebDialog", flyt=("OUTPUT_LINES",))

    def _processing_dialog(self, lav_algoritme, parameters, titel,
                           objekt_navn, flyt=()):
        """Kør en af pluginnets Processing-algoritmer i sin egen dialog.

        Dialogen bygges selv (i stedet for execAlgorithmDialog), fordi
        "Avanceret"-gruppen først kan foldes ind mellem show() og exec().
        """
        from qgis import processing
        try:
            algoritme = lav_algoritme()
            dialog = processing.createAlgorithmDialog(algoritme, parameters)
            if dialog is None:
                processing.execAlgorithmDialog(algoritme, parameters)
                return
            # Eget navn, saa QGIS gemmer "Avanceret"-tilstanden under vores
            # egen noegle i stedet for den alle Processing-dialoger deler.
            dialog.setObjectName(objekt_navn)
            dialog.show()
            self._fold_avanceret_ind(dialog, flyt)
            dialog.exec_()
            dialog.close()
        except Exception as exc:
            QMessageBox.critical(
                self.iface.mainWindow(), titel,
                "Værktøjet kunne ikke startes:\n\n%s" % exc)

    @staticmethod
    def _fold_avanceret_ind(dialog, flyt=()):
        """Fold "Avancerede parametre" ind, hver gang dialogen åbnes.

        Gruppen er en QgsCollapsibleGroupBox, der genskaber sin gemte
        tilstand i sin showEvent — har man foldet den ud én gang, åbner den
        udfoldet næste gang. Derfor foldes den ind EFTER show(). Klassen er
        ikke eksponeret i Python, så det sker gennem Qt-egenskaben
        ``collapsed`` (som kalder setCollapsed()).
        """
        from qgis.PyQt.QtWidgets import QGroupBox
        gruppe = dialog.findChild(QGroupBox, "grpAdvanced")
        if gruppe is None:
            return
        flyttet = []
        for parameter in flyt:
            flyttet += VaspPlugin._flyt_til_avanceret(
                dialog, gruppe, parameter)
        # Gruppen husker hvilke børn der var synlige, da den blev foldet ind,
        # og viser netop dem igen ved udfoldning. De nyflyttede widgets skal
        # derfor være synlige i en udfoldet gruppe først — ellers dukker de
        # aldrig op igen. Rækkefølgen ligger mellem show() og exec(), så
        # dialogen når ikke at blive tegnet undervejs.
        gruppe.setProperty("collapsed", False)
        for widget in flyttet:
            widget.setVisible(True)
        gruppe.setProperty("collapsed", True)

    @staticmethod
    def _flyt_til_avanceret(dialog, gruppe, parameter):
        """Flyt et valgfrit *output* ned i Avanceret-gruppen.

        QGIS samler alle destinations-parametre nederst i dialogen og ser bort
        fra FlagAdvanced for dem, så kontrol-laget ville ellers stå frit.
        Widget og tilhørende label flyttes derfor over i gruppens layout.

        Returnerer de flyttede widgets.
        """
        from qgis.PyQt.QtWidgets import QLabel
        panel = dialog.mainWidget()
        wrapper = getattr(panel, "wrappers", {}).get(parameter)
        if wrapper is None or not hasattr(wrapper, "wrappedWidget"):
            return []
        widget = wrapper.wrappedWidget()
        if widget is None or widget.parentWidget() is None:
            return []
        layout = widget.parentWidget().layout()
        maal = gruppe.layout()
        if layout is None or maal is None:
            return []
        plads = layout.indexOf(widget)
        if plads < 0:
            return []
        flyttet = []
        if plads > 0:
            forrige = layout.itemAt(plads - 1)
            if forrige is not None and isinstance(forrige.widget(), QLabel):
                flyttet.append(forrige.widget())
        flyttet.append(widget)
        for w in flyttet:
            layout.removeWidget(w)
            maal.addWidget(w)
        return flyttet

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

        samme_db = (os.path.normcase(os.path.abspath(path)) ==
                    os.path.normcase(os.path.abspath(current)))
        gpkg_findes = os.path.exists(config.DEFAULT_GPKG_PATH)
        # Spring KUN build over hvis det er samme database OG datafilen allerede
        # er bygget. Ellers (ny database, ELLER gpkg mangler/fejlede sidst) skal
        # vi bygge – så man altid kan prøve igen med samme fil.
        if samme_db and gpkg_findes:
            return False

        config.set_db_path(path)
        self.iface.messageBar().pushInfo(
            "VASP", "Aktiv database: %s" % path)

        # Datafilen (GeoPackagen) bygges ALTID automatisk fra den valgte
        # database – uden den kan ingen handlinger køre. Ingen frivillig
        # ja/nej: at vælge database = bygge datafilen.
        return self._rebuild_gpkg(db_path=path)

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
        """Importer længdeprofiler til GIS: alle terrænpunkter som de er.

        Der kan vælges flere på én gang. Ét profil opfører sig som før; er
        der flere, samles lagene i en gruppe, og der vises fremdrift.
        """
        profiles = self._profiles_or_warn()
        if profiles is None:
            return
        self._advar_om_gamle_koter()
        dialog = ProfileDialog(
            profiles, mode=ProfileDialog.MODE_PROFILE,
            parent=self.iface.mainWindow())
        if dialog.exec_() != ProfileDialog.Accepted:
            return
        valgte = dialog.selected_profiles()
        if not valgte:
            return
        if len(valgte) == 1:
            self._load_profile(valgte[0], interval=None, side=None)
        else:
            self._load_profiles(valgte)

    def _advar_om_gamle_koter(self):
        """Sig til, hvis datafilen stammer fra før kote-rettelsen.

        Vises kun én gang pr. QGIS-session, så den ikke bliver til støj.
        """
        if getattr(self, "_kote_advarsel_vist", False):
            return
        try:
            if dbaccess.datafil_har_rigtige_koter():
                return
        except Exception:
            return
        self._kote_advarsel_vist = True
        QMessageBox.information(
            self.iface.mainWindow(), "VASP — koter i længdeprofilet",
            "Datafilen er bygget før kote-rettelsen, så kote-feltet "
            "indeholder datum-korrektionen (tal omkring nul) i stedet for "
            "bundkoten.\n\nTryk \"Genindlæs database\" i VASP-dialogen for "
            "at bygge den forfra med rigtige koter.")

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

    def run_importer_vandspejl(self):
        """Importer en vandspejlsberegning (fra .ber-fil) til GIS."""
        win = self.iface.mainWindow()
        try:
            calcs = dbaccess.list_vsp_calcs()
        except dbaccess.VaspDbError as exc:
            QMessageBox.critical(win, "VASP — databasefejl", str(exc))
            return
        if not calcs:
            QMessageBox.information(
                win, "VASP", "Der blev ikke fundet nogen vandspejlsberegninger.")
            return

        dialog = VspDialog(calcs, win)
        if dialog.exec_() != VspDialog.Accepted:
            return
        calc = dialog.selected_calc()
        if not calc:
            return

        # Bruger ikke os.path.exists som forhåndstjek: det er upålideligt for
        # UNC-netværksstier i QGIS. Forsøg i stedet at læse filen direkte og
        # fang OSError med en tydelig besked.
        path = config.ber_path(
            calc["projektid"], calc["berid"], multi=calc["multi"])
        if calc["multi"]:
            self._load_vsp_multi(calc, path)
        else:
            self._load_vsp_simpel(calc, path)

    def _vsp_read_error(self, path, exc):
        """Vis en hjælpsom fejl når en .ber-fil ikke kunne læses."""
        QMessageBox.critical(
            self.iface.mainWindow(), "VASP — kunne ikke læse beregning",
            "Kunne ikke læse beregningsfilen:\n%s\n\n"
            "Fejl: %s\n\n"
            "Vandspejlsberegninger ligger i en PRJDATA-mappe sammen med "
            "VASP-databasen. Vælg den rigtige database (fx på netværket) "
            "under 'Vælg database …', så findes filerne automatisk."
            % (path, exc))

    def _load_vsp_simpel(self, calc, path):
        """Indlæs en simpel vandspejlsberegning som PointZ-lag."""
        win = self.iface.mainWindow()
        try:
            points = ber.decode_simpel(path)
        except (OSError, ValueError) as exc:
            self._vsp_read_error(path, exc)
            return
        if not points:
            QMessageBox.information(
                win, "VASP", "Beregningen indeholdt ingen punkter.")
            return

        fields_spec = [
            ("station", "station"), ("vsp", "vsp"), ("bund", "bund"),
            ("energi", "energi"), ("vnf", "vnf"), ("manning", "manning"),
            ("bredde", "bredde"), ("areal", "areal"), ("radius", "radius"),
        ]
        layer_name = "VASP vandspejl: %s" % calc["navn"]
        layer = layer_builder.build_vsp_layer(
            layer_name, points, calc["koordsysid"], fields_spec)
        self._add_vsp_layer(layer, calc, len(points))

    def _load_vsp_multi(self, calc, path):
        """Indlæs en multivandspejlsberegning: alle scenarier samlet.

        Hvert scenarie er en record-blok i .ber-filen. Punkterne har samme
        X/Y på tværs af scenarier, så vi samler dem til ét lag med ét
        vsp-felt pr. scenarie (vsp1, vsp2, …) og vsp fra første scenarie som Z.
        """
        win = self.iface.mainWindow()
        try:
            scenarier = ber.decode_multi(path)
        except (OSError, ValueError) as exc:
            self._vsp_read_error(path, exc)
            return
        if not scenarier:
            QMessageBox.information(
                win, "VASP", "Beregningen indeholdt ingen scenarier.")
            return

        # Saml scenarierne pr. punkt (samme rækkefølge/X-Y på tværs).
        # Ét felt pr. scenarie, navngivet efter scenariet (fx 'MedMin').
        keys = self._scenario_field_keys(scenarier)
        base = scenarier[0]["points"]
        merged = []
        for i, bp in enumerate(base):
            row = {"x": bp["x"], "y": bp["y"], "vsp": bp.get("vsp"),
                   "bund": bp.get("bund")}
            for s, scen in enumerate(scenarier):
                pts = scen["points"]
                row[keys[s]] = pts[i]["vsp"] if i < len(pts) else None
            merged.append(row)

        fields_spec = [("bund", "bund")]
        for s in range(len(scenarier)):
            fields_spec.append((keys[s], keys[s]))

        layer_name = "VASP multivandspejl: %s" % calc["navn"]
        layer = layer_builder.build_vsp_layer(
            layer_name, merged, calc["koordsysid"], fields_spec)
        self._add_vsp_layer(layer, calc, len(merged),
                            "%d scenarier" % len(scenarier))

    def _scenario_field_keys(self, scenarier):
        """Lav gyldige, unikke feltnavne ud fra scenariernes navne.

        Fx 'Sommer Middel' -> 'Sommer_Middel'. Sikrer entydighed hvis to
        scenarier har samme navn, og falder tilbage til 'vsp<N>' hvis et
        navn mangler.
        """
        import re
        keys = []
        seen = {}
        for i, scen in enumerate(scenarier):
            navn = (scen.get("navn") or "").strip()
            if navn:
                key = re.sub(r"[^0-9A-Za-zÆØÅæøå]+", "_", navn).strip("_")
            else:
                key = ""
            if not key:
                key = "vsp%d" % (i + 1)
            # Entydiggør dubletter.
            if key in seen:
                seen[key] += 1
                key = "%s_%d" % (key, seen[key])
            else:
                seen[key] = 1
            keys.append(key)
        return keys

    def _add_vsp_layer(self, layer, calc, n_points, ekstra=""):
        """Tilføj et vandspejls-lag til QGIS og meld resultat."""
        win = self.iface.mainWindow()
        if not layer.isValid():
            QMessageBox.critical(
                win, "VASP", "Kunne ikke oprette vandspejls-laget i QGIS.")
            return
        QgsProject.instance().addMapLayer(layer)
        self.iface.setActiveLayer(layer)
        self.iface.zoomToActiveLayer()
        besked = "Indlæste vandspejl '%s' (%d punkter" % (
            calc["navn"], n_points)
        besked += (", %s)." % ekstra) if ekstra else ")."
        self.iface.messageBar().pushSuccess("VASP", besked)

    # --- Afvandingsanalyse ------------------------------------------------

    # Margin omkring vandspejlspunkterne i det foreslåede beregningsområde.
    AFVANDING_MARGIN_M = 500.0

    def run_afvandingsanalyse(self):
        """Afvandingsanalyse ud fra et eller flere vandspejl.

        Brugeren samler vandspejlskilderne i en dialog — beregnede vandspejl
        fra VASP og/eller punktlag med opmålte vandspejl fra projektet — og
        de flettes til ét vandspejl, som analysen køres på. Kildelagene
        gemmes kun midlertidigt og lægges ikke i projektet; kun resultatet
        ender i kortet.
        """
        win = self.iface.mainWindow()
        try:
            calcs = dbaccess.list_vsp_calcs()
        except dbaccess.VaspDbError as exc:
            # Uden databasen kan man stadig regne på egne opmålte vandspejl.
            calcs = []
            self.iface.messageBar().pushWarning(
                "VASP", "Kunne ikke hente vandspejlsberegninger: %s" % exc)

        dialog = AfvandingKilderDialog(calcs, self._vsp_punkter, win)
        if dialog.exec_() != AfvandingKilderDialog.Accepted:
            return
        kilder = dialog.valgte_kilder()
        if not kilder:
            return

        kildelag, advarsler = self._byg_kildelag(kilder)
        if advarsler:
            self.iface.messageBar().pushInfo(
                "VASP — afvandingsanalyse", "  ".join(advarsler))
        if not kildelag:
            QMessageBox.warning(
                win, "VASP — afvandingsanalyse",
                "Ingen af de valgte vandspejl gav punkter med både "
                "koordinater og en kote.")
            return

        samlet = sum(lag.featureCount() for lag in kildelag)
        if samlet < 3:
            QMessageBox.warning(
                win, "VASP — afvandingsanalyse",
                "De valgte vandspejl har tilsammen kun %d punkter med en "
                "kote. Der skal mindst være 3 for at kunne interpolere et "
                "vandspejl." % samlet)
            return

        afstand = self._kilder_spredning(kildelag)
        if afstand is not None and afstand > 3000:
            svar = QMessageBox.question(
                win, "VASP — afvandingsanalyse",
                "De valgte vandspejl ligger op til ca. %.1f km fra "
                "hinanden.\n\nFlettes vandspejl fra forskellige vandløb — "
                "eller et punktlag i et forkert koordinatsystem — bliver den "
                "fælles vandspejlsflade og hele resultatet forkert. "
                "Vandspejlene skal høre til samme vandløbssystem."
                "\n\nFortsæt alligevel?" % (afstand / 1000),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if svar != QMessageBox.Yes:
                return

        try:
            vsp_kilder = self._skriv_kildelag(kildelag)
        except (OSError, RuntimeError) as exc:
            QMessageBox.critical(
                win, "VASP — afvandingsanalyse",
                "Kunne ikke gemme de flettede vandspejl midlertidigt:\n%s"
                % exc)
            return

        parameters = {"VSP": vsp_kilder, "VSP_FIELD": "vsp"}
        extent = self._kildelag_extent(kildelag)
        if extent is not None:
            parameters["EXTENT"] = extent
        self._afvanding_dialog(parameters)

    def _byg_kildelag(self, kilder):
        """Byg et normaliseret PointZ-lag (felt "vsp") pr. valgt kilde.

        Returnerer (lag_liste, advarsler). Kilder uden brugbare punkter
        udelades, og hver udeladelse/frasortering nævnes i advarsler.
        """
        lag_liste, advarsler = [], []
        for kilde in kilder:
            navn = kilde["navn"]
            if kilde["type"] == "vasp":
                brugbare, sprunget = self._brugbare_vsp_punkter(
                    kilde["punkter"])
                if not brugbare:
                    advarsler.append(
                        "«%s» har ingen punkter med både koordinater og en "
                        "vandspejlskote og udelades." % navn)
                    continue
                lag = layer_builder.build_vsp_layer(
                    "Vandspejl: %s" % navn, brugbare,
                    kilde["calc"]["koordsysid"], [("vsp", "vsp")])
                if sprunget:
                    advarsler.append(
                        "%d punkter i «%s» uden koordinater eller kote "
                        "indgår ikke." % (sprunget, navn))
            else:
                lag, sprunget, kun_markerede = self._punktlag_som_vsp(
                    kilde["lag"], kilde["felt"])
                if kun_markerede:
                    advarsler.append(
                        "Kun de markerede punkter i «%s» bruges." % navn)
                if lag is None:
                    advarsler.append(
                        "Punktlaget «%s» har ingen punkter med en talkote i "
                        "«%s» og udelades." % (navn, kilde["felt"]))
                    continue
                if sprunget:
                    advarsler.append(
                        "%d punkter i «%s» uden en kote i «%s» indgår ikke."
                        % (sprunget, navn, kilde["felt"]))
            if not lag.isValid():
                advarsler.append(
                    "Kunne ikke bygge et vandspejlslag ud fra «%s»." % navn)
                continue
            lag_liste.append(lag)
        return lag_liste, advarsler

    def _punktlag_som_vsp(self, src, felt):
        """Kopiér et projekt-punktlag til et PointZ-lag med ét "vsp"-felt.

        Koordinaterne omregnes til EPSG:25832 — som VASP-lagene og DHM'et —
        så sammenfletningen sker i ét koordinatsystem. Punkter uden en
        talværdi i feltet springes over; en kote skrevet som tekst med komma
        læses også.

        Et forespørgselsfilter på laget (subset string) respekteres, fordi
        iterationen kun ser de filtrerede punkter. Er der markerede punkter,
        bruges kun dem — så man kan udpege præcis de opmålte vandspejl der
        skal flettes. Returnerer (lag, antal_sprunget, kun_markerede), eller
        (None, antal_sprunget, kun_markerede) hvis ingen punkter kunne bruges.
        """
        maal = QgsCoordinateReferenceSystem("EPSG:25832")
        xform = None
        if src.crs().isValid() and src.crs() != maal:
            xform = QgsCoordinateTransform(
                src.crs(), maal, QgsProject.instance())

        kun_markerede = src.selectedFeatureCount() > 0
        features = (src.getSelectedFeatures() if kun_markerede
                    else src.getFeatures())
        punkter, sprunget = [], 0
        for feat in features:
            raa = feat[felt] if felt in feat.fields().names() else None
            if isinstance(raa, str):
                raa = raa.replace(",", ".").strip()
            try:
                vaerdi = float(raa)
            except (TypeError, ValueError):
                sprunget += 1
                continue
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                sprunget += 1
                continue
            try:
                if geom.isMultipart():
                    dele = geom.asMultiPoint()
                    pt = dele[0] if dele else None
                else:
                    pt = geom.asPoint()
            except (ValueError, TypeError):
                pt = None
            if pt is None:
                sprunget += 1
                continue
            if xform is not None:
                try:
                    pt = xform.transform(pt)
                except Exception:
                    sprunget += 1
                    continue
            punkter.append({"x": pt.x(), "y": pt.y(), "vsp": vaerdi})

        if not punkter:
            return None, sprunget, kun_markerede
        lag = layer_builder.build_vsp_point_layer(
            "Opmålt vandspejl: %s" % src.name(), punkter, 25832)
        return lag, sprunget, kun_markerede

    @staticmethod
    def _kilder_spredning(kildelag):
        """Største afstand mellem to kildelags midtpunkter i meter.

        Ligger kilderne langt fra hinanden, hører de sandsynligvis ikke til
        samme vandløb — eller ét af punktlagene er i et forkert
        koordinatsystem. Returnerer None hvis der kun er ét brugbart lag.
        """
        midter = [lag.extent().center() for lag in kildelag
                  if not lag.extent().isNull()]
        if len(midter) < 2:
            return None
        maks = 0.0
        for i in range(len(midter)):
            for j in range(i + 1, len(midter)):
                maks = max(maks, ((midter[i].x() - midter[j].x()) ** 2
                                  + (midter[i].y() - midter[j].y()) ** 2)
                           ** 0.5)
        return maks

    def _kildelag_extent(self, kildelag):
        """Samlet udstrækning for kildelagene plus margin — områdeforslag.

        Lagene kan ligge i hvert sit koordinatsystem, så hver udstrækning
        omregnes til EPSG:25832, før de lægges sammen. isNull og ikke
        isEmpty: et vandspejl på en ret linje har ingen bredde, og det er
        stadig en udstrækning.
        """
        maal = QgsCoordinateReferenceSystem("EPSG:25832")
        samlet = None
        for lag in kildelag:
            rect = lag.extent()
            if rect.isNull():
                continue
            if lag.crs().isValid() and lag.crs() != maal:
                try:
                    rect = QgsCoordinateTransform(
                        lag.crs(), maal, QgsProject.instance()
                    ).transformBoundingBox(rect)
                except Exception:
                    continue
            if samlet is None:
                samlet = QgsRectangle(rect)
            else:
                samlet.combineExtentWith(rect)
        if samlet is None:
            return None
        samlet.grow(self.AFVANDING_MARGIN_M)
        return QgsReferencedRectangle(samlet, maal)

    def _skriv_kildelag(self, kildelag):
        """Skriv kildelagene til midlertidige GeoPackage-filer.

        Memory-lag, der ikke ligger i projektet, er ikke altid til at nå for
        Processing-dialogen; en fil på disken er det altid. Filerne ligger i
        Processings temp-mappe og ryddes med QGIS. Returnerer listen af
        lag-kilder (stier med |layername=), som algoritmen fletter sammen.
        """
        ctx = QgsProject.instance().transformContext()
        stier = []
        for i, lag in enumerate(kildelag, 1):
            sti = QgsProcessingUtils.generateTempFilename(
                "afvanding_vsp_%d.gpkg" % i)
            muligheder = QgsVectorFileWriter.SaveVectorOptions()
            muligheder.driverName = "GPKG"
            muligheder.layerName = "vandspejl"
            resultat = QgsVectorFileWriter.writeAsVectorFormatV3(
                lag, sti, ctx, muligheder)
            if resultat[0] != QgsVectorFileWriter.NoError:
                raise RuntimeError(
                    resultat[1] if len(resultat) > 1 else "ukendt fejl")
            stier.append("%s|layername=vandspejl" % sti)
        return stier

    def _vsp_punkter(self, calc):
        """Hent punkterne til analysen: (punkter, scenarienavn).

        Returnerer (None, "") hvis filen ikke kunne læses, eller hvis
        brugeren fortrød scenarievalget.
        """
        win = self.iface.mainWindow()
        path = config.ber_path(
            calc["projektid"], calc["berid"], multi=calc["multi"])
        try:
            if calc["multi"]:
                scenarier = ber.decode_multi(path)
            else:
                return ber.decode_simpel(path), ""
        except (OSError, ValueError) as exc:
            self._vsp_read_error(path, exc)
            return None, ""

        if not scenarier:
            QMessageBox.information(
                win, "VASP", "Beregningen indeholdt ingen scenarier.")
            return None, ""
        dialog = ScenarieDialog(scenarier, calc["navn"], win)
        if dialog.exec_() != ScenarieDialog.Accepted:
            return None, ""
        i = dialog.selected_index()
        if i is None:
            return None, ""
        return scenarier[i]["points"], (scenarier[i].get("navn") or "").strip()

    @staticmethod
    def _brugbare_vsp_punkter(punkter):
        """Frasortér punkter der ikke kan interpoleres imellem.

        Punkter uden vandspejlskote ville blive læst som kote 0 af
        interpolationen, og et punkt i (0, 0) ville trække beregningsområdet
        hen til nulpunktet. Returnerer (brugbare, antal frasorterede).
        """
        brugbare = []
        for p in punkter or []:
            x, y, vsp = p.get("x"), p.get("y"), p.get("vsp")
            if x is None or y is None or vsp is None:
                continue
            if not x and not y:
                continue
            brugbare.append(p)
        return brugbare, len(punkter or []) - len(brugbare)

    def _afvanding_dialog(self, parameters):
        """Åbn Processing-dialogen for afvandingsanalysen."""
        def lav():
            from .afvandingsanalyse import AfvandingsanalyseAlgorithm
            return AfvandingsanalyseAlgorithm()

        self._processing_dialog(
            lav, parameters, "VASP — afvandingsanalyse",
            "VaspAfvandingsanalyseDialog")

    def run_oplande_til_vasp(self):
        """Skriv udpegede oplande ind som en ny serie i et VASP-datasæt.

        Oplandene kommer fra et lag i projektet — typisk fra "Udpeg
        oplande" — og stationeres på det længdeprofil, brugeren vælger.
        Rækkerne vises i dialogen, inden der skrives, og skrivningen går i
        selve .hds-filen, hvor VASP har sine hydrauliske parametre.
        """
        win = self.iface.mainWindow()
        profiles = self._profiles_or_warn()
        if profiles is None:
            return
        dialog = OplandeDialog(profiles, self._centerline_for, parent=win)
        if dialog.exec_() != OplandeDialog.Accepted:
            return
        valg = dialog.valg()
        datasaet = valg["datasaet"]
        if not datasaet or not valg["raekker"]:
            return
        if not self._bekraeft_oplande(valg, datasaet):
            return
        try:
            svar = hds.tilfoej_serie(
                datasaet["sti"], hds.OPLANDE, valg["navn"], valg["raekker"],
                initialer=valg["initialer"],
                bemaerkning=valg["bemaerkning"],
                backup_mappe=config.BACKUP_DIR)
        except hds.HdsFejl as exc:
            QMessageBox.critical(win, "VASP — kunne ikke skrive", str(exc))
            return
        QMessageBox.information(
            win, "VASP",
            "Serien «%s» er skrevet som nr. %d under Oplande i «%s».\n\n"
            "%d rækker. Åbn VASP og hent datasættet frem for at se den.\n\n"
            "Kopi af datasættet før ændringen: %s"
            % (svar["navn"], svar["nr"], datasaet["navn"], svar["raekker"],
               svar["backup"] or "(ingen)"))

    def _bekraeft_oplande(self, valg, datasaet):
        """Sidste kvittering, inden der skrives i VASP's egen fil."""
        noter = ""
        if valg["noter"]:
            noter = "\n\nBemærk:\n" + "\n".join(
                "• " + n for n in dict.fromkeys(valg["noter"]))
        svar = QMessageBox.question(
            self.iface.mainWindow(), "Skriv til VASP",
            "Serien «%s» skrives med %d rækker ind i datasættet «%s» "
            "(HYD%d).\n\nDer tages en kopi af filen først, og "
            "eksisterende serier bliver ikke rørt.%s"
            % (valg["navn"], len(valg["raekker"]), datasaet["navn"],
               datasaet["hydatid"], noter),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes)
        return svar == QMessageBox.Yes

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

        # Byg-scriptet kan tage flere minutter (Access-dump + GeoPackage-bygning).
        # Kør det i en baggrundstråd, så en flydende "arbejder"-dialog viser at
        # der sker noget, uden at UI'en fryser.
        progress = QProgressDialog(
            "Bygger datafil fra VASP-databasen …\n"
            "Dette kan tage nogle minutter.", None, 0, 0, win)
        progress.setWindowTitle("VASP")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setCancelButton(None)   # kan ikke annulleres midt i byg
        progress.show()

        worker = _BuildWorker(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", script, "-Mdb", db_path])
        loop = QEventLoop()
        outcome = {}

        def _on_done(code, tail):
            outcome["code"] = code
            outcome["tail"] = tail
            loop.quit()

        worker.done.connect(_on_done)
        worker.start()
        loop.exec_()          # holder UI'en i live indtil tråden er færdig
        worker.wait()
        progress.close()

        code = outcome.get("code", -1)
        tail = outcome.get("tail", "")
        if code == -1 and not os.path.exists(config.DEFAULT_GPKG_PATH):
            QMessageBox.critical(
                win, "VASP", "Kunne ikke starte opdateringen:\n%s" % tail)
            return False
        if code != 0:
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

    def _load_profiles(self, profs):
        """Importer flere længdeprofiler i én omgang.

        Ét lag pr. profil, samlet i en gruppe i lagpanelet. Et profil, der
        ikke kan læses, stopper ikke de andre — de sprungne samles i én
        besked til sidst, i stedet for en fejlboks pr. profil.
        """
        win = self.iface.mainWindow()
        fremdrift = QProgressDialog(
            "Henter længdeprofiler fra VASP …", "Afbryd", 0, len(profs), win)
        fremdrift.setWindowTitle("VASP — importerer")
        fremdrift.setWindowModality(Qt.WindowModal)
        fremdrift.setMinimumDuration(0)

        lag, sprunget, punkter_i_alt = [], [], 0
        for nr, prof in enumerate(profs):
            if fremdrift.wasCanceled():
                break
            fremdrift.setValue(nr)
            fremdrift.setLabelText(
                "Henter %d af %d: %s" % (nr + 1, len(profs), prof["navn"]))
            try:
                points = dbaccess.read_profile_points(prof["lgdid"])
            except dbaccess.VaspDbError as exc:
                sprunget.append("%s — %s" % (prof["navn"], exc))
                continue
            if not points:
                sprunget.append(
                    "%s — ingen geokodede punkter" % prof["navn"])
                continue
            laget = layer_builder.build_profile_layer(
                "VASP længdeprofil: %s" % prof["navn"], points,
                prof["koordsysid"])
            if not laget.isValid():
                sprunget.append("%s — laget kunne ikke oprettes" % prof["navn"])
                continue
            lag.append(laget)
            punkter_i_alt += len(points)
        fremdrift.setValue(len(profs))

        if not lag:
            QMessageBox.information(
                win, "VASP",
                "Ingen af de valgte længdeprofiler kunne hentes.\n\n"
                + "\n".join(sprunget[:10]))
            return

        self._tilfoej_i_gruppe(lag, "VASP længdeprofiler")
        self._zoom_til(lag)
        self.iface.messageBar().pushSuccess(
            "VASP", "Indlæste %d længdeprofiler med %d terrænpunkter i alt."
            % (len(lag), punkter_i_alt))
        if sprunget:
            QMessageBox.information(
                win, "VASP — nogle profiler kom ikke med",
                "%d af %d profiler blev sprunget over:\n\n%s"
                % (len(sprunget), len(profs), "\n".join(sprunget[:15]))
                + ("\n… og %d mere." % (len(sprunget) - 15)
                   if len(sprunget) > 15 else ""))

    def _tilfoej_i_gruppe(self, lag, gruppenavn):
        """Læg lagene i deres egen gruppe øverst i lagpanelet.

        Gruppen får et løbenummer, hvis den findes i forvejen, så to
        importer ikke bliver blandet sammen.
        """
        rod = QgsProject.instance().layerTreeRoot()
        navn, nr = gruppenavn, 1
        while rod.findGroup(navn) is not None:
            nr += 1
            navn = "%s (%d)" % (gruppenavn, nr)
        gruppe = rod.insertGroup(0, navn)
        for laget in lag:
            # addToLegend=False: laget skal i gruppen, ikke i toppen af
            # lagpanelet ved siden af den.
            QgsProject.instance().addMapLayer(laget, addToLegend=False)
            gruppe.addLayer(laget)

    #: Luft omkring et profil, hvis punkterne ligger på en ret linje.
    ZOOM_MARGIN_M = 50.0

    def _zoom_til(self, lag):
        """Zoom kortet ud til alle de nye lag, i projektets CRS."""
        projekt = QgsProject.instance()
        maal = projekt.crs()
        samlet = None
        for laget in lag:
            udstraekning = laget.extent()
            # isNull, ikke isEmpty: et profil, der løber lige nord-syd, har
            # ingen bredde, og isEmpty ville kalde det tomt og springe det
            # over. Luften nedenfor tager sig af den slags.
            if udstraekning.isNull():
                continue
            if laget.crs().isValid() and maal.isValid() and laget.crs() != maal:
                try:
                    udstraekning = QgsCoordinateTransform(
                        laget.crs(), maal, projekt).transformBoundingBox(
                            udstraekning)
                except Exception:
                    continue
            if samlet is None:
                samlet = QgsRectangle(udstraekning)
            else:
                samlet.combineExtentWith(udstraekning)
        if samlet is None:
            return
        # Et profil med punkterne på en ret linje har hverken højde eller
        # bredde. Zoomes der til det, ser man ingenting.
        if samlet.width() <= 0 or samlet.height() <= 0:
            samlet.grow(self.ZOOM_MARGIN_M)
        samlet.scale(1.05)
        self.iface.mapCanvas().setExtent(samlet)
        self.iface.mapCanvas().refresh()

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
