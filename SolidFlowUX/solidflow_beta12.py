# -*- coding: utf-8 -*-
"""SolidFlow UX beta12 stabilization.

Focused UX fixes agreed during user testing:
- safer external-geometry projection (skip duplicate references cleanly);
- direct profile-region picking in the 3D view for multi-region sketches;
- safe Coin3D vector assignment for Studio floor/contact shadow;
- calmer SolidWorks-like sketch dimension colours;
- release-level integration without changing native FreeCAD geometry kernels.
"""
from __future__ import annotations

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

try:
    from pivy import coin
except Exception:
    coin = None

VERSION = "0.4.0-beta.12"
PREF_PATH = "User parameter:BaseApp/Preferences/Mod/SolidFlowUX"
VIEW_PATH = "User parameter:BaseApp/Preferences/View"
_installed = False


def _prefs():
    return App.ParamGet(PREF_PATH)


def _view_prefs():
    return App.ParamGet(VIEW_PATH)


def _save_original_unsigned(group, key):
    p = _prefs()
    marker = "SavedOriginal_" + key
    if not p.GetBool(marker, False):
        p.SetUnsigned("Original_" + key, group.GetUnsigned(key, 0))
        p.SetBool(marker, True)


def apply_sketch_visual_theme():
    """Apply a calmer dimension palette while keeping errors visually obvious."""
    view = _view_prefs()
    values = {
        # Packed RGBA (RRGGBBAA).  Intentionally sober blue/grey tones.
        "ConstrainedDimColor": 0x52677FFF,
        "NonDrivingConstrDimColor": 0x7D8A99FF,
        "ExprBasedConstrDimColor": 0x627D98FF,
        "ConstrainedIcoColor": 0x52677FFF,
        "DeactivatedConstrDimColor": 0x9AA4AFFF,
    }
    for key, value in values.items():
        _save_original_unsigned(view, key)
        view.SetUnsigned(key, int(value))
    _prefs().SetBool("Beta12SketchThemeApplied", True)


def _normalise_external_links(sketch):
    """Return {(objectName, EdgeN)} from the ExternalGeometry link property."""
    existing = set()
    try:
        entries = list(getattr(sketch, "ExternalGeometry", []) or [])
    except Exception:
        entries = []
    for entry in entries:
        try:
            obj = entry[0]
            obj_name = getattr(obj, "Name", str(obj))
            subs = entry[1]
        except Exception:
            continue
        if isinstance(subs, str):
            subs = [subs]
        try:
            iterator = list(subs)
        except Exception:
            iterator = [subs]
        for sub in iterator:
            name = str(sub)
            if name:
                existing.add((str(obj_name), name))
    return existing


def _safe_add_face_external_refs(sketch, support, face_name):
    """Project face perimeter/holes but silently reuse existing references."""
    import solidflow_features as features

    added, failed = [], []
    existing = _normalise_external_links(sketch)
    for edge_name in features._face_edge_names(support, face_name):
        key = (support.Name, edge_name)
        if key in existing:
            continue
        try:
            sketch.addExternal(support.Name, edge_name)
            added.append(edge_name)
            existing.add(key)
        except Exception as exc:
            text = str(exc)
            # FreeCAD raises for duplicate external links.  In SolidFlow this is
            # an idempotent operation, so an already-existing link is success.
            if "already exists" in text.lower() or "gia" in text.lower():
                existing.add(key)
                continue
            failed.append((edge_name, text))
    try:
        sketch.Document.recompute()
    except Exception:
        pass
    return added, failed


def _sbvec3(x, y, z):
    if coin is None:
        return None
    return coin.SbVec3f(float(x), float(y), float(z))


def _safe_add_cube(self, parent, center, size, rgb, transparency=0.0):
    sep = coin.SoSeparator()
    sep.addChild(self._material(rgb, transparency))
    tr = coin.SoTransform()
    tr.translation.setValue(_sbvec3(center[0], center[1], center[2]))
    sep.addChild(tr)
    cube = coin.SoCube()
    cube.width = max(1e-5, float(size[0]))
    cube.height = max(1e-5, float(size[1]))
    cube.depth = max(1e-5, float(size[2]))
    sep.addChild(cube)
    parent.addChild(sep)


def _safe_soft_shadow_xy(self, parent, cx, cy, z, sx, sy, diag):
    for factor, alpha, dx, dy in (
        (1.00, 0.73, 0.035, -0.035),
        (0.72, 0.62, 0.020, -0.020),
    ):
        sep = coin.SoSeparator()
        sep.addChild(self._material((0.10, 0.11, 0.12), alpha))
        tr = coin.SoTransform()
        tr.translation.setValue(_sbvec3(
            cx + dx * sx,
            cy + dy * sy,
            z + diag * 0.0015,
        ))
        tr.scaleFactor.setValue(_sbvec3(
            max(sx * 0.55 * factor, diag * 0.04),
            max(sy * 0.42 * factor, diag * 0.04),
            max(diag * 0.002, 1e-4),
        ))
        sep.addChild(tr)
        sphere = coin.SoSphere()
        sphere.radius = 1.0
        sep.addChild(sphere)
        parent.addChild(sep)


class _RegionSelectionObserver:
    def __init__(self, picker):
        self.picker = picker

    def addSelection(self, doc, obj, sub, pnt):
        self.picker.on_selection(doc, obj)

    def removeSelection(self, doc, obj, sub):
        pass

    def clearSelection(self, doc):
        pass

    def setSelection(self, doc):
        pass


class DirectRegionPicker:
    """Synchronous-but-modeless region picker: click the highlighted area itself."""

    COLORS = [
        (0.25, 0.75, 1.00),
        (0.35, 0.90, 0.55),
        (1.00, 0.72, 0.28),
        (0.72, 0.50, 1.00),
        (1.00, 0.45, 0.55),
    ]

    def __init__(self, sketch, regions):
        self.sketch = sketch
        self.regions = list(regions)
        self.doc = sketch.Document
        self.result = None
        self.previews = []
        self.map = {}
        self.loop = QtCore.QEventLoop()
        self.observer = _RegionSelectionObserver(self)

        self.dialog = QtWidgets.QDialog(Gui.getMainWindow())
        self.dialog.setWindowTitle("SolidFlow — Scegli area")
        self.dialog.setModal(False)
        self.dialog.setWindowModality(QtCore.Qt.NonModal)
        self.dialog.resize(390, 150)
        lay = QtWidgets.QVBoxLayout(self.dialog)
        title = QtWidgets.QLabel("<b>Clicca direttamente la regione da lavorare</b>")
        lay.addWidget(title)
        hint = QtWidgets.QLabel(
            "Le regioni chiuse dello Sketch sono evidenziate sul modello. "
            "Ruota/zooma liberamente e clicca dentro la zona desiderata."
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.status = QtWidgets.QLabel("In attesa della selezione…")
        lay.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        buttons.rejected.connect(self.cancel)
        lay.addWidget(buttons)
        self.dialog.finished.connect(lambda _r: self.cancel() if self.result is None else None)

    def _placement(self):
        try:
            return self.sketch.getGlobalPlacement()
        except Exception:
            return getattr(self.sketch, "Placement", None)

    def build_previews(self):
        placement = self._placement()
        for idx, region in enumerate(self.regions):
            try:
                obj = self.doc.addObject("Part::Feature", "SolidFlowRegionPreview")
                obj.Label = "SolidFlow — regione %d (temporanea)" % (idx + 1)
                obj.Shape = region.shape
                if placement is not None:
                    obj.Placement = placement
                color = self.COLORS[idx % len(self.COLORS)]
                obj.ViewObject.ShapeColor = color
                obj.ViewObject.LineColor = color
                obj.ViewObject.Transparency = 45
                obj.ViewObject.LineWidth = 3.0
                obj.ViewObject.Selectable = True
                self.previews.append(obj)
                self.map[obj.Name] = region
            except Exception as exc:
                App.Console.PrintWarning("SolidFlow region preview: %s\n" % exc)
        try:
            self.doc.recompute()
        except Exception:
            pass

    def on_selection(self, doc_name, obj_name):
        region = self.map.get(str(obj_name))
        if region is None:
            return
        self.result = list(region.edge_names)
        self.status.setText("Regione selezionata — %d bordi" % len(self.result))
        if self.loop.isRunning():
            self.loop.quit()

    def cancel(self):
        if self.loop.isRunning():
            self.loop.quit()

    def cleanup(self):
        try:
            Gui.Selection.removeObserver(self.observer)
        except Exception:
            pass
        try:
            Gui.Selection.clearSelection()
        except Exception:
            pass
        for obj in list(self.previews):
            try:
                if self.doc.getObject(obj.Name):
                    self.doc.removeObject(obj.Name)
            except Exception:
                pass
        try:
            self.doc.recompute()
        except Exception:
            pass
        self.previews = []
        self.map = {}
        try:
            self.dialog.close()
        except Exception:
            pass

    def run(self):
        self.build_previews()
        if not self.previews:
            return None
        try:
            Gui.Selection.addObserver(self.observer)
        except Exception as exc:
            App.Console.PrintWarning("SolidFlow region observer: %s\n" % exc)
            self.cleanup()
            return None
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.loop.exec_()
        result = self.result
        self.cleanup()
        return result


def _pick_profile_region_direct(sketch, regions=None):
    import solidflow_profiles as profiles
    regions = list(regions if regions is not None else profiles.detect_profile_regions(sketch))
    if not regions:
        return []
    if len(regions) == 1:
        return list(regions[0].edge_names)
    return DirectRegionPicker(sketch, regions).run()


def _patch_shadows():
    if coin is None:
        return
    try:
        import solidflow_beta5 as beta5
        beta5.StudioShadowController._add_cube = _safe_add_cube
        beta5.StudioShadowController._add_soft_shadow_xy = _safe_soft_shadow_xy
        studio = getattr(beta5, "_studio", None)
        if studio is not None and getattr(studio, "enabled", False):
            # Rebuild with the safe vector conversion immediately.
            studio.refresh()
    except Exception as exc:
        App.Console.PrintWarning("SolidFlow beta12 shadow patch: %s\n" % exc)


def install():
    global _installed
    if _installed:
        return

    import solidflow_features as features
    import solidflow_profiles as profiles

    features._add_face_external_refs = _safe_add_face_external_refs
    profiles.pick_profile_region = _pick_profile_region_direct
    apply_sketch_visual_theme()
    _patch_shadows()

    _installed = True
    App.Console.PrintMessage(
        "SolidFlow beta12: stabilizzazione UX attiva (quote, riferimenti, regioni, ombre).\n"
    )
