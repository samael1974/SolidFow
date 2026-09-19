# -*- coding: utf-8 -*-
"""SolidFlow Appearance Studio foundation for FreeCAD 1.1.x.

Provides:
- editable appearance/material presets using FreeCAD's native ShapeMaterial;
- live color, shininess/roughness proxy and transparency;
- per-object Coin3D texture preview with persisted SolidFlow properties;
- scalable/rotatable/offset texture coordinates;
- Planar / Box / Cylindrical / Spherical mapping where Coin provides it.

The texture scene nodes are visualization-only; the parametric CAD Shape is not
modified. Settings are stored as document properties and reconstructed on load.
"""

from __future__ import annotations

import math
import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

try:
    from pivy import coin
except Exception:
    coin = None

VERSION = "0.1.0"
GROUP = "SolidFlow Appearance"


PRESETS = {
    "Tecnico grigio": {
        "diffuse": (0.78, 0.80, 0.82), "specular": (0.30, 0.32, 0.34), "shininess": 0.28, "transparency": 0.0, "density": 0.0,
    },
    "PLA opaco": {
        "diffuse": (0.72, 0.72, 0.72), "specular": (0.06, 0.06, 0.06), "shininess": 0.08, "transparency": 0.0, "density": 1.24,
    },
    "PETG satinato": {
        "diffuse": (0.65, 0.67, 0.69), "specular": (0.22, 0.22, 0.22), "shininess": 0.28, "transparency": 0.03, "density": 1.27,
    },
    "TPU / gomma": {
        "diffuse": (0.16, 0.16, 0.17), "specular": (0.03, 0.03, 0.03), "shininess": 0.03, "transparency": 0.0, "density": 1.20,
    },
    "Alluminio satinato": {
        "diffuse": (0.68, 0.70, 0.72), "specular": (0.88, 0.88, 0.90), "shininess": 0.72, "transparency": 0.0, "density": 2.70,
    },
    "Acciaio": {
        "diffuse": (0.46, 0.48, 0.50), "specular": (0.92, 0.92, 0.94), "shininess": 0.82, "transparency": 0.0, "density": 7.85,
    },
    "Rame": {
        "diffuse": (0.72, 0.32, 0.18), "specular": (0.82, 0.50, 0.34), "shininess": 0.62, "transparency": 0.0, "density": 8.96,
    },
    "Ottone": {
        "diffuse": (0.70, 0.52, 0.18), "specular": (0.92, 0.75, 0.34), "shininess": 0.65, "transparency": 0.0, "density": 8.50,
    },
    "Legno": {
        "diffuse": (0.48, 0.25, 0.10), "specular": (0.10, 0.07, 0.04), "shininess": 0.12, "transparency": 0.0, "density": 0.70,
    },
    "Ceramica": {
        "diffuse": (0.90, 0.90, 0.88), "specular": (0.72, 0.72, 0.70), "shininess": 0.62, "transparency": 0.0, "density": 2.40,
    },
    "Vetro": {
        "diffuse": (0.78, 0.90, 0.94), "specular": (0.95, 0.98, 1.0), "shininess": 0.92, "transparency": 0.72, "density": 2.50,
    },
    "Bianco opaco": {
        "diffuse": (0.94, 0.94, 0.94), "specular": (0.06, 0.06, 0.06), "shininess": 0.06, "transparency": 0.0, "density": 0.0,
    },
    "Nero opaco": {
        "diffuse": (0.035, 0.035, 0.04), "specular": (0.03, 0.03, 0.03), "shininess": 0.04, "transparency": 0.0, "density": 0.0,
    },
}


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


def selected_appearance_objects():
    result = []
    try:
        selected = Gui.Selection.getSelection()
    except Exception:
        selected = []
    for obj in selected:
        try:
            if obj.ViewObject is not None and hasattr(obj.ViewObject, "ShapeMaterial"):
                result.append(obj)
        except Exception:
            continue
    return result


def _rgba3(value, default=(0.8, 0.8, 0.8)):
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except Exception:
        return tuple(default)


def _material_snapshot(obj):
    v = obj.ViewObject
    mat = v.ShapeMaterial
    data = {
        "diffuse": _rgba3(getattr(mat, "DiffuseColor", (0.8, 0.8, 0.8))),
        "ambient": _rgba3(getattr(mat, "AmbientColor", (0.2, 0.2, 0.2))),
        "specular": _rgba3(getattr(mat, "SpecularColor", (0.0, 0.0, 0.0))),
        "emissive": _rgba3(getattr(mat, "EmissiveColor", (0.0, 0.0, 0.0))),
        "shininess": float(getattr(mat, "Shininess", 0.2)),
        "transparency": float(getattr(mat, "Transparency", 0.0)),
    }
    try:
        data["view_transparency"] = int(v.Transparency)
    except Exception:
        data["view_transparency"] = int(round(data["transparency"] * 100.0))
    try:
        data["shape_color"] = tuple(v.ShapeColor)
    except Exception:
        data["shape_color"] = data["diffuse"]
    return data


def _apply_material(obj, data):
    try:
        material = App.Material()
        material.AmbientColor = tuple(data.get("ambient", (0.2, 0.2, 0.2)))
        material.DiffuseColor = tuple(data.get("diffuse", (0.8, 0.8, 0.8)))
        material.SpecularColor = tuple(data.get("specular", (0.0, 0.0, 0.0)))
        material.EmissiveColor = tuple(data.get("emissive", (0.0, 0.0, 0.0)))
        material.Shininess = float(data.get("shininess", 0.2))
        material.Transparency = float(data.get("transparency", 0.0))
        obj.ViewObject.ShapeMaterial = material
    except Exception:
        # Some view providers expose an editable material object instead.
        mat = obj.ViewObject.ShapeMaterial
        for name, key in (
            ("AmbientColor", "ambient"), ("DiffuseColor", "diffuse"),
            ("SpecularColor", "specular"), ("EmissiveColor", "emissive"),
            ("Shininess", "shininess"), ("Transparency", "transparency"),
        ):
            try:
                setattr(mat, name, data[key])
            except Exception:
                pass
        try:
            obj.ViewObject.ShapeMaterial = mat
        except Exception:
            pass
    try:
        obj.ViewObject.ShapeColor = tuple(data.get("diffuse", (0.8, 0.8, 0.8)))
    except Exception:
        pass
    try:
        obj.ViewObject.Transparency = int(round(float(data.get("transparency", 0.0)) * 100.0))
    except Exception:
        pass


def _ensure_property(obj, type_id, name, doc):
    if name in getattr(obj, "PropertiesList", []):
        return
    try:
        obj.addProperty(type_id, name, GROUP, doc)
    except Exception:
        pass


def ensure_appearance_properties(obj):
    specs = [
        ("App::PropertyString", "SolidFlowMaterialPreset", "Preset materiale/aspetto SolidFlow"),
        ("App::PropertyFloat", "SolidFlowDensity", "Densità indicativa g/cm³"),
        ("App::PropertyPath", "SolidFlowTexture", "File texture"),
        ("App::PropertyEnumeration", "SolidFlowTextureMapping", "Mappatura texture"),
        ("App::PropertyFloat", "SolidFlowTextureScaleX", "Scala texture X percentuale"),
        ("App::PropertyFloat", "SolidFlowTextureScaleY", "Scala texture Y percentuale"),
        ("App::PropertyAngle", "SolidFlowTextureRotation", "Rotazione texture"),
        ("App::PropertyFloat", "SolidFlowTextureOffsetX", "Offset texture X"),
        ("App::PropertyFloat", "SolidFlowTextureOffsetY", "Offset texture Y"),
    ]
    for type_id, name, doc in specs:
        _ensure_property(obj, type_id, name, doc)
    try:
        enums = ["Planare", "Box", "Cilindrica", "Sferica"]
        obj.SolidFlowTextureMapping = enums
    except Exception:
        pass
    try:
        if float(obj.SolidFlowTextureScaleX) <= 0: obj.SolidFlowTextureScaleX = 100.0
        if float(obj.SolidFlowTextureScaleY) <= 0: obj.SolidFlowTextureScaleY = 100.0
    except Exception:
        pass


class TextureManager:
    def __init__(self):
        self.bindings = {}

    def _key(self, obj):
        return (obj.Document.Name, obj.Name)

    def remove(self, obj):
        key = self._key(obj)
        binding = self.bindings.pop(key, None)
        if not binding:
            return
        root, nodes = binding
        for node in reversed(nodes):
            try:
                root.removeChild(node)
            except Exception:
                pass
        try:
            Gui.updateGui()
        except Exception:
            pass

    def _coord_node(self, mapping):
        if coin is None:
            return None
        type_names = {
            "Planare": "SoTextureCoordinatePlane",
            "Box": "SoTextureCoordinateCube",
            "Cilindrica": "SoTextureCoordinateCylinder",
            "Sferica": "SoTextureCoordinateSphere",
        }
        type_name = type_names.get(mapping, "SoTextureCoordinatePlane")
        try:
            if hasattr(coin, type_name):
                return getattr(coin, type_name)()
            t = coin.SoType.fromName(type_name)
            if not t.isBad():
                return t.createInstance()
        except Exception:
            pass
        try:
            return coin.SoTextureCoordinatePlane()
        except Exception:
            return None

    def apply(self, obj):
        self.remove(obj)
        if coin is None:
            return False, "Coin3D/Pivy non disponibile"
        try:
            path = str(obj.SolidFlowTexture)
        except Exception:
            path = ""
        if not path:
            return False, "Nessuna texture selezionata"
        if not os.path.isfile(path):
            return False, "File texture non trovato: " + path
        try:
            root = obj.ViewObject.RootNode
            if root is None or not hasattr(root, "insertChild"):
                return False, "ViewProvider senza RootNode modificabile"

            tex = coin.SoTexture2()
            tex.filename = path
            try:
                tex.wrapS = coin.SoTexture2.REPEAT
                tex.wrapT = coin.SoTexture2.REPEAT
            except Exception:
                pass

            coord = self._coord_node(str(obj.SolidFlowTextureMapping))
            transform = coin.SoTexture2Transform()
            sx = max(1.0, float(obj.SolidFlowTextureScaleX))
            sy = max(1.0, float(obj.SolidFlowTextureScaleY))
            # Larger percentage means visually larger texture = fewer UV repeats.
            transform.scaleFactor.setValue(100.0 / sx, 100.0 / sy)
            transform.translation.setValue(
                float(obj.SolidFlowTextureOffsetX),
                float(obj.SolidFlowTextureOffsetY),
            )
            try:
                transform.rotation = math.radians(float(obj.SolidFlowTextureRotation))
            except Exception:
                try:
                    transform.rotation = math.radians(float(obj.SolidFlowTextureRotation.Value))
                except Exception:
                    pass

            nodes = [tex]
            root.insertChild(tex, 0)
            insert_index = 1
            if coord is not None:
                root.insertChild(coord, insert_index)
                nodes.append(coord)
                insert_index += 1
            root.insertChild(transform, insert_index)
            nodes.append(transform)
            self.bindings[self._key(obj)] = (root, nodes)
            Gui.updateGui()
            return True, "OK"
        except Exception as exc:
            self.remove(obj)
            return False, str(exc)

    def sync_document(self):
        doc = App.ActiveDocument
        if doc is None:
            return
        for obj in doc.Objects:
            try:
                if "SolidFlowTexture" not in obj.PropertiesList:
                    continue
                path = str(obj.SolidFlowTexture)
                if not path:
                    continue
                key = self._key(obj)
                if key not in self.bindings:
                    self.apply(obj)
            except Exception:
                continue


_texture_manager = TextureManager()


class AppearanceStudioDialog(QtWidgets.QDialog):
    def __init__(self, objects, parent=None):
        super().__init__(parent or Gui.getMainWindow())
        self.setWindowTitle("SolidFlow — Appearance Studio")
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.setModal(True)
        self.setMinimumWidth(600)
        self.objects = list(objects)
        self.original = {obj.Name: _material_snapshot(obj) for obj in self.objects}
        self.original_texture = {}
        self._accepted = False

        for obj in self.objects:
            ensure_appearance_properties(obj)
            self.original_texture[obj.Name] = self._texture_state(obj)

        first = self.objects[0]
        snap = self.original[first.Name]

        layout = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel("Materiali, aspetto e texture")
        heading.setStyleSheet("font-size:17px; font-weight:600;")
        layout.addWidget(heading)
        names = ", ".join(getattr(o, "Label", o.Name) for o in self.objects[:4])
        if len(self.objects) > 4: names += " …"
        target = QtWidgets.QLabel("Oggetti: " + names)
        target.setWordWrap(True)
        layout.addWidget(target)

        mat_box = QtWidgets.QGroupBox("Aspetto")
        form = QtWidgets.QFormLayout(mat_box)
        self.preset = QtWidgets.QComboBox()
        self.preset.addItem("Personalizzato")
        self.preset.addItems(list(PRESETS.keys()))
        form.addRow("Preset", self.preset)

        self.color_btn = QtWidgets.QPushButton("Scegli colore…")
        self._color = tuple(snap["diffuse"])
        self._update_color_button()
        form.addRow("Colore base", self.color_btn)

        self.shiny = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.shiny.setRange(0, 100)
        self.shiny.setValue(int(round(snap["shininess"] * 100)))
        form.addRow("Lucentezza", self.shiny)

        self.rough_label = QtWidgets.QLabel()
        form.addRow("Rugosità visiva", self.rough_label)

        self.transparency = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.transparency.setRange(0, 95)
        self.transparency.setValue(int(round(snap["transparency"] * 100)))
        form.addRow("Trasparenza", self.transparency)
        layout.addWidget(mat_box)

        tex_box = QtWidgets.QGroupBox("Texture")
        tform = QtWidgets.QFormLayout(tex_box)
        file_row = QtWidgets.QHBoxLayout()
        self.texture_path = QtWidgets.QLineEdit(str(getattr(first, "SolidFlowTexture", "")))
        self.browse = QtWidgets.QPushButton("Sfoglia…")
        file_row.addWidget(self.texture_path, 1)
        file_row.addWidget(self.browse)
        file_holder = QtWidgets.QWidget(); file_holder.setLayout(file_row)
        tform.addRow("Immagine", file_holder)

        self.mapping = QtWidgets.QComboBox()
        self.mapping.addItems(["Planare", "Box", "Cilindrica", "Sferica"])
        current_mapping = str(getattr(first, "SolidFlowTextureMapping", "Planare"))
        idx = self.mapping.findText(current_mapping)
        self.mapping.setCurrentIndex(max(0, idx))
        tform.addRow("Mappatura", self.mapping)

        self.scale_x = QtWidgets.QDoubleSpinBox(); self.scale_x.setRange(1, 10000); self.scale_x.setDecimals(1); self.scale_x.setSuffix(" %"); self.scale_x.setValue(float(getattr(first, "SolidFlowTextureScaleX", 100.0)))
        self.scale_y = QtWidgets.QDoubleSpinBox(); self.scale_y.setRange(1, 10000); self.scale_y.setDecimals(1); self.scale_y.setSuffix(" %"); self.scale_y.setValue(float(getattr(first, "SolidFlowTextureScaleY", 100.0)))
        self.lock_ratio = QtWidgets.QCheckBox("Mantieni proporzioni X/Y"); self.lock_ratio.setChecked(True)
        tform.addRow("Scala X", self.scale_x); tform.addRow("Scala Y", self.scale_y); tform.addRow("", self.lock_ratio)
        self.rotation = QtWidgets.QDoubleSpinBox(); self.rotation.setRange(-360, 360); self.rotation.setDecimals(1); self.rotation.setSuffix("°"); self.rotation.setValue(self._angle_value(getattr(first, "SolidFlowTextureRotation", 0.0)))
        self.offset_x = QtWidgets.QDoubleSpinBox(); self.offset_x.setRange(-100, 100); self.offset_x.setDecimals(3); self.offset_x.setValue(float(getattr(first, "SolidFlowTextureOffsetX", 0.0)))
        self.offset_y = QtWidgets.QDoubleSpinBox(); self.offset_y.setRange(-100, 100); self.offset_y.setDecimals(3); self.offset_y.setValue(float(getattr(first, "SolidFlowTextureOffsetY", 0.0)))
        tform.addRow("Rotazione", self.rotation); tform.addRow("Offset X", self.offset_x); tform.addRow("Offset Y", self.offset_y)
        tex_actions = QtWidgets.QHBoxLayout()
        self.apply_texture_btn = QtWidgets.QPushButton("Aggiorna texture")
        self.remove_texture_btn = QtWidgets.QPushButton("Rimuovi texture")
        tex_actions.addWidget(self.apply_texture_btn); tex_actions.addWidget(self.remove_texture_btn); tex_actions.addStretch(1)
        tex_holder = QtWidgets.QWidget(); tex_holder.setLayout(tex_actions)
        tform.addRow("", tex_holder)
        layout.addWidget(tex_box)

        self.status = QtWidgets.QLabel("La texture è una preview di visualizzazione e non modifica la geometria CAD.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(buttons)
        buttons.accepted.connect(self.accept_changes)
        buttons.rejected.connect(self.reject)

        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.color_btn.clicked.connect(self._choose_color)
        self.shiny.valueChanged.connect(self._appearance_changed)
        self.shiny.valueChanged.connect(self._update_rough_label)
        self.transparency.valueChanged.connect(self._appearance_changed)
        self.browse.clicked.connect(self._browse_texture)
        self.apply_texture_btn.clicked.connect(self.apply_texture)
        self.remove_texture_btn.clicked.connect(self.remove_texture)
        self.scale_x.valueChanged.connect(self._scale_x_changed)
        self._update_rough_label()

    def _angle_value(self, value):
        try: return float(value.Value)
        except Exception:
            try: return float(value)
            except Exception: return 0.0

    def _texture_state(self, obj):
        state = {}
        for name, default in (
            ("SolidFlowTexture", ""), ("SolidFlowTextureMapping", "Planare"),
            ("SolidFlowTextureScaleX", 100.0), ("SolidFlowTextureScaleY", 100.0),
            ("SolidFlowTextureRotation", 0.0), ("SolidFlowTextureOffsetX", 0.0),
            ("SolidFlowTextureOffsetY", 0.0), ("SolidFlowMaterialPreset", ""),
            ("SolidFlowDensity", 0.0),
        ):
            try:
                value = getattr(obj, name)
                if name == "SolidFlowTextureRotation": value = self._angle_value(value)
                state[name] = value
            except Exception:
                state[name] = default
        return state

    def _set_texture_state(self, obj, state):
        ensure_appearance_properties(obj)
        for name, value in state.items():
            try: setattr(obj, name, value)
            except Exception: pass

    def _update_color_button(self):
        r, g, b = [max(0, min(255, int(round(v * 255)))) for v in self._color]
        self.color_btn.setStyleSheet("QPushButton { background-color: rgb(%d,%d,%d); }" % (r, g, b))

    def _choose_color(self):
        initial = QtGui.QColor.fromRgbF(*self._color)
        color = QtWidgets.QColorDialog.getColor(initial, self, "Colore materiale")
        if not color.isValid(): return
        self._color = (color.redF(), color.greenF(), color.blueF())
        self.preset.blockSignals(True); self.preset.setCurrentIndex(0); self.preset.blockSignals(False)
        self._update_color_button(); self._appearance_changed()

    def _preset_changed(self, index):
        if index <= 0: return
        name = self.preset.currentText(); data = PRESETS[name]
        self._color = data["diffuse"]
        self.shiny.blockSignals(True); self.shiny.setValue(int(round(data["shininess"] * 100))); self.shiny.blockSignals(False)
        self.transparency.blockSignals(True); self.transparency.setValue(int(round(data["transparency"] * 100))); self.transparency.blockSignals(False)
        self._update_color_button(); self._update_rough_label(); self._appearance_changed()

    def _current_material_data(self):
        preset_name = self.preset.currentText() if self.preset.currentIndex() > 0 else None
        preset = PRESETS.get(preset_name, {})
        diffuse = self._color
        ambient = tuple(max(0.0, min(1.0, v * 0.32)) for v in diffuse)
        return {
            "diffuse": diffuse,
            "ambient": ambient,
            "specular": preset.get("specular", (0.25, 0.25, 0.25)),
            "emissive": (0.0, 0.0, 0.0),
            "shininess": self.shiny.value() / 100.0,
            "transparency": self.transparency.value() / 100.0,
        }

    def _appearance_changed(self, *args):
        data = self._current_material_data()
        preset_name = self.preset.currentText() if self.preset.currentIndex() > 0 else "Personalizzato"
        density = PRESETS.get(preset_name, {}).get("density", 0.0)
        for obj in self.objects:
            _apply_material(obj, data)
            ensure_appearance_properties(obj)
            try: obj.SolidFlowMaterialPreset = preset_name
            except Exception: pass
            try: obj.SolidFlowDensity = float(density)
            except Exception: pass
        try: Gui.updateGui()
        except Exception: pass

    def _update_rough_label(self, *args):
        rough = 100 - self.shiny.value()
        self.rough_label.setText("{} % (proxy: inverso della lucentezza Coin3D)".format(rough))

    def _browse_texture(self):
        path, _flt = QtWidgets.QFileDialog.getOpenFileName(
            self, "Seleziona texture", "", "Immagini (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;Tutti i file (*)"
        )
        if path:
            self.texture_path.setText(path)
            self.apply_texture()

    def _scale_x_changed(self, value):
        if self.lock_ratio.isChecked():
            self.scale_y.blockSignals(True); self.scale_y.setValue(value); self.scale_y.blockSignals(False)

    def _write_texture_controls(self, obj):
        ensure_appearance_properties(obj)
        obj.SolidFlowTexture = self.texture_path.text().strip()
        obj.SolidFlowTextureMapping = self.mapping.currentText()
        obj.SolidFlowTextureScaleX = float(self.scale_x.value())
        obj.SolidFlowTextureScaleY = float(self.scale_y.value())
        obj.SolidFlowTextureRotation = float(self.rotation.value())
        obj.SolidFlowTextureOffsetX = float(self.offset_x.value())
        obj.SolidFlowTextureOffsetY = float(self.offset_y.value())

    def apply_texture(self):
        path = self.texture_path.text().strip()
        if path and not os.path.isfile(path):
            self.status.setText("Texture non trovata: " + path)
            return
        messages = []
        for obj in self.objects:
            self._write_texture_controls(obj)
            if path:
                ok, detail = _texture_manager.apply(obj)
                messages.append(("✓ " if ok else "✗ ") + obj.Label + ("" if ok else ": " + detail))
            else:
                _texture_manager.remove(obj)
        self.status.setText("\n".join(messages) if messages else "Texture rimossa.")

    def remove_texture(self):
        self.texture_path.clear()
        for obj in self.objects:
            ensure_appearance_properties(obj)
            try: obj.SolidFlowTexture = ""
            except Exception: pass
            _texture_manager.remove(obj)
        self.status.setText("Texture rimossa dalla preview SolidFlow.")

    def accept_changes(self):
        self._appearance_changed()
        self.apply_texture()
        self._accepted = True
        try:
            for obj in self.objects:
                obj.Document.recompute()
        except Exception:
            pass
        super().accept()

    def reject(self):
        if not self._accepted:
            for obj in self.objects:
                original = self.original.get(obj.Name)
                if original: _apply_material(obj, original)
                old_state = self.original_texture.get(obj.Name)
                if old_state:
                    self._set_texture_state(obj, old_state)
                    if str(old_state.get("SolidFlowTexture", "")):
                        _texture_manager.apply(obj)
                    else:
                        _texture_manager.remove(obj)
            try: Gui.updateGui()
            except Exception: pass
        super().reject()


def launch_appearance_studio():
    objects = selected_appearance_objects()
    if not objects:
        _message("SolidFlow Appearance Studio", "Seleziona uno o più solidi/mesh e rilancia Materiale / Appearance Studio.")
        return
    _exec_dialog(AppearanceStudioDialog(objects))


def sync_textures():
    _texture_manager.sync_document()
