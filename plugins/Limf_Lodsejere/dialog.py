import os

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QProgressBar, QCheckBox,
    QDoubleSpinBox, QSpinBox, QMessageBox, QApplication
)
from qgis.PyQt.QtCore import QSettings, Qt, QUrl, QVariant
from qgis.PyQt.QtGui import QDesktopServices
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsField, QgsFields, QgsCoordinateReferenceSystem, QgsCoordinateTransform
)

from . import faelles_ui, filtrering
from .api import AdgangAfvist, DatafordelerClient


class LodsejerDialog(QDialog):
    def __init__(self, iface, geometry, source_crs=None):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.geometry = geometry
        self._source_crs = source_crs
        #: Tekst om hvad der blev sorteret fra, til beskeden bagefter.
        self._frasorteret = ''
        self.setWindowTitle('Hent Lodsejere')
        self.setMinimumWidth(500)
        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- Adgang til Datafordeleren ----------------------------------
        adgang, adgang_l = faelles_ui.afsnit('Adgang til Datafordeleren')
        vejledning = self._vejledningslink()
        if vejledning is not None:
            adgang_l.addWidget(vejledning)
        adgang_l.addWidget(QLabel('Matriklen API-nøgle:'))
        self.wfs_apikey_edit = QLineEdit()
        self.wfs_apikey_edit.setEchoMode(QLineEdit.Password)
        adgang_l.addWidget(self.wfs_apikey_edit)

        adgang_l.addWidget(QLabel('EJF Client ID:'))
        self.client_id_edit = QLineEdit()
        self.client_id_edit.setPlaceholderText(
            'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx')
        adgang_l.addWidget(self.client_id_edit)

        adgang_l.addWidget(QLabel('EJF Shared Secret:'))
        self.secret_edit = QLineEdit()
        self.secret_edit.setEchoMode(QLineEdit.Password)
        adgang_l.addWidget(self.secret_edit)

        vis_layout = QHBoxLayout()
        vis_layout.addStretch()
        self.vis_secret_cb = QCheckBox('Vis adgangskoder')
        self.vis_secret_cb.toggled.connect(self._toggle_secret)
        vis_layout.addWidget(self.vis_secret_cb)
        adgang_l.addLayout(vis_layout)
        layout.addWidget(adgang)

        # --- Hvilke matrikler ------------------------------------------
        layout.addWidget(self._afgraensningsboks())

        # --- Hvad der hentes --------------------------------------------
        udtraek, udtraek_l = faelles_ui.afsnit('Udtræk')
        self.only_companies_cb = QCheckBox(
            'Vis kun virksomhedsejere (CVR) — private ejere vises som '
            '"Privat ejer"'
        )
        self.only_companies_cb.setChecked(False)
        udtraek_l.addWidget(self.only_companies_cb)

        # CPR-numre er fortrolige og kræver en adgang, de fleste ikke har.
        # Derfor altid fra, når dialogen åbnes — valget huskes ikke.
        self.cpr_cb = QCheckBox(
            'Hent CPR-numre (kræver adgang til Ejerfortegnelsen Fortrolig)')
        self.cpr_cb.setChecked(False)
        self.cpr_cb.setToolTip(
            'CPR-numrene hentes fra Ejerfortegnelsen Fortrolig (entiteten '
            'EJF_Ejerskab).\nDen adgang gives kun til offentlige myndigheder '
            'og skal være godkendt under Dataadgang i Datafordeler '
            'Administration.')
        udtraek_l.addWidget(self.cpr_cb)
        self.cpr_note = QLabel(
            'Laget får en kolonne med CPR-numre. De er fortrolige: del ikke '
            'laget, og brug det ikke i atlas eller udsendelser uden at fjerne '
            'kolonnen.')
        self.cpr_note.setWordWrap(True)
        self.cpr_note.setStyleSheet('color: #a33;')
        self.cpr_note.setVisible(False)
        udtraek_l.addWidget(self.cpr_note)
        self.cpr_cb.toggled.connect(self.cpr_note.setVisible)
        # Uden private ejere er der ingen CPR-numre at hente.
        self.only_companies_cb.toggled.connect(self._kun_virksomheder_skiftet)
        layout.addWidget(udtraek)

        # --- Fremdrift ---------------------------------------------------
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status_label = QLabel('')
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        layout.addStretch(1)

        # --- Bundrække ----------------------------------------------------
        self.run_btn = faelles_ui.knap(
            'Hent lodsejere', self._run, primaer=True,
            tip='Henter matrikler og ejeroplysninger for det valgte polygon')
        luk_btn = faelles_ui.knap('Luk', self.reject)
        layout.addLayout(faelles_ui.bundraekke(self.run_btn, luk_btn))

        faelles_ui.anvend_stil(self)

    def _afgraensningsboks(self):
        """Hvilke matrikler der kommer med — og hvilke der sorteres fra."""
        boks, ind = faelles_ui.afsnit('Afgrænsning')

        raekke = QHBoxLayout()
        raekke.addWidget(QLabel('Udvid området med:'))
        self.buffer_spin = QDoubleSpinBox()
        self.buffer_spin.setRange(0, 2000)
        self.buffer_spin.setDecimals(0)
        self.buffer_spin.setSingleStep(10)
        self.buffer_spin.setSuffix(' m')
        self.buffer_spin.setToolTip(
            'Lægger en bræmme uden om det valgte polygon, så naboer kommer '
            'med. 0 m tager kun de matrikler, projektområdet selv ligger på.')
        raekke.addWidget(self.buffer_spin)
        raekke.addStretch(1)
        ind.addLayout(raekke)

        note = QLabel(
            'Med 0 m kommer kun de matrikler med, som området overlapper. '
            'Naboer, der kun deler skel, kommer ikke med.')
        note.setWordWrap(True)
        note.setStyleSheet('color: gray;')
        ind.addWidget(note)

        self.udelad_vej_cb = QCheckBox('Udelad vej- og jernbanematrikler')
        self.udelad_vej_cb.setChecked(True)
        self.udelad_vej_cb.setToolTip(
            'Udskilte veje kendes på, at hele arealet er vejareal — det '
            'gælder både vejlitra i 7000-serien og byveje med almindeligt '
            'matrikelnummer. Banearealer kendes på arealtypen.')
        ind.addWidget(self.udelad_vej_cb)

        raekke = QHBoxLayout()
        self.mindste_areal_cb = QCheckBox('Udelad matrikler under')
        self.mindste_areal_cb.setToolTip(
            'Matriklen oplyser ikke, hvad der står på en matrikel, så der '
            'er ingen boligtype at gå efter. Byhuse og sommerhuse ligger '
            'til gengæld på små grunde, så en nedre arealgrænse rammer dem.')
        raekke.addWidget(self.mindste_areal_cb)
        self.mindste_areal_spin = QSpinBox()
        self.mindste_areal_spin.setRange(1, 1000000)
        self.mindste_areal_spin.setSingleStep(500)
        self.mindste_areal_spin.setValue(1500)
        self.mindste_areal_spin.setSuffix(' m²')
        self.mindste_areal_spin.setEnabled(False)
        self.mindste_areal_cb.toggled.connect(self.mindste_areal_spin.setEnabled)
        raekke.addWidget(self.mindste_areal_spin)
        raekke.addStretch(1)
        ind.addLayout(raekke)
        return boks

    # Vejledningerne ligger i pluginmappen: Word-dokumentet med trin 1-7 og
    # skærmbilleder, og tillægget med felterne, entiteterne og fejlsøgningen.
    VEJLEDNINGER = [
        ('docx', 'Dokumentation_datafordeler.docx',
         'Sådan opretter du adgang'),
        ('md', 'Dokumentation_datafordeler.md',
         'Felter, entiteter og fejlsøgning'),
    ]

    def _vejledningslink(self):
        """Linje med links til vejledningerne, eller None hvis ingen findes."""
        mappe = os.path.dirname(__file__)
        dele = []
        for noegle, filnavn, tekst in self.VEJLEDNINGER:
            if os.path.exists(os.path.join(mappe, filnavn)):
                dele.append('<a href="%s">%s</a>' % (noegle, tekst))
        if not dele:
            return None
        label = QLabel('Mangler du nøgler? ' + ' &nbsp;·&nbsp; '.join(dele))
        label.setWordWrap(True)
        label.setStyleSheet('color: gray;')
        label.setToolTip('Åbner vejledningen fra pluginmappen')
        label.linkActivated.connect(self._aabn_vejledning)
        return label

    def _aabn_vejledning(self, noegle):
        """Åbn en vejledning i systemets standardprogram."""
        filnavn = dict((n, f) for n, f, _ in self.VEJLEDNINGER).get(noegle)
        sti = os.path.join(os.path.dirname(__file__), filnavn or '')
        if not filnavn or not os.path.exists(sti):
            QMessageBox.information(
                self, 'Lodsejere',
                'Vejledningen blev ikke fundet:\n %s' % sti)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(sti)):
            QMessageBox.information(
                self, 'Lodsejere',
                'Kunne ikke åbne vejledningen. Den ligger her:\n %s' % sti)

    def _kun_virksomheder_skiftet(self, kun_virksomheder):
        if kun_virksomheder:
            self.cpr_cb.setChecked(False)
        self.cpr_cb.setEnabled(not kun_virksomheder)

    def _toggle_secret(self, checked):
        mode = QLineEdit.Normal if checked else QLineEdit.Password
        self.wfs_apikey_edit.setEchoMode(mode)
        self.secret_edit.setEchoMode(mode)

    def _load_settings(self):
        s = QSettings()
        self.wfs_apikey_edit.setText(s.value('lodsejere/wfs_apikey', ''))
        self.client_id_edit.setText(s.value('lodsejere/client_id', ''))
        self.secret_edit.setText(s.value('lodsejere/client_secret', ''))
        # Afgrænsningen huskes, så den ikke skal sættes forfra hver gang.
        self.buffer_spin.setValue(
            float(s.value('lodsejere/buffer_m', 0) or 0))
        self.udelad_vej_cb.setChecked(
            s.value('lodsejere/udelad_vej', True, type=bool))
        self.mindste_areal_cb.setChecked(
            s.value('lodsejere/mindste_areal_til', False, type=bool))
        self.mindste_areal_spin.setValue(
            int(s.value('lodsejere/mindste_areal_m2', 1500) or 1500))

    def _save_settings(self):
        s = QSettings()
        s.setValue('lodsejere/wfs_apikey', self.wfs_apikey_edit.text().strip())
        s.setValue('lodsejere/client_id', self.client_id_edit.text().strip())
        s.setValue('lodsejere/client_secret', self.secret_edit.text().strip())
        s.setValue('lodsejere/buffer_m', self.buffer_spin.value())
        s.setValue('lodsejere/udelad_vej', self.udelad_vej_cb.isChecked())
        s.setValue('lodsejere/mindste_areal_til',
                   self.mindste_areal_cb.isChecked())
        s.setValue('lodsejere/mindste_areal_m2',
                   self.mindste_areal_spin.value())

    def _mindste_areal(self):
        """Nedre arealgrænse i m², eller 0 når der ikke er sat nogen."""
        if not self.mindste_areal_cb.isChecked():
            return 0.0
        return float(self.mindste_areal_spin.value())

    def _omraade(self, geom_25832):
        """Projektområdet med den valgte bræmme lagt uden om."""
        bredde = self.buffer_spin.value()
        if bredde <= 0:
            return geom_25832
        udvidet = geom_25832.buffer(bredde, 8)
        # Slår bufferen fejl (fx ved en ugyldig geometri), bruges polygonet
        # som det er, frem for at hente hele rektanglet igen.
        return geom_25832 if udvidet.isEmpty() else udvidet

    def _run(self):
        wfs_apikey = self.wfs_apikey_edit.text().strip()
        client_id  = self.client_id_edit.text().strip()
        secret     = self.secret_edit.text().strip()

        if not wfs_apikey:
            QMessageBox.warning(self, 'Lodsejere', 'Udfyld Matriklen API-nøgle.')
            return
        if not client_id or not secret:
            QMessageBox.warning(self, 'Lodsejere', 'Udfyld EJF Client ID og Shared Secret.')
            return

        self._save_settings()
        self.run_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)

        try:
            client = DatafordelerClient(client_id, secret, wfs_apikey)

            # Test token med det samme — fejler hurtigt hvis credentials er forkerte
            self.status_label.setText('Henter OAuth-token...')
            QApplication.processEvents()
            client._get_token()

            self.status_label.setText('Læser matrikler...')
            QApplication.processEvents()

            geom_25832 = self._to_epsg25832(self.geometry)
            omraade = self._omraade(geom_25832)
            fundne = client.get_jordstykker(omraade)

            if not fundne:
                QMessageBox.information(
                    self, 'Lodsejere', 'Ingen matrikler fundet i det valgte område.'
                )
                return

            # Matriklen svarer på et rektangel, så svaret skæres til her —
            # før ejeropslagene, der tager ét kald pr. matrikel.
            jordstykker, grunde = filtrering.frasorter(
                fundne, omraade,
                udelad_vej_og_bane=self.udelad_vej_cb.isChecked(),
                mindste_areal_m2=self._mindste_areal())
            self._frasorteret = filtrering.forklar(grunde)

            if not jordstykker:
                QMessageBox.information(
                    self, 'Lodsejere',
                    'Alle %d matrikler i rektanglet omkring området blev '
                    'sorteret fra.\n\n%s\n\nPrøv at udvide området eller '
                    'slå en af frasorteringerne fra.'
                    % (len(fundne), self._frasorteret))
                return

            self.progress.setRange(0, len(jordstykker))
            only_companies = self.only_companies_cb.isChecked()
            med_cpr = self.cpr_cb.isChecked() and not only_companies
            results = []
            errors = []

            for i, js in enumerate(jordstykker):
                self.status_label.setText(
                    f'Henter ejeroplysninger... {i + 1}/{len(jordstykker)}'
                )
                self.progress.setValue(i + 1)
                QApplication.processEvents()

                try:
                    ejer = client.get_ejer(js.get('bfe_nummer', ''),
                                           only_companies=only_companies,
                                           med_cpr=med_cpr)
                except AdgangAfvist as e:
                    if med_cpr:
                        # Uden adgangen fejler hver eneste matrikel — og et
                        # lag uden ejere er ikke det, der blev bedt om.
                        raise
                    errors.append(str(e))
                    ejer = {}
                except Exception as e:
                    errors.append(str(e))
                    ejer = {}

                results.append({**js, **ejer})

            self.status_label.setText('Opretter lag...')
            self._create_layer(results, med_cpr)

            if errors:
                # Ens fejl tælles sammen: 8 gange samme afviste adgang er ét
                # problem, ikke otte. Er der flere slags, nævnes antallet.
                unikke = list(dict.fromkeys(errors))
                tekst = (f'Laget er oprettet, men ejeroplysninger manglede '
                         f'for {len(errors)} af {len(jordstykker)} '
                         f'matrikler.\n\n{unikke[0]}')
                if len(unikke) > 1:
                    tekst += f'\n\n(+ {len(unikke) - 1} andre fejltyper)'
                QMessageBox.warning(self, 'Lodsejere', tekst)

        except Exception as e:
            QMessageBox.critical(self, 'Fejl', f'Fejl under datahentning:\n{str(e)}')
        finally:
            self.run_btn.setEnabled(True)
            self.progress.setVisible(False)
            self.status_label.setText('')

    def _to_epsg25832(self, geometry):
        source_crs = self._source_crs or self.iface.activeLayer().crs()
        target_crs = QgsCoordinateReferenceSystem('EPSG:25832')
        if source_crs == target_crs:
            return geometry
        transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
        geom = QgsGeometry(geometry)
        geom.transform(transform)
        return geom

    def _create_layer(self, results, med_cpr=False):
        # Navnet siger det, så laget ikke deles ved en fejl.
        navn = 'Lodsejere – med CPR (fortroligt)' if med_cpr else 'Lodsejere'
        layer = QgsVectorLayer('Polygon?crs=EPSG:25832', navn, 'memory')
        provider = layer.dataProvider()

        felter = [
            ('ejerlavskode',       QVariant.Int),
            ('ejerlavsnavn',       QVariant.String),
            ('matrikelnummer',     QVariant.String),
            ('bfe_nummer',         QVariant.String),
            # Areal, vejareal og arealtype følger med, så man selv kan
            # sortere videre i laget bagefter.
            ('areal_m2',           QVariant.Double),
            ('vejareal_m2',        QVariant.Double),
            ('arealtype',          QVariant.String),
            ('ejernavn',           QVariant.String),
            ('ejeradresse',        QVariant.String),
            ('postnr',             QVariant.String),
            ('postby',             QVariant.String),
            ('ejerforhold',        QVariant.String),
            ('ejerforhold_tekst',  QVariant.String),
            ('cvr_nummer',         QVariant.String),
        ]
        # Kun når der er bedt om det — ellers ville en tom CPR-kolonne
        # følge med alle lag.
        if med_cpr:
            felter.append(('cpr_nummer', QVariant.String))
        felter.append(('adressebeskyttelse', QVariant.String))

        fields = QgsFields()
        for name, typ in felter:
            fields.append(QgsField(name, typ))

        provider.addAttributes(fields)
        layer.updateFields()

        features = []
        for r in results:
            feat = QgsFeature()
            feat.setGeometry(QgsGeometry.fromWkt(r.get('geometri_wkt', '')))
            vaerdier = {
                'ejerlavskode': r.get('ejerlavskode'),
                'bfe_nummer': str(r.get('bfe_nummer', '')),
                'areal_m2': filtrering.tal(r.get('registreret_areal')),
                'vejareal_m2': filtrering.tal(r.get('vejareal')),
            }
            feat.setAttributes([
                vaerdier[name] if name in vaerdier else r.get(name, '')
                for name, _typ in felter
            ])
            features.append(feat)

        provider.addFeatures(features)
        layer.updateExtents()
        QgsProject.instance().addMapLayer(layer)
        besked = f'Lag oprettet med {len(features)} matrikler.'
        if self._frasorteret:
            besked += ' ' + self._frasorteret
        self.iface.messageBar().pushSuccess('Lodsejere', besked)
        self.accept()
