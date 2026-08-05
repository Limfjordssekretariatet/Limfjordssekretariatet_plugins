import os
import base64
from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtWidgets import QMessageBox, QFileDialog
from qgis.PyQt.QtGui import QTextDocument
from qgis.PyQt.QtCore import QSizeF
from qgis.PyQt.QtPrintSupport import QPrinter
from qgis.core import (
    QgsProject, QgsWkbTypes,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsCoordinateTransformContext,
)

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'lav_pdf_dialog.ui'))

UTM_CRS = QgsCoordinateReferenceSystem('EPSG:25832')


def _img_tag(path, max_width=220):
    """Returnerer en base64-indlejret <img>-tag, eller tom streng hvis filen ikke findes."""
    if not path or not os.path.isfile(str(path)):
        return '<i style="color:#aaa">Intet foto</i>'
    try:
        with open(path, 'rb') as f:
            data = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(path)[1].lower().lstrip('.')
        mime = 'jpeg' if ext in ('jpg', 'jpeg') else ext
        return f'<img src="data:image/{mime};base64,{data}" width="{max_width}">'
    except Exception:
        return '<i style="color:#aaa">Foto kunne ikke indlæses</i>'


def _val(feat, name):
    """Returnerer feltværdi som streng, eller tom streng."""
    try:
        v = feat[name]
        return str(v) if v is not None and str(v) not in ('NULL', 'None', '') else ''
    except Exception:
        return ''


def _build_html(layer):
    src_crs = layer.crs()
    transform = QgsCoordinateTransform(src_crs, UTM_CRS, QgsCoordinateTransformContext())
    need_transform = src_crs != UTM_CRS
    is_polygon = layer.geometryType() == QgsWkbTypes.PolygonGeometry

    rows = []
    for feat in layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isNull() or geom.isEmpty():
            continue

        # Koordinater
        pt = geom.centroid().asPoint() if is_polygon else geom.asPoint()
        if need_transform:
            pt = transform.transform(pt)
        coord_html = f'32V<br>{int(pt.x())} {int(int(pt.y()))}<br>UTM'

        # ID / navn
        feat_id = _val(feat, 'ID') or str(feat.id())

        # Lagbeskrivelser
        lag_rows = ''
        for i in range(1, 5):
            dybde = _val(feat, f'lag {i}')
            jordtype = _val(feat, f'lag {i} type')
            if dybde or jordtype:
                lag_rows += f'''
                <tr>
                  <td style="white-space:nowrap;padding-right:8px;vertical-align:top;">
                    {dybde + ' cm' if dybde else ''}
                  </td>
                  <td style="vertical-align:top;">{jordtype}</td>
                </tr>'''

        comment = _val(feat, 'comment')
        if comment:
            lag_rows += f'''
            <tr>
              <td colspan="2" style="padding-top:6px;font-style:italic;color:#555;">
                {comment}
              </td>
            </tr>'''

        # Status
        status = _val(feat, 'status')
        status_color = '#2ecc71' if status == 'Udtaget' else '#e74c3c'
        status_html = (
            f'<span style="background:{status_color};color:white;'
            f'padding:1px 5px;border-radius:3px;font-size:8pt;">{status}</span>'
            if status else ''
        )

        # Foto
        foto_html = _img_tag(_val(feat, 'Foto'))

        rows.append(f'''
        <tr>
          <td style="width:22%;vertical-align:top;padding:8px;border:1px solid #555;">
            <b>Område {feat_id}</b><br>
            <span style="font-size:8pt;">{coord_html}</span><br><br>
            {status_html}
          </td>
          <td style="width:43%;vertical-align:top;padding:8px;border:1px solid #555;">
            <table style="width:100%;border:none;">
              {lag_rows}
            </table>
          </td>
          <td style="width:35%;vertical-align:top;padding:4px;border:1px solid #555;text-align:center;">
            {foto_html}
          </td>
        </tr>''')

    if not rows:
        return None

    rows_html = '\n'.join(rows)
    return f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: Arial, sans-serif; font-size: 10pt; margin: 0; }}
  table.main {{ border-collapse: collapse; width: 100%; }}
  table.main tr {{ page-break-inside: avoid; }}
</style>
</head>
<body>
  <table class="main">
    {rows_html}
  </table>
</body>
</html>'''


from . import faelles_ui


class LavPDFDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        faelles_ui.anvend_stil(self)
        self._populate_lag()
        self.btnBrowse.clicked.connect(self._browse)
        self.btnKor.clicked.connect(self.kor)

    def _populate_lag(self):
        self.cboLag.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if layer.type() == layer.VectorLayer:
                if layer.geometryType() in (QgsWkbTypes.PolygonGeometry, QgsWkbTypes.PointGeometry):
                    self.cboLag.addItem(layer.name(), layer.id())

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(
            self, 'Gem PDF', '', 'PDF-filer (*.pdf)'
        )
        if path:
            if not path.lower().endswith('.pdf'):
                path += '.pdf'
            self.txtUddata.setText(path)

    def kor(self):
        layer_id = self.cboLag.currentData()
        pdf_path = self.txtUddata.text().strip()

        if not layer_id:
            QMessageBox.warning(self, 'Fejl', 'Vælg et lag.')
            return
        if not pdf_path:
            QMessageBox.warning(self, 'Fejl', 'Vælg en placering til PDF-filen.')
            return

        layer = QgsProject.instance().mapLayer(layer_id)
        if not layer:
            QMessageBox.warning(self, 'Fejl', 'Laget kunne ikke findes.')
            return

        html = _build_html(layer)
        if html is None:
            QMessageBox.warning(self, 'Fejl', 'Ingen features med geometri fundet i laget.')
            return

        # Print til PDF
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(pdf_path)
        printer.setPageSize(QPrinter.A4)
        printer.setOrientation(QPrinter.Landscape)
        printer.setPageMargins(10, 10, 10, 10, QPrinter.Millimeter)

        doc = QTextDocument()
        doc.setHtml(html)
        doc.setPageSize(QSizeF(printer.pageRect().size()))
        doc.print_(printer)

        QMessageBox.information(
            self, 'PDF oprettet',
            f'PDF gemt som:\n{pdf_path}'
        )
        self.accept()
