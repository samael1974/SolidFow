# -*- coding: utf-8 -*-
"""SolidFlow Mesh Studio foundation for FreeCAD 1.1.x.

Non-destructive Mesh Doctor:
- analysis of native Mesh::Feature objects;
- repair preview on a copied mesh;
- normals / duplicates / degenerations / indices / non-manifold / holes;
- optional decimation;
- conversion of a closed repaired mesh to a faceted Part solid.
"""

from __future__ import annotations

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

try:
    import Mesh
except Exception:
    Mesh = None

try:
    import Part
except Exception:
    Part = None

VERSION = "0.1.0"


def _exec_dialog(dialog):
    runner = getattr(dialog, "exec", None)
    if callable(runner):
        return runner()
    return dialog.exec_()


def _message(title, text, icon=QtWidgets.QMessageBox.Information):
    box = QtWidgets.QMessageBox(Gui.getMainWindow())
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(icon)
    _exec_dialog(box)


def selected_mesh_object():
    try:
        selected = Gui.Selection.getSelection()
    except Exception:
        selected = []
    for obj in selected:
        try:
            mesh = obj.Mesh
            _ = mesh.CountFacets
            return obj
        except Exception:
            continue
    return None


def _call_bool(mesh, name, default=None):
    fn = getattr(mesh, name, None)
    if not callable(fn):
        return default
    try:
        return bool(fn())
    except Exception:
        return default


def _call_int(mesh, name, default=None):
    fn = getattr(mesh, name, None)
    if not callable(fn):
        return default
    try:
        return int(fn())
    except Exception:
        return default


def analyse_mesh(mesh):
    """Return a conservative dictionary. Missing APIs are reported as None."""
    data = {}
    for key, attr in (
        ("points", "CountPoints"),
        ("edges", "CountEdges"),
        ("facets", "CountFacets"),
    ):
        try:
            data[key] = int(getattr(mesh, attr))
        except Exception:
            data[key] = None

    try:
        bb = mesh.BoundBox
        data["size"] = (float(bb.XLength), float(bb.YLength), float(bb.ZLength))
    except Exception:
        data["size"] = None

    data["solid"] = _call_bool(mesh, "isSolid")
    data["non_manifold"] = _call_bool(mesh, "hasNonManifolds")
    data["self_intersections"] = _call_bool(mesh, "hasSelfIntersections")
    data["invalid_points"] = _call_bool(mesh, "hasInvalidPoints")
    data["invalid_neighbourhood"] = _call_bool(mesh, "hasInvalidNeighbourhood")
    data["points_out_of_range"] = _call_bool(mesh, "hasPointsOutOfRange")
    data["facets_out_of_range"] = _call_bool(mesh, "hasFacetsOutOfRange")
    data["corrupted_facets"] = _call_bool(mesh, "hasCorruptedFacets")
    data["non_uniform_normals"] = _call_bool(mesh, "hasNonUniformOrientedFacets")
    data["wrong_normals_count"] = _call_int(mesh, "countNonUniformOrientedFacets")
    data["components"] = _call_int(mesh, "countComponents")
    return data


def _yes_no(value):
    if value is None:
        return "n/d"
    return "SÌ" if value else "no"


def analysis_text(data):
    lines = []
    lines.append("GEOMETRIA")
    lines.append("Vertici: {}".format(data.get("points", "n/d")))
    lines.append("Spigoli: {}".format(data.get("edges", "n/d")))
    lines.append("Triangoli: {}".format(data.get("facets", "n/d")))
    size = data.get("size")
    if size:
        lines.append("Dimensioni: {:.3f} × {:.3f} × {:.3f} mm".format(*size))
    lines.append("Componenti separate: {}".format(data.get("components", "n/d")))
    lines.append("")
    lines.append("VALIDAZIONE")
    lines.append("Mesh chiusa/solida: {}".format(_yes_no(data.get("solid"))))
    lines.append("Non-manifold: {}".format(_yes_no(data.get("non_manifold"))))
    lines.append("Self-intersection: {}".format(_yes_no(data.get("self_intersections"))))
    lines.append("Normali incoerenti: {} ({} facce)".format(
        _yes_no(data.get("non_uniform_normals")),
        data.get("wrong_normals_count", "n/d"),
    ))
    lines.append("Punti invalidi: {}".format(_yes_no(data.get("invalid_points"))))
    lines.append("Neighbourhood invalido: {}".format(_yes_no(data.get("invalid_neighbourhood"))))
    lines.append("Indici punti fuori range: {}".format(_yes_no(data.get("points_out_of_range"))))
    lines.append("Indici facce fuori range: {}".format(_yes_no(data.get("facets_out_of_range"))))
    lines.append("Facce corrotte: {}".format(_yes_no(data.get("corrupted_facets"))))
    return "\n".join(lines)


def _safe_mutate(mesh, method, *args, **kwargs):
    fn = getattr(mesh, method, None)
    if not callable(fn):
        return False, "API non disponibile"
    try:
        fn(*args, **kwargs)
        return True, "OK"
    except Exception as exc:
        return False, str(exc)


def repaired_copy(source_mesh, options):
    mesh = source_mesh.copy()
    log = []

    operations = [
        ("remove_invalid", "removeInvalidPoints", (), {}),
        ("fix_indices", "fixIndices", (), {}),
        ("remove_duplicate_points", "removeDuplicatedPoints", (), {}),
        ("remove_duplicate_facets", "removeDuplicatedFacets", (), {}),
        ("fix_degenerations", "fixDegenerations", (), {}),
        ("fix_deformations", "fixDeformations", (), {}),
        ("remove_non_manifold", "removeNonManifolds", (), {}),
        ("harmonize_normals", "harmonizeNormals", (), {}),
        ("fill_holes", "fillupHoles", (), {}),
        ("fix_self_intersections", "fixSelfIntersections", (), {}),
    ]

    for flag, method, args, kwargs in operations:
        if not options.get(flag, False):
            continue
        ok, detail = _safe_mutate(mesh, method, *args, **kwargs)
        log.append((method, ok, detail))

    reduction = float(options.get("decimate", 0.0) or 0.0)
    if reduction > 0.0:
        tolerance = float(options.get("decimate_tolerance", 0.1) or 0.1)
        ok, detail = _safe_mutate(mesh, "decimate", tolerance, reduction)
        log.append(("decimate", ok, detail))

    # Neighbourhood may change after repairs. Rebuild it last when available.
    ok, detail = _safe_mutate(mesh, "rebuildNeighbourHood")
    if ok:
        log.append(("rebuildNeighbourHood", True, detail))
    return mesh, log


class MeshDoctorDialog(QtWidgets.QDialog):
    def __init__(self, source, parent=None):
        super().__init__(parent or Gui.getMainWindow())
        self.setWindowTitle("SolidFlow — Mesh Doctor")
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.setModal(True)
        self.setMinimumWidth(650)
        self.source = source
        self.doc = source.Document
        self.preview = None
        self._transaction = False
        self._finished = False
        try:
            self._source_visible = bool(source.ViewObject.Visibility)
        except Exception:
            self._source_visible = True

        layout = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel("Mesh Doctor")
        heading.setStyleSheet("font-size:17px; font-weight:600;")
        layout.addWidget(heading)
        subtitle = QtWidgets.QLabel(
            "Analisi e riparazione non distruttiva. SolidFlow lavora su una copia della mesh originale."
        )
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.analysis = QtWidgets.QPlainTextEdit()
        self.analysis.setReadOnly(True)
        self.analysis.setMinimumHeight(205)
        layout.addWidget(self.analysis)

        box = QtWidgets.QGroupBox("Riparazioni da applicare alla copia")
        grid = QtWidgets.QGridLayout(box)
        self.checks = {}
        specs = [
            ("remove_invalid", "Rimuovi punti invalidi", True),
            ("fix_indices", "Ripara indici", True),
            ("remove_duplicate_points", "Rimuovi punti duplicati", True),
            ("remove_duplicate_facets", "Rimuovi triangoli duplicati", True),
            ("fix_degenerations", "Rimuovi degenerazioni", True),
            ("fix_deformations", "Correggi facce deformate", False),
            ("remove_non_manifold", "Rimuovi non-manifold", False),
            ("harmonize_normals", "Uniforma normali", True),
            ("fill_holes", "Chiudi fori", False),
            ("fix_self_intersections", "Prova a correggere self-intersection", False),
        ]
        for idx, (key, label, default) in enumerate(specs):
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(default)
            self.checks[key] = cb
            grid.addWidget(cb, idx // 2, idx % 2)
        layout.addWidget(box)

        simplify = QtWidgets.QGroupBox("Semplificazione")
        sform = QtWidgets.QFormLayout(simplify)
        self.decimate = QtWidgets.QSpinBox()
        self.decimate.setRange(0, 95)
        self.decimate.setSuffix(" %")
        self.decimate.setValue(0)
        sform.addRow("Riduzione massima", self.decimate)
        self.tolerance = QtWidgets.QDoubleSpinBox()
        self.tolerance.setRange(0.001, 1000.0)
        self.tolerance.setDecimals(3)
        self.tolerance.setSuffix(" mm")
        self.tolerance.setValue(0.1)
        sform.addRow("Tolleranza decimazione", self.tolerance)
        layout.addWidget(simplify)

        self.result = QtWidgets.QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setMinimumHeight(115)
        layout.addWidget(self.result)

        row = QtWidgets.QHBoxLayout()
        self.analyse_btn = QtWidgets.QPushButton("Rianalizza")
        self.preview_btn = QtWidgets.QPushButton("Anteprima riparazione")
        self.solid_btn = QtWidgets.QPushButton("Converti copia in solido faccettato")
        row.addWidget(self.analyse_btn)
        row.addWidget(self.preview_btn)
        row.addWidget(self.solid_btn)
        layout.addLayout(row)

        self.keep_original_hidden = QtWidgets.QCheckBox("Nascondi la mesh originale dopo la conferma")
        self.keep_original_hidden.setChecked(True)
        layout.addWidget(self.keep_original_hidden)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        layout.addWidget(buttons)
        buttons.accepted.connect(self.accept_repair)
        buttons.rejected.connect(self.reject)
        self.analyse_btn.clicked.connect(self.refresh_analysis)
        self.preview_btn.clicked.connect(self.update_preview)
        self.solid_btn.clicked.connect(self.convert_to_solid)

        self.refresh_analysis()
        self._start_transaction()

    def _start_transaction(self):
        if self._transaction:
            return
        self.doc.openTransaction("SolidFlow - Mesh Doctor")
        self._transaction = True
        self.preview = self.doc.addObject("Mesh::Feature", "MeshRepairPreview")
        self.preview.Label = self.source.Label + " — repaired preview"
        try:
            self.preview.Mesh = self.source.Mesh.copy()
            self.preview.ViewObject.Transparency = 25
            self.source.ViewObject.Visibility = False
        except Exception:
            pass
        self.doc.recompute()

    def options(self):
        data = {key: cb.isChecked() for key, cb in self.checks.items()}
        data["decimate"] = float(self.decimate.value()) / 100.0
        data["decimate_tolerance"] = float(self.tolerance.value())
        return data

    def refresh_analysis(self):
        try:
            self.analysis.setPlainText(analysis_text(analyse_mesh(self.source.Mesh)))
        except Exception as exc:
            self.analysis.setPlainText("Analisi fallita: " + str(exc))

    def update_preview(self):
        if self.preview is None:
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            repaired, log = repaired_copy(self.source.Mesh, self.options())
            self.preview.Mesh = repaired
            self.doc.recompute()
            after = analyse_mesh(repaired)
            lines = ["RISULTATO PREVIEW", analysis_text(after), "", "OPERAZIONI"]
            for method, ok, detail in log:
                lines.append(("✓ " if ok else "✗ ") + method + ("" if ok else ": " + detail))
            self.result.setPlainText("\n".join(lines))
        except Exception as exc:
            self.result.setPlainText("Riparazione preview fallita: " + str(exc))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def accept_repair(self):
        if self.preview is None:
            return
        self.update_preview()
        try:
            self.preview.Label = self.source.Label + " — Repaired"
            self.preview.ViewObject.Transparency = 0
            if not self.keep_original_hidden.isChecked():
                self.source.ViewObject.Visibility = self._source_visible
            if self._transaction:
                self.doc.commitTransaction()
                self._transaction = False
            self._finished = True
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(self.preview)
            super().accept()
        except Exception as exc:
            _message("SolidFlow Mesh Doctor", "Conferma fallita:\n" + str(exc), QtWidgets.QMessageBox.Critical)

    def convert_to_solid(self):
        if Part is None or self.preview is None:
            _message("SolidFlow Mesh Doctor", "Modulo Part non disponibile.", QtWidgets.QMessageBox.Warning)
            return
        self.update_preview()
        try:
            mesh = self.preview.Mesh
            if not mesh.isSolid():
                _message(
                    "Mesh non chiusa",
                    "La mesh riparata non risulta un volume chiuso. Prima correggi fori/non-manifold e riprova.",
                    QtWidgets.QMessageBox.Warning,
                )
                return
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            shape = Part.Shape()
            shape.makeShapeFromMesh(mesh.Topology, float(self.tolerance.value()))
            try:
                solid = Part.Solid(shape)
            except Exception:
                solid = shape
            try:
                solid = solid.removeSplitter()
            except Exception:
                pass
            if solid.isNull() or not solid.isValid():
                raise RuntimeError("La conversione ha prodotto una Shape non valida")
            obj = self.doc.addObject("Part::Feature", "MeshSolid")
            obj.Label = self.source.Label + " — Faceted Solid"
            obj.Shape = solid
            self.doc.recompute()
            if self._transaction:
                self.doc.commitTransaction()
                self._transaction = False
            self._finished = True
            try:
                self.preview.ViewObject.Visibility = False
                self.source.ViewObject.Visibility = False
            except Exception:
                pass
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(obj)
            super().accept()
        except Exception as exc:
            _message("Conversione mesh → solido", "Conversione fallita:\n" + str(exc), QtWidgets.QMessageBox.Critical)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _rollback(self):
        if self._finished:
            return
        if self._transaction:
            try:
                self.doc.abortTransaction()
            except Exception as exc:
                App.Console.PrintError("SolidFlow Mesh Doctor rollback: %s\n" % exc)
            self._transaction = False
        try:
            self.source.ViewObject.Visibility = self._source_visible
        except Exception:
            pass
        self._finished = True

    def reject(self):
        self._rollback()
        super().reject()

    def closeEvent(self, event):
        if not self._finished:
            self._rollback()
        event.accept()


def launch_mesh_doctor():
    obj = selected_mesh_object()
    if obj is None:
        _message(
            "SolidFlow Mesh Doctor",
            "Seleziona un oggetto Mesh (per esempio STL/OBJ importato) e rilancia Mesh Doctor.",
        )
        return
    _exec_dialog(MeshDoctorDialog(obj))
