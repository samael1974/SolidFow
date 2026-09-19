# -*- coding: utf-8 -*-
"""SolidFlow profile-region workflow.

Detects connected closed regions in a Sketch, highlights the chosen area in the
3D view, and passes only that region's EdgeN sub-elements to native PartDesign
Pad/Pocket/Revolution features.  The original Sketch remains reusable.
"""
from __future__ import annotations

from dataclasses import dataclass

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

try:
    import Part
except Exception:
    Part = None

VERSION = "0.4.0-beta.11-profiles"


@dataclass
class ProfileRegion:
    index: int
    edge_names: list
    shape: object
    area: float
    boundary_count: int


def _main_window():
    return Gui.getMainWindow()


def _same_edge(a, b):
    try:
        return bool(a.isSame(b))
    except Exception:
        try:
            return bool(a.isEqual(b))
        except Exception:
            return False


def _wire_edge_names(sketch, wire):
    result = []
    try:
        all_edges = list(sketch.Shape.Edges)
    except Exception:
        return result
    for wedge in list(getattr(wire, "Edges", []) or []):
        for idx, edge in enumerate(all_edges, 1):
            if _same_edge(wedge, edge):
                name = "Edge%d" % idx
                if name not in result:
                    result.append(name)
                break
    return result


def _is_closed_wire(wire):
    try:
        return bool(wire.isClosed())
    except Exception:
        try:
            return bool(wire.Closed)
        except Exception:
            return False


def _make_face(wire):
    if Part is None:
        return None
    try:
        face = Part.Face(wire)
        if face.isNull() or not face.isValid():
            return None
        return face
    except Exception:
        return None


def _contains(face, point):
    try:
        return bool(face.isInside(point, 1e-7, True))
    except Exception:
        return False


def detect_profile_regions(sketch):
    """Return connected planar regions with the EdgeN boundaries they require.

    For nested closed wires, a parent region includes its own boundary plus its
    direct child boundaries.  Example: hexagon + inner circle gives two useful
    regions: hexagon-minus-circle and circle.
    """
    if Part is None or sketch is None:
        return []
    try:
        sketch.Document.recompute()
        wires = [w for w in list(sketch.Shape.Wires) if _is_closed_wire(w)]
    except Exception:
        return []

    records = []
    for wire in wires:
        face = _make_face(wire)
        names = _wire_edge_names(sketch, wire)
        if face is None or not names:
            continue
        try:
            area = float(face.Area)
            point = face.CenterOfMass
        except Exception:
            continue
        if area <= 1e-10:
            continue
        records.append({"wire": wire, "face": face, "names": names, "area": area, "point": point, "parent": None})

    # Smallest containing larger loop is the direct parent.
    for i, rec in enumerate(records):
        candidates = []
        for j, other in enumerate(records):
            if i == j or other["area"] <= rec["area"]:
                continue
            if _contains(other["face"], rec["point"]):
                candidates.append((other["area"], j))
        if candidates:
            rec["parent"] = min(candidates)[1]

    children = {i: [] for i in range(len(records))}
    for i, rec in enumerate(records):
        if rec["parent"] is not None:
            children[rec["parent"]].append(i)

    regions = []
    for i, rec in enumerate(records):
        region_shape = rec["face"]
        names = list(rec["names"])
        direct_children = children.get(i, [])
        for child_idx in direct_children:
            child = records[child_idx]
            for name in child["names"]:
                if name not in names:
                    names.append(name)
            try:
                region_shape = region_shape.cut(child["face"])
            except Exception:
                pass
        try:
            area = float(region_shape.Area)
        except Exception:
            area = rec["area"]
        if area <= 1e-10:
            continue
        regions.append(ProfileRegion(len(regions) + 1, names, region_shape, area, 1 + len(direct_children)))

    # Largest regions first is generally the most intuitive ordering.
    regions.sort(key=lambda r: r.area, reverse=True)
    for index, region in enumerate(regions, 1):
        region.index = index
    return regions


class ProfilePickerDialog(QtWidgets.QDialog):
    def __init__(self, sketch, regions, parent=None):
        super().__init__(parent or _main_window())
        self.sketch = sketch
        self.regions = list(regions)
        self.selected_edge_names = None
        self.preview = None
        self.setWindowTitle("SolidFlow — Scegli area dello Sketch")
        self.resize(520, 360)

        root = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("<b>Scegli l'area da lavorare</b>")
        root.addWidget(title)
        hint = QtWidgets.QLabel(
            "SolidFlow ha trovato più regioni chiuse. Seleziona una voce: l'area corrispondente "
            "si illumina nella vista 3D. Solo quella regione verrà usata dalla lavorazione."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.list_widget = QtWidgets.QListWidget()
        for region in self.regions:
            holes = max(0, region.boundary_count - 1)
            label = "Regione %d — area %.2f mm²" % (region.index, region.area)
            if holes:
                label += " — %d foro/i interno/i" % holes
            self.list_widget.addItem(label)
        root.addWidget(self.list_widget)

        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        root.addWidget(buttons)
        self.ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        self.ok_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.list_widget.currentRowChanged.connect(self._region_changed)

        if self.regions:
            self.list_widget.setCurrentRow(0)

    def _delete_preview(self):
        if self.preview is None:
            return
        try:
            doc = self.preview.Document
            name = self.preview.Name
            self.preview = None
            if doc and doc.getObject(name):
                doc.removeObject(name)
                doc.recompute()
        except Exception:
            self.preview = None

    def _region_changed(self, row):
        self._delete_preview()
        if row < 0 or row >= len(self.regions):
            self.ok_button.setEnabled(False)
            return
        region = self.regions[row]
        self.selected_edge_names = list(region.edge_names)
        self.ok_button.setEnabled(True)
        self.info.setText(
            "Bordi usati: %d. Questa scelta non duplica lo Sketch: lo stesso Sketch potrà essere riutilizzato "
            "per altre lavorazioni compatibili." % len(region.edge_names)
        )
        try:
            doc = self.sketch.Document
            preview = doc.addObject("PartDesign::Feature", "SolidFlowProfilePreview")
            preview.Label = "SolidFlow — area selezionata (temporanea)"
            preview.Shape = region.shape
            try:
                placement = self.sketch.getGlobalPlacement()
            except Exception:
                placement = getattr(self.sketch, "Placement", None)
            if placement is not None:
                try:
                    preview.Placement = placement
                except Exception:
                    pass
            try:
                preview.ViewObject.ShapeColor = (0.20, 0.75, 1.00)
                preview.ViewObject.LineColor = (0.05, 0.25, 0.80)
                preview.ViewObject.Transparency = 45
                preview.ViewObject.LineWidth = 3.0
                if hasattr(preview.ViewObject, "Selectable"):
                    preview.ViewObject.Selectable = False
            except Exception:
                pass
            self.preview = preview
            doc.recompute()
        except Exception as exc:
            App.Console.PrintWarning("SolidFlow Profile Picker preview: %s\n" % exc)

    def accept(self):
        if not self.selected_edge_names:
            return
        self._delete_preview()
        super().accept()

    def reject(self):
        self.selected_edge_names = None
        self._delete_preview()
        super().reject()

    def closeEvent(self, event):
        self._delete_preview()
        super().closeEvent(event)


def pick_profile_region(sketch, regions=None):
    regions = list(regions if regions is not None else detect_profile_regions(sketch))
    if not regions:
        return []
    if len(regions) == 1:
        return list(regions[0].edge_names)
    dlg = ProfilePickerDialog(sketch, regions)
    result = dlg.exec_()
    if result == QtWidgets.QDialog.Accepted:
        return list(dlg.selected_edge_names or [])
    return None


def _finish_edit_and_relaunch(sketch, mode):
    try:
        import solidflow_features as features
        if features._active_edit_sketch() is not sketch:
            return False
    except Exception:
        return False
    try:
        Gui.ActiveDocument.resetEdit()
    except Exception:
        try:
            Gui.runCommand("Sketcher_LeaveSketch", 0)
        except Exception:
            pass
    try:
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sketch)
    except Exception:
        pass
    QtCore.QTimer.singleShot(150, lambda: launch_profile_feature(mode))
    return True


def launch_profile_feature(mode):
    import solidflow_features as features

    sketch, subs = features.selected_profile()
    if sketch is None:
        features._warning("SolidFlow", "Seleziona oppure modifica uno Sketch, poi riprova.")
        return
    if _finish_edit_and_relaunch(sketch, mode):
        return

    if not subs:
        regions = detect_profile_regions(sketch)
        if regions:
            subs = pick_profile_region(sketch, regions)
            if subs is None:
                return

    dlg = features.FeaturePreviewDialog(mode, sketch, subs)
    result = dlg.exec_()
    if result == QtWidgets.QDialog.Accepted:
        # Deliberately keep the source sketch visible/reusable.  PartDesign may
        # hide a consumed profile automatically, but SolidFlow's workflow is
        # based on reusing regions from one master sketch where valid.
        try:
            sketch.ViewObject.Visibility = True
        except Exception:
            pass


def launch_pad():
    launch_profile_feature("pad")


def launch_pocket():
    launch_profile_feature("pocket")


def launch_revolution():
    launch_profile_feature("revolution")


def show_closed_profiles():
    import solidflow_features as features

    sketch, _subs = features.selected_profile()
    if sketch is None:
        features._warning("SolidFlow", "Seleziona uno Sketch.")
        return
    regions = detect_profile_regions(sketch)
    if not regions:
        features._warning("SolidFlow", "Non ho trovato regioni chiuse utilizzabili nello Sketch.")
        return
    pick_profile_region(sketch, regions)
