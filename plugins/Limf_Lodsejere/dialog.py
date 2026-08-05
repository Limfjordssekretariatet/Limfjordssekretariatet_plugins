from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QProgressBar, QCheckBox,
    QMessageBox, QApplication
)
from qgis.PyQt.QtCore import QSettings, Qt, QVariant
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsField, QgsFields, QgsCoordinateReferenceSystem, QgsCoordinateTransform
)

from . import faelles_ui
from .api import DatafordelerClient


class LodsejerDialog(QDialog):
    def __init__(self, iface, geometry, source_crs=None):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.geometry = geometry
        self._source_crs = source_crs
        self.setWindowTitle('Hent Lodsejere')
        self.setMinimumWidth(500)
        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- Adgang til Datafordeleren ----------------------------------
        adgang, adgang_l = faelles_ui.afsnit('Adgang til Datafordeleren')
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

        # --- Hvad der hentes --------------------------------------------
        udtraek, udtraek_l = faelles_ui.afsnit('Udtræk')
        self.only_companies_cb = QCheckBox(
            'Vis kun virksomhedsejere (CVR) — private ejere vises som '
            '"Privat ejer"'
        )
        self.only_companies_cb.setChecked(False)
        udtraek_l.addWidget(self.only_companies_cb)
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

    def _toggle_secret(self, checked):
        mode = QLineEdit.Normal if checked else QLineEdit.Password
        self.wfs_apikey_edit.setEchoMode(mode)
        self.secret_edit.setEchoMode(mode)

    def _load_settings(self):
        s = QSettings()
        self.wfs_apikey_edit.setText(s.value('lodsejere/wfs_apikey', ''))
        self.client_id_edit.setText(s.value('lodsejere/client_id', ''))
        self.secret_edit.setText(s.value('lodsejere/client_secret', ''))

    def _save_settings(self):
        s = QSettings()
        s.setValue('lodsejere/wfs_apikey', self.wfs_apikey_edit.text().strip())
        s.setValue('lodsejere/client_id', self.client_id_edit.text().strip())
        s.setValue('lodsejere/client_secret', self.secret_edit.text().strip())

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
            jordstykker = client.get_jordstykker(geom_25832)

            if not jordstykker:
                QMessageBox.information(
                    self, 'Lodsejere', 'Ingen matrikler fundet i det valgte område.'
                )
                return

            self.progress.setRange(0, len(jordstykker))
            only_companies = self.only_companies_cb.isChecked()
            results = []
            errors = []

            for i, js in enumerate(jordstykker):
                self.status_label.setText(
                    f'Henter ejeroplysninger... {i + 1}/{len(jordstykker)}'
                )
                self.progress.setValue(i + 1)
                QApplication.processEvents()

                try:
                    ejer = client.get_ejer(js.get('bfe_nummer', ''), only_companies=only_companies)
                except Exception as e:
                    errors.append(str(e))
                    ejer = {}

                results.append({**js, **ejer})

            self.status_label.setText('Opretter lag...')
            self._create_layer(results)

            if errors:
                QMessageBox.warning(
                    self, 'Lodsejere',
                    f'Lag oprettet.\n\nFørste fejl ({len(errors)} i alt):\n{errors[0]}'
                )

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

    def _create_layer(self, results):
        layer = QgsVectorLayer('Polygon?crs=EPSG:25832', 'Lodsejere', 'memory')
        provider = layer.dataProvider()

        fields = QgsFields()
        for name, typ in [
            ('ejerlavskode',       QVariant.Int),
            ('ejerlavsnavn',       QVariant.String),
            ('matrikelnummer',     QVariant.String),
            ('bfe_nummer',         QVariant.String),
            ('ejernavn',           QVariant.String),
            ('ejeradresse',        QVariant.String),
            ('ejerforhold',        QVariant.String),
            ('ejerforhold_tekst',  QVariant.String),
            ('cvr_nummer',         QVariant.String),
            ('adressebeskyttelse', QVariant.String),
        ]:
            fields.append(QgsField(name, typ))

        provider.addAttributes(fields)
        layer.updateFields()

        features = []
        for r in results:
            feat = QgsFeature()
            feat.setGeometry(QgsGeometry.fromWkt(r.get('geometri_wkt', '')))
            feat.setAttributes([
                r.get('ejerlavskode'),
                r.get('ejerlavsnavn', ''),
                r.get('matrikelnummer', ''),
                str(r.get('bfe_nummer', '')),
                r.get('ejernavn', ''),
                r.get('ejeradresse', ''),
                r.get('ejerforhold', ''),
                r.get('ejerforhold_tekst', ''),
                r.get('cvr_nummer', ''),
                r.get('adressebeskyttelse', ''),
            ])
            features.append(feat)

        provider.addFeatures(features)
        layer.updateExtents()
        QgsProject.instance().addMapLayer(layer)
        self.iface.messageBar().pushSuccess(
            'Lodsejere', f'Lag oprettet med {len(features)} matrikler.'
        )
        self.accept()
