# -*- coding: utf-8 -*-
"""SolidFlow UX v0.4 beta.5 advanced workflow layer.

Additive layer on top of beta.3 + beta.4. It intentionally creates native
FreeCAD features and does not replace the underlying PartDesign algorithms.

Implemented in this revision:
- Fillet Doctor: validates selected edge/face fillets on a copy of the shape,
  estimates a safe maximum radius, identifies problematic combinations and
  can create a native PartDesign::Fillet. Selecting one face automatically
  means "fillet the complete perimeter of that face".
- Revolution+: native PartDesign::Revolution with live preview and an axis
  chosen from H/V sketch axes, sketch construction axes, or a selected solid
  edge/reference.
- Studio Ground / Shadows preview: a lightweight Coin3D floor and soft contact
  shadow under visible model geometry. This is a viewport preview, not a
  ray-traced cast-shadow renderer.
- SolidFlow menu commands plus a dynamic "Ombre" button inserted next to the
  existing SolidFlow display controls when that bar is present.
"""

from __future__ import annotations

import math

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

try:
    from pivy import coin
except Exception:
    coin = None

VERSION = "0.4.0-beta.5"
ROOT = App.ParamGet("User parameter:BaseApp/Preferences/Mod/SolidFlowUX")


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


def _message(title: str, text: str, icon=QtWidgets.QMessageBox.Information):
    box = QtWidgets.QMessageBox(Gui.getMainWindow())
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(icon)
    box.exec_()


def _is_sketch(obj) -> bool:
    try:
        return bool(obj and obj.isDerivedFrom("Sketcher::SketchObject"))
    except Exception:
        return False


def _body_for(obj):
    if obj is None:
        return None
    try:
        parent = obj.getParentGeoFeatureGroup()
        if parent and parent.isDerivedFrom("PartDesign::Body"):
            return parent
    except Exception:
        pass
    doc = getattr(obj, "Document", None)
    if doc:
        for candidate in doc.Objects:
            try:
                if not candidate.isDerivedFrom("PartDesign::Body"):
                    continue
                group = list(getattr(candidate, "Group", []) or [])
                if obj in group:
                    return candidate
            except Exception:
                continue
    return None


def _shape_element(shape, sub_name: str):
    try:
        return shape.getElement(sub_name)
    except Exception:
        pass
    try:
        if sub_name.startswith("Edge"):
            return shape.Edges[int(sub_name[4:]) - 1]
        if sub_name.startswith("Face"):
            return shape.Faces[int(sub_name[4:]) - 1]
    except Exception:
        pass
    return None


def _shape_ok(shape) -> bool:
    try:
        if shape is None or shape.isNull():
            return False
    except Exception:
        return False
    try:
        if hasattr(shape, "isValid") and not shape.isValid():
            return False
    except Exception:
        pass
    return True


def _visible_model_bbox():
    doc = App.ActiveDocument
    if doc is None:
        return None
    bounds = None
    for obj in doc.Objects:
        try:
            if not hasattr(obj, "Shape") or obj.Shape.isNull():
                continue
            if hasattr(obj, "ViewObject") and not obj.ViewObject.Visibility:
                continue
            bb = obj.Shape.BoundBox
            vals = (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)
            if not all(math.isfinite(v) for v in vals):
                continue
            if bounds is None:
                bounds = list(vals)
            else:
                bounds[0] = min(bounds[0], bb.XMin)
                bounds[1] = min(bounds[1], bb.YMin)
                bounds[2] = min(bounds[2], bb.ZMin)
                bounds[3] = max(bounds[3], bb.XMax)
                bounds[4] = max(bounds[4], bb.YMax)
                bounds[5] = max(bounds[5], bb.ZMax)
        except Exception:
            continue
    return tuple(bounds) if bounds else None


# ---------------------------------------------------------------------------
# Fillet Doctor
# ---------------------------------------------------------------------------


def _fillet_selection():
    selection = Gui.Selection.getSelectionEx()
    if not selection:
        return None, [], []

    base = None
    names = []
    for sx in selection:
        obj = sx.Object
        sub_names = list(getattr(sx, "SubElementNames", []) or [])
        usable = [n for n in sub_names if n.startswith("Edge") or n.startswith("Face")]
        if not usable:
            continue
        if base is None:
            base = obj
        elif obj != base:
            return None, [], []
        for name in usable:
            if name not in names:
                names.append(name)

    if base is None or not names:
        return None, [], []

    test_edges = []
    for name in names:
        element = _shape_element(base.Shape, name)
        if element is None:
            continue
        if name.startswith("Edge"):
            test_edges.append((name, element))
        elif name.startswith("Face"):
            # Testing the face means testing every edge on its perimeter. The
            # actual PartDesign feature keeps FaceN as Base, which lets FreeCAD
            # resolve the perimeter parametrically.
            try:
                for index, edge in enumerate(element.Edges, 1):
                    test_edges.append((f"{name}/bordo{index}", edge))
            except Exception:
                pass

    return base, names, test_edges


def _try_shape_fillet(shape, edges, radius: float):
    if radius <= 0 or not edges:
        return False, "Raggio o selezione non validi"
    try:
        result = shape.makeFillet(float(radius), list(edges))
        if not _shape_ok(result):
            return False, "Il kernel ha prodotto una forma non valida"
        # A solid base should remain a solid after a dress-up operation.
        try:
            if len(shape.Solids) and not len(result.Solids):
                return False, "Il risultato non contiene più un solido"
        except Exception:
            pass
        return True, "OK"
    except Exception as exc:
        return False, str(exc) or exc.__class__.__name__


def _suggest_upper_radius(shape, edges) -> float:
    dims = []
    try:
        bb = shape.BoundBox
        dims = [d for d in (bb.XLength, bb.YLength, bb.ZLength) if d > 1e-6]
    except Exception:
        pass
    candidates = []
    if dims:
        candidates.append(min(dims) * 0.499999)
    try:
        lengths = [e.Length for e in edges if getattr(e, "Length", 0.0) > 1e-6]
        if lengths:
            candidates.append(min(lengths) * 0.499999)
    except Exception:
        pass
    if not candidates:
        return 100.0
    return max(0.01, min(candidates))


def _maximum_valid_radius(shape, edges, upper=None) -> float:
    if not edges:
        return 0.0
    if upper is None:
        upper = _suggest_upper_radius(shape, edges)
    upper = max(0.01, float(upper))

    tiny = min(0.01, upper * 0.1)
    tiny = max(tiny, 1e-5)
    ok, _ = _try_shape_fillet(shape, edges, tiny)
    if not ok:
        return 0.0

    # If the conservative geometric upper bound is valid, keep it. Otherwise
    # binary-search the valid/invalid boundary. This is deliberately called a
    # safe maximum, not a mathematical exact limit.
    ok_upper, _ = _try_shape_fillet(shape, edges, upper)
    if ok_upper:
        return upper

    lo, hi = tiny, upper
    for _ in range(18):
        mid = (lo + hi) * 0.5
        ok, _ = _try_shape_fillet(shape, edges, mid)
        if ok:
            lo = mid
        else:
            hi = mid
    return lo


class FilletDoctorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or Gui.getMainWindow())
        self.setWindowTitle("SolidFlow — Fillet Doctor")
        self.setMinimumWidth(500)
        self.base, self.base_names, self.edge_pairs = _fillet_selection()

        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("Raccordo intelligente")
        title.setStyleSheet("font-size:16px; font-weight:600;")
        layout.addWidget(title)

        self.selection_label = QtWidgets.QLabel()
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)

        form = QtWidgets.QFormLayout()
        self.radius = QtWidgets.QDoubleSpinBox()
        self.radius.setDecimals(3)
        self.radius.setRange(0.001, 100000.0)
        self.radius.setSuffix(" mm")
        self.radius.setSingleStep(0.25)
        self.radius.setValue(self._default_radius())
        form.addRow("Raggio", self.radius)

        self.use_all = QtWidgets.QCheckBox("Raccorda tutti gli spigoli del solido")
        form.addRow("", self.use_all)
        layout.addLayout(form)

        self.status = QtWidgets.QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMinimumHeight(150)
        layout.addWidget(self.status)

        row = QtWidgets.QHBoxLayout()
        self.analyse_btn = QtWidgets.QPushButton("Analizza")
        self.max_btn = QtWidgets.QPushButton("Trova massimo sicuro")
        self.create_btn = QtWidgets.QPushButton("Crea raccordo")
        close_btn = QtWidgets.QPushButton("Chiudi")
        self.create_btn.setDefault(True)
        row.addWidget(self.analyse_btn)
        row.addWidget(self.max_btn)
        row.addStretch(1)
        row.addWidget(self.create_btn)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self.analyse_btn.clicked.connect(self.analyse)
        self.max_btn.clicked.connect(self.find_maximum)
        self.create_btn.clicked.connect(self.create_fillet)
        close_btn.clicked.connect(self.reject)
        self.use_all.toggled.connect(self._update_enabled)

        self._refresh_selection_text()
        self._update_enabled()
        QtCore.QTimer.singleShot(50, self.analyse)

    def _default_radius(self):
        if not self.base or not self.edge_pairs:
            return 1.0
        upper = _suggest_upper_radius(self.base.Shape, [e for _, e in self.edge_pairs])
        return max(0.1, min(2.0, upper * 0.2))

    def _refresh_selection_text(self):
        if self.base is None:
            self.selection_label.setText(
                "Seleziona uno o più spigoli dello stesso solido, oppure una faccia. "
                "Selezionando una faccia SolidFlow raccorda automaticamente tutto il suo perimetro."
            )
            return
        label = getattr(self.base, "Label", self.base.Name)
        refs = ", ".join(self.base_names)
        suffix = ""
        if len(self.base_names) == 1 and self.base_names[0].startswith("Face"):
            suffix = "  → perimetro completo della faccia"
        self.selection_label.setText(f"Base: {label} — {refs}{suffix}")

    def _update_enabled(self):
        valid = self.base is not None and (bool(self.edge_pairs) or self.use_all.isChecked())
        self.analyse_btn.setEnabled(valid)
        self.max_btn.setEnabled(valid)
        self.create_btn.setEnabled(valid)

    def _edges_for_test(self):
        if not self.base:
            return []
        if self.use_all.isChecked():
            try:
                return list(self.base.Shape.Edges)
            except Exception:
                return []
        return [edge for _, edge in self.edge_pairs]

    def analyse(self):
        if self.base is None:
            self.status.setPlainText("Nessuna selezione valida.")
            return
        edges = self._edges_for_test()
        radius = self.radius.value()
        ok, reason = _try_shape_fillet(self.base.Shape, edges, radius)
        lines = []
        lines.append(f"Raggio richiesto: {radius:.3f} mm")
        lines.append(f"Elementi verificati: {len(edges)}")
        if ok:
            lines.append("✓ La combinazione è valida con questo raggio.")
        else:
            lines.append("✗ La combinazione completa non è valida.")
            if reason and reason != "OK":
                lines.append(f"Kernel: {reason}")

            # Check whether one edge fails even on its own.
            individually_bad = []
            for label, edge in self.edge_pairs:
                one_ok, _ = _try_shape_fillet(self.base.Shape, [edge], radius)
                if not one_ok:
                    individually_bad.append(label)
            if individually_bad:
                lines.append("Non validi già singolarmente: " + ", ".join(individually_bad))
            elif len(self.edge_pairs) > 1:
                # Detect edges whose removal makes the remaining combination work.
                suspects = []
                raw_edges = [e for _, e in self.edge_pairs]
                for i, (label, _edge) in enumerate(self.edge_pairs):
                    subset = raw_edges[:i] + raw_edges[i + 1 :]
                    if subset:
                        subset_ok, _ = _try_shape_fillet(self.base.Shape, subset, radius)
                        if subset_ok:
                            suspects.append(label)
                if suspects:
                    lines.append(
                        "La combinazione torna valida rimuovendo: " + ", ".join(suspects)
                    )
                else:
                    lines.append(
                        "Gli spigoli sono validi singolarmente: il limite nasce dalla loro combinazione."
                    )

        maximum = _maximum_valid_radius(self.base.Shape, edges)
        if maximum > 0:
            lines.append(f"Massimo sicuro stimato per questa selezione: {maximum:.3f} mm")
            if not ok and maximum < radius:
                lines.append("Suggerimento: usa 'Trova massimo sicuro' e riprova.")
        else:
            lines.append("Non ho trovato un raggio stabile per questa combinazione.")
        self.status.setPlainText("\n".join(lines))

    def find_maximum(self):
        edges = self._edges_for_test()
        if not edges or not self.base:
            return
        maximum = _maximum_valid_radius(self.base.Shape, edges)
        if maximum <= 0:
            self.status.setPlainText("Impossibile trovare un raggio valido per questa selezione.")
            return
        # Leave a tiny safety margin under the detected limit.
        safe = max(0.001, maximum * 0.995)
        self.radius.setValue(safe)
        self.status.setPlainText(
            f"Raggio sicuro impostato a {safe:.3f} mm\n"
            f"(limite stimato {maximum:.3f} mm)."
        )

    def create_fillet(self):
        if not self.base:
            return
        doc = self.base.Document
        body = _body_for(self.base)
        if body is None:
            _message(
                "SolidFlow Fillet Doctor",
                "La base selezionata non appartiene a un Body Part Design.",
                QtWidgets.QMessageBox.Warning,
            )
            return

        edges = self._edges_for_test()
        radius = self.radius.value()
        ok, reason = _try_shape_fillet(self.base.Shape, edges, radius)
        if not ok:
            _message(
                "Raccordo non valido",
                "La geometria non è valida con questo raggio.\n\n"
                "Premi 'Trova massimo sicuro' oppure riduci il raggio.\n\n" + reason,
                QtWidgets.QMessageBox.Warning,
            )
            return

        doc.openTransaction("SolidFlow - Fillet Doctor")
        try:
            feature = body.newObject("PartDesign::Fillet", "Fillet")
            if self.use_all.isChecked():
                feature.Base = (self.base, [""])
                feature.UseAllEdges = True
            else:
                feature.Base = (self.base, list(self.base_names))
                feature.UseAllEdges = False
            feature.Radius = radius
            doc.recompute()
            if not feature.isValid() or not _shape_ok(feature.Shape):
                raise RuntimeError("FreeCAD non ha prodotto un raccordo Part Design valido")
            doc.commitTransaction()
            try:
                self.base.ViewObject.Visibility = False
            except Exception:
                pass
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(feature)
            self.accept()
        except Exception as exc:
            try:
                doc.abortTransaction()
            except Exception:
                pass
            _message(
                "SolidFlow Fillet Doctor",
                "Creazione del raccordo fallita:\n" + str(exc),
                QtWidgets.QMessageBox.Critical,
            )


_fillet_dialog = None


def launch_fillet_doctor():
    global _fillet_dialog
    base, _names, _edges = _fillet_selection()
    if base is None:
        _message(
            "SolidFlow Fillet Doctor",
            "Seleziona uno o più spigoli dello stesso oggetto oppure una faccia.\n\n"
            "Per il tuo caso del cubo puoi selezionare direttamente la faccia: "
            "SolidFlow userà automaticamente tutti e quattro i bordi del perimetro.",
            QtWidgets.QMessageBox.Information,
        )
        return
    _fillet_dialog = FilletDoctorDialog()
    _fillet_dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    _fillet_dialog.show()
    _fillet_dialog.raise_()
    _fillet_dialog.activateWindow()


# ---------------------------------------------------------------------------
# Revolution+ with selectable/reference axis
# ---------------------------------------------------------------------------


def _geometry_is_line(geometry) -> bool:
    try:
        return "line" in type(geometry).__name__.lower()
    except Exception:
        return False


def _construction_axis_for_edge(sketch, edge_name: str):
    if not edge_name.startswith("Edge"):
        return None
    try:
        geo_index = int(edge_name[4:]) - 1
        if geo_index < 0 or geo_index >= len(sketch.Geometry):
            return None
        if not sketch.getConstruction(geo_index):
            return None
        if not _geometry_is_line(sketch.Geometry[geo_index]):
            return None
        axis_index = 0
        for i in range(geo_index):
            try:
                if sketch.getConstruction(i) and _geometry_is_line(sketch.Geometry[i]):
                    axis_index += 1
            except Exception:
                continue
        return f"Axis{axis_index}"
    except Exception:
        return None


def _revolution_selection():
    sketch = None
    detected_axes = []
    selection = Gui.Selection.getSelectionEx()

    for sx in selection:
        obj = sx.Object
        if _is_sketch(obj) and sketch is None:
            sketch = obj

    if sketch is None:
        return None, []

    for sx in selection:
        obj = sx.Object
        names = list(getattr(sx, "SubElementNames", []) or [])
        subs = list(getattr(sx, "SubObjects", []) or [])
        for i, name in enumerate(names):
            if not name.startswith("Edge"):
                continue
            if obj == sketch:
                axis_name = _construction_axis_for_edge(sketch, name)
                if axis_name:
                    detected_axes.append(
                        (sketch, axis_name, f"Linea di costruzione selezionata ({axis_name})")
                    )
            else:
                sub = subs[i] if i < len(subs) else None
                # FreeCAD's native Revolution accepts edge/circle references. We
                # keep the external selected edge and let the native feature do
                # the final geometric validation.
                if sub is not None:
                    label = getattr(obj, "Label", obj.Name)
                    detected_axes.append((obj, name, f"Spigolo selezionato — {label}:{name}"))

    # Remove duplicates while keeping selection order.
    unique = []
    seen = set()
    for obj, sub, label in detected_axes:
        key = (getattr(obj, "Name", str(obj)), sub)
        if key in seen:
            continue
        seen.add(key)
        unique.append((obj, sub, label))
    return sketch, unique


class RevolutionPlusDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or Gui.getMainWindow())
        self.setWindowTitle("SolidFlow — Rivoluzione+")
        self.setMinimumWidth(460)
        self.sketch, self.detected_axes = _revolution_selection()
        self.doc = getattr(self.sketch, "Document", None)
        self.body = _body_for(self.sketch)
        self.feature = None
        self._transaction_open = False
        self._finished = False
        self.axes = []

        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("Rivoluzione attorno a un asse esistente")
        title.setStyleSheet("font-size:16px; font-weight:600;")
        layout.addWidget(title)

        form = QtWidgets.QFormLayout()
        self.profile_label = QtWidgets.QLabel(
            getattr(self.sketch, "Label", "Nessuno") if self.sketch else "Nessuno"
        )
        form.addRow("Profilo", self.profile_label)

        self.axis_combo = QtWidgets.QComboBox()
        form.addRow("Asse", self.axis_combo)

        self.angle = QtWidgets.QDoubleSpinBox()
        self.angle.setRange(0.001, 360.0)
        self.angle.setDecimals(2)
        self.angle.setSuffix("°")
        self.angle.setValue(360.0)
        form.addRow("Angolo", self.angle)

        self.reverse = QtWidgets.QCheckBox("Inverti direzione")
        self.symmetric = QtWidgets.QCheckBox("Simmetrica")
        form.addRow("", self.reverse)
        form.addRow("", self.symmetric)
        layout.addLayout(form)

        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        layout.addWidget(buttons)
        buttons.accepted.connect(self.accept_preview)
        buttons.rejected.connect(self.cancel_preview)
        self.ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)

        self.axis_combo.currentIndexChanged.connect(self.update_preview)
        self.angle.valueChanged.connect(self.update_preview)
        self.reverse.toggled.connect(self.update_preview)
        self.symmetric.toggled.connect(self.update_preview)

        self._fill_axes()
        if self.sketch is None or self.body is None:
            self.ok_button.setEnabled(False)
            self.status.setText(
                "Seleziona uno Sketch appartenente a un Body. Puoi selezionare anche "
                "una linea di costruzione dello Sketch oppure Ctrl+selezionare uno spigolo del solido."
            )
        else:
            self._create_preview()

    def _fill_axes(self):
        self.axis_combo.clear()
        self.axes = []
        if not self.sketch:
            return

        # Detected explicit selection first so the user's intent wins.
        for ref in self.detected_axes:
            self.axes.append(ref)
            self.axis_combo.addItem(ref[2])

        defaults = [
            (self.sketch, "V_Axis", "Asse verticale dello Sketch"),
            (self.sketch, "H_Axis", "Asse orizzontale dello Sketch"),
        ]
        for ref in defaults:
            key = (ref[0].Name, ref[1])
            if not any((r[0].Name, r[1]) == key for r in self.axes):
                self.axes.append(ref)
                self.axis_combo.addItem(ref[2])

        try:
            count = int(self.sketch.getAxisCount())
        except Exception:
            count = 0
        for i in range(count):
            ref = (self.sketch, f"Axis{i}", f"Linea di costruzione {i + 1}")
            key = (ref[0].Name, ref[1])
            if not any((r[0].Name, r[1]) == key for r in self.axes):
                self.axes.append(ref)
                self.axis_combo.addItem(ref[2])

        self.axis_combo.setCurrentIndex(0 if self.axes else -1)

    def _create_preview(self):
        if not self.doc or not self.body or not self.sketch:
            return
        self.doc.openTransaction("SolidFlow - Rivoluzione+ preview")
        self._transaction_open = True
        try:
            self.feature = self.body.newObject("PartDesign::Revolution", "Revolution")
            self.feature.Profile = self.sketch
            try:
                self.feature.Refine = True
            except Exception:
                pass
            try:
                self.feature.ViewObject.Transparency = 55
            except Exception:
                pass
            self.update_preview()
        except Exception as exc:
            self.status.setText("Impossibile creare l'anteprima: " + str(exc))
            self.ok_button.setEnabled(False)

    def update_preview(self, *args):
        if not self.feature or not self.axes or self.axis_combo.currentIndex() < 0:
            return
        index = self.axis_combo.currentIndex()
        if index >= len(self.axes):
            return
        axis_obj, axis_sub, _label = self.axes[index]
        try:
            self.feature.ReferenceAxis = (axis_obj, [axis_sub])
            self.feature.Angle = self.angle.value()
            self.feature.Reversed = self.reverse.isChecked()
            if hasattr(self.feature, "SideType"):
                self.feature.SideType = 2 if self.symmetric.isChecked() else 0
            elif hasattr(self.feature, "Midplane"):
                self.feature.Midplane = self.symmetric.isChecked()
            self.doc.recompute()
            valid = bool(self.feature.isValid() and _shape_ok(self.feature.Shape))
            self.ok_button.setEnabled(valid)
            if valid:
                self.status.setText("✓ Anteprima valida — asse collegato parametricamente.")
            else:
                self.status.setText(
                    "Profilo/asse non producono una rivoluzione valida. Prova un altro asse o angolo."
                )
        except Exception as exc:
            self.ok_button.setEnabled(False)
            self.status.setText("Anteprima non valida: " + str(exc))

    def accept_preview(self):
        if self._finished or not self.feature:
            return
        try:
            self.doc.recompute()
            if not self.feature.isValid() or not _shape_ok(self.feature.Shape):
                raise RuntimeError("La rivoluzione non è valida")
            try:
                self.feature.ViewObject.Transparency = 0
                self.sketch.ViewObject.Visibility = False
            except Exception:
                pass
            if self._transaction_open:
                self.doc.commitTransaction()
                self._transaction_open = False
            self._finished = True
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(self.feature)
            super().accept()
        except Exception as exc:
            self.status.setText("Impossibile confermare: " + str(exc))

    def cancel_preview(self):
        if self._finished:
            return
        if self._transaction_open:
            try:
                self.doc.abortTransaction()
            except Exception:
                try:
                    if self.feature:
                        self.doc.removeObject(self.feature.Name)
                        self.doc.recompute()
                except Exception:
                    pass
            self._transaction_open = False
        self._finished = True
        super().reject()

    def closeEvent(self, event):
        if not self._finished:
            self.cancel_preview()
        event.accept()


_revolution_dialog = None


def launch_revolution_plus():
    global _revolution_dialog
    sketch, _axes = _revolution_selection()
    if sketch is None:
        _message(
            "SolidFlow Rivoluzione+",
            "Seleziona lo Sketch del profilo.\n\n"
            "Facoltativo: Ctrl+seleziona anche uno spigolo del solido, oppure seleziona "
            "una linea di costruzione nello Sketch. SolidFlow la proporrà come asse prioritario.",
            QtWidgets.QMessageBox.Information,
        )
        return
    _revolution_dialog = RevolutionPlusDialog()
    _revolution_dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    _revolution_dialog.show()
    _revolution_dialog.raise_()
    _revolution_dialog.activateWindow()


# ---------------------------------------------------------------------------
# Studio floor + soft contact-shadow preview
# ---------------------------------------------------------------------------


class StudioShadowController:
    def __init__(self):
        self.enabled = ROOT.GetBool("StudioShadow", False)
        self.plane = ROOT.GetString("StudioPlane", "XY") or "XY"
        self.root = None
        self.graph = None
        self.shadow_button = None
        self.timer = QtCore.QTimer()
        self.timer.setInterval(900)
        self.timer.timeout.connect(self.refresh)

    def set_enabled(self, state: bool):
        self.enabled = bool(state)
        ROOT.SetBool("StudioShadow", self.enabled)
        if self.enabled:
            self.timer.start()
            self.refresh()
        else:
            self.timer.stop()
            self.remove_scene()
        if self.shadow_button is not None:
            self.shadow_button.blockSignals(True)
            self.shadow_button.setChecked(self.enabled)
            self.shadow_button.blockSignals(False)

    def toggle(self):
        self.set_enabled(not self.enabled)

    def remove_scene(self):
        if self.graph is not None and self.root is not None:
            try:
                self.graph.removeChild(self.root)
            except Exception:
                pass
        self.root = None
        self.graph = None
        try:
            Gui.updateGui()
        except Exception:
            pass

    def _material(self, rgb, transparency=0.0):
        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(float(rgb[0]), float(rgb[1]), float(rgb[2]))
        mat.ambientColor.setValue(float(rgb[0]) * 0.55, float(rgb[1]) * 0.55, float(rgb[2]) * 0.55)
        mat.transparency = float(transparency)
        return mat

    def _add_cube(self, parent, center, size, rgb, transparency=0.0):
        sep = coin.SoSeparator()
        sep.addChild(self._material(rgb, transparency))
        tr = coin.SoTransform()
        tr.translation.setValue(*[float(v) for v in center])
        sep.addChild(tr)
        cube = coin.SoCube()
        cube.width = max(1e-5, float(size[0]))
        cube.height = max(1e-5, float(size[1]))
        cube.depth = max(1e-5, float(size[2]))
        sep.addChild(cube)
        parent.addChild(sep)

    def _add_soft_shadow_xy(self, parent, cx, cy, z, sx, sy, diag):
        # Two flattened transparent spheres create a soft, unobtrusive contact
        # shadow. It deliberately communicates grounding/depth without claiming
        # to be a physically correct ray-traced shadow.
        for factor, alpha, dx, dy in (
            (1.00, 0.73, 0.035, -0.035),
            (0.72, 0.62, 0.020, -0.020),
        ):
            sep = coin.SoSeparator()
            sep.addChild(self._material((0.10, 0.11, 0.12), alpha))
            tr = coin.SoTransform()
            tr.translation.setValue(
                float(cx + dx * sx), float(cy + dy * sy), float(z + diag * 0.0015)
            )
            tr.scaleFactor.setValue(
                float(max(sx * 0.55 * factor, diag * 0.04)),
                float(max(sy * 0.42 * factor, diag * 0.04)),
                float(max(diag * 0.002, 1e-4)),
            )
            sep.addChild(tr)
            sphere = coin.SoSphere()
            sphere.radius = 1.0
            sep.addChild(sphere)
            parent.addChild(sep)

    def build_scene(self):
        if coin is None or not Gui.ActiveDocument:
            return
        bbox = _visible_model_bbox()
        if bbox is None:
            return
        xmin, ymin, zmin, xmax, ymax, zmax = bbox
        sx = max(xmax - xmin, 1.0)
        sy = max(ymax - ymin, 1.0)
        sz = max(zmax - zmin, 1.0)
        diag = max(math.sqrt(sx * sx + sy * sy + sz * sz), 1.0)
        margin = max(diag * 0.65, 10.0)
        thickness = max(diag * 0.002, 0.02)
        cx, cy, cz = (xmin + xmax) * 0.5, (ymin + ymax) * 0.5, (zmin + zmax) * 0.5

        view = Gui.ActiveDocument.ActiveView
        graph = view.getSceneGraph()
        root = coin.SoSeparator()
        try:
            pick = coin.SoPickStyle()
            pick.style = coin.SoPickStyle.UNPICKABLE
            root.addChild(pick)
        except Exception:
            pass

        if self.plane == "XZ":
            yfloor = ymin - diag * 0.002
            self._add_cube(
                root,
                (cx, yfloor - thickness * 0.5, cz),
                (sx + margin * 2, thickness, sz + margin * 2),
                (0.86, 0.87, 0.88),
                0.03,
            )
        elif self.plane == "YZ":
            xfloor = xmin - diag * 0.002
            self._add_cube(
                root,
                (xfloor - thickness * 0.5, cy, cz),
                (thickness, sy + margin * 2, sz + margin * 2),
                (0.86, 0.87, 0.88),
                0.03,
            )
        else:
            zfloor = zmin - diag * 0.002
            self._add_cube(
                root,
                (cx, cy, zfloor - thickness * 0.5),
                (sx + margin * 2, sy + margin * 2, thickness),
                (0.86, 0.87, 0.88),
                0.03,
            )
            self._add_soft_shadow_xy(root, cx, cy, zfloor, sx, sy, diag)

        graph.addChild(root)
        self.root = root
        self.graph = graph

    def refresh(self):
        if not self.enabled:
            return
        self.remove_scene()
        try:
            self.build_scene()
            Gui.updateGui()
        except Exception as exc:
            App.Console.PrintWarning("SolidFlow Ombre/Piano: %s\n" % exc)

    def set_plane(self, plane: str):
        plane = plane.upper()
        if plane not in ("XY", "XZ", "YZ"):
            return
        self.plane = plane
        ROOT.SetString("StudioPlane", plane)
        if self.enabled:
            self.refresh()

    def install_into_display_bar(self):
        if self.shadow_button is not None:
            return True
        main = Gui.getMainWindow()
        for button in main.findChildren(QtWidgets.QToolButton):
            text = (button.text() or "").strip().lower()
            if text not in ("materiale", "material"):
                continue
            parent = button.parentWidget()
            layout = parent.layout() if parent else None
            if layout is None:
                continue
            if parent.findChild(QtWidgets.QToolButton, "SolidFlowShadowButton"):
                self.shadow_button = parent.findChild(QtWidgets.QToolButton, "SolidFlowShadowButton")
                return True
            new_button = QtWidgets.QToolButton(parent)
            new_button.setObjectName("SolidFlowShadowButton")
            new_button.setText("Ombre")
            new_button.setToolTip("Piano d'appoggio + ombra di contatto SolidFlow")
            new_button.setCheckable(True)
            new_button.setChecked(self.enabled)
            new_button.setToolButtonStyle(button.toolButtonStyle())
            try:
                new_button.setIconSize(button.iconSize())
                new_button.setFixedHeight(button.height())
            except Exception:
                pass
            new_button.toggled.connect(self.set_enabled)
            index = layout.indexOf(button)
            if index >= 0 and hasattr(layout, "insertWidget"):
                layout.insertWidget(index, new_button)
            else:
                layout.addWidget(new_button)
            self.shadow_button = new_button
            return True
        return False


_studio = StudioShadowController()


# ---------------------------------------------------------------------------
# Commands / menu integration
# ---------------------------------------------------------------------------


class _CmdFilletDoctor:
    def GetResources(self):
        return {
            "MenuText": "Fillet Doctor…",
            "ToolTip": "Raccordo intelligente: perimetro faccia, diagnosi e raggio massimo sicuro",
        }

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        launch_fillet_doctor()


class _CmdRevolutionPlus:
    def GetResources(self):
        return {
            "MenuText": "Rivoluzione+…",
            "ToolTip": "Rivoluzione con asse Sketch, linea di costruzione o spigolo selezionato",
        }

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        launch_revolution_plus()


class _CmdStudioShadow:
    def GetResources(self):
        return {
            "MenuText": "Ombre + piano",
            "ToolTip": "Attiva/disattiva piano d'appoggio e ombra di contatto",
        }

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        _studio.toggle()


class AdvancedController:
    def __init__(self):
        self._installed = False
        self._menu_marker = None

    def _solidflow_menu(self):
        main = Gui.getMainWindow()
        for menu in main.findChildren(QtWidgets.QMenu):
            title = menu.title().replace("&", "").strip().lower()
            if title == "solidflow":
                return menu
        return None

    def augment_menu(self):
        menu = self._solidflow_menu()
        if menu is None:
            return
        if menu.findChild(QtCore.QObject, "SolidFlowBeta5Marker"):
            return
        marker = QtCore.QObject(menu)
        marker.setObjectName("SolidFlowBeta5Marker")
        self._menu_marker = marker
        menu.addSeparator()
        menu.addAction(Gui.Command.get("SolidFlow_FilletDoctor").getAction()[0])
        menu.addAction(Gui.Command.get("SolidFlow_RevolutionPlus").getAction()[0])

        shadows = menu.addAction("Ombre + piano d'appoggio")
        shadows.setCheckable(True)
        shadows.setChecked(_studio.enabled)
        shadows.toggled.connect(_studio.set_enabled)

        plane_menu = menu.addMenu("Piano d'appoggio")
        group = QtWidgets.QActionGroup(plane_menu)
        group.setExclusive(True)
        for plane in ("XY", "XZ", "YZ"):
            action = plane_menu.addAction(plane)
            action.setCheckable(True)
            action.setChecked(_studio.plane == plane)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, p=plane: _studio.set_plane(p))

    def install(self):
        if self._installed:
            return
        for name, command in (
            ("SolidFlow_FilletDoctor", _CmdFilletDoctor()),
            ("SolidFlow_RevolutionPlus", _CmdRevolutionPlus()),
            ("SolidFlow_StudioShadow", _CmdStudioShadow()),
        ):
            try:
                Gui.addCommand(name, command)
            except Exception as exc:
                App.Console.PrintWarning("SolidFlow comando %s: %s\n" % (name, exc))
        self._installed = True
        QtCore.QTimer.singleShot(500, self.augment_menu)
        QtCore.QTimer.singleShot(1100, self.augment_menu)
        QtCore.QTimer.singleShot(700, _studio.install_into_display_bar)
        QtCore.QTimer.singleShot(1600, _studio.install_into_display_bar)
        if _studio.enabled:
            _studio.timer.start()
            QtCore.QTimer.singleShot(900, _studio.refresh)
        App.Console.PrintMessage(
            "SolidFlow UX %s: Fillet Doctor, Rivoluzione+ e Studio Ombre attivi.\n" % VERSION
        )

    def uninstall(self):
        _studio.timer.stop()
        _studio.remove_scene()
        self._installed = False


_controller = None


def install():
    global _controller
    app = QtWidgets.QApplication.instance()
    old = getattr(app, "_solidflow_beta5_controller", None) if app else None
    if old is not None and old is not _controller:
        try:
            old.uninstall()
        except Exception:
            pass
    if _controller is None or not getattr(_controller, "_installed", False):
        _controller = AdvancedController()
        _controller.install()
    if app:
        app._solidflow_beta5_controller = _controller
    return _controller


def uninstall():
    global _controller
    if _controller:
        _controller.uninstall()
        _controller = None
