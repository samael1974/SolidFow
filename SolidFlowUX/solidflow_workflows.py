# -*- coding: utf-8 -*-
"""SolidFlow beta11 workflow integration.

One cohesive integration layer for the consolidated beta10 core:
- region/profile picker for Pad/Pocket;
- interactive Fillet Doctor;
- 3D helix paths and sweep-on-path;
- 3D pattern commands for any selected PartDesign feature, including Fillet.
"""
from __future__ import annotations

import FreeCAD as App
import FreeCADGui as Gui

import solidflow_ui as ui
import solidflow_features as features
import solidflow_profiles as profiles
import solidflow_fillet as fillet
import solidflow_paths as paths

VERSION = "0.4.0-beta.11-workflows"
_installed = False
_original_selection_kind = None
_original_current_context = None
_original_partdesign_groups = None


def _selected_partdesign_feature():
    try:
        objects = Gui.Selection.getSelection()
    except Exception:
        return None
    if len(objects) != 1:
        return None
    obj = objects[0]
    if obj is None or getattr(obj, "TypeId", "") in ("PartDesign::Body", "Sketcher::SketchObject"):
        return None
    try:
        if obj.isDerivedFrom("PartDesign::Feature"):
            return obj
    except Exception:
        pass
    return None


def _launch_sweep_best():
    profile, path = paths.selected_profile_path_pair()
    if profile is not None and path is not None:
        paths.launch_sweep_path(False)
        return
    try:
        import solidflow_beta6
        solidflow_beta6._launch_sweep(False)
    except Exception as exc:
        App.Console.PrintError("SolidFlow Sweep: %s\n" % exc)


def _selection_kind():
    profile, path = paths.selected_profile_path_pair()
    if profile is not None and path is not None:
        return "SweepPair"
    kind = _original_selection_kind()
    if kind == "Object" and _selected_partdesign_feature() is not None:
        return "Feature"
    return kind


def _current_context():
    if _selection_kind() == "SweepPair":
        return "PartDesign"
    return _original_current_context()


def _partdesign_groups():
    kind = _selection_kind()
    if kind == "SweepPair":
        stable = [
            ui.ActionSpec("Nuovo Sketch", "PartDesign_NewSketch"),
            ui.ActionSpec("Importa", callback=ui.show_import_assistant),
            ui.ActionSpec("Isometrica", callback=ui._view_axo),
            ui.ActionSpec("Adatta", callback=ui._view_fit),
        ]
        smart = [
            ui.ActionSpec("Sweep su elica", callback=_launch_sweep_best, emphasis=True),
            ui.ActionSpec("Sweep Cut su elica", callback=lambda: paths.launch_sweep_path(True)),
        ]
        return "PRINCIPALI", stable, "PER LA SELEZIONE", smart

    title_a, stable, title_b, smart = _original_partdesign_groups()

    if kind == "Face":
        # HelixPath is especially useful when the reference is cylindrical.  If
        # the face is planar, the command will explain that a cylinder is needed.
        smart = list(smart)
        smart.insert(1, ui.ActionSpec("Elica percorso 3D", callback=paths.launch_helix_path, emphasis=True))

    if kind == "Feature":
        feature = _selected_partdesign_feature()
        # The beta10 Quick Edit dialog only understands Pad/Pocket/Revolution.
        # Do not expose it for Fillet, Chamfer, Hole, patterns, etc.
        if features.selected_editable_feature() is None:
            smart = [spec for spec in list(smart) if spec.label != "Modifica rapida"]
        # Series buttons from the beta10 core remain present; the broader
        # feature classification above now makes them available on Fillet001,
        # Chamfer, Hole and other PartDesign features too.
        if feature is not None and getattr(feature, "TypeId", "") == "PartDesign::Fillet":
            smart = list(smart)
            for spec in smart:
                if spec.label in ("Serie lineare 3D", "Serie polare 3D"):
                    # visual emphasis without changing the native command
                    pass

    return title_a, stable, title_b, smart


def install():
    global _installed, _original_selection_kind, _original_current_context, _original_partdesign_groups
    if _installed:
        return

    fillet.install()
    paths.install()

    _original_selection_kind = ui._selection_kind
    _original_current_context = ui.current_context
    _original_partdesign_groups = ui._partdesign_groups

    # Replace beta10 callbacks with the consolidated workflows.  The palette's
    # group builders resolve these globals at use time, so no UI duplication is
    # introduced.
    ui.launch_pad = profiles.launch_pad
    ui.launch_pocket = profiles.launch_pocket
    ui.launch_revolution = profiles.launch_revolution
    ui.show_closed_profiles = profiles.show_closed_profiles
    ui.launch_sweep = _launch_sweep_best
    ui._fillet_best = fillet.launch_fillet_doctor
    ui._selection_kind = _selection_kind
    ui.current_context = _current_context
    ui._partdesign_groups = _partdesign_groups

    # Keep direct helper imports in other SolidFlow modules aligned as well.
    features.launch_pad = profiles.launch_pad
    features.launch_pocket = profiles.launch_pocket
    features.launch_revolution = profiles.launch_revolution
    features.launch_sweep = _launch_sweep_best
    features.show_closed_profiles = profiles.show_closed_profiles

    _installed = True
    App.Console.PrintMessage("SolidFlow workflows: beta11 profile/fillet/path integration active.\n")
