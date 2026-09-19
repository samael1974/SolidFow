# -*- coding: utf-8 -*-
"""SolidFlow parametric 3D paths.

Provides a HelixPath (Part::FeaturePython) that can be created directly from a
cylindrical face and used as the Spine of a native PartDesign Pipe/Sweep.
A helix is intrinsically a 3D curve, so it is represented as a parametric path
rather than pretending to be a planar Sketcher sketch.
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

VERSION = "0.4.0-beta.11-paths"
ROLE = "HelixPath"


def _main_window():
    return Gui.getMainWindow()


def _message(title, text, icon=QtWidgets.QMessageBox.Information):
    box = QtWidgets.QMessageBox(_main_window())
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(icon)
    box.exec_()


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
                pass
    return None


def _unit(vec):
    length = math.sqrt(vec.x * vec.x + vec.y * vec.y + vec.z * vec.z)
    if length <= 1e-12:
        return App.Vector(0, 0, 1)
    return App.Vector(vec.x / length, vec.y / length, vec.z / length)


def _cylindrical_face_info():
    try:
        selection = Gui.Selection.getSelectionEx()
    except Exception:
        return None
    for sx in selection:
        obj = getattr(sx, "Object", None)
        names = list(getattr(sx, "SubElementNames", []) or [])
        subs = list(getattr(sx, "SubObjects", []) or [])
        for i, name in enumerate(names):
            if not str(name).startswith("Face"):
                continue
            face = subs[i] if i < len(subs) else None
            if face is None:
                try:
                    face = obj.Shape.getElement(str(name))
                except Exception:
                    continue
            surface = getattr(face, "Surface", None)
            if surface is None or not all(hasattr(surface, p) for p in ("Radius", "Axis", "Center")):
                continue
            try:
                axis = _unit(surface.Axis)
                center = App.Vector(surface.Center)
                projections = []
                for vertex in list(getattr(face, "Vertexes", []) or []):
                    delta = vertex.Point - center
                    projections.append(delta.dot(axis))
                if projections:
                    t0, t1 = min(projections), max(projections)
                else:
                    # Fallback for unusual cylinder trims.
                    t0, t1 = 0.0, max(1.0, float(face.BoundBox.DiagonalLength))
                start = center + axis * t0
                height = max(0.001, t1 - t0)
                return {
                    "object": obj,
                    "face_name": str(name),
                    "face": face,
                    "surface": surface,
                    "axis": axis,
                    "start": start,
                    "radius": float(surface.Radius),
                    "height": height,
                }
            except Exception:
                continue
    return None


def is_helix_path(obj):
    try:
        return str(getattr(obj, "SolidFlowRole", "")) == ROLE
    except Exception:
        return False


class HelixPathProxy:
    def __init__(self, obj=None):
        if obj is not None:
            self.attach(obj)

    def attach(self, obj):
        obj.Proxy = self
        existing = set(getattr(obj, "PropertiesList", []) or [])
        if "SolidFlowRole" not in existing:
            obj.addProperty("App::PropertyString", "SolidFlowRole", "SolidFlow", "SolidFlow helper type")
        obj.SolidFlowRole = ROLE
        if "Definition" not in existing:
            obj.addProperty("App::PropertyEnumeration", "Definition", "Helix", "Define by height or turns")
            obj.Definition = ["Altezza", "Giri"]
            obj.Definition = "Altezza"
        if "Pitch" not in existing:
            obj.addProperty("App::PropertyLength", "Pitch", "Helix", "Helix pitch")
            obj.Pitch = 1.5
        if "Height" not in existing:
            obj.addProperty("App::PropertyLength", "Height", "Helix", "Helix height")
            obj.Height = 10.0
        if "Turns" not in existing:
            obj.addProperty("App::PropertyFloat", "Turns", "Helix", "Number of turns")
            obj.Turns = 6.6666667
        if "Radius" not in existing:
            obj.addProperty("App::PropertyLength", "Radius", "Helix", "Base radius")
            obj.Radius = 10.0
        if "Offset" not in existing:
            obj.addProperty("App::PropertyLength", "Offset", "Helix", "Radial offset from detected cylinder")
            obj.Offset = 0.0
        if "ConeAngle" not in existing:
            obj.addProperty("App::PropertyAngle", "ConeAngle", "Helix", "Conical helix angle")
            obj.ConeAngle = 0.0
        if "LeftHanded" not in existing:
            obj.addProperty("App::PropertyBool", "LeftHanded", "Helix", "Left-handed helix")
            obj.LeftHanded = False
        if "Axis" not in existing:
            obj.addProperty("App::PropertyVector", "Axis", "Placement", "Helix axis")
            obj.Axis = App.Vector(0, 0, 1)
        if "StartPoint" not in existing:
            obj.addProperty("App::PropertyVector", "StartPoint", "Placement", "Point where the helix starts")
            obj.StartPoint = App.Vector(0, 0, 0)
        if "SupportFeature" not in existing:
            obj.addProperty("App::PropertyLinkSub", "SupportFeature", "Reference", "Optional source cylindrical face")

    def execute(self, obj):
        if Part is None:
            return
        try:
            pitch = max(1e-6, float(obj.Pitch.Value))
            radius = max(1e-6, float(obj.Radius.Value) + float(obj.Offset.Value))
            definition = str(obj.Definition)
            if definition == "Giri":
                height = max(1e-6, pitch * max(1e-6, float(obj.Turns)))
            else:
                height = max(1e-6, float(obj.Height.Value))
            try:
                angle = float(obj.ConeAngle.Value)
            except Exception:
                angle = float(obj.ConeAngle)
            shape = Part.makeHelix(pitch, height, radius, angle, bool(obj.LeftHanded))
            axis = _unit(App.Vector(obj.Axis))
            rotation = App.Rotation(App.Vector(0, 0, 1), axis)
            shape.Placement = App.Placement(App.Vector(obj.StartPoint), rotation)
            obj.Shape = shape
        except Exception as exc:
            App.Console.PrintError("SolidFlow HelixPath: %s\n" % exc)

    def onChanged(self, obj, prop):
        if prop in ("Pitch", "Height", "Turns", "Radius", "Offset", "ConeAngle", "LeftHanded", "Axis", "StartPoint", "Definition"):
            try:
                obj.Document.recompute()
            except Exception:
                pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class HelixPathDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or _main_window())
        self.info = _cylindrical_face_info()
        self.doc = self.info["object"].Document if self.info else App.ActiveDocument
        self.path = None
        self._tx = False
        self._accepted = False
        self.setWindowTitle("SolidFlow — Percorso elicoidale 3D")
        self.resize(500, 390)

        root = QtWidgets.QVBoxLayout(self)
        root.addWidget(QtWidgets.QLabel("<b>Percorso elicoidale parametrico</b>"))
        hint = QtWidgets.QLabel(
            "Seleziona una faccia cilindrica prima di aprire il comando. L'elica viene creata come curva 3D "
            "parametrica, pronta per essere usata come percorso di uno Sweep/Pipe."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QtWidgets.QFormLayout()
        self.source = QtWidgets.QLabel("—")
        form.addRow("Riferimento", self.source)
        self.definition = QtWidgets.QComboBox(); self.definition.addItems(["Altezza", "Giri"]); form.addRow("Definizione", self.definition)
        self.pitch = QtWidgets.QDoubleSpinBox(); self.pitch.setDecimals(3); self.pitch.setRange(0.001, 100000.0); self.pitch.setSuffix(" mm"); self.pitch.setValue(1.5); form.addRow("Passo", self.pitch)
        self.height = QtWidgets.QDoubleSpinBox(); self.height.setDecimals(3); self.height.setRange(0.001, 1000000.0); self.height.setSuffix(" mm"); self.height.setValue(10.0); form.addRow("Altezza", self.height)
        self.turns = QtWidgets.QDoubleSpinBox(); self.turns.setDecimals(3); self.turns.setRange(0.001, 1000000.0); self.turns.setValue(6.0); form.addRow("Giri", self.turns)
        self.radius = QtWidgets.QDoubleSpinBox(); self.radius.setDecimals(3); self.radius.setRange(0.001, 1000000.0); self.radius.setSuffix(" mm"); self.radius.setValue(10.0); form.addRow("Raggio", self.radius)
        self.offset = QtWidgets.QDoubleSpinBox(); self.offset.setDecimals(3); self.offset.setRange(-1000000.0, 1000000.0); self.offset.setSuffix(" mm"); self.offset.setValue(0.0); form.addRow("Offset radiale", self.offset)
        self.angle = QtWidgets.QDoubleSpinBox(); self.angle.setDecimals(2); self.angle.setRange(-89.0, 89.0); self.angle.setSuffix(" °"); form.addRow("Angolo cono", self.angle)
        self.left = QtWidgets.QCheckBox("Elica sinistrorsa"); form.addRow("", self.left)
        root.addLayout(form)

        self.status = QtWidgets.QLabel(); self.status.setWordWrap(True); root.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        root.addWidget(buttons)
        self.ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        for widget in (self.definition, self.pitch, self.height, self.turns, self.radius, self.offset, self.angle, self.left):
            signal = getattr(widget, "currentIndexChanged", None) or getattr(widget, "valueChanged", None) or getattr(widget, "toggled", None)
            if signal is not None:
                signal.connect(self._update_preview)
        self.definition.currentIndexChanged.connect(self._sync_definition)

        if self.info and Part is not None:
            self.source.setText("%s : %s" % (getattr(self.info["object"], "Label", self.info["object"].Name), self.info["face_name"]))
            self.radius.setValue(self.info["radius"])
            self.height.setValue(self.info["height"])
            self.turns.setValue(max(0.001, self.info["height"] / self.pitch.value()))
            self._begin_preview()
        else:
            self.ok_button.setEnabled(False)
            self.status.setText("Seleziona una faccia cilindrica appartenente al modello e riapri il comando.")
        self._sync_definition()

    def _sync_definition(self, *_args):
        by_turns = self.definition.currentText() == "Giri"
        self.height.setEnabled(not by_turns)
        self.turns.setEnabled(by_turns)

    def _begin_preview(self):
        try:
            self.doc.openTransaction("SolidFlow - Helix Path preview")
            self._tx = True
            self.path = self.doc.addObject("Part::FeaturePython", "HelixPath")
            self.path.Label = "Percorso elicoidale"
            HelixPathProxy(self.path)
            self.path.Axis = self.info["axis"]
            self.path.StartPoint = self.info["start"]
            self.path.Radius = self.info["radius"]
            try:
                self.path.SupportFeature = (self.info["object"], [self.info["face_name"]])
            except Exception:
                pass
            try:
                self.path.ViewObject.LineColor = (1.0, 0.45, 0.05)
                self.path.ViewObject.PointColor = (1.0, 0.45, 0.05)
                self.path.ViewObject.LineWidth = 4.0
            except Exception:
                pass
            self._update_preview()
        except Exception as exc:
            self.ok_button.setEnabled(False)
            self.status.setText("Impossibile creare l'anteprima: " + str(exc))

    def _update_preview(self, *_args):
        if self.path is None:
            return
        try:
            self.path.Definition = self.definition.currentText()
            self.path.Pitch = self.pitch.value()
            self.path.Height = self.height.value()
            self.path.Turns = self.turns.value()
            self.path.Radius = self.radius.value()
            self.path.Offset = self.offset.value()
            self.path.ConeAngle = self.angle.value()
            self.path.LeftHanded = self.left.isChecked()
            self.doc.recompute()
            valid = self.path.Shape is not None and not self.path.Shape.isNull() and len(self.path.Shape.Edges) > 0
            self.ok_button.setEnabled(valid)
            if self.definition.currentText() == "Giri":
                computed = self.pitch.value() * self.turns.value()
                self.status.setText("✓ Percorso valido — altezza risultante %.3f mm. Selezionalo con uno Sketch profilo e usa Sweep." % computed if valid else "Percorso non valido.")
            else:
                computed_turns = self.height.value() / max(1e-9, self.pitch.value())
                self.status.setText("✓ Percorso valido — %.3f giri. Selezionalo con uno Sketch profilo e usa Sweep." % computed_turns if valid else "Percorso non valido.")
        except Exception as exc:
            self.ok_button.setEnabled(False)
            self.status.setText("Anteprima non valida: " + str(exc))

    def _rollback(self):
        if self._tx:
            try:
                self.doc.abortTransaction()
            except Exception:
                try:
                    if self.path and self.doc.getObject(self.path.Name):
                        self.doc.removeObject(self.path.Name)
                        self.doc.recompute()
                except Exception:
                    pass
            self._tx = False
        self.path = None

    def accept(self):
        if self.path is None or not self.ok_button.isEnabled():
            return
        try:
            self.doc.recompute()
            if self._tx:
                self.doc.commitTransaction()
                self._tx = False
            self._accepted = True
            try:
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(self.path)
            except Exception:
                pass
            super().accept()
        except Exception as exc:
            _message("SolidFlow", "Conferma del percorso fallita:\n" + str(exc), QtWidgets.QMessageBox.Critical)

    def reject(self):
        if not self._accepted:
            self._rollback()
        super().reject()

    def closeEvent(self, event):
        if not self._accepted:
            self._rollback()
        super().closeEvent(event)


def launch_helix_path():
    if _cylindrical_face_info() is None:
        _message("SolidFlow — Percorso elicoidale", "Seleziona una faccia cilindrica, poi avvia 'Elica percorso 3D'.")
        return
    HelixPathDialog().exec_()


def selected_profile_path_pair():
    try:
        objects = Gui.Selection.getSelection()
    except Exception:
        return None, None
    profile = None
    path = None
    for obj in objects:
        if _is_sketch(obj):
            if profile is not None:
                return None, None
            profile = obj
        elif is_helix_path(obj):
            if path is not None:
                return None, None
            path = obj
    return (profile, path) if profile is not None and path is not None and len(objects) == 2 else (None, None)


class SweepPathDialog(QtWidgets.QDialog):
    def __init__(self, profile, path, subtractive=False, parent=None):
        super().__init__(parent or _main_window())
        self.profile = profile
        self.path = path
        self.subtractive = bool(subtractive)
        self.body = _body_for(profile)
        self.doc = profile.Document
        self.feature = None
        self._tx = False
        self._accepted = False
        self.setWindowTitle("SolidFlow — Sweep su percorso elicoidale")
        self.resize(500, 330)

        root = QtWidgets.QVBoxLayout(self)
        root.addWidget(QtWidgets.QLabel("<b>Sweep / Pipe su percorso 3D</b>"))
        form = QtWidgets.QFormLayout()
        form.addRow("Profilo", QtWidgets.QLabel(getattr(profile, "Label", profile.Name)))
        form.addRow("Percorso", QtWidgets.QLabel(getattr(path, "Label", path.Name)))
        self.operation = QtWidgets.QComboBox(); self.operation.addItems(["Aggiungi materiale", "Rimuovi materiale"]); self.operation.setCurrentIndex(1 if subtractive else 0); form.addRow("Operazione", self.operation)
        self.mode = QtWidgets.QComboBox(); self.mode.addItems(["Standard", "Fisso", "Frenet"]); form.addRow("Orientamento", self.mode)
        self.transition = QtWidgets.QComboBox(); self.transition.addItems(["Trasformato", "Angolo retto", "Angolo arrotondato"]); form.addRow("Transizione", self.transition)
        self.keep_path = QtWidgets.QCheckBox("Mantieni visibile il percorso dopo la lavorazione"); self.keep_path.setChecked(True); form.addRow("", self.keep_path)
        root.addLayout(form)
        self.status = QtWidgets.QLabel(); self.status.setWordWrap(True); root.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        root.addWidget(buttons)
        self.ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        self.operation.currentIndexChanged.connect(self._recreate_feature)
        self.mode.currentIndexChanged.connect(self._update_preview)
        self.transition.currentIndexChanged.connect(self._update_preview)

        if self.body is None:
            self.ok_button.setEnabled(False)
            self.status.setText("Lo Sketch profilo deve appartenere a un Body Part Design.")
        else:
            self._begin()

    def _begin(self):
        self.doc.openTransaction("SolidFlow - Sweep path preview")
        self._tx = True
        self._create_feature()

    def _create_feature(self):
        try:
            if self.feature is not None and self.doc.getObject(self.feature.Name):
                self.doc.removeObject(self.feature.Name)
                self.feature = None
                self.doc.recompute()
            subtractive = self.operation.currentIndex() == 1
            type_id = "PartDesign::SubtractivePipe" if subtractive else "PartDesign::AdditivePipe"
            self.feature = self.body.newObject(type_id, "SweepHelixCut" if subtractive else "SweepHelix")
            self.feature.Profile = self.profile
            self.feature.Spine = (self.path, ["Edge1"])
            try:
                self.feature.ViewObject.Transparency = 55
            except Exception:
                pass
            self._update_preview()
        except Exception as exc:
            self.ok_button.setEnabled(False)
            self.status.setText("Impossibile creare lo Sweep: " + str(exc))

    def _recreate_feature(self, *_args):
        if self._tx:
            self._create_feature()

    def _update_preview(self, *_args):
        if self.feature is None:
            return
        try:
            self.feature.Profile = self.profile
            self.feature.Spine = (self.path, ["Edge1"])
            self.feature.Mode = self.mode.currentIndex()
            self.feature.Transition = self.transition.currentIndex()
            self.feature.SpineTangent = False
            self.doc.recompute()
            shape = self.feature.Shape
            valid = bool(self.feature.isValid() and shape is not None and not shape.isNull() and shape.isValid())
            self.ok_button.setEnabled(valid)
            self.status.setText("✓ Anteprima valida — Sweep Part Design nativo." if valid else "Il profilo/percorso non produce uno Sweep valido. Controlla posizione e orientamento dello Sketch profilo.")
        except Exception as exc:
            self.ok_button.setEnabled(False)
            self.status.setText("Anteprima non valida: " + str(exc))

    def _rollback(self):
        if self._tx:
            try:
                self.doc.abortTransaction()
            except Exception:
                try:
                    if self.feature and self.doc.getObject(self.feature.Name):
                        self.doc.removeObject(self.feature.Name)
                    self.doc.recompute()
                except Exception:
                    pass
            self._tx = False
        self.feature = None

    def accept(self):
        if self.feature is None or not self.ok_button.isEnabled():
            return
        try:
            self.feature.ViewObject.Transparency = 0
            self.doc.recompute()
            if self._tx:
                self.doc.commitTransaction()
                self._tx = False
            self._accepted = True
            try:
                self.profile.ViewObject.Visibility = False
                self.path.ViewObject.Visibility = self.keep_path.isChecked()
            except Exception:
                pass
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(self.feature)
            super().accept()
        except Exception as exc:
            _message("SolidFlow", "Conferma dello Sweep fallita:\n" + str(exc), QtWidgets.QMessageBox.Critical)

    def reject(self):
        if not self._accepted:
            self._rollback()
        super().reject()

    def closeEvent(self, event):
        if not self._accepted:
            self._rollback()
        super().closeEvent(event)


def launch_sweep_path(subtractive=False):
    profile, path = selected_profile_path_pair()
    if profile is None or path is None:
        _message("SolidFlow — Sweep", "Seleziona esattamente uno Sketch profilo e un Percorso elicoidale, tenendo premuto Ctrl.")
        return
    SweepPathDialog(profile, path, subtractive=subtractive).exec_()


def install():
    class _HelixCommand:
        def GetResources(self):
            return {"MenuText": "Elica percorso 3D...", "ToolTip": "Crea un percorso elicoidale parametrico da una faccia cilindrica"}
        def IsActive(self): return App.ActiveDocument is not None
        def Activated(self): launch_helix_path()

    class _SweepCommand:
        def GetResources(self):
            return {"MenuText": "Sweep su percorso 3D...", "ToolTip": "Usa uno Sketch e un percorso elicoidale come Pipe/Sweep Part Design"}
        def IsActive(self): return App.ActiveDocument is not None
        def Activated(self): launch_sweep_path(False)

    try:
        Gui.addCommand("SolidFlow_HelixPath", _HelixCommand())
        Gui.addCommand("SolidFlow_SweepPath", _SweepCommand())
    except Exception:
        pass
