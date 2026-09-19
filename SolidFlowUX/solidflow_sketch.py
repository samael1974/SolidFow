# -*- coding: utf-8 -*-
"""SolidFlow consolidated Sketch UX controller — beta15.

Replaces the old beta.4 quick-constraint runtime layer.  Responsibilities:
- reversible SolidWorks-like viewport/sketch colour preset;
- one automatic mini-window with 3-5 likely native constraints/dimensions;
- explicit user confirmation for every constraint;
- shared Smart Sketch settings and snap tuning.

Pattern buttons live directly in ``solidflow_ui`` so they cannot disappear
because of a patch/load-order issue.
"""
from __future__ import annotations

import time

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

try:
    import Part
except Exception:
    Part = None

try:
    from pivy import coin
except Exception:
    coin = None

import solidflow_smart as smart

VERSION = "0.4.0-beta.19-actions-plane"
ROOT = App.ParamGet("User parameter:BaseApp/Preferences/Mod/SolidFlowUX")
VIEW = App.ParamGet("User parameter:BaseApp/Preferences/View")
THEME_BACKUP = App.ParamGet("User parameter:BaseApp/Preferences/Mod/SolidFlowUX/ThemeBackup")


def _rgba(r, g, b, a=255):
    return ((r & 255) << 24) | ((g & 255) << 16) | ((b & 255) << 8) | (a & 255)


_THEME_BOOL = {
    "Gradient": True,
    "RadialGradient": False,
    "Simple": False,
    "UseBackgroundColorMid": False,
}

# SolidWorks-like working palette.  These are intentionally a reproducible
# SolidFlow preset, not a claim that every SolidWorks installation uses fixed
# RGB values (SolidWorks colour schemes/scenes are user-configurable).
_THEME_UINT = {
    "BackgroundColor": _rgba(224, 232, 239),
    "BackgroundColor2": _rgba(111, 143, 171),
    "BackgroundColor3": _rgba(232, 238, 244),
    "BackgroundColor4": _rgba(248, 250, 252),
    "DefaultShapeColor": _rgba(210, 215, 220),
    "DefaultShapeLineColor": _rgba(55, 62, 68),
    "DefaultShapeVertexColor": _rgba(55, 62, 68),
    "HighlightColor": _rgba(255, 176, 0),
    "SelectionColor": _rgba(0, 153, 204),
    "AxisLetterColor": _rgba(40, 45, 50),
    "AnnotationTextColor": _rgba(35, 40, 45),
    # Sketch status convention: blue under-defined, dark fully-defined,
    # external/reference purple, invalid red.
    "SketchEdgeColor": _rgba(38, 103, 189),
    "SketchVertexColor": _rgba(38, 103, 189),
    "EditedEdgeColor": _rgba(38, 103, 189),
    "FullyConstrainedColor": _rgba(35, 35, 35),
    "FullyConstraintElementColor": _rgba(35, 35, 35),
    "FullyConstraintConstructionElementColor": _rgba(90, 105, 120),
    "ConstructionColor": _rgba(105, 135, 165),
    "ExternalColor": _rgba(176, 72, 184),
    "ExternalDefiningColor": _rgba(176, 72, 184),
    "InvalidSketchColor": _rgba(210, 45, 45),
    "InternalAlignedGeoColor": _rgba(95, 110, 125),
    "ConstrainedIcoColor": _rgba(55, 60, 65),
    "ConstrainedDimColor": _rgba(55, 60, 65),
    "NonDrivingConstrDimColor": _rgba(75, 125, 155),
    "ExprBasedConstrDimColor": _rgba(95, 75, 145),
    "DeactivatedConstrDimColor": _rgba(135, 140, 145),
    "CursorTextColor": _rgba(25, 30, 35),
    "CursorCrosshairColor": _rgba(40, 45, 50),
    "CreateLineColor": _rgba(38, 103, 189),
}


def _backup_theme_once():
    if THEME_BACKUP.GetBool("Stored", False):
        return
    for key in _THEME_BOOL:
        THEME_BACKUP.SetBool("b_" + key, VIEW.GetBool(key, False))
    for key in _THEME_UINT:
        THEME_BACKUP.SetUnsigned("u_" + key, VIEW.GetUnsigned(key, 0))
    THEME_BACKUP.SetBool("Stored", True)


def apply_sw_like_theme():
    _backup_theme_once()
    for key, value in _THEME_BOOL.items():
        VIEW.SetBool(key, value)
    for key, value in _THEME_UINT.items():
        VIEW.SetUnsigned(key, value)
    ROOT.SetBool("SWLikeTheme", True)
    try:
        Gui.updateGui()
    except Exception:
        pass


def restore_previous_theme():
    if not THEME_BACKUP.GetBool("Stored", False):
        return
    for key in _THEME_BOOL:
        VIEW.SetBool(key, THEME_BACKUP.GetBool("b_" + key, False))
    for key in _THEME_UINT:
        VIEW.SetUnsigned(key, THEME_BACKUP.GetUnsigned("u_" + key, 0))
    ROOT.SetBool("SWLikeTheme", False)
    try:
        Gui.updateGui()
    except Exception:
        pass


def _command_action(command):
    try:
        native = Gui.Command.get(command)
        actions = native.getAction() if native else []
        return actions[0] if actions else None
    except Exception:
        return None


def _run_native(command):
    try:
        Gui.runCommand(command, 0)
        Gui.updateGui()
    except Exception as exc:
        App.Console.PrintError("SolidFlow Sketch command %s: %s\n" % (command, exc))


def _active_body():
    """Return the most likely active Part Design Body without changing the document."""
    doc = App.ActiveDocument
    if doc is None:
        return None

    # Prefer a Body related to the current selection.
    try:
        for obj in Gui.Selection.getSelection():
            if getattr(obj, "TypeId", "") == "PartDesign::Body":
                return obj
            try:
                parent = obj.getParentGeoFeatureGroup()
                if parent is not None and getattr(parent, "TypeId", "") == "PartDesign::Body":
                    return parent
            except Exception:
                pass
    except Exception:
        pass

    # Prefer the Body containing the sketch currently being edited.
    try:
        sketch = smart.active_sketch()
        if sketch is not None:
            parent = sketch.getParentGeoFeatureGroup()
            if parent is not None and getattr(parent, "TypeId", "") == "PartDesign::Body":
                return parent
    except Exception:
        pass

    # Ask the GUI for the active Part Design body when available.
    try:
        view = Gui.ActiveDocument.ActiveView if Gui.ActiveDocument else None
        if view is not None and hasattr(view, "getActiveObject"):
            body = view.getActiveObject("pdbody")
            if body is not None and getattr(body, "TypeId", "") == "PartDesign::Body":
                return body
    except Exception:
        pass

    # If the document only has one Body there is no ambiguity.
    try:
        bodies = [o for o in doc.Objects if getattr(o, "TypeId", "") == "PartDesign::Body"]
        if len(bodies) == 1:
            return bodies[0]
    except Exception:
        pass
    return None


def _activate_body(body):
    if body is None:
        return
    try:
        if Gui.ActiveDocument:
            Gui.ActiveDocument.ActiveView.setActiveObject("pdbody", body)
    except Exception:
        pass


def _origin_plane(body, role):
    """Find one of the Body Origin planes by Role (XY_Plane/XZ_Plane/YZ_Plane)."""
    if body is None:
        return None
    try:
        origin = body.Origin
        for feature in origin.OriginFeatures:
            if getattr(feature, "Role", "") == role:
                return feature
    except Exception:
        pass
    # Compatibility fallback for documents/builds exposing the role by object name.
    try:
        origin = body.Origin
        for feature in origin.OriginFeatures:
            if getattr(feature, "Name", "") == role or getattr(feature, "Label", "") == role:
                return feature
    except Exception:
        pass
    return None


def _xyz_rotation(rx, ry, rz):
    """Compose local X/Y/Z rotations for an AttachmentOffset."""
    rxr = App.Rotation(App.Vector(1, 0, 0), float(rx))
    ryr = App.Rotation(App.Vector(0, 1, 0), float(ry))
    rzr = App.Rotation(App.Vector(0, 0, 1), float(rz))
    try:
        return rzr.multiply(ryr.multiply(rxr))
    except Exception:
        try:
            return rzr * ryr * rxr
        except Exception:
            # Last-resort fallback; on standard FreeCAD builds the branches above are used.
            return App.Rotation(float(rz), float(ry), float(rx))


def _create_construction_plane(body, base_role, offset, rx, ry, rz):
    """Create a current FreeCAD Part::DatumPlane attached to one Body origin plane."""
    doc = App.ActiveDocument
    if doc is None or body is None:
        raise RuntimeError("Nessun documento/Body attivo.")
    base = _origin_plane(body, base_role)
    if base is None:
        raise RuntimeError("Piano origine %s non trovato." % base_role.replace("_Plane", ""))

    doc.openTransaction("SolidFlow - Piano di costruzione")
    plane = None
    try:
        # FreeCAD 1.1 favours the generic Part datum types. Keep a legacy fallback.
        try:
            plane = body.newObject("Part::DatumPlane", "ConstructionPlane")
        except Exception:
            try:
                plane = doc.addObject("Part::DatumPlane", "ConstructionPlane")
                if hasattr(body, "addObjects"):
                    body.addObjects([plane])
                else:
                    body.addObject(plane)
            except Exception:
                plane = body.newObject("PartDesign::Plane", "ConstructionPlane")

        plane.Label = "Piano costruzione %s" % base_role.replace("_Plane", "")
        origin = body.Origin

        attached = False
        # Current generic datum API: reference the role through the Body Origin.
        if hasattr(plane, "AttachmentSupport"):
            try:
                plane.AttachmentSupport = [(origin, (base.Name + ".",))]
                attached = True
            except Exception:
                try:
                    plane.AttachmentSupport = [(base, "")]
                    attached = True
                except Exception:
                    pass
        if not attached and hasattr(plane, "Support"):
            try:
                plane.Support = [(base, "")]
                attached = True
            except Exception:
                try:
                    plane.Support = (base, [""])
                    attached = True
                except Exception:
                    pass
        if not attached:
            raise RuntimeError("Impossibile collegare il piano al riferimento di origine.")

        try:
            plane.MapMode = "FlatFace"
        except Exception:
            pass

        plane.AttachmentOffset = App.Placement(
            App.Vector(0.0, 0.0, float(offset)),
            _xyz_rotation(rx, ry, rz),
        )
        doc.recompute()
        try:
            plane.Visibility = True
        except Exception:
            pass
        try:
            doc.commitTransaction()
        except Exception:
            pass
        return plane
    except Exception:
        try:
            doc.abortTransaction()
        except Exception:
            if plane is not None:
                try:
                    doc.removeObject(plane.Name)
                except Exception:
                    pass
        raise


class ConstructionPlaneDialog(QtWidgets.QDialog):
    """Small SolidWorks-like helper for creating rotated construction planes."""

    def __init__(self, parent=None):
        super().__init__(parent or Gui.getMainWindow())
        self.setWindowTitle("SolidFlow - Piano di costruzione")
        self.setModal(False)
        self.setMinimumWidth(420)
        self.body = None
        self._last_plane = None

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(9)

        title = QtWidgets.QLabel("Piano di costruzione rapido")
        font = title.font()
        font.setBold(True)
        font.setPointSize(max(font.pointSize(), 11))
        title.setFont(font)
        outer.addWidget(title)

        info = QtWidgets.QLabel(
            "Scegli il piano base del Body, poi ruotalo attorno agli assi locali X/Y/Z. "
            "L'offset Z sposta il nuovo piano lungo la propria normale."
        )
        info.setWordWrap(True)
        outer.addWidget(info)

        self.body_label = QtWidgets.QLabel()
        outer.addWidget(self.body_label)

        base_box = QtWidgets.QGroupBox("Piano base")
        base_layout = QtWidgets.QHBoxLayout(base_box)
        self.base_group = QtWidgets.QButtonGroup(self)
        self.base_buttons = {}
        for index, (role, label) in enumerate((("XY_Plane", "XY"), ("XZ_Plane", "XZ"), ("YZ_Plane", "YZ"))):
            rb = QtWidgets.QRadioButton(label)
            self.base_group.addButton(rb, index)
            self.base_buttons[role] = rb
            base_layout.addWidget(rb)
        self.base_buttons["XY_Plane"].setChecked(True)
        outer.addWidget(base_box)

        form_box = QtWidgets.QGroupBox("Posizione e rotazione")
        form = QtWidgets.QFormLayout(form_box)
        self.offset = QtWidgets.QDoubleSpinBox()
        self.offset.setRange(-100000.0, 100000.0)
        self.offset.setDecimals(3)
        self.offset.setSingleStep(1.0)
        self.offset.setSuffix(" mm")
        form.addRow("Offset normale Z", self.offset)

        self.rx = self._angle_spin()
        self.ry = self._angle_spin()
        self.rz = self._angle_spin()
        form.addRow("Ruota attorno X", self.rx)
        form.addRow("Ruota attorno Y", self.ry)
        form.addRow("Ruota attorno Z", self.rz)
        outer.addWidget(form_box)

        quick_box = QtWidgets.QGroupBox("Rotazioni rapide")
        quick_grid = QtWidgets.QGridLayout(quick_box)
        for row, (axis, spin) in enumerate((("X", self.rx), ("Y", self.ry), ("Z", self.rz))):
            lab = QtWidgets.QLabel(axis)
            lab.setAlignment(QtCore.Qt.AlignCenter)
            quick_grid.addWidget(lab, row, 0)
            for col, angle in enumerate((-90, -45, 45, 90), start=1):
                btn = QtWidgets.QPushButton(("+" if angle > 0 else "") + str(angle) + "°")
                btn.setToolTip("Imposta %s a %s°" % (axis, angle))
                btn.clicked.connect(lambda _checked=False, sp=spin, a=angle: sp.setValue(float(a)))
                quick_grid.addWidget(btn, row, col)
        zero = QtWidgets.QPushButton("Azzera rotazioni")
        zero.clicked.connect(self._zero_rotations)
        quick_grid.addWidget(zero, 3, 0, 1, 5)
        outer.addWidget(quick_box)

        hint = QtWidgets.QLabel(
            "Suggerimento: per un semplice schizzo su XY/XZ/YZ usa direttamente i piani origine. "
            "Crea un piano di costruzione quando servono inclinazione o offset."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        outer.addWidget(hint)

        buttons = QtWidgets.QHBoxLayout()
        self.create_button = QtWidgets.QPushButton("Crea piano")
        self.sketch_button = QtWidgets.QPushButton("Crea piano + Schizzo")
        close_button = QtWidgets.QPushButton("Chiudi")
        self.create_button.clicked.connect(lambda: self._create(False))
        self.sketch_button.clicked.connect(lambda: self._create(True))
        close_button.clicked.connect(self.close)
        buttons.addWidget(self.create_button)
        buttons.addWidget(self.sketch_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        outer.addLayout(buttons)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.refresh_body()

    def _angle_spin(self):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(-360.0, 360.0)
        spin.setDecimals(2)
        spin.setSingleStep(5.0)
        spin.setSuffix("°")
        return spin

    def _zero_rotations(self):
        self.rx.setValue(0.0)
        self.ry.setValue(0.0)
        self.rz.setValue(0.0)

    def refresh_body(self):
        self.body = _active_body()
        if self.body is None:
            self.body_label.setText("Body attivo: nessuno")
            self.body_label.setStyleSheet("color:#b03a2e;")
        else:
            self.body_label.setText("Body attivo: %s" % getattr(self.body, "Label", self.body.Name))
            self.body_label.setStyleSheet("")
        return self.body

    def _selected_role(self):
        for role, button in self.base_buttons.items():
            if button.isChecked():
                return role
        return "XY_Plane"

    def _ensure_body(self):
        body = self.refresh_body()
        if body is not None:
            return body
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.information(self, "SolidFlow", "Apri o crea prima un documento FreeCAD.")
            return None
        reply = QtWidgets.QMessageBox.question(
            self,
            "SolidFlow - Body",
            "Non trovo un Body attivo. Vuoi crearne uno adesso?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return None
        try:
            body = doc.addObject("PartDesign::Body", "Body")
            doc.recompute()
            _activate_body(body)
            self.refresh_body()
            return body
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "SolidFlow", "Impossibile creare il Body:\n%s" % exc)
            return None

    def _create(self, create_sketch):
        # Creating Part Design features while another sketch is in edit mode is unsafe/confusing.
        try:
            if smart.active_sketch() is not None:
                QtWidgets.QMessageBox.information(
                    self,
                    "SolidFlow - Piano di costruzione",
                    "Chiudi prima lo schizzo attualmente in modifica, poi crea il nuovo piano.",
                )
                return
        except Exception:
            pass

        body = self._ensure_body()
        if body is None:
            return
        _activate_body(body)
        try:
            plane = _create_construction_plane(
                body,
                self._selected_role(),
                self.offset.value(),
                self.rx.value(),
                self.ry.value(),
                self.rz.value(),
            )
            self._last_plane = plane
            self.status.setText(
                "Creato: %s  |  X %.1f°  Y %.1f°  Z %.1f°  |  offset %.2f mm"
                % (plane.Label, self.rx.value(), self.ry.value(), self.rz.value(), self.offset.value())
            )
            try:
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(plane)
            except Exception:
                pass
            if create_sketch:
                QtCore.QTimer.singleShot(80, lambda: _run_native("PartDesign_NewSketch"))
        except Exception as exc:
            self.status.setText("Errore: %s" % exc)
            App.Console.PrintError("SolidFlow Piano di costruzione: %s\n" % exc)
            QtWidgets.QMessageBox.warning(self, "SolidFlow - Piano di costruzione", str(exc))


_CONSTRUCTION_PLANE_DIALOG = None


def show_construction_plane_dialog():
    global _CONSTRUCTION_PLANE_DIALOG
    try:
        if _CONSTRUCTION_PLANE_DIALOG is None:
            _CONSTRUCTION_PLANE_DIALOG = ConstructionPlaneDialog(Gui.getMainWindow())
        _CONSTRUCTION_PLANE_DIALOG.refresh_body()
        _CONSTRUCTION_PLANE_DIALOG.show()
        _CONSTRUCTION_PLANE_DIALOG.raise_()
        _CONSTRUCTION_PLANE_DIALOG.activateWindow()
        return _CONSTRUCTION_PLANE_DIALOG
    except Exception as exc:
        App.Console.PrintError("SolidFlow Piano di costruzione UI: %s\n" % exc)
        return None


# ---------------------------------------------------------------------------
# SolidFlow work-plane Task panel
# ---------------------------------------------------------------------------
# The user-facing names follow the usual CAD convention:
#   Superiore = XY, Frontale = XZ, Destro = YZ.
# Rotation is intentionally limited to the two axes contained in the selected
# base plane.  A third rotation would only spin the sketch coordinate system
# inside the plane and is not needed to tilt the work plane itself.
_WORK_PLANE_PRESETS = {
    "XY_Plane": {
        "title": "Superiore (XY)",
        "normal": "Z",
        "axis_1": "X",
        "axis_2": "Y",
    },
    "XZ_Plane": {
        "title": "Frontale (XZ)",
        "normal": "Y",
        "axis_1": "X",
        "axis_2": "Z",
    },
    "YZ_Plane": {
        "title": "Destro (YZ)",
        "normal": "X",
        "axis_1": "Y",
        "axis_2": "Z",
    },
}


def _close_solidflow_task_panel():
    try:
        Gui.Control.closeDialog()
    except Exception:
        pass


class ConstructionPlaneTaskPanel:
    """Task-panel workflow for creating an offset/tilted sketch work plane."""

    def __init__(self):
        self.form = QtWidgets.QWidget()
        self.form.setObjectName("SolidFlowConstructionPlaneTaskPanel")
        self.body = None
        self._last_plane = None

        outer = QtWidgets.QVBoxLayout(self.form)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        title = QtWidgets.QLabel("Crea piano di lavoro")
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(max(title_font.pointSize(), 11))
        title.setFont(title_font)
        outer.addWidget(title)

        intro = QtWidgets.QLabel(
            "Scegli Superiore, Frontale o Destro; imposta l'offset e inclina "
            "il piano sui due assi del piano stesso."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self.body_label = QtWidgets.QLabel()
        outer.addWidget(self.body_label)

        # --- Base plane ----------------------------------------------------
        base_box = QtWidgets.QGroupBox("Piano di riferimento")
        base_layout = QtWidgets.QVBoxLayout(base_box)
        self.base_group = QtWidgets.QButtonGroup(self.form)
        self.base_buttons = {}
        for index, role in enumerate(("XY_Plane", "XZ_Plane", "YZ_Plane")):
            radio = QtWidgets.QRadioButton(_WORK_PLANE_PRESETS[role]["title"])
            self.base_group.addButton(radio, index)
            self.base_buttons[role] = radio
            base_layout.addWidget(radio)
            radio.toggled.connect(self._base_changed)
        self.base_buttons["XY_Plane"].setChecked(True)
        outer.addWidget(base_box)

        # --- Offset --------------------------------------------------------
        offset_box = QtWidgets.QGroupBox("Offset")
        offset_form = QtWidgets.QFormLayout(offset_box)
        self.offset = QtWidgets.QDoubleSpinBox()
        self.offset.setRange(-100000.0, 100000.0)
        self.offset.setDecimals(3)
        self.offset.setSingleStep(1.0)
        self.offset.setSuffix(" mm")
        self.offset_label = QtWidgets.QLabel("Distanza lungo Z")
        offset_form.addRow(self.offset_label, self.offset)
        outer.addWidget(offset_box)

        # --- Two-axis tilt -------------------------------------------------
        rotation_box = QtWidgets.QGroupBox("Rotazione piano di schizzo")
        rotation_layout = QtWidgets.QGridLayout(rotation_box)
        self.rot_1_label = QtWidgets.QLabel("Asse X")
        self.rot_2_label = QtWidgets.QLabel("Asse Y")
        self.rot_1 = self._angle_spin()
        self.rot_2 = self._angle_spin()
        rotation_layout.addWidget(self.rot_1_label, 0, 0)
        rotation_layout.addWidget(self.rot_1, 0, 1)
        rotation_layout.addWidget(self.rot_2_label, 1, 0)
        rotation_layout.addWidget(self.rot_2, 1, 1)

        # Fast, predictable angles without making the panel too dense.
        quick_1 = QtWidgets.QHBoxLayout()
        quick_2 = QtWidgets.QHBoxLayout()
        for angle in (-90, -45, 0, 45, 90):
            b1 = QtWidgets.QToolButton()
            b1.setText(("+" if angle > 0 else "") + str(angle) + "°")
            b1.clicked.connect(lambda _checked=False, a=angle: self.rot_1.setValue(float(a)))
            quick_1.addWidget(b1)
            b2 = QtWidgets.QToolButton()
            b2.setText(("+" if angle > 0 else "") + str(angle) + "°")
            b2.clicked.connect(lambda _checked=False, a=angle: self.rot_2.setValue(float(a)))
            quick_2.addWidget(b2)
        rotation_layout.addLayout(quick_1, 2, 0, 1, 2)
        rotation_layout.addLayout(quick_2, 3, 0, 1, 2)
        outer.addWidget(rotation_box)

        self.explanation = QtWidgets.QLabel()
        self.explanation.setWordWrap(True)
        self.explanation.setStyleSheet("color: palette(mid);")
        outer.addWidget(self.explanation)

        # --- Actions -------------------------------------------------------
        actions = QtWidgets.QGroupBox("Azioni")
        actions_layout = QtWidgets.QVBoxLayout(actions)
        self.create_button = QtWidgets.QPushButton("Crea piano di lavoro")
        self.create_button.setMinimumHeight(32)
        self.sketch_button = QtWidgets.QPushButton("Crea piano + nuovo schizzo")
        self.sketch_button.setMinimumHeight(32)
        self.reset_button = QtWidgets.QPushButton("Azzera offset e rotazioni")
        self.close_button = QtWidgets.QPushButton("Chiudi")
        actions_layout.addWidget(self.create_button)
        actions_layout.addWidget(self.sketch_button)
        actions_layout.addWidget(self.reset_button)
        actions_layout.addWidget(self.close_button)
        outer.addWidget(actions)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        outer.addStretch(1)

        self.create_button.clicked.connect(lambda: self._create(False))
        self.sketch_button.clicked.connect(lambda: self._create(True))
        self.reset_button.clicked.connect(self._reset)
        self.close_button.clicked.connect(_close_solidflow_task_panel)

        self.refresh_body()
        self._base_changed()

    def _angle_spin(self):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(-180.0, 180.0)
        spin.setDecimals(2)
        spin.setSingleStep(5.0)
        spin.setSuffix("°")
        return spin

    def _selected_role(self):
        for role, button in self.base_buttons.items():
            if button.isChecked():
                return role
        return "XY_Plane"

    def _base_changed(self, *_args):
        role = self._selected_role()
        preset = _WORK_PLANE_PRESETS[role]
        self.offset_label.setText("Distanza lungo %s" % preset["normal"])
        self.rot_1_label.setText("Rotazione asse %s" % preset["axis_1"])
        self.rot_2_label.setText("Rotazione asse %s" % preset["axis_2"])
        self.explanation.setText(
            "%s: offset lungo l'asse %s. Le due rotazioni inclinano il piano attorno "
            "agli assi %s e %s; non viene aggiunta una rotazione inutile attorno alla normale."
            % (
                preset["title"],
                preset["normal"],
                preset["axis_1"],
                preset["axis_2"],
            )
        )

    def _reset(self):
        self.offset.setValue(0.0)
        self.rot_1.setValue(0.0)
        self.rot_2.setValue(0.0)
        self.status.setText("")

    def refresh_body(self):
        self.body = _active_body()
        if self.body is None:
            self.body_label.setText("Body attivo: nessuno")
            self.body_label.setStyleSheet("color:#b03a2e;")
        else:
            self.body_label.setText(
                "Body attivo: %s" % getattr(self.body, "Label", self.body.Name)
            )
            self.body_label.setStyleSheet("")
        return self.body

    def _ensure_body(self):
        body = self.refresh_body()
        if body is not None:
            return body
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.information(
                Gui.getMainWindow(), "SolidFlow", "Apri o crea prima un documento FreeCAD."
            )
            return None
        reply = QtWidgets.QMessageBox.question(
            Gui.getMainWindow(),
            "SolidFlow - Body",
            "Non trovo un Body attivo. Vuoi crearne uno adesso?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return None
        try:
            body = doc.addObject("PartDesign::Body", "Body")
            doc.recompute()
            _activate_body(body)
            self.refresh_body()
            return body
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                Gui.getMainWindow(), "SolidFlow", "Impossibile creare il Body:\n%s" % exc
            )
            return None

    def _create(self, create_sketch):
        # A new datum plane changes the Body structure, therefore do not do it
        # while an unrelated Sketch is already in edit mode.
        try:
            if smart.active_sketch() is not None:
                QtWidgets.QMessageBox.information(
                    Gui.getMainWindow(),
                    "SolidFlow - Piano di lavoro",
                    "Chiudi prima lo schizzo attualmente in modifica, poi crea il nuovo piano.",
                )
                return
        except Exception:
            pass

        body = self._ensure_body()
        if body is None:
            return
        _activate_body(body)
        role = self._selected_role()
        preset = _WORK_PLANE_PRESETS[role]

        # rot_1/rot_2 are the two in-plane local axes.  In AttachmentOffset
        # these are local X and local Y; local Z is the plane normal and remains 0°.
        try:
            plane = _create_construction_plane(
                body,
                role,
                self.offset.value(),
                self.rot_1.value(),
                self.rot_2.value(),
                0.0,
            )
            plane.Label = "Piano lavoro %s" % preset["title"].split(" ")[0]
            try:
                App.ActiveDocument.recompute()
            except Exception:
                pass
            self._last_plane = plane
            self.status.setText(
                "Creato %s — offset %.2f mm, %s %.1f°, %s %.1f°"
                % (
                    preset["title"],
                    self.offset.value(),
                    preset["axis_1"],
                    self.rot_1.value(),
                    preset["axis_2"],
                    self.rot_2.value(),
                )
            )
            try:
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(plane)
            except Exception:
                pass
            if create_sketch:
                # Close our task first so FreeCAD can show the native New Sketch task cleanly.
                _close_solidflow_task_panel()
                QtCore.QTimer.singleShot(100, lambda: _run_native("PartDesign_NewSketch"))
        except Exception as exc:
            self.status.setText("Errore: %s" % exc)
            App.Console.PrintError("SolidFlow Piano di lavoro: %s\n" % exc)
            QtWidgets.QMessageBox.warning(
                Gui.getMainWindow(), "SolidFlow - Piano di lavoro", str(exc)
            )

    # FreeCAD Task panel protocol -------------------------------------------------
    def getStandardButtons(self):
        # All actions are explicit in the panel; avoid duplicate OK/Cancel buttons.
        return 0

    def accept(self):
        _close_solidflow_task_panel()
        return True

    def reject(self):
        _close_solidflow_task_panel()
        return True


_CONSTRUCTION_PLANE_TASK = None


def show_construction_plane_task_panel():
    """Open the work-plane creator inside FreeCAD's Tasks/Azioni pane."""
    global _CONSTRUCTION_PLANE_TASK
    try:
        # Do not silently replace another native task (Pad, Sketch edit, etc.).
        try:
            active = Gui.Control.activeDialog()
        except Exception:
            active = None
        if active:
            QtWidgets.QMessageBox.information(
                Gui.getMainWindow(),
                "SolidFlow - Piano di lavoro",
                "Termina prima l'azione attualmente aperta nella scheda Azioni, poi riprova.",
            )
            return None

        _CONSTRUCTION_PLANE_TASK = ConstructionPlaneTaskPanel()
        Gui.Control.showDialog(_CONSTRUCTION_PLANE_TASK)
        return _CONSTRUCTION_PLANE_TASK
    except Exception as exc:
        App.Console.PrintError("SolidFlow Piano di lavoro - pannello Azioni: %s\n" % exc)
        return None



# ---------------------------------------------------------------------------
# SolidFlow native editable construction plane — beta16
# ---------------------------------------------------------------------------
# IMPORTANT: this intentionally creates a real PartDesign::Plane (Datum Plane),
# not a temporary drawing aid.  Therefore the object remains in the Model tree,
# can support Sketches, and can later be edited with FreeCAD's native datum
# editor (double-click / Edit datum) or through its Attachment Offset property.


def _quantity_float(value, default=0.0):
    try:
        if hasattr(value, "Value"):
            return float(value.Value)
        return float(value)
    except Exception:
        return float(default)


def _new_native_datum_plane(body, base_role, px, py, pz, rx, ry, rz):
    """Create a true PartDesign Datum Plane with six editable attachment DOF.

    Position X/Y/Z and Rotation X/Y/Z are local to the selected reference plane,
    exactly like FreeCAD's native Attachment Offset.  The result is deliberately
    native so it remains editable in the Model tree throughout the project.
    """
    doc = App.ActiveDocument
    if doc is None or body is None:
        raise RuntimeError("Nessun documento/Body attivo.")

    base = _origin_plane(body, base_role)
    if base is None:
        raise RuntimeError("Piano di origine %s non trovato." % base_role.replace("_Plane", ""))

    doc.openTransaction("SolidFlow - Crea piano di costruzione")
    plane = None
    try:
        # FreeCAD's own Part Design datum command creates this exact native type.
        plane = body.newObject("PartDesign::Plane", "SolidFlowConstructionPlane")
        plane.Label = "Piano di costruzione"

        # Attach it to one of the Body origin planes.  FreeCAD 1.x may expose
        # AttachmentSupport or the legacy Support property depending on build.
        attached = False
        if hasattr(plane, "AttachmentSupport"):
            try:
                plane.AttachmentSupport = [(base, "")]
                attached = True
            except Exception:
                try:
                    plane.AttachmentSupport = [(base, ("",))]
                    attached = True
                except Exception:
                    pass
        if not attached and hasattr(plane, "Support"):
            try:
                plane.Support = (base, [""])
                attached = True
            except Exception:
                try:
                    plane.Support = [(base, "")]
                    attached = True
                except Exception:
                    pass
        if not attached:
            raise RuntimeError("Impossibile collegare il Datum Plane al piano di origine.")

        try:
            plane.MapMode = "FlatFace"
        except Exception:
            pass

        plane.AttachmentOffset = App.Placement(
            App.Vector(float(px), float(py), float(pz)),
            _xyz_rotation(float(rx), float(ry), float(rz)),
        )

        # Keep the datum visible after creation so it can immediately be used.
        try:
            plane.Visibility = True
        except Exception:
            pass
        try:
            if hasattr(plane, "ResizeMode"):
                plane.ResizeMode = "Automatic"
        except Exception:
            pass

        doc.recompute()
        doc.commitTransaction()
        return plane
    except Exception:
        try:
            doc.abortTransaction()
        except Exception:
            if plane is not None:
                try:
                    doc.removeObject(plane.Name)
                except Exception:
                    pass
        raise




def _create_sketch_attached_to_datum_plane(body, plane, enter_edit=True):
    """Create a Sketch that is *parametrically attached* to ``plane``.

    Using the generic PartDesign_NewSketch command after merely selecting a
    datum plane is not reliable across FreeCAD builds: a Sketch may be created
    with an absolute Placement instead of a persistent AttachmentSupport link.
    This helper writes the attachment explicitly so edits to the Datum Plane
    (AttachmentOffset / rotation) propagate to the Sketch and downstream
    features on recompute.
    """
    if body is None or plane is None:
        raise RuntimeError("Body o piano di costruzione non valido.")
    doc = getattr(body, "Document", None) or App.ActiveDocument
    if doc is None:
        raise RuntimeError("Nessun documento attivo.")

    doc.openTransaction("SolidFlow - Sketch su piano di costruzione")
    sketch = None
    try:
        sketch = body.newObject("Sketcher::SketchObject", "Sketch")
        try:
            sketch.Label = "Schizzo su %s" % getattr(plane, "Label", plane.Name)
        except Exception:
            pass

        attached = False

        # FreeCAD 1.x preferred property.  Datum-plane attachment uses an empty
        # sub-element because the whole plane is the support.
        if hasattr(sketch, "AttachmentSupport"):
            for value in (
                [(plane, "")],
                (plane, [""]),
                [(plane, ("",))],
            ):
                try:
                    sketch.AttachmentSupport = value
                    attached = True
                    break
                except Exception:
                    pass

        # Compatibility path for builds exposing the legacy Support alias.
        if not attached and hasattr(sketch, "Support"):
            for value in (
                (plane, [""]),
                [(plane, "")],
            ):
                try:
                    sketch.Support = value
                    attached = True
                    break
                except Exception:
                    pass

        if not attached:
            raise RuntimeError("Impossibile creare il collegamento parametrico Schizzo ↔ Piano.")

        sketch.MapMode = "FlatFace"

        # The sketch must inherit the Datum Plane placement.  Keep its own
        # attachment offset neutral; user offsets belong on the Datum Plane.
        try:
            sketch.AttachmentOffset = App.Placement()
        except Exception:
            pass

        doc.recompute()

        # Basic sanity check: an attached sketch must not be in Deactivated mode.
        try:
            if str(sketch.MapMode) == "Deactivated":
                raise RuntimeError("FreeCAD ha disattivato l'aggancio dello schizzo.")
        except RuntimeError:
            raise
        except Exception:
            pass

        doc.commitTransaction()
    except Exception:
        try:
            doc.abortTransaction()
        except Exception:
            if sketch is not None:
                try:
                    doc.removeObject(sketch.Name)
                    doc.recompute()
                except Exception:
                    pass
        raise

    try:
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sketch)
    except Exception:
        pass

    if enter_edit:
        def _enter():
            try:
                if Gui.ActiveDocument:
                    Gui.ActiveDocument.setEdit(sketch.Name)
            except Exception as exc:
                App.Console.PrintWarning(
                    "SolidFlow: schizzo agganciato ma apertura edit fallita: %s\\n" % exc
                )
        QtCore.QTimer.singleShot(80, _enter)

    return sketch


def _selected_sketch_and_datum_plane():
    """Return (sketch, datum_plane) when exactly one of each is selected."""
    try:
        selected = list(Gui.Selection.getSelection())
    except Exception:
        selected = []
    sketch = None
    plane = None
    for obj in selected:
        try:
            if getattr(obj, "TypeId", "") == "PartDesign::Plane":
                plane = obj
            elif obj.isDerivedFrom("Sketcher::SketchObject"):
                sketch = obj
        except Exception:
            pass
    return sketch, plane


def reattach_selected_sketch_to_datum_plane():
    """Repair an existing Sketch so it follows the selected Datum Plane."""
    sketch, plane = _selected_sketch_and_datum_plane()
    if sketch is None or plane is None:
        QtWidgets.QMessageBox.information(
            Gui.getMainWindow(),
            "SolidFlow - Aggancia schizzo",
            "Seleziona nell'albero un solo Schizzo e un solo Piano di costruzione, poi riprova.",
        )
        return False

    try:
        body = sketch.getParentGeoFeatureGroup()
    except Exception:
        body = None
    if body is None:
        body = _active_body()
    if body is None:
        QtWidgets.QMessageBox.warning(
            Gui.getMainWindow(),
            "SolidFlow - Aggancia schizzo",
            "Lo schizzo deve appartenere a un Body Part Design.",
        )
        return False

    doc = sketch.Document
    doc.openTransaction("SolidFlow - Riaggancia schizzo al piano")
    try:
        attached = False
        if hasattr(sketch, "AttachmentSupport"):
            for value in ([(plane, "")], (plane, [""])):
                try:
                    sketch.AttachmentSupport = value
                    attached = True
                    break
                except Exception:
                    pass
        if not attached and hasattr(sketch, "Support"):
            try:
                sketch.Support = (plane, [""])
                attached = True
            except Exception:
                pass
        if not attached:
            raise RuntimeError("FreeCAD non accetta il Datum Plane come supporto dello schizzo.")

        sketch.MapMode = "FlatFace"
        try:
            sketch.AttachmentOffset = App.Placement()
        except Exception:
            pass
        doc.recompute()
        doc.commitTransaction()
        App.Console.PrintMessage(
            "SolidFlow: %s agganciato parametricamente a %s.\\n"
            % (getattr(sketch, "Label", sketch.Name), getattr(plane, "Label", plane.Name))
        )
        return True
    except Exception as exc:
        try:
            doc.abortTransaction()
        except Exception:
            pass
        QtWidgets.QMessageBox.warning(
            Gui.getMainWindow(),
            "SolidFlow - Aggancia schizzo",
            "Aggancio fallito:\\n%s" % exc,
        )
        return False

def _selected_native_construction_plane():
    """Return a selected datum plane, favouring planes created by SolidFlow."""
    try:
        selected = Gui.Selection.getSelection()
    except Exception:
        selected = []
    for obj in selected:
        try:
            if getattr(obj, "TypeId", "") == "PartDesign::Plane":
                return obj
        except Exception:
            pass
    return None


def _edit_selected_construction_plane():
    """Open FreeCAD's native datum editor for the selected PartDesign plane."""
    plane = _selected_native_construction_plane()
    if plane is None:
        QtWidgets.QMessageBox.information(
            Gui.getMainWindow(),
            "SolidFlow - Piano di costruzione",
            "Seleziona prima un Piano di costruzione nell'albero Modello.",
        )
        return False
    try:
        if Gui.ActiveDocument:
            Gui.ActiveDocument.setEdit(plane.Name)
            return True
    except Exception as exc:
        App.Console.PrintWarning("SolidFlow modifica piano: %s\n" % exc)
    return False


class ConstructionPlaneTaskPanel:
    """Create a persistent, fully editable native Datum Plane in FreeCAD Tasks."""

    _BASES = (
        ("XY_Plane", "Superiore (XY)"),
        ("XZ_Plane", "Frontale (XZ)"),
        ("YZ_Plane", "Destro (YZ)"),
    )

    def __init__(self):
        self.form = QtWidgets.QWidget()
        self.form.setObjectName("SolidFlowConstructionPlaneTaskPanelV16")
        self.body = None
        self._last_plane = None

        outer = QtWidgets.QVBoxLayout(self.form)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        title = QtWidgets.QLabel("Crea piano di costruzione")
        f = title.font()
        f.setBold(True)
        f.setPointSize(max(f.pointSize(), 11))
        title.setFont(f)
        outer.addWidget(title)

        intro = QtWidgets.QLabel(
            "Crea un vero Datum Plane parametrico di FreeCAD. Puoi spostarlo e "
            "ruotarlo liberamente; resta nell'albero Modello e può essere modificato "
            "anche in seguito con doppio clic / Modifica datum."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self.body_label = QtWidgets.QLabel()
        outer.addWidget(self.body_label)

        base_box = QtWidgets.QGroupBox("Riferimento iniziale")
        base_layout = QtWidgets.QVBoxLayout(base_box)
        self.base_group = QtWidgets.QButtonGroup(self.form)
        self.base_buttons = {}
        for index, (role, label) in enumerate(self._BASES):
            rb = QtWidgets.QRadioButton(label)
            self.base_group.addButton(rb, index)
            self.base_buttons[role] = rb
            base_layout.addWidget(rb)
        self.base_buttons["XY_Plane"].setChecked(True)
        outer.addWidget(base_box)

        # Full 3D position, expressed in the local coordinates of the base plane.
        pos_box = QtWidgets.QGroupBox("Posizione del piano")
        pos_grid = QtWidgets.QGridLayout(pos_box)
        self.pos_x = self._distance_spin()
        self.pos_y = self._distance_spin()
        self.pos_z = self._distance_spin()
        for row, (label, spin) in enumerate((("X", self.pos_x), ("Y", self.pos_y), ("Z / normale", self.pos_z))):
            pos_grid.addWidget(QtWidgets.QLabel(label), row, 0)
            pos_grid.addWidget(spin, row, 1)
        outer.addWidget(pos_box)

        # Full 3-axis orientation.  Z is useful too: it rotates the local sketch
        # coordinate system inside the plane, something the previous UI omitted.
        rot_box = QtWidgets.QGroupBox("Rotazione del piano")
        rot_grid = QtWidgets.QGridLayout(rot_box)
        self.rot_x = self._angle_spin()
        self.rot_y = self._angle_spin()
        self.rot_z = self._angle_spin()
        for row, (axis, spin) in enumerate((("X", self.rot_x), ("Y", self.rot_y), ("Z", self.rot_z))):
            rot_grid.addWidget(QtWidgets.QLabel("Asse " + axis), row, 0)
            rot_grid.addWidget(spin, row, 1)
            quick = QtWidgets.QHBoxLayout()
            for angle in (-90, -45, 0, 45, 90):
                b = QtWidgets.QToolButton()
                b.setText(("+" if angle > 0 else "") + str(angle) + "°")
                b.clicked.connect(lambda _checked=False, sp=spin, a=angle: sp.setValue(float(a)))
                quick.addWidget(b)
            rot_grid.addLayout(quick, row, 2)
        outer.addWidget(rot_box)

        note = QtWidgets.QLabel(
            "X/Y/Z sono coordinate locali del riferimento scelto. Una volta creato, "
            "il piano è un oggetto PartDesign::Plane nativo: selezionalo nell'albero "
            "e modifica Attachment Offset, oppure fai doppio clic per riaprire l'editor datum."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        outer.addWidget(note)

        actions = QtWidgets.QGroupBox("Azioni")
        actions_layout = QtWidgets.QVBoxLayout(actions)
        self.create_button = QtWidgets.QPushButton("Crea piano di costruzione")
        self.create_button.setMinimumHeight(34)
        self.sketch_button = QtWidgets.QPushButton("Crea piano + nuovo schizzo")
        self.sketch_button.setMinimumHeight(34)
        self.edit_button = QtWidgets.QPushButton("Modifica piano selezionato")
        self.reset_button = QtWidgets.QPushButton("Azzera posizione e rotazioni")
        self.close_button = QtWidgets.QPushButton("Chiudi")
        for b in (self.create_button, self.sketch_button, self.edit_button, self.reset_button, self.close_button):
            actions_layout.addWidget(b)
        outer.addWidget(actions)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        outer.addStretch(1)

        self.create_button.clicked.connect(lambda: self._create(False))
        self.sketch_button.clicked.connect(lambda: self._create(True))
        self.edit_button.clicked.connect(self._edit_selected)
        self.reset_button.clicked.connect(self._reset)
        self.close_button.clicked.connect(_close_solidflow_task_panel)

        self.refresh_body()

    @staticmethod
    def _distance_spin():
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(-1000000.0, 1000000.0)
        spin.setDecimals(3)
        spin.setSingleStep(1.0)
        spin.setSuffix(" mm")
        return spin

    @staticmethod
    def _angle_spin():
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(-360.0, 360.0)
        spin.setDecimals(3)
        spin.setSingleStep(5.0)
        spin.setSuffix("°")
        return spin

    def _selected_role(self):
        for role, button in self.base_buttons.items():
            if button.isChecked():
                return role
        return "XY_Plane"

    def _reset(self):
        for spin in (self.pos_x, self.pos_y, self.pos_z, self.rot_x, self.rot_y, self.rot_z):
            spin.setValue(0.0)
        self.status.setText("")

    def refresh_body(self):
        self.body = _active_body()
        if self.body is None:
            self.body_label.setText("Body attivo: nessuno")
            self.body_label.setStyleSheet("color:#b03a2e;")
        else:
            self.body_label.setText("Body attivo: %s" % getattr(self.body, "Label", self.body.Name))
            self.body_label.setStyleSheet("")
        return self.body

    def _ensure_body(self):
        body = self.refresh_body()
        if body is not None:
            return body
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.information(
                Gui.getMainWindow(), "SolidFlow", "Apri o crea prima un documento FreeCAD."
            )
            return None
        reply = QtWidgets.QMessageBox.question(
            Gui.getMainWindow(),
            "SolidFlow - Body",
            "Non trovo un Body attivo. Vuoi crearne uno adesso?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return None
        try:
            body = doc.addObject("PartDesign::Body", "Body")
            doc.recompute()
            _activate_body(body)
            self.refresh_body()
            return body
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                Gui.getMainWindow(), "SolidFlow", "Impossibile creare il Body:\n%s" % exc
            )
            return None

    def _create(self, create_sketch):
        try:
            if smart.active_sketch() is not None:
                QtWidgets.QMessageBox.information(
                    Gui.getMainWindow(),
                    "SolidFlow - Piano di costruzione",
                    "Chiudi prima lo schizzo attualmente in modifica, poi crea il piano.",
                )
                return
        except Exception:
            pass

        body = self._ensure_body()
        if body is None:
            return
        _activate_body(body)

        try:
            plane = _new_native_datum_plane(
                body,
                self._selected_role(),
                self.pos_x.value(), self.pos_y.value(), self.pos_z.value(),
                self.rot_x.value(), self.rot_y.value(), self.rot_z.value(),
            )
            self._last_plane = plane
            try:
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(plane)
            except Exception:
                pass
            self.status.setText(
                "Creato %s — posizione (%.2f, %.2f, %.2f) mm — rotazione (%.1f°, %.1f°, %.1f°)."
                % (
                    getattr(plane, "Label", plane.Name),
                    self.pos_x.value(), self.pos_y.value(), self.pos_z.value(),
                    self.rot_x.value(), self.rot_y.value(), self.rot_z.value(),
                )
            )
            if create_sketch:
                # Do not delegate this to the generic New Sketch command: merely
                # selecting a datum plane is not sufficient in every FreeCAD build
                # to persist the support link.  Create and attach the Sketch explicitly.
                sketch = _create_sketch_attached_to_datum_plane(body, plane, enter_edit=False)
                self.status.setText(
                    "Creati %s e %s — collegamento parametrico attivo."
                    % (getattr(plane, "Label", plane.Name), getattr(sketch, "Label", sketch.Name))
                )
                _close_solidflow_task_panel()
                def _edit_new_sketch():
                    try:
                        Gui.Selection.clearSelection()
                        Gui.Selection.addSelection(sketch)
                        if Gui.ActiveDocument:
                            Gui.ActiveDocument.setEdit(sketch.Name)
                    except Exception as exc:
                        App.Console.PrintWarning(
                            "SolidFlow: apertura schizzo su piano fallita: %s\n" % exc
                        )
                QtCore.QTimer.singleShot(120, _edit_new_sketch)
        except Exception as exc:
            self.status.setText("Errore: %s" % exc)
            App.Console.PrintError("SolidFlow Piano di costruzione: %s\n" % exc)
            QtWidgets.QMessageBox.warning(
                Gui.getMainWindow(), "SolidFlow - Piano di costruzione", str(exc)
            )

    def _edit_selected(self):
        # Close this custom task first; the native FreeCAD datum editor then owns
        # the Tasks panel and exposes the same persistent Attachment Offset data.
        plane = _selected_native_construction_plane()
        if plane is None:
            QtWidgets.QMessageBox.information(
                Gui.getMainWindow(),
                "SolidFlow - Piano di costruzione",
                "Seleziona nell'albero il piano che vuoi modificare.",
            )
            return
        _close_solidflow_task_panel()
        QtCore.QTimer.singleShot(100, _edit_selected_construction_plane)

    def getStandardButtons(self):
        return 0

    def accept(self):
        _close_solidflow_task_panel()
        return True

    def reject(self):
        _close_solidflow_task_panel()
        return True


def show_construction_plane_task_panel():
    """Open SolidFlow's persistent construction-plane creator in Tasks/Azioni."""
    global _CONSTRUCTION_PLANE_TASK
    try:
        try:
            active = Gui.Control.activeDialog()
        except Exception:
            active = None
        if active:
            QtWidgets.QMessageBox.information(
                Gui.getMainWindow(),
                "SolidFlow - Piano di costruzione",
                "Termina prima l'azione attualmente aperta nella scheda Azioni, poi riprova.",
            )
            return None
        _CONSTRUCTION_PLANE_TASK = ConstructionPlaneTaskPanel()
        Gui.Control.showDialog(_CONSTRUCTION_PLANE_TASK)
        return _CONSTRUCTION_PLANE_TASK
    except Exception as exc:
        App.Console.PrintError("SolidFlow Piano di costruzione - pannello Azioni: %s\n" % exc)
        return None


def _active_sketch():
    """Return the sketch currently being edited, without depending on GUI internals."""
    try:
        sketch = smart.active_sketch()
        if sketch is not None:
            return sketch
    except Exception:
        pass

    try:
        edit = Gui.ActiveDocument.getInEdit() if Gui.ActiveDocument else None
        if isinstance(edit, (tuple, list)):
            edit = edit[0] if edit else None
        if hasattr(edit, "Object"):
            edit = edit.Object
        if edit is not None and hasattr(edit, "addGeometry") and hasattr(edit, "Geometry"):
            return edit
    except Exception:
        pass
    return None


def _global_placement(obj):
    try:
        return obj.getGlobalPlacement()
    except Exception:
        try:
            return obj.Placement
        except Exception:
            return App.Placement()


def _to_local(sketch, world_point):
    try:
        p = _global_placement(sketch).inverse().multVec(world_point)
        return App.Vector(float(p.x), float(p.y), 0.0)
    except Exception:
        return App.Vector(float(world_point.x), float(world_point.y), 0.0)


def _to_world(sketch, local_point):
    p = App.Vector(float(local_point.x), float(local_point.y), 0.0)
    try:
        return _global_placement(sketch).multVec(p)
    except Exception:
        return p


def _screen_coords(view, world_point):
    try:
        p = view.getScreenCoords(world_point)
        return float(p[0]), float(p[1])
    except Exception:
        return None


def _mouse_local_point(view, sketch, position):
    """Project the cursor point back onto the active sketch plane."""
    try:
        x, y = int(position[0]), int(position[1])
        world = view.getPoint(x, y)
        return _to_local(sketch, world)
    except Exception:
        return None


def _distance_2d(a, b):
    dx = float(a[0]) - float(b[0])
    dy = float(a[1]) - float(b[1])
    return (dx * dx + dy * dy) ** 0.5


def _vector_len(v):
    return (float(v.x) * float(v.x) + float(v.y) * float(v.y)) ** 0.5


def _unit_2d(v):
    length = _vector_len(v)
    if length <= 1.0e-12:
        return None
    return App.Vector(float(v.x) / length, float(v.y) / length, 0.0)


def _project_on_line(point, a, b):
    d = b - a
    den = float(d.x) * float(d.x) + float(d.y) * float(d.y)
    if den <= 1.0e-16:
        return a, 0.0
    ap = point - a
    t = (float(ap.x) * float(d.x) + float(ap.y) * float(d.y)) / den
    return a + d * t, t


def _object_visible(obj):
    try:
        vp = Gui.ActiveDocument.getObject(obj.Name)
        return bool(vp and vp.Visibility)
    except Exception:
        return True


def _collect_inference_references(sketch, max_points=900, max_lines=450):
    """Collect non-destructive sketch + visible-solid references in sketch-local XY."""
    refs = []
    ref_lines = []
    if sketch is None:
        return refs, ref_lines

    def add_point(p, label, source):
        try:
            p = App.Vector(float(p.x), float(p.y), 0.0)
        except Exception:
            return
        refs.append({"p": p, "label": label, "source": source})

    def add_line(a, b, label, source):
        try:
            a = App.Vector(float(a.x), float(a.y), 0.0)
            b = App.Vector(float(b.x), float(b.y), 0.0)
        except Exception:
            return
        if _vector_len(b - a) <= 1.0e-10:
            return
        ref_lines.append({"a": a, "b": b, "label": label, "source": source})

    # Native geometry already present in the active sketch.
    try:
        for geo in sketch.Geometry:
            type_id = str(getattr(geo, "TypeId", ""))
            start = getattr(geo, "StartPoint", None)
            end = getattr(geo, "EndPoint", None)
            centre = getattr(geo, "Center", None)
            if start is not None:
                add_point(start, "Estremo schizzo", "sketch")
            if end is not None:
                add_point(end, "Estremo schizzo", "sketch")
            if centre is not None:
                add_point(centre, "Centro schizzo", "sketch")
            if start is not None and end is not None:
                midpoint = (App.Vector(start) + App.Vector(end)) * 0.5
                add_point(midpoint, "Punto medio", "sketch")
                if "Line" in type_id:
                    add_line(start, end, "Segmento schizzo", "sketch")
    except Exception as exc:
        App.Console.PrintWarning("SolidFlow inferenze schizzo: %s\\n" % exc)

    # Visible solid geometry is projected onto the sketch plane.  It remains
    # only a visual/reference aid: no ExternalGeometry or permanent relation is created.
    doc = App.ActiveDocument
    if doc is None:
        return refs, ref_lines

    for obj in doc.Objects:
        if len(refs) >= max_points and len(ref_lines) >= max_lines:
            break
        if obj is sketch or not hasattr(obj, "Shape") or not _object_visible(obj):
            continue
        try:
            shape = obj.Shape
            if shape is None or shape.isNull():
                continue
        except Exception:
            continue

        placement = _global_placement(obj)
        try:
            for vertex in shape.Vertexes:
                if len(refs) >= max_points:
                    break
                world = placement.multVec(vertex.Point)
                add_point(_to_local(sketch, world), "Vertice solido", "solid")
        except Exception:
            pass

        try:
            for edge in shape.Edges:
                if len(refs) >= max_points and len(ref_lines) >= max_lines:
                    break
                try:
                    first = float(edge.FirstParameter)
                    last = float(edge.LastParameter)
                    mid_local = edge.valueAt((first + last) * 0.5)
                    mid_world = placement.multVec(mid_local)
                    add_point(_to_local(sketch, mid_world), "Punto su spigolo", "solid")
                except Exception:
                    pass

                try:
                    curve_name = edge.Curve.__class__.__name__
                except Exception:
                    curve_name = ""
                if "Line" not in curve_name or len(ref_lines) >= max_lines:
                    continue
                try:
                    verts = edge.Vertexes
                    if len(verts) < 2:
                        continue
                    wa = placement.multVec(verts[0].Point)
                    wb = placement.multVec(verts[-1].Point)
                    add_line(
                        _to_local(sketch, wa),
                        _to_local(sketch, wb),
                        "Spigolo solido",
                        "solid",
                    )
                except Exception:
                    pass
        except Exception:
            pass

    return refs, ref_lines


class _GuideOverlay:
    """Coin overlay for the centred-line preview and dashed inference guides."""

    def __init__(self, view):
        self.view = view
        self.root = None
        self.guides_coord = None
        self.guides_lines = None
        self.preview_coord = None
        self.preview_lines = None
        if coin is None:
            return
        try:
            scene = view.getSceneGraph()
            self.root = coin.SoSeparator()

            guides = coin.SoSeparator()
            guide_colour = coin.SoBaseColor()
            guide_colour.rgb = (0.15, 0.55, 0.95)
            guide_style = coin.SoDrawStyle()
            guide_style.style = coin.SoDrawStyle.LINES
            guide_style.lineWidth = 1.3
            try:
                guide_style.linePattern = 0x0F0F
            except Exception:
                pass
            self.guides_coord = coin.SoCoordinate3()
            self.guides_lines = coin.SoLineSet()
            guides.addChild(guide_colour)
            guides.addChild(guide_style)
            guides.addChild(self.guides_coord)
            guides.addChild(self.guides_lines)

            preview = coin.SoSeparator()
            preview_colour = coin.SoBaseColor()
            preview_colour.rgb = (0.12, 0.35, 0.85)
            preview_style = coin.SoDrawStyle()
            preview_style.style = coin.SoDrawStyle.LINES
            preview_style.lineWidth = 2.2
            self.preview_coord = coin.SoCoordinate3()
            self.preview_lines = coin.SoLineSet()
            preview.addChild(preview_colour)
            preview.addChild(preview_style)
            preview.addChild(self.preview_coord)
            preview.addChild(self.preview_lines)

            try:
                annotation = coin.SoAnnotation()
                annotation.addChild(guides)
                annotation.addChild(preview)
                self.root.addChild(annotation)
            except Exception:
                self.root.addChild(guides)
                self.root.addChild(preview)
            scene.addChild(self.root)
        except Exception as exc:
            App.Console.PrintWarning("SolidFlow inferenze: overlay non disponibile: %s\\n" % exc)
            self.root = None

    @staticmethod
    def _set_segments(coord_node, line_node, segments):
        if coord_node is None or line_node is None:
            return
        values = []
        for a, b in segments:
            values.append((float(a.x), float(a.y), float(a.z)))
            values.append((float(b.x), float(b.y), float(b.z)))
        try:
            if values:
                coord_node.point.setValues(0, len(values), values)
                line_node.numVertices.setValues(0, len(segments), [2] * len(segments))
            else:
                coord_node.point.setNum(0)
                line_node.numVertices.setNum(0)
        except Exception:
            pass

    def set_guides(self, segments):
        self._set_segments(self.guides_coord, self.guides_lines, segments)

    def set_preview(self, segments):
        self._set_segments(self.preview_coord, self.preview_lines, segments)

    def clear(self):
        self.set_guides([])
        self.set_preview([])

    def remove(self):
        if self.root is None:
            return
        try:
            self.view.getSceneGraph().removeChild(self.root)
        except Exception:
            pass
        self.root = None


class CenteredLineTool:
    """SolidWorks/Fusion-like symmetric line tool with non-destructive inference guides."""

    def __init__(self, controller):
        self.controller = controller
        self.active = False
        self.view = None
        self.sketch = None
        self.callbacks = []
        self.overlay = None
        self.center = None
        self.refs = []
        self.ref_lines = []
        self.last_hint = ""
        self.snap_px = max(5, ROOT.GetInt("InferenceSnapPx", 12))
        self.angle_deg = max(1.0, min(15.0, ROOT.GetFloat("InferenceAngleDeg", 5.0)))

    def start(self):
        if self.active:
            self.stop()
            return
        sketch = _active_sketch()
        if sketch is None or not Gui.ActiveDocument:
            QtWidgets.QMessageBox.information(
                Gui.getMainWindow(),
                "SolidFlow - Linea dal centro",
                "Apri uno schizzo in modifica, poi avvia Linea dal centro.",
            )
            return
        try:
            view = Gui.ActiveDocument.ActiveView
        except Exception:
            view = None
        if view is None:
            return

        self.active = True
        self.view = view
        self.sketch = sketch
        self.center = None
        self.refresh_references()
        self.overlay = _GuideOverlay(view)
        try:
            for event_name in ("SoLocation2Event", "SoMouseButtonEvent", "SoKeyboardEvent"):
                callback_id = view.addEventCallback(event_name, self._event)
                self.callbacks.append((event_name, callback_id))
        except Exception as exc:
            for event_name, callback_id in list(self.callbacks):
                try:
                    view.removeEventCallback(event_name, callback_id)
                except Exception:
                    pass
            self.callbacks = []
            self.active = False
            if self.overlay is not None:
                self.overlay.remove()
            self.overlay = None
            App.Console.PrintError("SolidFlow Linea dal centro: impossibile agganciare il mouse: %s\\n" % exc)
            return

        self.controller.bar.hide()
        App.Console.PrintMessage(
            "SolidFlow Linea dal centro: clic = centro, secondo clic = estremita; "
            "Esc o clic destro = termina. Inferenze magnetiche attive.\\n"
        )

    def stop(self):
        if self.view is not None:
            for event_name, callback_id in list(self.callbacks):
                try:
                    self.view.removeEventCallback(event_name, callback_id)
                except Exception:
                    pass
        self.callbacks = []
        if self.overlay is not None:
            self.overlay.remove()
        self.overlay = None
        self.active = False
        self.center = None
        self.refs = []
        self.ref_lines = []
        self.last_hint = ""
        self.view = None
        self.sketch = None
        try:
            Gui.getMainWindow().statusBar().clearMessage()
        except Exception:
            pass
        try:
            Gui.updateGui()
        except Exception:
            pass

    def _event(self, info):
        if not self.active:
            return
        try:
            if _active_sketch() is not self.sketch:
                self.stop()
                return
        except Exception:
            pass
        try:
            event_type = str(info.get("Type", ""))
        except Exception:
            return

        if event_type == "SoKeyboardEvent":
            key = str(info.get("Key", "")).upper()
            state = str(info.get("State", "")).upper()
            if key in ("ESCAPE", "ESC") and state in ("DOWN", "PRESS", ""):
                self.stop()
            return

        if event_type == "SoMouseButtonEvent":
            state = str(info.get("State", "")).upper()
            if state not in ("DOWN", "PRESS"):
                return
            button = str(info.get("Button", "")).upper()
            if button in ("BUTTON3", "RIGHT"):
                self.stop()
                return
            if button != "BUTTON1":
                return
            position = info.get("Position")
            if position is not None:
                self._click(position)
            return

        if event_type in ("SoLocation2Event", "SoMouseMoveEvent"):
            position = info.get("Position")
            if position is not None:
                self._move(position)

    def _status_hint(self, hint):
        hint = str(hint or "")
        if hint == self.last_hint:
            return
        self.last_hint = hint
        try:
            message = "SolidFlow — Linea dal centro"
            if hint:
                message += " | " + hint
            Gui.getMainWindow().statusBar().showMessage(message)
        except Exception:
            pass

    def _click(self, position):
        raw = _mouse_local_point(self.view, self.sketch, position)
        if raw is None:
            return
        if self.center is None:
            snapped, anchor_hint, guides = self._snap_anchor(raw, position)
            self.center = snapped
            self._draw_anchor_guides(guides)
            self._status_hint("Centro impostato" + ((" | " + anchor_hint) if anchor_hint else ""))
            return

        endpoint, hint, guides = self._infer_endpoint(raw, position)
        if _vector_len(endpoint - self.center) <= 1.0e-8:
            return
        self._commit(endpoint)
        self.center = None
        self._status_hint("Linea creata — scegli un nuovo centro")
        self.refresh_references()
        if self.overlay:
            self.overlay.clear()

    def _move(self, position):
        if self.center is None:
            raw = _mouse_local_point(self.view, self.sketch, position)
            if raw is None:
                return
            _, anchor_hint, guides = self._snap_anchor(raw, position)
            self._draw_anchor_guides(guides)
            self._status_hint(("Aggancio: " + anchor_hint) if anchor_hint else "Scegli il centro")
            return

        raw = _mouse_local_point(self.view, self.sketch, position)
        if raw is None:
            return
        endpoint, hint, guides = self._infer_endpoint(raw, position)
        d = endpoint - self.center
        if _vector_len(d) <= 1.0e-10:
            if self.overlay:
                self.overlay.set_preview([])
            return
        a = self.center - d
        b = self.center + d
        if self.overlay:
            self.overlay.set_preview([(_to_world(self.sketch, a), _to_world(self.sketch, b))])
            self.overlay.set_guides(
                [(_to_world(self.sketch, g0), _to_world(self.sketch, g1)) for g0, g1 in guides]
            )
        self._status_hint(hint)

    def _draw_anchor_guides(self, guides):
        if not self.overlay:
            return
        self.overlay.set_preview([])
        self.overlay.set_guides(
            [(_to_world(self.sketch, g0), _to_world(self.sketch, g1)) for g0, g1 in guides]
        )

    def _local_per_pixel(self, position):
        try:
            x, y = int(position[0]), int(position[1])
            p0 = _mouse_local_point(self.view, self.sketch, (x, y))
            p1 = _mouse_local_point(self.view, self.sketch, (x + 50, y))
            if p0 is not None and p1 is not None:
                return max(1.0e-6, _vector_len(p1 - p0) / 50.0)
        except Exception:
            pass
        return 0.1

    def _point_screen_distance(self, local_point, position):
        screen = _screen_coords(self.view, _to_world(self.sketch, local_point))
        if screen is None:
            return 1.0e9
        return _distance_2d(screen, position)

    def _snap_anchor(self, raw, position):
        best = None
        best_dist = float(self.snap_px) + 1.0
        for ref in self.refs:
            dist = self._point_screen_distance(ref["p"], position)
            if dist < best_dist:
                best_dist = dist
                best = ref
        if best is not None:
            span = self._local_per_pixel(position) * 34.0
            p = best["p"]
            guides = [
                (App.Vector(p.x - span, p.y, 0), App.Vector(p.x + span, p.y, 0)),
                (App.Vector(p.x, p.y - span, 0), App.Vector(p.x, p.y + span, 0)),
            ]
            return App.Vector(p.x, p.y, 0), best.get("label", "Punto"), guides

        line_hit = self._nearest_reference_line(raw, position, allow_extension=True)
        if line_hit is not None:
            projected, line, _ = line_hit
            return projected, line.get("label", "Segmento"), self._line_guide(line, projected, position)

        return raw, "", []

    def _nearest_reference_line(self, raw, position, allow_extension=False):
        best = None
        best_dist = float(self.snap_px) + 1.0
        for line in self.ref_lines:
            a, b = line["a"], line["b"]
            projected, t = _project_on_line(raw, a, b)
            if not allow_extension and (t < 0.0 or t > 1.0):
                continue
            if allow_extension and (t < -3.0 or t > 4.0):
                continue
            dist = self._point_screen_distance(projected, position)
            if dist < best_dist:
                best_dist = dist
                best = (projected, line, t)
        return best

    def _line_guide(self, line, around, position):
        unit = _unit_2d(line["b"] - line["a"])
        if unit is None:
            return []
        length = max(_vector_len(line["b"] - line["a"]) * 0.75, self._local_per_pixel(position) * 100.0)
        return [(around - unit * length, around + unit * length)]

    def _infer_endpoint(self, raw, position):
        # 1) Exact reference points have highest priority.
        best = None
        best_dist = float(self.snap_px) + 1.0
        for ref in self.refs:
            dist = self._point_screen_distance(ref["p"], position)
            if dist < best_dist:
                best_dist = dist
                best = ref
        if best is not None:
            p = App.Vector(best["p"].x, best["p"].y, 0)
            span = self._local_per_pixel(position) * 38.0
            guides = [
                (App.Vector(p.x - span, p.y, 0), App.Vector(p.x + span, p.y, 0)),
                (App.Vector(p.x, p.y - span, 0), App.Vector(p.x, p.y + span, 0)),
            ]
            return p, best.get("label", "Punto"), guides

        # 2) Existing sketch/solid line or its nearby extension.
        line_hit = self._nearest_reference_line(raw, position, allow_extension=True)
        if line_hit is not None:
            projected, line, _ = line_hit
            return projected, line.get("label", "Segmento"), self._line_guide(line, projected, position)

        d = raw - self.center
        length = _vector_len(d)
        if length <= 1.0e-12:
            return raw, "", []

        # 3) Horizontal / vertical alignment with the selected centre.
        center_screen = _screen_coords(self.view, _to_world(self.sketch, self.center))
        raw_screen = _screen_coords(self.view, _to_world(self.sketch, raw))
        if center_screen is not None and raw_screen is not None:
            if abs(raw_screen[1] - center_screen[1]) <= self.snap_px:
                p = App.Vector(raw.x, self.center.y, 0)
                span = max(length * 1.35, self._local_per_pixel(position) * 90.0)
                return p, "Orizzontale", [(self.center - App.Vector(span, 0, 0), self.center + App.Vector(span, 0, 0))]
            if abs(raw_screen[0] - center_screen[0]) <= self.snap_px:
                p = App.Vector(self.center.x, raw.y, 0)
                span = max(length * 1.35, self._local_per_pixel(position) * 90.0)
                return p, "Verticale", [(self.center - App.Vector(0, span, 0), self.center + App.Vector(0, span, 0))]

        # 4) Parallel / perpendicular to nearby linear geometry.
        raw_u = _unit_2d(d)
        if raw_u is not None:
            import math
            angle_tol = math.radians(self.angle_deg)
            best_angle = angle_tol + 1.0
            best_candidate = None
            for line in self.ref_lines:
                line_u = _unit_2d(line["b"] - line["a"])
                if line_u is None:
                    continue
                dot = max(-1.0, min(1.0, abs(float(raw_u.x) * float(line_u.x) + float(raw_u.y) * float(line_u.y))))
                parallel_angle = math.acos(dot)
                perp_angle = abs((math.pi / 2.0) - math.acos(max(-1.0, min(1.0,
                    float(raw_u.x) * float(line_u.x) + float(raw_u.y) * float(line_u.y)))))
                if parallel_angle < best_angle:
                    sign = 1.0 if (float(d.x) * float(line_u.x) + float(d.y) * float(line_u.y)) >= 0.0 else -1.0
                    candidate = self.center + line_u * (length * sign)
                    best_angle = parallel_angle
                    best_candidate = (candidate, "Parallela", line_u)
                if perp_angle < best_angle:
                    perp = App.Vector(-line_u.y, line_u.x, 0)
                    sign = 1.0 if (float(d.x) * float(perp.x) + float(d.y) * float(perp.y)) >= 0.0 else -1.0
                    candidate = self.center + perp * (length * sign)
                    best_angle = perp_angle
                    best_candidate = (candidate, "Perpendicolare", perp)
            if best_candidate is not None:
                candidate, label, direction = best_candidate
                span = max(length * 1.35, self._local_per_pixel(position) * 90.0)
                return candidate, label, [(self.center - direction * span, self.center + direction * span)]

        return raw, "", []

    def _commit(self, endpoint):
        d = endpoint - self.center
        start = self.center - d
        end = self.center + d
        if Part is None:
            App.Console.PrintError("SolidFlow Linea dal centro: modulo Part non disponibile.\\n")
            return
        try:
            self.sketch.addGeometry(
                Part.LineSegment(
                    App.Vector(float(start.x), float(start.y), 0.0),
                    App.Vector(float(end.x), float(end.y), 0.0),
                ),
                False,
            )
            try:
                self.sketch.solve()
            except Exception:
                pass
            try:
                App.ActiveDocument.recompute()
            except Exception:
                pass
            Gui.updateGui()
        except Exception as exc:
            App.Console.PrintError("SolidFlow Linea dal centro: %s\\n" % exc)

    def refresh_references(self):
        self.refs, self.ref_lines = _collect_inference_references(self.sketch)


class _PassiveInferenceOverlay:
    """Two-colour dashed guide overlay: sketch refs in blue, solid refs in purple."""

    def __init__(self, view):
        self.view = view
        self.root = None
        self.sketch_coord = None
        self.sketch_lines = None
        self.solid_coord = None
        self.solid_lines = None
        if coin is None:
            return
        try:
            scene = view.getSceneGraph()
            root = coin.SoSeparator()
            root.setName("SolidFlowPassiveInferenceGuides")

            try:
                light = coin.SoLightModel()
                light.model = coin.SoLightModel.BASE_COLOR
                root.addChild(light)
            except Exception:
                pass

            try:
                depth = coin.SoDepthBuffer()
                depth.test = False
                depth.write = False
                root.addChild(depth)
            except Exception:
                pass

            try:
                pick = coin.SoPickStyle()
                pick.style = coin.SoPickStyle.UNPICKABLE
                root.addChild(pick)
            except Exception:
                pass

            def make_group(rgb, width, pattern):
                sep = coin.SoSeparator()
                colour = coin.SoBaseColor()
                colour.rgb = rgb
                style = coin.SoDrawStyle()
                style.style = coin.SoDrawStyle.LINES
                style.lineWidth = float(width)
                try:
                    style.linePattern = int(pattern)
                except Exception:
                    pass
                coords = coin.SoCoordinate3()
                lines = coin.SoLineSet()
                sep.addChild(colour)
                sep.addChild(style)
                sep.addChild(coords)
                sep.addChild(lines)
                return sep, coords, lines

            sketch_group, self.sketch_coord, self.sketch_lines = make_group(
                (0.12, 0.32, 0.88), 1.7, 0x0F0F
            )
            solid_group, self.solid_coord, self.solid_lines = make_group(
                (0.55, 0.30, 0.72), 1.5, 0x3333
            )

            try:
                annotation = coin.SoAnnotation()
                annotation.addChild(sketch_group)
                annotation.addChild(solid_group)
                root.addChild(annotation)
            except Exception:
                root.addChild(sketch_group)
                root.addChild(solid_group)

            scene.addChild(root)
            self.root = root
        except Exception as exc:
            App.Console.PrintWarning("SolidFlow inferenze passive: overlay non disponibile: %s\\n" % exc)
            self.root = None

    @staticmethod
    def _set_segments(coord_node, line_node, segments):
        if coord_node is None or line_node is None:
            return
        values = []
        for a, b in segments:
            values.append((float(a.x), float(a.y), float(a.z)))
            values.append((float(b.x), float(b.y), float(b.z)))
        try:
            if values:
                coord_node.point.setValues(0, len(values), values)
                line_node.numVertices.setValues(0, len(segments), [2] * len(segments))
            else:
                coord_node.point.setNum(0)
                line_node.numVertices.setNum(0)
        except Exception:
            pass

    def set_guides(self, sketch_segments, solid_segments):
        self._set_segments(self.sketch_coord, self.sketch_lines, sketch_segments)
        self._set_segments(self.solid_coord, self.solid_lines, solid_segments)

    def clear(self):
        self.set_guides([], [])

    def remove(self):
        if self.root is None:
            return
        try:
            self.view.getSceneGraph().removeChild(self.root)
        except Exception:
            pass
        self.root = None


class PassiveInferenceGuideController(QtCore.QObject):
    """Shows nearby construction-style references while any Sketcher tool is active."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.enabled = ROOT.GetBool("DynamicInferenceGuides", True)
        self.view = None
        self.sketch = None
        self.overlay = None
        self.callback = None
        self.callback_kind = None
        self.refs = []
        self.ref_lines = []
        self.last_refresh = 0.0
        self.last_position = None
        self.align_px = max(4, ROOT.GetInt("InferenceAlignPx", 8))
        self.line_px = max(4, ROOT.GetInt("InferenceLinePx", 9))
        self.point_px = max(5, ROOT.GetInt("InferencePointPx", 12))
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(220)
        self.timer.timeout.connect(self.sync)
        self._last_event_error = 0.0

    def install(self):
        if not self.timer.isActive():
            self.timer.start()
        self.sync(force=True)
        return self

    def uninstall(self):
        self.timer.stop()
        self._detach()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        ROOT.SetBool("DynamicInferenceGuides", self.enabled)
        if self.enabled:
            self.sync(force=True)
        else:
            self._detach()

    def _detach(self):
        if self.view is not None and self.callback is not None:
            try:
                if self.callback_kind == "pivy" and coin is not None:
                    self.view.removeEventCallbackPivy(
                        coin.SoLocation2Event.getClassTypeId(), self.callback
                    )
                elif self.callback_kind == "dict-event":
                    self.view.removeEventCallback("SoEvent", self.callback)
                else:
                    self.view.removeEventCallback("SoLocation2Event", self.callback)
            except Exception:
                pass
        self.callback = None
        self.callback_kind = None
        if self.overlay is not None:
            self.overlay.remove()
        self.overlay = None
        self.view = None
        self.sketch = None
        self.refs = []
        self.ref_lines = []
        self.last_position = None

    def sync(self, force=False):
        # Avoid duplicate overlays while the dedicated symmetric-line tool is running.
        if not self.enabled or getattr(self.owner.centered_line, "active", False):
            if self.view is not None:
                self._detach()
            return

        sketch = _active_sketch()
        if sketch is None or not Gui.ActiveDocument:
            if self.view is not None:
                self._detach()
            return
        try:
            view = Gui.ActiveDocument.ActiveView
        except Exception:
            view = None
        if view is None:
            self._detach()
            return

        if force or sketch is not self.sketch or view is not self.view:
            self._detach()
            self.sketch = sketch
            self.view = view
            self.overlay = _PassiveInferenceOverlay(view)
            self.callback = None
            self.callback_kind = None

            # Primary path: raw Pivy mouse callback. Sketcher can keep handling
            # the same event because this observer never marks it as handled.
            if coin is not None:
                try:
                    self.callback = view.addEventCallbackPivy(
                        coin.SoLocation2Event.getClassTypeId(), self._event_pivy
                    )
                    self.callback_kind = "pivy"
                except Exception as exc:
                    App.Console.PrintWarning(
                        "SolidFlow inferenze: callback Pivy non disponibile: %s\n" % exc
                    )

            # Compatibility fallback. Listening to SoEvent is broader than the old
            # SoLocation2Event wrapper and survives more Sketcher tool states.
            if self.callback is None:
                try:
                    self.callback = view.addEventCallback("SoEvent", self._event_dict)
                    self.callback_kind = "dict-event"
                except Exception as exc:
                    App.Console.PrintWarning(
                        "SolidFlow inferenze: callback mouse non disponibile: %s\n" % exc
                    )
                    self.callback = None
                    self.callback_kind = None

            self._refresh_references()
            if self.callback is not None:
                App.Console.PrintMessage(
                    "SolidFlow: inferenze tratteggiate attive (%s).\n" % self.callback_kind
                )
            return

        now = time.monotonic()
        if now - self.last_refresh >= 1.0:
            self._refresh_references()
            if self.last_position is not None:
                self._update_guides(self.last_position)

    def _refresh_references(self):
        self.refs, self.ref_lines = _collect_inference_references(self.sketch)
        self.last_refresh = time.monotonic()

    def _consume_position(self, position):
        if not self.enabled or self.sketch is None or self.overlay is None:
            return
        if position is None:
            return
        try:
            if hasattr(position, "getValue"):
                position = position.getValue()
            position = (float(position[0]), float(position[1]))
        except Exception:
            return
        self.last_position = position
        self._update_guides(position)

    def _event_pivy(self, event_callback):
        """Raw Coin callback; observes mouse motion without consuming it."""
        try:
            event = event_callback.getEvent()
            self._consume_position(event.getPosition())
        except Exception as exc:
            now = time.monotonic()
            if now - self._last_event_error > 2.0:
                self._last_event_error = now
                App.Console.PrintWarning("SolidFlow inferenze mouse: %s\n" % exc)

    def _event_dict(self, info):
        """Fallback callback for builds without addEventCallbackPivy()."""
        try:
            event_type = str(info.get("Type", ""))
        except Exception:
            event_type = ""
        if event_type and event_type not in ("SoLocation2Event", "SoMouseMoveEvent"):
            return
        try:
            self._consume_position(info.get("Position"))
        except Exception:
            return

    def _local_units_per_pixel(self):
        # Convert a screen length into sketch-local units using the current zoom.
        if self.view is None or self.sketch is None:
            return 0.1
        try:
            p0 = App.Vector(0, 0, 0)
            p1 = App.Vector(10, 0, 0)
            s0 = _screen_coords(self.view, _to_world(self.sketch, p0))
            s1 = _screen_coords(self.view, _to_world(self.sketch, p1))
            if s0 is not None and s1 is not None:
                px = _distance_2d(s0, s1)
                if px > 1.0e-6:
                    return 10.0 / px
        except Exception:
            pass
        return 0.1

    @staticmethod
    def _screen_line_projection(position, sa, sb):
        vx = float(sb[0]) - float(sa[0])
        vy = float(sb[1]) - float(sa[1])
        den = vx * vx + vy * vy
        if den <= 1.0e-12:
            return None
        wx = float(position[0]) - float(sa[0])
        wy = float(position[1]) - float(sa[1])
        t = (wx * vx + wy * vy) / den
        px = float(sa[0]) + t * vx
        py = float(sa[1]) + t * vy
        dist = ((px - float(position[0])) ** 2 + (py - float(position[1])) ** 2) ** 0.5
        return t, dist

    def _full_guide(self, point, direction, half_span):
        unit = _unit_2d(direction)
        if unit is None:
            return None
        return point - unit * half_span, point + unit * half_span

    def _update_guides(self, position):
        if self.overlay is None or self.view is None or self.sketch is None:
            return

        units_per_px = max(1.0e-8, self._local_units_per_pixel())
        half_span = units_per_px * 900.0
        sketch_segments = []
        solid_segments = []

        # A) Nearest point / horizontal / vertical alignments.
        best_point = None
        best_point_score = 1.0e9
        best_h = None
        best_h_score = 1.0e9
        best_v = None
        best_v_score = 1.0e9

        for ref in self.refs:
            sp = _screen_coords(self.view, _to_world(self.sketch, ref["p"]))
            if sp is None:
                continue
            dx = abs(float(position[0]) - float(sp[0]))
            dy = abs(float(position[1]) - float(sp[1]))
            dist = (dx * dx + dy * dy) ** 0.5
            penalty = 0.8 if ref.get("source") == "solid" else 0.0
            if dist <= self.point_px and dist + penalty < best_point_score:
                best_point = ref
                best_point_score = dist + penalty
            if dy <= self.align_px and dx <= 1000.0 and dy + penalty < best_h_score:
                best_h = ref
                best_h_score = dy + penalty
            if dx <= self.align_px and dy <= 1000.0 and dx + penalty < best_v_score:
                best_v = ref
                best_v_score = dx + penalty

        def add_local_segment(seg, source):
            if seg is None:
                return
            world_seg = (_to_world(self.sketch, seg[0]), _to_world(self.sketch, seg[1]))
            (solid_segments if source == "solid" else sketch_segments).append(world_seg)

        # Direct point proximity: crosshair through the reference point.
        if best_point is not None:
            p = best_point["p"]
            src = best_point.get("source", "sketch")
            add_local_segment(
                (App.Vector(p.x - half_span, p.y, 0), App.Vector(p.x + half_span, p.y, 0)),
                src,
            )
            add_local_segment(
                (App.Vector(p.x, p.y - half_span, 0), App.Vector(p.x, p.y + half_span, 0)),
                src,
            )
        else:
            if best_h is not None:
                p = best_h["p"]
                add_local_segment(
                    (App.Vector(p.x - half_span, p.y, 0), App.Vector(p.x + half_span, p.y, 0)),
                    best_h.get("source", "sketch"),
                )
            if best_v is not None:
                p = best_v["p"]
                add_local_segment(
                    (App.Vector(p.x, p.y - half_span, 0), App.Vector(p.x, p.y + half_span, 0)),
                    best_v.get("source", "sketch"),
                )

        # B) Extension of the closest linear sketch/solid geometry.
        best_line = None
        best_line_score = 1.0e9
        for line in self.ref_lines:
            sa = _screen_coords(self.view, _to_world(self.sketch, line["a"]))
            sb = _screen_coords(self.view, _to_world(self.sketch, line["b"]))
            if sa is None or sb is None:
                continue
            hit = self._screen_line_projection(position, sa, sb)
            if hit is None:
                continue
            t, dist = hit
            # Keep the extension useful but local enough to avoid remote geometry noise.
            if t < -4.0 or t > 5.0 or dist > self.line_px:
                continue
            penalty = 0.8 if line.get("source") == "solid" else 0.0
            score = dist + penalty
            if score < best_line_score:
                best_line = line
                best_line_score = score

        if best_line is not None:
            mid = (best_line["a"] + best_line["b"]) * 0.5
            seg = self._full_guide(mid, best_line["b"] - best_line["a"], half_span)
            add_local_segment(seg, best_line.get("source", "sketch"))

        # Keep the overlay intentionally sparse: usually 1-3 guides, not a projection forest.
        self.overlay.set_guides(sketch_segments[:3], solid_segments[:3])
        try:
            self.view.redraw()
        except Exception:
            pass



class _ConstructionPlaneCommand:
    def GetResources(self):
        return {
            "MenuText": "Crea piano di costruzione",
            "ToolTip": (
                "Apre nella scheda Azioni il creatore di un Datum Plane nativo: "
                "posizione X/Y/Z e rotazione X/Y/Z, modificabile successivamente nell'albero."
            ),
        }

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        show_construction_plane_task_panel()


class _CenteredLineCommand:
    def GetResources(self):
        return {
            "MenuText": "Linea dal centro",
            "ToolTip": (
                "Crea una linea simmetrica rispetto al primo punto. "
                "Mostra snap e inferenze tratteggiate su schizzo e solidi visibili."
            ),
        }

    def IsActive(self):
        return _active_sketch() is not None

    def Activated(self):
        controller = install()
        controller.start_centered_line()


class SketchHintBar(QtWidgets.QFrame):
    """Small confirmation bar shown automatically after Sketch selection."""

    def __init__(self, controller):
        super().__init__(Gui.getMainWindow(), QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        self.controller = controller
        self.setObjectName("SolidFlowSketchHintBar")
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setStyleSheet(
            "QFrame#SolidFlowSketchHintBar {background:rgba(248,249,251,248);"
            "border:1px solid #8193a2;border-radius:7px;}"
            "QToolButton{min-height:31px;padding:3px 8px;border:0;border-radius:4px;"
            "font-size:11px;color:#20262b;}"
            "QToolButton:hover{background:#dce9f3;}"
            "QToolButton[dimension='true']{font-weight:600;}"
        )
        self.layout = QtWidgets.QHBoxLayout(self)
        self.layout.setContentsMargins(5, 4, 5, 4)
        self.layout.setSpacing(3)

    def _clear(self):
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def rebuild(self, suggestions):
        self._clear()
        for suggestion in suggestions[:5]:
            button = QtWidgets.QToolButton(self)
            button.setText(suggestion["label"])
            button.setToolTip(suggestion["reason"] + " — clic per confermare")
            button.setFocusPolicy(QtCore.Qt.NoFocus)
            button.setProperty("dimension", "true" if suggestion.get("kind") == "dimension" else "false")
            action = _command_action(suggestion["command"])
            if action:
                try:
                    button.setIcon(action.icon())
                    button.setIconSize(QtCore.QSize(18, 18))
                except Exception:
                    pass
            button.clicked.connect(
                lambda _checked=False, cmd=suggestion["command"]: self.controller.run_command(cmd)
            )
            self.layout.addWidget(button)

        more = QtWidgets.QToolButton(self)
        more.setText("Altri…")
        more.setToolTip("Mostra tutti i vincoli e le quote native disponibili")
        more.setFocusPolicy(QtCore.Qt.NoFocus)
        more.clicked.connect(self.controller.show_more)
        self.layout.addWidget(more)
        self.adjustSize()

    def show_near_cursor(self):
        self.adjustSize()
        cursor = QtGui.QCursor.pos()
        pos = cursor + QtCore.QPoint(22, 24)
        screen = QtWidgets.QApplication.screenAt(cursor) or QtWidgets.QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            pos.setX(max(area.left() + 8, min(pos.x(), area.right() - self.width() - 8)))
            pos.setY(max(area.top() + 8, min(pos.y(), area.bottom() - self.height() - 8)))
        self.move(pos)
        self.show()
        self.raise_()


class _SelectionObserver:
    def __init__(self, controller):
        self.controller = controller

    def addSelection(self, *args):
        self.controller.schedule_update()

    def removeSelection(self, *args):
        self.controller.schedule_update()

    def clearSelection(self, *args):
        self.controller.schedule_update()

    def setSelection(self, *args):
        self.controller.schedule_update()


# -----------------------------------------------------------------------------
# Persistent launcher inside FreeCAD's default Tasks/Azioni pane
# -----------------------------------------------------------------------------

def _normalized_ui_text(value):
    try:
        return str(value or "").replace("&", "").strip().lower()
    except Exception:
        return ""


def _open_construction_plane_from_actions():
    """Replace the default Start Part task with SolidFlow's construction-plane panel."""
    try:
        # The launcher is injected only beside FreeCAD's "Nuovo corpo" Start Part
        # action, so closing that passive start panel is safe.  An actual feature
        # task never contains our injected launcher.
        try:
            Gui.Control.closeDialog()
        except Exception:
            pass
        QtCore.QTimer.singleShot(80, show_construction_plane_task_panel)
    except Exception as exc:
        App.Console.PrintWarning("SolidFlow avvio piano da Azioni: %s\\n" % exc)


def _ensure_construction_plane_actions_button():
    """Add 'Crea piano di costruzione' to FreeCAD's visible Start Part/Azioni panel.

    FreeCAD rebuilds the Tasks pane as commands start/end.  The controller calls
    this helper periodically, therefore the launcher is restored automatically
    instead of depending on a fragile one-shot UI patch.
    """
    try:
        main = Gui.getMainWindow()
        if main is None:
            return False

        # Do not inject while our own task panel is open.
        if main.findChild(QtWidgets.QWidget, "SolidFlowConstructionPlaneTaskPanelV16"):
            return True

        # Find FreeCAD's default "New body / Nuovo corpo" launcher.  This is much
        # less version-sensitive than relying on private object names of Combo View.
        new_body = None
        for button in main.findChildren(QtWidgets.QAbstractButton):
            text = _normalized_ui_text(button.text())
            if text in ("nuovo corpo", "new body"):
                try:
                    if button.isVisible():
                        new_body = button
                        break
                except Exception:
                    new_body = button
                    break
        if new_body is None:
            return False

        # If a previous instance survived a Tasks-pane rebuild, do nothing.
        existing = main.findChild(QtWidgets.QPushButton, "SolidFlowActionsConstructionPlaneButton")
        if existing is not None:
            try:
                if existing.isVisible():
                    return True
            except Exception:
                return True

        # Prefer the same layout/group as "Nuovo corpo", so the command really
        # appears in the user's Azioni pane rather than in a separate dock/toolbar.
        host = new_body.parentWidget()
        target_layout = None
        probe = host
        for _ in range(6):
            if probe is None:
                break
            try:
                lay = probe.layout()
            except Exception:
                lay = None
            if lay is not None:
                target_layout = lay
                host = probe
                # A Start Part group box is the ideal host; otherwise the first
                # usable layout above the New Body button is sufficient.
                if isinstance(probe, QtWidgets.QGroupBox):
                    break
            probe = probe.parentWidget()
        if target_layout is None or host is None:
            return False

        button = QtWidgets.QPushButton("Crea piano di costruzione", host)
        button.setObjectName("SolidFlowActionsConstructionPlaneButton")
        button.setToolTip(
            "Crea un Datum Plane parametrico. Puoi impostare X/Y/Z e rotazioni, "
            "poi modificarlo in qualsiasi momento dall'albero Modello."
        )
        try:
            button.setMinimumHeight(max(28, int(new_body.minimumHeight())))
        except Exception:
            button.setMinimumHeight(28)
        button.clicked.connect(_open_construction_plane_from_actions)
        target_layout.addWidget(button)
        button.show()
        return True
    except Exception as exc:
        App.Console.PrintWarning("SolidFlow pulsante Piano in Azioni: %s\\n" % exc)
        return False


class SketchUXController(QtCore.QObject):
    def __init__(self):
        super().__init__(Gui.getMainWindow())
        self.bar = SketchHintBar(self)
        self.observer = _SelectionObserver(self)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.update)
        self.centered_line = CenteredLineTool(self)
        self.inference_guides = PassiveInferenceGuideController(self)
        # FreeCAD rebuilds the Tasks/Azioni pane frequently.  Keep a lightweight
        # timer that restores the direct construction-plane launcher whenever the
        # default Start Part panel is visible.
        self.actions_plane_timer = QtCore.QTimer(self)
        self.actions_plane_timer.setInterval(900)
        self.actions_plane_timer.timeout.connect(_ensure_construction_plane_actions_button)
        # Global Smart-S key handling is attached to QApplication rather than
        # the 3D view.  This keeps the shortcut alive when FreeCAD swaps task
        # panels, MDI subwindows or Sketcher view widgets during a long session.
        self._key_filter_installed = False
        self._installed = False

    def install(self):
        if self._installed:
            return self
        Gui.Selection.addObserver(self.observer)
        self._installed = True
        smart.apply_smart_snap_preferences()
        if ROOT.GetBool("SWLikeTheme", True):
            apply_sw_like_theme()
        self.inference_guides.install()
        self._install_global_s_key()
        self.actions_plane_timer.start()
        QtCore.QTimer.singleShot(250, _ensure_construction_plane_actions_button)
        QtCore.QTimer.singleShot(700, self._augment_menu)
        QtCore.QTimer.singleShot(1500, self._augment_menu)
        App.Console.PrintMessage("SolidFlow %s: Smart Sketch consolidato attivo.\n" % VERSION)
        return self

    def schedule_update(self):
        if self._installed:
            self.timer.start()

    def _install_global_s_key(self):
        """Install a durable plain-S shortcut at QApplication level.

        FreeCAD replaces/focuses several widgets while modelling (3D view,
        Sketcher, Combo View and task panels).  A shortcut owned by one of
        those transient widgets can therefore stop receiving key presses.
        The application event filter survives those focus/view changes.
        """
        if self._key_filter_installed:
            return
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.installEventFilter(self)
                self._key_filter_installed = True
        except Exception as exc:
            App.Console.PrintWarning("SolidFlow Smart-S installazione: %s\n" % exc)

    def _remove_global_s_key(self):
        if not self._key_filter_installed:
            return
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
        except Exception:
            pass
        self._key_filter_installed = False

    @staticmethod
    def _focus_accepts_text():
        """Do not steal S while the user is typing/editing a numeric field."""
        try:
            focus = QtWidgets.QApplication.focusWidget()
        except Exception:
            focus = None
        if focus is None:
            return False
        editable_types = (
            QtWidgets.QLineEdit,
            QtWidgets.QTextEdit,
            QtWidgets.QPlainTextEdit,
            QtWidgets.QAbstractSpinBox,
            QtWidgets.QComboBox,
        )
        try:
            return isinstance(focus, editable_types)
        except Exception:
            return False

    def _show_s_palette(self):
        """Open the SolidFlow S palette without changing geometry."""
        if self.centered_line.active:
            return False
        try:
            sketch = smart.active_sketch()
        except Exception:
            sketch = None
        if sketch is None:
            return False

        try:
            suggestions = smart.constraint_suggestions(limit=5)
        except Exception as exc:
            App.Console.PrintWarning("SolidFlow Smart-S suggerimenti: %s\n" % exc)
            suggestions = []

        # rebuild([]) intentionally still shows the 'Altri…' button, so S
        # remains useful even when there is no immediately likely constraint.
        self.bar.rebuild(suggestions or [])
        self.bar.show_near_cursor()
        try:
            Gui.getMainWindow().statusBar().showMessage(
                "SolidFlow S — suggerimenti Smart Sketch", 1800
            )
        except Exception:
            pass
        return True

    def eventFilter(self, watched, event):
        """Persistent Smart-S handler; active only while editing a Sketch."""
        try:
            if event.type() == QtCore.QEvent.KeyPress:
                if event.key() == QtCore.Qt.Key_S:
                    modifiers = event.modifiers()
                    blocked = (
                        QtCore.Qt.ControlModifier
                        | QtCore.Qt.AltModifier
                        | QtCore.Qt.MetaModifier
                    )
                    if modifiers & blocked:
                        return False
                    if event.isAutoRepeat():
                        return True
                    if self._focus_accepts_text():
                        return False
                    if self._show_s_palette():
                        event.accept()
                        return True
        except Exception as exc:
            App.Console.PrintWarning("SolidFlow Smart-S evento: %s\n" % exc)
        return super().eventFilter(watched, event)

    def update(self):
        if self.centered_line.active:
            self.bar.hide()
            return
        if not smart.constraint_hints_enabled() or smart.active_sketch() is None:
            self.bar.hide()
            return
        suggestions = smart.constraint_suggestions(limit=5)
        if not suggestions:
            self.bar.hide()
            return
        self.bar.rebuild(suggestions)
        self.bar.show_near_cursor()

    def start_centered_line(self):
        self.bar.hide()
        self.centered_line.start()

    def run_command(self, command):
        self.bar.hide()
        _run_native(command)
        QtCore.QTimer.singleShot(120, self.schedule_update)

    def show_more(self):
        menu = QtWidgets.QMenu(self.bar)
        for label, command in smart.all_constraint_actions():
            action = menu.addAction(label)
            native = _command_action(command)
            if native:
                try:
                    action.setIcon(native.icon())
                except Exception:
                    pass
            action.triggered.connect(lambda _checked=False, cmd=command: self.run_command(cmd))
        menu.exec_(QtGui.QCursor.pos())

    def _augment_menu(self):
        try:
            target = None
            for menu in Gui.getMainWindow().findChildren(QtWidgets.QMenu):
                if menu.title().replace("&", "").strip().lower() == "solidflow":
                    target = menu
                    break
            if target is None or target.findChild(QtCore.QObject, "SolidFlowSketchCoreMarker"):
                return
            marker = QtCore.QObject(target)
            marker.setObjectName("SolidFlowSketchCoreMarker")
            target.addSeparator()
            submenu = target.addMenu("Sketch intelligente")
            centered = submenu.addAction("Linea dal centro (simmetrica)…")
            centered.setToolTip(
                "Primo clic: centro. Secondo clic: estremita. "
                "Inferenze tratteggiate e snap magnetico su schizzo/solidi."
            )
            centered.triggered.connect(self.start_centered_line)
            plane = submenu.addAction("Crea piano di costruzione…")
            plane.setToolTip(
                "Crea un Datum Plane nativo e persistente, posizionabile e ruotabile liberamente; "
                "resta modificabile dall'albero Modello."
            )
            plane.triggered.connect(show_construction_plane_task_panel)
            reattach = submenu.addAction("Riaggancia schizzo al piano…")
            reattach.setToolTip(
                "Ripara uno schizzo esistente: seleziona Schizzo + Piano di costruzione. "
                "Lo schizzo seguirà offset e rotazioni del Datum Plane."
            )
            reattach.triggered.connect(reattach_selected_sketch_to_datum_plane)
            guides = submenu.addAction("Inferenze tratteggiate dinamiche")
            guides.setCheckable(True)
            guides.setChecked(ROOT.GetBool("DynamicInferenceGuides", True))
            guides.setToolTip(
                "Mostra prolungamenti/allineamenti temporanei su geometrie dello schizzo e solidi visibili."
            )
            guides.toggled.connect(self.inference_guides.set_enabled)
            submenu.addSeparator()
            settings = submenu.addAction("Impostazioni Smart Sketch…")
            settings.triggered.connect(smart.show_smart_sketch_settings)
            theme = submenu.addAction("Tema SolidWorks-like")
            theme.setCheckable(True)
            theme.setChecked(ROOT.GetBool("SWLikeTheme", True))
            theme.toggled.connect(lambda state: apply_sw_like_theme() if state else restore_previous_theme())
            restore = submenu.addAction("Ripristina colori precedenti")
            restore.triggered.connect(restore_previous_theme)
        except Exception as exc:
            App.Console.PrintWarning("SolidFlow Sketch menu: %s\n" % exc)

    def uninstall(self):
        if not self._installed:
            return
        try:
            Gui.Selection.removeObserver(self.observer)
        except Exception:
            pass
        self.timer.stop()
        self.actions_plane_timer.stop()
        try:
            button = Gui.getMainWindow().findChild(QtWidgets.QPushButton, "SolidFlowActionsConstructionPlaneButton")
            if button is not None:
                button.deleteLater()
        except Exception:
            pass
        self.centered_line.stop()
        self.inference_guides.uninstall()
        self._remove_global_s_key()
        self.bar.hide()
        self._installed = False


_controller = None
_command_registered = False


def _register_commands():
    global _command_registered
    if _command_registered:
        return
    try:
        Gui.addCommand("SolidFlow_CenteredLine", _CenteredLineCommand())
        Gui.addCommand("SolidFlow_ConstructionPlane", _ConstructionPlaneCommand())
        _command_registered = True
    except Exception as exc:
        App.Console.PrintWarning("SolidFlow registrazione comandi Sketch: %s\n" % exc)


def install():
    global _controller
    _register_commands()
    if _controller is None:
        _controller = SketchUXController().install()
    return _controller


def uninstall():
    global _controller
    if _controller:
        _controller.uninstall()
        _controller = None
