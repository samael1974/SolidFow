# -*- coding: utf-8 -*-
"""SolidFlow UX v0.4 beta.7 — Mesh / Appearance integration.

This is a small integration layer. Functional code lives in:
- solidflow_mesh.py
- solidflow_appearance.py

It also upgrades the main S palette so the improved SolidFlow commands are
preferred over the older native shortcuts for Fillet/Revolution/Sweep.
"""

from __future__ import annotations

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import solidflow_mesh
import solidflow_appearance

VERSION = "0.4.0-beta.7"


def _run_beta5(name):
    try:
        import solidflow_beta5
        fn = getattr(solidflow_beta5, name)
        fn()
    except Exception as exc:
        App.Console.PrintError("SolidFlow beta.7 -> beta.5 %s: %s\n" % (name, exc))


def _run_beta6(name, *args):
    try:
        import solidflow_beta6
        fn = getattr(solidflow_beta6, name)
        fn(*args)
    except Exception as exc:
        App.Console.PrintError("SolidFlow beta.7 -> beta.6 %s: %s\n" % (name, exc))


def launch_fillet_doctor():
    _run_beta5("launch_fillet_doctor")


def launch_revolution_plus():
    _run_beta5("launch_revolution_plus")


def launch_sweep():
    _run_beta6("_launch_sweep", False)


def launch_loft():
    _run_beta6("_launch_loft", False)


def launch_helix():
    _run_beta6("_launch_helix", False)


def launch_thread():
    _run_beta6("_launch_thread")


def launch_mesh_doctor():
    solidflow_mesh.launch_mesh_doctor()


def launch_appearance():
    solidflow_appearance.launch_appearance_studio()


def _patch_s_palette():
    """Keep beta.3 palette UI, but route key workflows to newer implementations."""
    try:
        import solidflow_ui as ui
    except Exception as exc:
        App.Console.PrintWarning("SolidFlow beta.7: palette base non disponibile: %s\n" % exc)
        return
    if getattr(ui, "_solidflow_beta7_patched", False):
        return

    ActionSpec = ui.ActionSpec
    original_part = ui._partdesign_groups
    original_general = ui._general_groups

    def modernize(spec):
        label = str(getattr(spec, "label", ""))
        if label == "Fillet":
            return ActionSpec(
                "Fillet Doctor",
                callback=launch_fillet_doctor,
                tooltip="Raccordo con analisi preventiva e perimetro faccia",
                icon_command="PartDesign_Fillet",
                emphasis=True,
            )
        if label in ("Rivoluzione", "Rivolvi"):
            return ActionSpec(
                "Rivoluzione+",
                callback=launch_revolution_plus,
                tooltip="Rivoluzione con asse Sketch, linea di costruzione o spigolo selezionato",
                icon_command="PartDesign_Revolution",
            )
        if label == "Sweep":
            return ActionSpec(
                "Sweep",
                callback=launch_sweep,
                tooltip="Sweep SolidFlow con preview: primo Sketch profilo, secondo percorso",
                emphasis=True,
            )
        return spec

    def contains_label(specs, label):
        return any(str(getattr(s, "label", "")) == label for s in specs)

    def part_groups():
        # Meshes deserve their own context even when the active workbench happens
        # to be Part Design.
        if solidflow_mesh.selected_mesh_object() is not None:
            stable = [
                ActionSpec("Mesh Doctor", callback=launch_mesh_doctor, tooltip="Analizza, ripara e semplifica la mesh", emphasis=True),
                ActionSpec("Aspetto", callback=launch_appearance, tooltip="Materiale, colore e texture scalabile"),
                ActionSpec("Adatta", callback=ui._view_fit),
                ActionSpec("Isometrica", callback=ui._view_axo),
            ]
            smart = [
                ActionSpec("Ripara", callback=launch_mesh_doctor, emphasis=True),
                ActionSpec("Materiale / Texture", callback=launch_appearance),
            ]
            return "MESH", stable, "PER LA SELEZIONE", smart

        title_a, stable, title_b, smart = original_part()
        smart = [modernize(spec) for spec in smart]
        kind = ui._selection_kind()

        if kind in ("Sketch", "SketchEdges"):
            if not contains_label(smart, "Elica"):
                smart.append(ActionSpec("Elica", callback=launch_helix, tooltip="Elica parametrica additiva", icon_command="PartDesign_AdditiveHelix"))
        elif kind == "TwoSketches":
            if not contains_label(smart, "Sweep"):
                smart.insert(0, ActionSpec("Sweep", callback=launch_sweep, emphasis=True))
            if not contains_label(smart, "Loft"):
                smart.append(ActionSpec("Loft", callback=launch_loft, tooltip="Loft tra gli Sketch selezionati", icon_command="PartDesign_AdditiveLoft"))
        elif kind == "Face":
            smart.append(ActionSpec("Thread Wizard", callback=launch_thread, tooltip="Filettatura fisica su faccia cilindrica"))
            smart.append(ActionSpec("Aspetto", callback=launch_appearance, tooltip="Materiale e texture"))
        elif kind in ("Feature", "Object"):
            smart.append(ActionSpec("Aspetto", callback=launch_appearance, tooltip="Materiale e texture"))

        return title_a, stable, title_b, smart

    def general_groups():
        if solidflow_mesh.selected_mesh_object() is not None:
            stable = [
                ActionSpec("Mesh Doctor", callback=launch_mesh_doctor, emphasis=True),
                ActionSpec("Aspetto", callback=launch_appearance),
                ActionSpec("Adatta", callback=ui._view_fit),
                ActionSpec("Isometrica", callback=ui._view_axo),
            ]
            return "MESH", stable, "AZIONI", [ActionSpec("Materiale / Texture", callback=launch_appearance)]
        title_a, stable, title_b, smart = original_general()
        try:
            if Gui.Selection.getSelection():
                smart = list(smart) + [ActionSpec("Aspetto", callback=launch_appearance)]
        except Exception:
            pass
        return title_a, stable, title_b, smart

    ui._solidflow_beta7_original_partdesign_groups = original_part
    ui._solidflow_beta7_original_general_groups = original_general
    ui._partdesign_groups = part_groups
    ui._general_groups = general_groups
    ui._solidflow_beta7_patched = True
    App.Console.PrintMessage("SolidFlow beta.7: palette S collegata ai workflow moderni.\n")


class _Command:
    def __init__(self, text, tip, callback):
        self.text = text
        self.tip = tip
        self.callback = callback

    def GetResources(self):
        return {"MenuText": self.text, "ToolTip": self.tip}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        self.callback()


_COMMANDS = {
    "SolidFlow_MeshDoctor": _Command("Mesh Doctor…", "Analizza, ripara, semplifica e converte mesh", launch_mesh_doctor),
    "SolidFlow_AppearanceStudio": _Command("Appearance Studio…", "Materiali, colore e texture riscalabili", launch_appearance),
}


class IntegrationController:
    def __init__(self):
        self._installed = False
        self.timer = QtCore.QTimer()
        self.timer.setInterval(1800)
        self.timer.timeout.connect(solidflow_appearance.sync_textures)

    def _register(self):
        for command_id, command in _COMMANDS.items():
            try:
                Gui.addCommand(command_id, command)
            except Exception as exc:
                App.Console.PrintWarning("SolidFlow beta.7 comando %s: %s\n" % (command_id, exc))

    def _find_menu(self):
        for menu in Gui.getMainWindow().findChildren(QtWidgets.QMenu):
            try:
                if menu.title().replace("&", "").strip().lower() == "solidflow":
                    return menu
            except Exception:
                pass
        return None

    def _augment_menu(self):
        menu = self._find_menu()
        if menu is None or menu.findChild(QtCore.QObject, "SolidFlowBeta7Marker"):
            return
        marker = QtCore.QObject(menu)
        marker.setObjectName("SolidFlowBeta7Marker")
        menu.addSeparator()
        studio = menu.addMenu("Mesh & Aspetto")
        studio.addAction("Mesh Doctor…", lambda: Gui.runCommand("SolidFlow_MeshDoctor", 0))
        studio.addAction("Appearance Studio…", lambda: Gui.runCommand("SolidFlow_AppearanceStudio", 0))

    def install(self):
        if self._installed:
            return
        self._register()
        _patch_s_palette()
        self._installed = True
        self.timer.start()
        QtCore.QTimer.singleShot(900, self._augment_menu)
        QtCore.QTimer.singleShot(1700, self._augment_menu)
        QtCore.QTimer.singleShot(1200, solidflow_appearance.sync_textures)
        App.Console.PrintMessage(
            "SolidFlow UX %s: Mesh Doctor / Appearance Studio caricati.\n" % VERSION
        )


_controller = None


def install():
    global _controller
    app = QtWidgets.QApplication.instance()
    old = getattr(app, "_solidflow_beta7_controller", None) if app else None
    if old is not None and old is not _controller:
        try:
            old.timer.stop()
        except Exception:
            pass
    if _controller is None or not getattr(_controller, "_installed", False):
        _controller = IntegrationController()
        _controller.install()
    if app:
        app._solidflow_beta7_controller = _controller
    return _controller
