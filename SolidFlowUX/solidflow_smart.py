# -*- coding: utf-8 -*-
"""SolidFlow Smart Sketch core.

One shared engine for:
- native FreeCAD AutoConstraint tuning;
- selection classification while a Sketch is edited;
- 3-5 ranked constraint/dimension suggestions;
- automatic dimension-type recognition (the user always confirms by clicking).

The engine never creates a constraint on its own.  It only ranks native
Sketcher commands; FreeCAD remains the solver and source of truth.
"""
from __future__ import annotations

import math

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

VERSION = "0.4.0-beta.10-smart"
PREF_PATH = "User parameter:BaseApp/Preferences/Mod/SolidFlowUX"
SKETCH_GENERAL_PATH = "User parameter:BaseApp/Preferences/Mod/Sketcher/General"


def _prefs():
    return App.ParamGet(PREF_PATH)


def _sketch_general():
    return App.ParamGet(SKETCH_GENERAL_PATH)


def smart_snap_enabled():
    return _prefs().GetBool("SmartSnapEnabled", True)


def constraint_hints_enabled():
    return _prefs().GetBool("ConstraintHintsEnabled", True)


def smart_snap_delay():
    return max(0, min(1000, _prefs().GetInt("SmartSnapDelay", 150)))


def set_smart_snap_enabled(enabled):
    enabled = bool(enabled)
    _prefs().SetBool("SmartSnapEnabled", enabled)
    if enabled:
        apply_smart_snap_preferences()
    else:
        restore_native_snap_delay()


def set_constraint_hints_enabled(enabled):
    _prefs().SetBool("ConstraintHintsEnabled", bool(enabled))


def set_smart_snap_delay(value):
    value = max(0, min(1000, int(value)))
    _prefs().SetInt("SmartSnapDelay", value)
    if smart_snap_enabled():
        _sketch_general().SetInt("DragAutoConstraintDelay", value)


def active_sketch():
    try:
        if not Gui.ActiveDocument:
            return None
        edited = Gui.ActiveDocument.getInEdit()
        if edited is None:
            return None
        obj = getattr(edited, "Object", None)
        if obj is not None:
            return obj
        if hasattr(edited, "isDerivedFrom") and edited.isDerivedFrom("SketcherGui::ViewProviderSketch"):
            return edited.Object
    except Exception:
        pass
    return None


def ensure_current_sketch_autoconstraints():
    if not smart_snap_enabled():
        return False
    try:
        if not Gui.ActiveDocument:
            return False
        vp = Gui.ActiveDocument.getInEdit()
        if vp is None:
            return False
        if hasattr(vp, "Autoconstraints"):
            vp.Autoconstraints = True
            return True
        obj = getattr(vp, "Object", None)
        view = getattr(obj, "ViewObject", None) if obj else None
        if view is not None and hasattr(view, "Autoconstraints"):
            view.Autoconstraints = True
            return True
    except Exception:
        pass
    return False


def apply_smart_snap_preferences():
    """Tune FreeCAD's native AutoConstraint engine, without replacing it."""
    if not smart_snap_enabled():
        return
    grp = _sketch_general()
    p = _prefs()
    if not p.GetBool("SavedOriginalDragAutoConstraintDelay", False):
        p.SetInt("OriginalDragAutoConstraintDelay", grp.GetInt("DragAutoConstraintDelay", 400))
        p.SetBool("SavedOriginalDragAutoConstraintDelay", True)
    grp.SetInt("DragAutoConstraintDelay", smart_snap_delay())
    ensure_current_sketch_autoconstraints()


def restore_native_snap_delay():
    p = _prefs()
    if p.GetBool("SavedOriginalDragAutoConstraintDelay", False):
        _sketch_general().SetInt(
            "DragAutoConstraintDelay",
            p.GetInt("OriginalDragAutoConstraintDelay", 400),
        )


def _command_exists(name):
    try:
        return bool(name and Gui.Command.get(name))
    except Exception:
        return False


def _first_command(*names):
    for name in names:
        if _command_exists(name):
            return name
    return None


COMMAND_CANDIDATES = {
    "coincident": ("Sketcher_ConstrainCoincidentUnified", "Sketcher_ConstrainCoincident"),
    "point_on": ("Sketcher_ConstrainPointOnObject",),
    "horizontal": ("Sketcher_ConstrainHorizontal",),
    "vertical": ("Sketcher_ConstrainVertical",),
    "horver": ("Sketcher_ConstrainHorVer",),
    "parallel": ("Sketcher_ConstrainParallel",),
    "perpendicular": ("Sketcher_ConstrainPerpendicular",),
    "tangent": ("Sketcher_ConstrainTangent",),
    "equal": ("Sketcher_ConstrainEqual",),
    "symmetric": ("Sketcher_ConstrainSymmetric",),
    "distance": ("Sketcher_ConstrainDistance",),
    "distance_x": ("Sketcher_ConstrainDistanceX",),
    "distance_y": ("Sketcher_ConstrainDistanceY",),
    "radius": ("Sketcher_ConstrainRadius",),
    "diameter": ("Sketcher_ConstrainDiameter",),
    "angle": ("Sketcher_ConstrainAngle",),
    "lock": ("Sketcher_ConstrainLock",),
}

# Workbench commands may be registered after addon startup.
COMMANDS = {}


def refresh_commands():
    for key, names in COMMAND_CANDIDATES.items():
        COMMANDS[key] = _first_command(*names)


def all_constraint_actions():
    """Ordered list used by the 'Altri…' menu."""
    refresh_commands()
    rows = [
        ("Coincidente / Concentrico", "coincident"),
        ("Punto su oggetto", "point_on"),
        ("Orizzontale / Verticale auto", "horver"),
        ("Orizzontale", "horizontal"),
        ("Verticale", "vertical"),
        ("Parallelo", "parallel"),
        ("Perpendicolare", "perpendicular"),
        ("Tangente", "tangent"),
        ("Uguale", "equal"),
        ("Simmetria", "symmetric"),
        ("Lunghezza / distanza", "distance"),
        ("Quota X", "distance_x"),
        ("Quota Y", "distance_y"),
        ("Raggio", "radius"),
        ("Diametro", "diameter"),
        ("Angolo", "angle"),
        ("Blocca", "lock"),
    ]
    out = []
    for label, key in rows:
        cmd = COMMANDS.get(key)
        if cmd:
            out.append((label, cmd))
    return out


def _vec2(point):
    try:
        return (float(point.x), float(point.y))
    except Exception:
        return None


def _line_data(geo):
    if geo is None:
        return None
    try:
        a = _vec2(geo.StartPoint)
        b = _vec2(geo.EndPoint)
        if a is None or b is None:
            return None
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            return None
        return {"a": a, "b": b, "dx": dx, "dy": dy, "length": length}
    except Exception:
        return None


def _circle_data(geo):
    if geo is None:
        return None
    try:
        radius = float(geo.Radius)
        center = _vec2(geo.Center)
        if radius <= 0 or center is None:
            return None
        return {"radius": radius, "center": center}
    except Exception:
        return None


def _subobject_geometry(sub):
    if sub is None:
        return None
    try:
        return sub.Curve
    except Exception:
        return None


def selected_geometry_info():
    """Return ``(sketch, descriptors)`` for the current Sketch selection.

    Each descriptor is a dict with name/kind/geometry/source.  Internal sketch
    geometry is read from ``Sketch.Geometry`` when possible; external edges use
    the selected TopoShape curve.  This lets SolidFlow recognise circles/holes
    projected from the supporting solid as well as normal sketch geometry.
    """
    sketch = active_sketch()
    if sketch is None:
        return None, []

    descriptors = []
    try:
        selection = Gui.Selection.getSelectionEx()
    except Exception:
        selection = []

    for item in selection:
        if getattr(item, "Object", None) is not sketch:
            continue
        names = list(getattr(item, "SubElementNames", []) or [])
        subs = list(getattr(item, "SubObjects", []) or [])
        for i, raw_name in enumerate(names):
            name = str(raw_name)
            sub = subs[i] if i < len(subs) else None
            lower = name.lower()
            if lower.startswith("vertex"):
                descriptors.append({"name": name, "kind": "vertex", "geo": None, "source": "point"})
                continue
            if not (lower.startswith("edge") or lower.startswith("externaledge")):
                continue

            geo = None
            source = "external" if lower.startswith("externaledge") else "internal"
            if source == "internal":
                try:
                    idx = int(name[4:]) - 1
                    if 0 <= idx < len(sketch.Geometry):
                        geo = sketch.Geometry[idx]
                except Exception:
                    geo = None
            if geo is None:
                geo = _subobject_geometry(sub)
                if source == "internal" and geo is not None:
                    # EdgeN beyond the internal geometry count is usually an
                    # external/reference edge exposed by the ViewProvider.
                    source = "external"

            line = _line_data(geo)
            circ = _circle_data(geo)
            if line:
                kind = "line"
            elif circ:
                kind = "circle"
            elif geo is not None:
                kind = "curve"
            else:
                kind = "edge"
            descriptors.append({"name": name, "kind": kind, "geo": geo, "source": source})
    return sketch, descriptors


def sketch_selection_count():
    _sketch, items = selected_geometry_info()
    return len(items)


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _suggest(label, key, reason, score, kind="constraint"):
    command = COMMANDS.get(key)
    if not command:
        return None
    return {
        "label": label,
        "command": command,
        "reason": reason,
        "score": float(score),
        "kind": kind,
    }


def constraint_suggestions(limit=5):
    """Rank the most probable native constraints/dimensions.

    No suggestion is applied automatically.  The user confirms by clicking a
    button in the mini-window or S palette.
    """
    refresh_commands()
    if not constraint_hints_enabled():
        return []
    _sketch, items = selected_geometry_info()
    if not items:
        return []

    suggestions = []
    n = len(items)
    kinds = [x["kind"] for x in items]

    if n == 1:
        item = items[0]
        line = _line_data(item["geo"])
        circle = _circle_data(item["geo"])
        if line:
            ratio_h = abs(line["dy"]) / max(line["length"], 1e-9)
            ratio_v = abs(line["dx"]) / max(line["length"], 1e-9)
            if ratio_h < 0.15:
                suggestions.append(_suggest("Orizzontale", "horizontal", "Linea quasi orizzontale", 100 - ratio_h * 100))
                suggestions.append(_suggest("Quota X", "distance_x", "Quota automaticamente la proiezione X", 82, "dimension"))
            elif ratio_v < 0.15:
                suggestions.append(_suggest("Verticale", "vertical", "Linea quasi verticale", 100 - ratio_v * 100))
                suggestions.append(_suggest("Quota Y", "distance_y", "Quota automaticamente la proiezione Y", 82, "dimension"))
            else:
                suggestions.append(_suggest("Lunghezza", "distance", "Quota automaticamente la lunghezza", 92, "dimension"))
                suggestions.append(_suggest("Quota X", "distance_x", "Quota la proiezione X", 55, "dimension"))
                suggestions.append(_suggest("Quota Y", "distance_y", "Quota la proiezione Y", 54, "dimension"))
            suggestions.append(_suggest("Lunghezza", "distance", "Quota la lunghezza della linea", 78, "dimension"))
        elif circle:
            suggestions.append(_suggest("Diametro", "diameter", "Quota automatica più comune per cerchi/fori", 100, "dimension"))
            suggestions.append(_suggest("Raggio", "radius", "Quota il raggio", 88, "dimension"))
        elif item["kind"] == "vertex":
            suggestions.append(_suggest("Quota X", "distance_x", "Posizione X del punto", 96, "dimension"))
            suggestions.append(_suggest("Quota Y", "distance_y", "Posizione Y del punto", 95, "dimension"))

    elif n == 2:
        a, b = items
        l1, l2 = _line_data(a["geo"]), _line_data(b["geo"])
        c1, c2 = _circle_data(a["geo"]), _circle_data(b["geo"])
        pair = set(kinds)

        if l1 and l2:
            u1 = (l1["dx"] / l1["length"], l1["dy"] / l1["length"])
            u2 = (l2["dx"] / l2["length"], l2["dy"] / l2["length"])
            dot = abs(u1[0] * u2[0] + u1[1] * u2[1])
            cross = abs(u1[0] * u2[1] - u1[1] * u2[0])
            rel = abs(l1["length"] - l2["length"]) / max((l1["length"] + l2["length"]) * 0.5, 1e-9)
            near = min(_distance(p, q) for p in (l1["a"], l1["b"]) for q in (l2["a"], l2["b"]))
            scale = max(l1["length"], l2["length"], 1e-9)

            if cross < math.sin(math.radians(12)):
                suggestions.append(_suggest("Parallelo", "parallel", "Le linee sono quasi parallele", 100 - cross * 100))
            else:
                suggestions.append(_suggest("Parallelo", "parallel", "Rendi parallele le linee", 58))
            if dot < math.sin(math.radians(12)):
                suggestions.append(_suggest("Perpendicolare", "perpendicular", "Le linee sono quasi a 90°", 99 - dot * 100))
            else:
                suggestions.append(_suggest("Perpendicolare", "perpendicular", "Rendi perpendicolari le linee", 57))
            if rel < 0.18:
                suggestions.append(_suggest("Uguale", "equal", "Lunghezze simili", 88 - rel * 100))
            else:
                suggestions.append(_suggest("Uguale", "equal", "Rendi uguali le lunghezze", 48))
            if near / scale < 0.10:
                suggestions.append(_suggest("Coincidente", "coincident", "Estremità molto vicine", 91 - near / scale * 100))
            suggestions.append(_suggest("Angolo", "angle", "Quota automatica tra due linee", 76, "dimension"))

        elif (l1 and c2) or (c1 and l2) or pair == {"circle", "edge"}:
            suggestions.append(_suggest("Tangente", "tangent", "Linea/bordo e cerchio possono essere tangenti", 100))
            suggestions.append(_suggest("Distanza", "distance", "Quota tra gli elementi", 55, "dimension"))

        elif c1 and c2:
            rel = abs(c1["radius"] - c2["radius"]) / max((c1["radius"] + c2["radius"]) * 0.5, 1e-9)
            suggestions.append(_suggest("Concentrico", "coincident", "Allinea i centri: utile anche sui fori proiettati", 100))
            suggestions.append(_suggest("Uguale", "equal", "Raggi uguali", 88 if rel < 0.18 else 58))
            suggestions.append(_suggest("Tangente", "tangent", "Rendi tangenti cerchi/archi", 72))

        elif "vertex" in pair and ("line" in pair or "edge" in pair or "curve" in pair):
            suggestions.append(_suggest("Punto su oggetto", "point_on", "Vincola il punto alla geometria", 100))
            suggestions.append(_suggest("Distanza", "distance", "Quota punto-geometria", 70, "dimension"))

        elif kinds.count("vertex") == 2:
            suggestions.append(_suggest("Coincidente", "coincident", "Unisci i due punti", 100))
            suggestions.append(_suggest("Distanza", "distance", "Quota distanza tra i punti", 92, "dimension"))
            suggestions.append(_suggest("Quota X", "distance_x", "Distanza orizzontale", 83, "dimension"))
            suggestions.append(_suggest("Quota Y", "distance_y", "Distanza verticale", 82, "dimension"))

        else:
            suggestions.append(_suggest("Coincidente", "coincident", "Vincolo di coincidenza/concentricità", 60))
            suggestions.append(_suggest("Tangente", "tangent", "Prova tangenza tra gli elementi", 55))
            suggestions.append(_suggest("Distanza", "distance", "Quota gli elementi", 50, "dimension"))

    else:
        suggestions.append(_suggest("Uguale", "equal", "Uguaglianza per geometrie compatibili", 70))
        suggestions.append(_suggest("Coincidente", "coincident", "Coincidenza per punti compatibili", 55))
        suggestions.append(_suggest("Simmetria", "symmetric", "Tre elementi possono definire una simmetria", 50))

    dedup = {}
    for suggestion in suggestions:
        if not suggestion:
            continue
        command = suggestion["command"]
        previous = dedup.get(command)
        if previous is None or suggestion["score"] > previous["score"]:
            dedup[command] = suggestion
    ordered = sorted(dedup.values(), key=lambda x: (-x["score"], x["label"]))
    return ordered[: max(1, int(limit))]


def best_dimension_suggestion():
    for suggestion in constraint_suggestions(limit=8):
        if suggestion.get("kind") == "dimension":
            return suggestion
    return None


def apply_best_constraint():
    suggestions = constraint_suggestions(limit=1)
    if not suggestions:
        return False
    try:
        Gui.runCommand(suggestions[0]["command"], 0)
        return True
    except Exception as exc:
        App.Console.PrintWarning("SolidFlow Smart constraint: %s\n" % exc)
        return False


class SmartSketchSettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or Gui.getMainWindow())
        self.setWindowTitle("SolidFlow - Smart Sketch")
        self.resize(450, 250)
        root = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "SolidFlow usa il motore AutoConstraint nativo di FreeCAD. I suggerimenti "
            "di vincolo e quota non vengono mai applicati automaticamente: scegli tu.",
            self,
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.snap = QtWidgets.QCheckBox("Smart Snap / AutoConstraint reattivo", self)
        self.snap.setChecked(smart_snap_enabled())
        root.addWidget(self.snap)

        self.hints = QtWidgets.QCheckBox("Mostra mini-finestra vincoli/quote alla selezione", self)
        self.hints.setChecked(constraint_hints_enabled())
        root.addWidget(self.hints)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Ritardo snap", self))
        self.delay = QtWidgets.QSpinBox(self)
        self.delay.setRange(0, 1000)
        self.delay.setSingleStep(25)
        self.delay.setSuffix(" ms")
        self.delay.setValue(smart_snap_delay())
        row.addWidget(self.delay)
        row.addStretch(1)
        root.addLayout(row)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            parent=self,
        )
        root.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    def accept(self):
        set_smart_snap_enabled(self.snap.isChecked())
        set_constraint_hints_enabled(self.hints.isChecked())
        set_smart_snap_delay(self.delay.value())
        super().accept()


def show_smart_sketch_settings():
    dialog = SmartSketchSettingsDialog()
    runner = getattr(dialog, "exec", None)
    if callable(runner):
        runner()
    else:
        dialog.exec_()
