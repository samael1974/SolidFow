# -*- coding: utf-8 -*-
"""SolidFlow UX v0.4 beta.6 — Advanced Features.

This layer adds simplified, modal, live-preview workflows for native PartDesign
features:
- Additive/Subtractive Sweep (PartDesign::AdditivePipe/SubtractivePipe)
- Additive/Subtractive Loft
- Additive/Subtractive Helix
- Thread Wizard beta (physical V-groove thread on Z-axis cylindrical faces)

The resulting objects remain ordinary FreeCAD PartDesign features.
"""

from __future__ import annotations

import math

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

try:
    import Part
except Exception:
    Part = None

VERSION = "0.4.0-beta.6"
ROOT = App.ParamGet("User parameter:BaseApp/Preferences/Mod/SolidFlowUX")


def _message(title, text, icon=QtWidgets.QMessageBox.Information):
    box = QtWidgets.QMessageBox(Gui.getMainWindow())
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(icon)
    runner = getattr(box, "exec", None)
    if callable(runner):
        runner()
    else:
        box.exec_()


def _shape_ok(shape):
    try:
        if shape is None or shape.isNull():
            return False
        if hasattr(shape, "isValid") and not shape.isValid():
            return False
        if hasattr(shape, "Solids") and len(shape.Solids) == 0:
            return False
        return True
    except Exception:
        return False


def _is_sketch(obj):
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
                if candidate.isDerivedFrom("PartDesign::Body") and obj in list(candidate.Group):
                    return candidate
            except Exception:
                continue
    return None


def _selected_sketches():
    result = []
    seen = set()
    try:
        selection = Gui.Selection.getSelectionEx()
    except Exception:
        selection = []
    for sx in selection:
        obj = getattr(sx, "Object", None)
        if _is_sketch(obj) and obj.Name not in seen:
            seen.add(obj.Name)
            result.append(obj)
    if not result:
        try:
            for obj in Gui.Selection.getSelection():
                if _is_sketch(obj) and obj.Name not in seen:
                    seen.add(obj.Name)
                    result.append(obj)
        except Exception:
            pass
    return result


def _same_body(objects):
    bodies = [_body_for(obj) for obj in objects]
    if not objects or any(body is None for body in bodies):
        return None
    first = bodies[0]
    return first if all(body == first for body in bodies) else None


def _set_preview_style(feature):
    try:
        feature.ViewObject.Transparency = 55
    except Exception:
        pass


def _restore_final_style(feature):
    try:
        feature.ViewObject.Transparency = 0
    except Exception:
        pass


def _exec_dialog(dialog):
    runner = getattr(dialog, "exec", None)
    if callable(runner):
        return runner()
    return dialog.exec_()


class _NativePreviewDialog(QtWidgets.QDialog):
    """Safety wrapper for one native PartDesign preview transaction."""

    def __init__(self, title, parent=None):
        super().__init__(parent or Gui.getMainWindow())
        self.setWindowTitle(title)
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.setModal(True)
        self.doc = None
        self.body = None
        self.feature = None
        self._transaction_open = False
        self._finished = False
        self._old_tip = None
        self._old_visibility = {}

    def _begin(self, doc, body, transaction_name):
        self.doc = doc
        self.body = body
        self._old_tip = getattr(body, "Tip", None)
        self.doc.openTransaction(transaction_name)
        self._transaction_open = True

    def _remember_visibility(self, *objects):
        for obj in objects:
            if obj is None:
                continue
            try:
                self._old_visibility[obj.Name] = bool(obj.ViewObject.Visibility)
            except Exception:
                pass

    def _hide_inputs_on_commit(self, *objects):
        for obj in objects:
            if obj is None:
                continue
            try:
                obj.ViewObject.Visibility = False
            except Exception:
                pass

    def _restore_visibility(self):
        if not self.doc:
            return
        for name, state in self._old_visibility.items():
            try:
                obj = self.doc.getObject(name)
                if obj:
                    obj.ViewObject.Visibility = state
            except Exception:
                pass

    def _commit_feature(self, inputs=()):
        if not self.feature or not self.doc:
            return False
        try:
            self.doc.recompute()
            if not self.feature.isValid() or not _shape_ok(self.feature.Shape):
                raise RuntimeError("La feature non produce un solido valido.")
            _restore_final_style(self.feature)
            self._hide_inputs_on_commit(*inputs)
            if self._transaction_open:
                self.doc.commitTransaction()
                self._transaction_open = False
            self._finished = True
            try:
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(self.feature)
            except Exception:
                pass
            return True
        except Exception as exc:
            _message(
                "SolidFlow",
                "Impossibile confermare la feature:\n" + str(exc),
                QtWidgets.QMessageBox.Warning,
            )
            return False

    def _rollback(self):
        if self._finished:
            return
        if self.doc and self._transaction_open:
            try:
                self.doc.abortTransaction()
            except Exception:
                try:
                    if self.feature and self.doc.getObject(self.feature.Name):
                        self.doc.removeObject(self.feature.Name)
                    if self.body is not None and self._old_tip is not None:
                        self.body.Tip = self._old_tip
                    self.doc.recompute()
                except Exception as exc:
                    App.Console.PrintError(
                        "SolidFlow beta.6 rollback fallito: %s\n" % exc
                    )
            self._transaction_open = False
        self._restore_visibility()
        self._finished = True

    def reject(self):
        self._rollback()
        super().reject()

    def closeEvent(self, event):
        if not self._finished:
            self._rollback()
        event.accept()


class SweepDialog(_NativePreviewDialog):
    def __init__(self, subtractive=False, parent=None):
        super().__init__("SolidFlow — Sweep Cut" if subtractive else "SolidFlow — Sweep", parent)
        self.subtractive = subtractive
        self.sketches = _selected_sketches()
        self.profile = self.sketches[0] if len(self.sketches) >= 1 else None
        self.spine = self.sketches[1] if len(self.sketches) >= 2 else None
        self.body = _same_body(self.sketches[:2]) if len(self.sketches) >= 2 else None

        self.setMinimumWidth(480)
        layout = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel("Sweep sottrattivo" if subtractive else "Sweep additivo")
        heading.setStyleSheet("font-size:16px; font-weight:600;")
        layout.addWidget(heading)
        hint = QtWidgets.QLabel(
            "Seleziona due Sketch dello stesso Body: il primo è il profilo, "
            "il secondo è il percorso. Puoi scambiarli qui sotto."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QtWidgets.QFormLayout()
        self.profile_label = QtWidgets.QLabel()
        self.spine_label = QtWidgets.QLabel()
        form.addRow("Profilo", self.profile_label)
        form.addRow("Percorso", self.spine_label)
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(["Standard", "Fisso", "Frenet"])
        form.addRow("Orientamento", self.mode)
        self.transition = QtWidgets.QComboBox()
        self.transition.addItems(["Trasformato", "Angolo retto", "Angolo arrotondato"])
        form.addRow("Transizione", self.transition)
        self.tangent = QtWidgets.QCheckBox("Includi bordi tangenti del percorso")
        form.addRow("", self.tangent)
        layout.addLayout(form)

        row = QtWidgets.QHBoxLayout()
        self.swap_btn = QtWidgets.QPushButton("Scambia profilo/percorso")
        row.addWidget(self.swap_btn)
        row.addStretch(1)
        layout.addLayout(row)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        layout.addWidget(buttons)
        self.ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self.swap_btn.clicked.connect(self._swap)
        self.mode.currentIndexChanged.connect(self._update_preview)
        self.transition.currentIndexChanged.connect(self._update_preview)
        self.tangent.toggled.connect(self._update_preview)

        self._refresh_labels()
        if self.profile and self.spine and self.body:
            self._create_preview()
        else:
            self.ok_button.setEnabled(False)
            self.status.setText(
                "Servono due Sketch appartenenti allo stesso Body. "
                "Selezionali nell'albero tenendo premuto Ctrl."
            )

    def _refresh_labels(self):
        self.profile_label.setText(getattr(self.profile, "Label", "—") if self.profile else "—")
        self.spine_label.setText(getattr(self.spine, "Label", "—") if self.spine else "—")

    def _create_preview(self):
        self._begin(self.profile.Document, self.body, "SolidFlow - Sweep preview")
        self._remember_visibility(self.profile, self.spine)
        type_id = "PartDesign::SubtractivePipe" if self.subtractive else "PartDesign::AdditivePipe"
        try:
            self.feature = self.body.newObject(type_id, "SweepCut" if self.subtractive else "Sweep")
            self.feature.Profile = self.profile
            self.feature.Spine = self.spine
            _set_preview_style(self.feature)
            self._update_preview()
        except Exception as exc:
            self.ok_button.setEnabled(False)
            self.status.setText("Impossibile creare l'anteprima: " + str(exc))

    def _update_preview(self, *args):
        if not self.feature:
            return
        try:
            self.feature.Profile = self.profile
            self.feature.Spine = self.spine
            self.feature.Mode = self.mode.currentIndex()
            self.feature.Transition = self.transition.currentIndex()
            self.feature.SpineTangent = self.tangent.isChecked()
            if hasattr(self.feature, "Transformation"):
                self.feature.Transformation = 0
            self.doc.recompute()
            valid = bool(self.feature.isValid() and _shape_ok(self.feature.Shape))
            self.ok_button.setEnabled(valid)
            self.status.setText(
                "✓ Anteprima valida — feature Part Design nativa."
                if valid else "Il profilo e il percorso non producono uno Sweep valido."
            )
        except Exception as exc:
            self.ok_button.setEnabled(False)
            self.status.setText("Anteprima non valida: " + str(exc))

    def _swap(self):
        if not self.profile or not self.spine:
            return
        self.profile, self.spine = self.spine, self.profile
        self._refresh_labels()
        self._update_preview()

    def _accept(self):
        if self._commit_feature((self.profile, self.spine)):
            super().accept()


class LoftDialog(_NativePreviewDialog):
    def __init__(self, subtractive=False, parent=None):
        super().__init__("SolidFlow — Loft Cut" if subtractive else "SolidFlow — Loft", parent)
        self.subtractive = subtractive
        self.sketches = _selected_sketches()
        self.body = _same_body(self.sketches) if len(self.sketches) >= 2 else None
        self.setMinimumWidth(500)
        layout = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel("Loft sottrattivo" if subtractive else "Loft additivo")
        heading.setStyleSheet("font-size:16px; font-weight:600;")
        layout.addWidget(heading)
        hint = QtWidgets.QLabel(
            "Seleziona almeno due Sketch dello stesso Body. "
            "L'ordine dall'alto verso il basso è l'ordine delle sezioni."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list_widget = QtWidgets.QListWidget()
        for sketch in self.sketches:
            item = QtWidgets.QListWidgetItem(getattr(sketch, "Label", sketch.Name))
            item.setData(QtCore.Qt.UserRole, sketch.Name)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget)
        order_row = QtWidgets.QHBoxLayout()
        self.up_btn = QtWidgets.QPushButton("↑ Su")
        self.down_btn = QtWidgets.QPushButton("↓ Giù")
        order_row.addWidget(self.up_btn)
        order_row.addWidget(self.down_btn)
        order_row.addStretch(1)
        layout.addLayout(order_row)
        self.ruled = QtWidgets.QCheckBox("Superficie rigata tra le sezioni")
        self.closed = QtWidgets.QCheckBox("Chiudi il Loft")
        layout.addWidget(self.ruled)
        layout.addWidget(self.closed)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        layout.addWidget(buttons)
        self.ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.ruled.toggled.connect(self._update_preview)
        self.closed.toggled.connect(self._update_preview)

        if self.body and len(self.sketches) >= 2:
            self._create_preview()
        else:
            self.ok_button.setEnabled(False)
            self.status.setText("Servono almeno due Sketch appartenenti allo stesso Body.")

    def _ordered_sketches(self):
        doc = self.doc or (self.sketches[0].Document if self.sketches else None)
        if doc is None:
            return list(self.sketches)
        result = []
        for i in range(self.list_widget.count()):
            name = self.list_widget.item(i).data(QtCore.Qt.UserRole)
            obj = doc.getObject(name)
            if obj:
                result.append(obj)
        return result

    def _create_preview(self):
        self._begin(self.sketches[0].Document, self.body, "SolidFlow - Loft preview")
        self._remember_visibility(*self.sketches)
        type_id = "PartDesign::SubtractiveLoft" if self.subtractive else "PartDesign::AdditiveLoft"
        try:
            self.feature = self.body.newObject(type_id, "LoftCut" if self.subtractive else "Loft")
            _set_preview_style(self.feature)
            self._update_preview()
        except Exception as exc:
            self.ok_button.setEnabled(False)
            self.status.setText("Impossibile creare l'anteprima: " + str(exc))

    def _move(self, delta):
        row = self.list_widget.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.list_widget.count():
            return
        item = self.list_widget.takeItem(row)
        self.list_widget.insertItem(target, item)
        self.list_widget.setCurrentRow(target)
        self._update_preview()

    def _update_preview(self, *args):
        if not self.feature:
            return
        sketches = self._ordered_sketches()
        if len(sketches) < 2:
            self.ok_button.setEnabled(False)
            return
        try:
            self.feature.Profile = sketches[0]
            self.feature.Sections = sketches[1:]
            self.feature.Ruled = self.ruled.isChecked()
            self.feature.Closed = self.closed.isChecked()
            self.doc.recompute()
            valid = bool(self.feature.isValid() and _shape_ok(self.feature.Shape))
            self.ok_button.setEnabled(valid)
            self.status.setText(
                "✓ Anteprima valida — puoi riordinare le sezioni."
                if valid else "Le sezioni selezionate non producono un Loft valido."
            )
        except Exception as exc:
            self.ok_button.setEnabled(False)
            self.status.setText("Anteprima non valida: " + str(exc))

    def _accept(self):
        sketches = self._ordered_sketches()
        if self._commit_feature(tuple(sketches)):
            super().accept()


def _axis_choices(sketch):
    axes = [("V_Axis", "Asse verticale dello Sketch"), ("H_Axis", "Asse orizzontale dello Sketch")]
    try:
        count = int(sketch.getAxisCount())
    except Exception:
        count = 0
    for i in range(count):
        axes.append((f"Axis{i}", f"Linea di costruzione {i + 1}"))
    return axes


class HelixDialog(_NativePreviewDialog):
    MODES = [
        ("Passo + altezza + angolo", 0),
        ("Passo + giri + angolo", 1),
        ("Altezza + giri + angolo", 2),
        ("Altezza + giri + crescita", 3),
    ]

    def __init__(self, subtractive=False, parent=None):
        super().__init__("SolidFlow — Elica sottrattiva" if subtractive else "SolidFlow — Elica additiva", parent)
        self.subtractive = subtractive
        sketches = _selected_sketches()
        self.sketch = sketches[0] if sketches else None
        self.body = _body_for(self.sketch)
        self.setMinimumWidth(500)
        layout = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel("Elica sottrattiva" if subtractive else "Elica additiva")
        heading.setStyleSheet("font-size:16px; font-weight:600;")
        layout.addWidget(heading)

        form = QtWidgets.QFormLayout()
        self.profile_label = QtWidgets.QLabel(getattr(self.sketch, "Label", "—") if self.sketch else "—")
        form.addRow("Profilo", self.profile_label)
        self.axis = QtWidgets.QComboBox()
        self.axes = _axis_choices(self.sketch) if self.sketch else []
        for _sub, label in self.axes:
            self.axis.addItem(label)
        form.addRow("Asse", self.axis)
        self.mode = QtWidgets.QComboBox()
        for label, _value in self.MODES:
            self.mode.addItem(label)
        form.addRow("Definizione", self.mode)
        self.pitch = QtWidgets.QDoubleSpinBox(); self.pitch.setRange(0.001, 100000.0); self.pitch.setDecimals(3); self.pitch.setSuffix(" mm"); self.pitch.setValue(2.0)
        self.height = QtWidgets.QDoubleSpinBox(); self.height.setRange(0.001, 1000000.0); self.height.setDecimals(3); self.height.setSuffix(" mm"); self.height.setValue(10.0)
        self.turns = QtWidgets.QDoubleSpinBox(); self.turns.setRange(0.001, 1000000.0); self.turns.setDecimals(3); self.turns.setValue(5.0)
        self.angle = QtWidgets.QDoubleSpinBox(); self.angle.setRange(-89.0, 89.0); self.angle.setDecimals(2); self.angle.setSuffix("°")
        self.growth = QtWidgets.QDoubleSpinBox(); self.growth.setRange(-100000.0, 100000.0); self.growth.setDecimals(3); self.growth.setSuffix(" mm/giro")
        form.addRow("Passo", self.pitch); form.addRow("Altezza", self.height); form.addRow("Giri", self.turns); form.addRow("Angolo cono", self.angle); form.addRow("Crescita", self.growth)
        self.left = QtWidgets.QCheckBox("Elica sinistrorsa")
        self.reverse = QtWidgets.QCheckBox("Inverti direzione")
        form.addRow("", self.left); form.addRow("", self.reverse)
        layout.addLayout(form)
        self.status = QtWidgets.QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(buttons)
        self.ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject)

        self.axis.currentIndexChanged.connect(self._update_preview)
        self.mode.currentIndexChanged.connect(self._sync_mode_controls)
        self.mode.currentIndexChanged.connect(self._update_preview)
        self.pitch.valueChanged.connect(self._update_preview)
        self.height.valueChanged.connect(self._update_preview)
        self.turns.valueChanged.connect(self._update_preview)
        self.angle.valueChanged.connect(self._update_preview)
        self.growth.valueChanged.connect(self._update_preview)
        self.left.toggled.connect(self._update_preview)
        self.reverse.toggled.connect(self._update_preview)
        self._sync_mode_controls()

        if self.sketch and self.body:
            self._create_preview()
        else:
            self.ok_button.setEnabled(False)
            self.status.setText("Seleziona uno Sketch appartenente a un Body.")

    def _sync_mode_controls(self, *args):
        mode = self.mode.currentIndex()
        self.pitch.setEnabled(mode in (0, 1)); self.height.setEnabled(mode in (0, 2, 3)); self.turns.setEnabled(mode in (1, 2, 3)); self.angle.setEnabled(mode in (0, 1, 2)); self.growth.setEnabled(mode == 3)

    def _create_preview(self):
        self._begin(self.sketch.Document, self.body, "SolidFlow - Helix preview")
        self._remember_visibility(self.sketch)
        type_id = "PartDesign::SubtractiveHelix" if self.subtractive else "PartDesign::AdditiveHelix"
        try:
            self.feature = self.body.newObject(type_id, "HelixCut" if self.subtractive else "Helix")
            self.feature.Profile = self.sketch
            _set_preview_style(self.feature)
            self._update_preview()
        except Exception as exc:
            self.ok_button.setEnabled(False); self.status.setText("Impossibile creare l'anteprima: " + str(exc))

    def _update_preview(self, *args):
        if not self.feature or not self.axes:
            return
        try:
            mode = self.mode.currentIndex()
            axis_sub = self.axes[self.axis.currentIndex()][0]
            self.feature.Profile = self.sketch
            self.feature.ReferenceAxis = (self.sketch, [axis_sub])
            self.feature.Mode = mode
            if mode in (0, 1): self.feature.Pitch = self.pitch.value()
            if mode in (0, 2, 3): self.feature.Height = self.height.value()
            if mode in (1, 2, 3): self.feature.Turns = self.turns.value()
            if mode in (0, 1, 2): self.feature.Angle = self.angle.value()
            if mode == 3: self.feature.Growth = self.growth.value()
            self.feature.LeftHanded = self.left.isChecked()
            if hasattr(self.feature, "Reversed"): self.feature.Reversed = self.reverse.isChecked()
            self.doc.recompute()
            valid = bool(self.feature.isValid() and _shape_ok(self.feature.Shape))
            self.ok_button.setEnabled(valid)
            self.status.setText("✓ Anteprima valida — elica parametrica nativa." if valid else "Il profilo e i parametri non producono un'elica valida.")
        except Exception as exc:
            self.ok_button.setEnabled(False); self.status.setText("Anteprima non valida: " + str(exc))

    def _accept(self):
        if self._commit_feature((self.sketch,)):
            super().accept()


_METRIC_COARSE = [
    ("M3 × 0.5", 3.0, 0.5), ("M4 × 0.7", 4.0, 0.7), ("M5 × 0.8", 5.0, 0.8),
    ("M6 × 1.0", 6.0, 1.0), ("M8 × 1.25", 8.0, 1.25), ("M10 × 1.5", 10.0, 1.5),
    ("M12 × 1.75", 12.0, 1.75), ("M16 × 2.0", 16.0, 2.0), ("M20 × 2.5", 20.0, 2.5),
    ("M24 × 3.0", 24.0, 3.0), ("M30 × 3.5", 30.0, 3.5),
]


def _selected_cylindrical_face():
    try:
        selection = Gui.Selection.getSelectionEx()
    except Exception:
        return None
    for sx in selection:
        obj = getattr(sx, "Object", None)
        names = list(getattr(sx, "SubElementNames", []) or [])
        subs = list(getattr(sx, "SubObjects", []) or [])
        for i, name in enumerate(names):
            if not name.startswith("Face"):
                continue
            face = subs[i] if i < len(subs) else None
            if face is None:
                try: face = obj.Shape.getElement(name)
                except Exception: continue
            surface = getattr(face, "Surface", None)
            if surface is not None and hasattr(surface, "Radius") and hasattr(surface, "Axis") and hasattr(surface, "Center"):
                return obj, name, face, surface
    return None


def _clear_sketch_geometry(sketch):
    try: count = len(sketch.Geometry)
    except Exception: count = 0
    for index in range(count - 1, -1, -1):
        try: sketch.delGeometry(index)
        except Exception: pass


class ThreadWizardDialog(_NativePreviewDialog):
    """Physical 60° V-groove thread; beta.6 supports global-Z cylinders."""
    def __init__(self, parent=None):
        super().__init__("SolidFlow — Thread Wizard", parent)
        self.selection = _selected_cylindrical_face()
        self.base_obj = self.selection[0] if self.selection else None
        self.face_name = self.selection[1] if self.selection else None
        self.face = self.selection[2] if self.selection else None
        self.surface = self.selection[3] if self.selection else None
        self.body = _body_for(self.base_obj)
        self.profile_sketch = None
        self.base_feature = getattr(self.body, "Tip", None) if self.body else None
        self.setMinimumWidth(520)
        layout = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel("Filettatura fisica per stampa 3D")
        heading.setStyleSheet("font-size:16px; font-weight:600;")
        layout.addWidget(heading)
        info = QtWidgets.QLabel(
            "Seleziona una faccia cilindrica. Questa prima versione genera una gola elicoidale a 60° "
            "tramite SubtractiveHelix. In beta.6 sono supportati cilindri con asse parallelo a Z."
        )
        info.setWordWrap(True); layout.addWidget(info)
        form = QtWidgets.QFormLayout()
        self.face_label = QtWidgets.QLabel((getattr(self.base_obj, "Label", "—") + ":" + str(self.face_name)) if self.base_obj else "—")
        form.addRow("Superficie", self.face_label)
        self.kind = QtWidgets.QComboBox(); self.kind.addItems(["Esterna (vite)", "Interna (foro)"]); form.addRow("Tipo", self.kind)
        self.standard = QtWidgets.QComboBox(); self.standard.addItem("Personalizzata")
        for label, _d, _p in _METRIC_COARSE: self.standard.addItem(label)
        form.addRow("Preset ISO metrico", self.standard)
        self.pitch = QtWidgets.QDoubleSpinBox(); self.pitch.setRange(0.1, 20.0); self.pitch.setDecimals(3); self.pitch.setSuffix(" mm"); self.pitch.setValue(1.0); form.addRow("Passo", self.pitch)
        self.length = QtWidgets.QDoubleSpinBox(); self.length.setRange(0.1, 100000.0); self.length.setDecimals(3); self.length.setSuffix(" mm"); self.length.setValue(self._face_height()); form.addRow("Lunghezza", self.length)
        self.depth_factor = QtWidgets.QDoubleSpinBox(); self.depth_factor.setRange(0.20, 0.80); self.depth_factor.setDecimals(3); self.depth_factor.setSingleStep(0.01); self.depth_factor.setValue(0.613); form.addRow("Profondità / passo", self.depth_factor)
        self.left = QtWidgets.QCheckBox("Filettatura sinistrorsa"); form.addRow("", self.left)
        layout.addLayout(form)
        self.detected = QtWidgets.QLabel(); self.detected.setWordWrap(True); layout.addWidget(self.detected)
        self.status = QtWidgets.QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.preview_btn = buttons.addButton("Aggiorna anteprima", QtWidgets.QDialogButtonBox.ActionRole)
        layout.addWidget(buttons)
        self.ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject); self.preview_btn.clicked.connect(self._update_thread_preview)
        self.standard.currentIndexChanged.connect(self._apply_standard); self.kind.currentIndexChanged.connect(self._update_thread_preview); self.left.toggled.connect(self._update_thread_preview)

        if not self._selection_supported():
            self.ok_button.setEnabled(False); self.preview_btn.setEnabled(False)
        else:
            self._autodetect_standard(); self._create_preview()

    def _face_height(self):
        try: return max(0.1, float(self.face.BoundBox.ZLength))
        except Exception: return 10.0

    def _selection_supported(self):
        if not self.selection or not self.body or Part is None:
            self.detected.setText("Seleziona una singola faccia cilindrica appartenente a un Body Part Design."); return False
        try:
            axis = self.surface.Axis
            axis_len = math.sqrt(axis.x * axis.x + axis.y * axis.y + axis.z * axis.z)
            if axis_len <= 1e-12: raise ValueError("asse nullo")
            cosz = abs(axis.z / axis_len); diameter = 2.0 * float(self.surface.Radius)
            self.detected.setText(f"Diametro rilevato: {diameter:.3f} mm — lunghezza cilindrica: {self._face_height():.3f} mm")
            if cosz < 0.999:
                self.status.setText("Questa beta supporta il Thread Wizard automatico solo con asse cilindro parallelo a Z. Puoi comunque usare Elica sottrattiva con un profilo manuale."); return False
            return True
        except Exception as exc:
            self.status.setText("Superficie cilindrica non utilizzabile: " + str(exc)); return False

    def _autodetect_standard(self):
        diameter = 2.0 * float(self.surface.Radius); best_i = 0; best_error = 1e9
        for i, (_label, d, _p) in enumerate(_METRIC_COARSE, start=1):
            err = abs(diameter - d)
            if err < best_error: best_error, best_i = err, i
        self.standard.setCurrentIndex(best_i if best_error <= max(0.35, diameter * 0.04) else 0)

    def _apply_standard(self, index):
        if index <= 0: return
        _label, _diam, pitch = _METRIC_COARSE[index - 1]
        self.pitch.blockSignals(True); self.pitch.setValue(pitch); self.pitch.blockSignals(False)
        self._update_thread_preview()

    def _create_preview(self):
        doc = self.base_obj.Document
        self._begin(doc, self.body, "SolidFlow - Thread Wizard preview")
        self._remember_visibility(self.base_feature, self.base_obj)
        try:
            self.profile_sketch = self.body.newObject("Sketcher::SketchObject", "ThreadProfile")
            center = self.surface.Center; z0 = float(self.face.BoundBox.ZMin)
            self.profile_sketch.Placement = App.Placement(App.Vector(float(center.x), float(center.y), z0), App.Rotation(App.Vector(1, 0, 0), 90))
            self.feature = self.body.newObject("PartDesign::SubtractiveHelix", "Thread")
            self.feature.Profile = self.profile_sketch
            self.feature.ReferenceAxis = (self.profile_sketch, ["V_Axis"])
            self.feature.Mode = 0; self.feature.Angle = 0.0; self.feature.Growth = 0.0
            _set_preview_style(self.feature); self._update_thread_preview()
        except Exception as exc:
            self.ok_button.setEnabled(False); self.status.setText("Impossibile creare l'anteprima: " + str(exc))

    def _update_thread_preview(self, *args):
        if not self.feature or not self.profile_sketch or Part is None: return
        try:
            pitch = float(self.pitch.value()); height = float(self.length.value()); depth = pitch * float(self.depth_factor.value()); radius = float(self.surface.Radius)
            half_width = max(0.02, depth * math.tan(math.radians(30.0))); epsilon = max(0.015, min(0.05, pitch * 0.03))
            if self.kind.currentIndex() == 0:
                x_base, x_tip = radius + epsilon, max(0.001, radius - depth)
            else:
                x_base, x_tip = max(0.001, radius - epsilon), radius + depth
            _clear_sketch_geometry(self.profile_sketch)
            p1 = App.Vector(x_base, -half_width, 0); p2 = App.Vector(x_tip, 0, 0); p3 = App.Vector(x_base, half_width, 0)
            self.profile_sketch.addGeometry([Part.LineSegment(p1, p2), Part.LineSegment(p2, p3), Part.LineSegment(p3, p1)], False)
            self.feature.Profile = self.profile_sketch; self.feature.ReferenceAxis = (self.profile_sketch, ["V_Axis"]); self.feature.Mode = 0; self.feature.Pitch = pitch; self.feature.Height = height; self.feature.Angle = 0.0; self.feature.LeftHanded = self.left.isChecked()
            if hasattr(self.feature, "Reversed"): self.feature.Reversed = False
            self.doc.recompute(); valid = bool(self.feature.isValid() and _shape_ok(self.feature.Shape)); self.ok_button.setEnabled(valid)
            self.status.setText(f"✓ Anteprima valida — profondità radiale {depth:.3f} mm. Profilo V 60° semplificato, pensato per prototipazione/stampa 3D." if valid else "La filettatura non è valida con questi parametri. Prova a ridurre profondità, passo o lunghezza.")
        except Exception as exc:
            self.ok_button.setEnabled(False); self.status.setText("Anteprima non valida: " + str(exc))

    def _accept(self):
        if self._commit_feature((self.profile_sketch,)):
            try:
                if self.base_feature: self.base_feature.ViewObject.Visibility = False
                self.profile_sketch.ViewObject.Visibility = False
            except Exception: pass
            super().accept()


def _launch_sweep(subtractive=False):
    if len(_selected_sketches()) < 2:
        _message("SolidFlow Sweep", "Seleziona due Sketch dello stesso Body:\n1) profilo\n2) percorso\n\nPoi rilancia Sweep."); return
    _exec_dialog(SweepDialog(subtractive=subtractive))


def _launch_loft(subtractive=False):
    if len(_selected_sketches()) < 2:
        _message("SolidFlow Loft", "Seleziona almeno due Sketch dello stesso Body nell'ordine desiderato."); return
    _exec_dialog(LoftDialog(subtractive=subtractive))


def _launch_helix(subtractive=False):
    if not _selected_sketches():
        _message("SolidFlow Elica", "Seleziona lo Sketch del profilo. Le linee di costruzione dello Sketch saranno disponibili come assi."); return
    _exec_dialog(HelixDialog(subtractive=subtractive))


def _launch_thread():
    if _selected_cylindrical_face() is None:
        _message("SolidFlow Thread Wizard", "Seleziona una faccia cilindrica del solido e rilancia Thread Wizard."); return
    _exec_dialog(ThreadWizardDialog())


class _Command:
    def __init__(self, text, tip, callback): self.text, self.tip, self.callback = text, tip, callback
    def GetResources(self): return {"MenuText": self.text, "ToolTip": self.tip}
    def IsActive(self): return App.ActiveDocument is not None
    def Activated(self): self.callback()


_COMMANDS = {
    "SolidFlow_AdditiveSweep": _Command("Sweep", "Crea uno Sweep additivo nativo da profilo e percorso selezionati", lambda: _launch_sweep(False)),
    "SolidFlow_SubtractiveSweep": _Command("Sweep Cut", "Crea uno Sweep sottrattivo nativo", lambda: _launch_sweep(True)),
    "SolidFlow_AdditiveLoft": _Command("Loft", "Crea un Loft additivo nativo da più Sketch", lambda: _launch_loft(False)),
    "SolidFlow_SubtractiveLoft": _Command("Loft Cut", "Crea un Loft sottrattivo nativo", lambda: _launch_loft(True)),
    "SolidFlow_AdditiveHelix": _Command("Elica", "Crea un'elica additiva parametrica", lambda: _launch_helix(False)),
    "SolidFlow_SubtractiveHelix": _Command("Elica Cut", "Crea un'elica sottrattiva parametrica", lambda: _launch_helix(True)),
    "SolidFlow_ThreadWizard": _Command("Thread Wizard…", "Genera una filettatura fisica beta su una faccia cilindrica", _launch_thread),
}


class AdvancedFeatureController:
    def __init__(self): self._installed = False; self._marker = None
    def _register_commands(self):
        for command_id, command in _COMMANDS.items():
            try: Gui.addCommand(command_id, command)
            except Exception as exc: App.Console.PrintWarning("SolidFlow beta.6 comando %s: %s\n" % (command_id, exc))
    def _find_solidflow_menu(self):
        main = Gui.getMainWindow()
        for menu in main.findChildren(QtWidgets.QMenu):
            try: title = menu.title().replace("&", "").strip().lower()
            except Exception: continue
            if title == "solidflow": return menu
        return None
    def _augment_menu(self):
        menu = self._find_solidflow_menu()
        if menu is None or menu.findChild(QtCore.QObject, "SolidFlowBeta6Marker"): return
        marker = QtCore.QObject(menu); marker.setObjectName("SolidFlowBeta6Marker"); self._marker = marker
        menu.addSeparator(); advanced = menu.addMenu("Feature avanzate")
        advanced.addAction("Sweep", lambda: Gui.runCommand("SolidFlow_AdditiveSweep", 0)); advanced.addAction("Sweep Cut", lambda: Gui.runCommand("SolidFlow_SubtractiveSweep", 0)); advanced.addSeparator()
        advanced.addAction("Loft", lambda: Gui.runCommand("SolidFlow_AdditiveLoft", 0)); advanced.addAction("Loft Cut", lambda: Gui.runCommand("SolidFlow_SubtractiveLoft", 0)); advanced.addSeparator()
        advanced.addAction("Elica", lambda: Gui.runCommand("SolidFlow_AdditiveHelix", 0)); advanced.addAction("Elica Cut", lambda: Gui.runCommand("SolidFlow_SubtractiveHelix", 0)); advanced.addSeparator()
        advanced.addAction("Thread Wizard…", lambda: Gui.runCommand("SolidFlow_ThreadWizard", 0))
    def install(self):
        if self._installed: return
        self._register_commands(); self._installed = True
        QtCore.QTimer.singleShot(800, self._augment_menu); QtCore.QTimer.singleShot(1600, self._augment_menu)
        App.Console.PrintMessage("SolidFlow UX %s: Sweep / Loft / Helix / Thread Wizard caricati.\n" % VERSION)


_controller = None

def install():
    global _controller
    app = QtWidgets.QApplication.instance()
    old = getattr(app, "_solidflow_beta6_controller", None) if app else None
    if old is not None and old is not _controller:
        try: old._installed = False
        except Exception: pass
    if _controller is None or not getattr(_controller, "_installed", False):
        _controller = AdvancedFeatureController(); _controller.install()
    if app: app._solidflow_beta6_controller = _controller
    return _controller
