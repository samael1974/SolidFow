# -*- coding: utf-8 -*-
"""SolidFlow Import Assistant: DXF/DWG/SVG and calibrated raster references."""
import os
import importlib

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui, QtWidgets


def _main_window():
    return Gui.getMainWindow()


def _warning(text):
    QtWidgets.QMessageBox.warning(_main_window(), "SolidFlow - Importa", text)


class ImportAssistant(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or _main_window())
        self.setWindowTitle("SolidFlow - Import Assistant")
        self.resize(500, 430)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        intro = QtWidgets.QLabel(
            "Importa DXF, DWG, SVG o immagini raster e portale subito alla scala utile.", self
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        file_row = QtWidgets.QHBoxLayout()
        self.file_edit = QtWidgets.QLineEdit(self)
        browse = QtWidgets.QPushButton("Sfoglia…", self)
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(browse)
        root.addLayout(file_row)

        vector_box = QtWidgets.QGroupBox("DXF / DWG / SVG — scala", self)
        vector_form = QtWidgets.QFormLayout(vector_box)
        self.scale = QtWidgets.QDoubleSpinBox(self)
        self.scale.setDecimals(6)
        self.scale.setRange(0.000001, 1000000.0)
        self.scale.setValue(1.0)
        vector_form.addRow("Fattore scala", self.scale)

        cal_row = QtWidgets.QHBoxLayout()
        self.source_length = QtWidgets.QDoubleSpinBox(self)
        self.source_length.setDecimals(4)
        self.source_length.setRange(0.0001, 1000000000.0)
        self.source_length.setValue(100.0)
        self.target_length = QtWidgets.QDoubleSpinBox(self)
        self.target_length.setDecimals(4)
        self.target_length.setRange(0.0001, 1000000000.0)
        self.target_length.setValue(100.0)
        calc = QtWidgets.QPushButton("Calcola scala", self)
        cal_row.addWidget(QtWidgets.QLabel("Misura file", self))
        cal_row.addWidget(self.source_length)
        cal_row.addWidget(QtWidgets.QLabel("→ reale", self))
        cal_row.addWidget(self.target_length)
        cal_row.addWidget(calc)
        vector_form.addRow("Calibrazione", cal_row)
        root.addWidget(vector_box)

        raster_box = QtWidgets.QGroupBox("PNG / JPG / BMP — riferimento", self)
        raster_form = QtWidgets.QFormLayout(raster_box)
        self.width = QtWidgets.QDoubleSpinBox(self)
        self.width.setDecimals(2)
        self.width.setRange(0.1, 1000000.0)
        self.width.setValue(100.0)
        self.width.setSuffix(" mm")
        raster_form.addRow("Larghezza reale", self.width)
        self.plane = QtWidgets.QComboBox(self)
        self.plane.addItems(["XY", "XZ", "YZ"])
        raster_form.addRow("Piano", self.plane)
        root.addWidget(raster_box)

        note = QtWidgets.QLabel(
            "Per i vettori puoi inserire una misura che conosci nel file e la misura reale desiderata: "
            "SolidFlow calcola automaticamente il fattore. Le immagini mantengono le proporzioni.",
            self,
        )
        note.setWordWrap(True)
        root.addWidget(note)

        self.status = QtWidgets.QLabel("", self)
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Importa")
        root.addWidget(buttons)

        browse.clicked.connect(self.browse)
        calc.clicked.connect(self.calculate_scale)
        buttons.accepted.connect(self.do_import)
        buttons.rejected.connect(self.reject)

    def browse(self):
        path, _flt = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Importa con SolidFlow",
            "",
            "File supportati (*.dxf *.dwg *.svg *.png *.jpg *.jpeg *.bmp);;"
            "DXF (*.dxf);;DWG (*.dwg);;SVG (*.svg);;Immagini (*.png *.jpg *.jpeg *.bmp);;Tutti i file (*.*)",
        )
        if path:
            self.file_edit.setText(path)

    def calculate_scale(self):
        source = float(self.source_length.value())
        target = float(self.target_length.value())
        if source <= 0:
            self.status.setText("La misura nel file deve essere maggiore di zero.")
            return
        factor = target / source
        self.scale.setValue(factor)
        self.status.setText("Scala calcolata: {:.6f}  ({} → {})".format(factor, source, target))

    def do_import(self):
        path = self.file_edit.text().strip()
        if not path or not os.path.isfile(path):
            self.status.setText("Seleziona un file esistente.")
            return
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in (".png", ".jpg", ".jpeg", ".bmp"):
                self._import_raster(path)
            elif ext in (".dxf", ".dwg", ".svg"):
                self._import_vector(path, ext)
            else:
                self.status.setText("Formato non supportato da questa beta.")
                return
            self.accept()
        except Exception as exc:
            self.status.setText("Importazione non riuscita: {}".format(exc))

    def _doc(self):
        return App.ActiveDocument or App.newDocument("SolidFlowImport")

    def _import_vector(self, path, ext):
        doc = self._doc()
        before = {o.Name for o in doc.Objects}
        modules = {".dxf": "importDXF", ".dwg": "importDWG", ".svg": "importSVG"}
        module = importlib.import_module(modules[ext])
        module.insert(path, doc.Name)
        doc.recompute()
        new_objects = [o for o in doc.Objects if o.Name not in before]
        factor = float(self.scale.value())
        if abs(factor - 1.0) > 1e-12 and new_objects:
            try:
                from draftfunctions.scale import scale as draft_scale
                draft_scale(
                    new_objects,
                    App.Vector(factor, factor, factor),
                    center=App.Vector(0, 0, 0),
                    copy=False,
                )
                doc.recompute()
            except Exception as exc:
                App.Console.PrintWarning(
                    "[SolidFlowUX] Import completed, but scale application failed: {}\n".format(exc)
                )
                _warning(
                    "Il file è stato importato, ma il fattore scala non è stato applicato automaticamente.\n"
                    "La geometria è comunque presente nel documento."
                )
        try:
            Gui.ActiveDocument.ActiveView.fitAll()
        except Exception:
            pass

    def _import_raster(self, path):
        doc = self._doc()
        reader = QtGui.QImageReader(path)
        size = reader.size()
        if not size.isValid() or size.width() <= 0 or size.height() <= 0:
            image = QtGui.QImage(path)
            size = image.size()
        if not size.isValid() or size.width() <= 0 or size.height() <= 0:
            raise RuntimeError("Non riesco a leggere le dimensioni dell'immagine")

        width_mm = float(self.width.value())
        height_mm = width_mm * float(size.height()) / float(size.width())
        obj = doc.addObject("Image::ImagePlane", "SolidFlowImage")
        obj.Label = os.path.basename(path)
        obj.ImageFile = path
        obj.XSize = width_mm
        obj.YSize = height_mm

        plane = self.plane.currentText()
        if plane == "XZ":
            obj.Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(1, 0, 0), 90))
        elif plane == "YZ":
            obj.Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(0, 1, 0), 90))
        doc.recompute()
        try:
            Gui.ActiveDocument.ActiveView.fitAll()
        except Exception:
            pass


_open_importers = []


def show_import_assistant():
    dlg = ImportAssistant()
    _open_importers.append(dlg)
    dlg.finished.connect(lambda _r, d=dlg: _open_importers.remove(d) if d in _open_importers else None)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
