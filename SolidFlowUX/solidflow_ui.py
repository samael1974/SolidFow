# -*- coding: utf-8 -*-
"""SolidFlow UX consolidated GUI core — beta10.

The plain ``S`` key is the centre of the interface and is owned only here.
Sketch intelligence is provided by ``solidflow_smart``/``solidflow_sketch``.
Patterns are exposed directly by the core so they cannot disappear because of
an additive patch/load-order problem.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from solidflow_features import (
    create_sketch_on_face,
    add_support_face_references_to_active_sketch,
    auto_face_references_enabled,
    set_auto_face_references_enabled,
    launch_pad,
    launch_pocket,
    launch_revolution,
    launch_sweep,
    launch_edit_selected_feature,
    show_closed_profiles,
    selected_editable_feature,
    selected_face,
    selected_profile,
    selected_sketch_objects,
    selection_snapshot,
)
from solidflow_import import show_import_assistant
from solidflow_viewbar import DisplayStyleBar, display_bar_enabled, set_display_bar_enabled
from solidflow_smart import (
    apply_smart_snap_preferences,
    constraint_suggestions,
    all_constraint_actions,
    set_smart_snap_enabled,
    show_smart_sketch_settings,
    smart_snap_enabled,
)

VERSION = "0.4.0-beta.13-test.5-ux"
PREF_PATH = "User parameter:BaseApp/Preferences/Mod/SolidFlowUX"
_controller = None
_menu = None

SKETCH_RECTANGULAR = "Sketcher_RectangularArray"
SKETCH_POLAR = "Sketcher_Rotate"
PD_LINEAR = "PartDesign_LinearPattern"
PD_POLAR = "PartDesign_PolarPattern"


@dataclass(frozen=True)
class ActionSpec:
    label: str
    command: Optional[str] = None
    callback: Optional[Callable] = None
    tooltip: str = ""
    icon_command: Optional[str] = None
    emphasis: bool = False


def _prefs():
    return App.ParamGet(PREF_PATH)


def shortcut_enabled():
    return _prefs().GetBool("EnableSShortcut", True)


def set_shortcut_enabled(value):
    _prefs().SetBool("EnableSShortcut", bool(value))
    if _controller:
        _controller.sync_menu_state()


def toggle_shortcut_enabled():
    set_shortcut_enabled(not shortcut_enabled())


def toggle_shortcut():
    toggle_shortcut_enabled()


def toggle_s_shortcut():
    toggle_shortcut_enabled()


def auto_mini_toolbar_enabled():
    return _prefs().GetBool("AutoMiniToolbar", True)


def set_auto_mini_toolbar_enabled(value):
    _prefs().SetBool("AutoMiniToolbar", bool(value))
    if _controller:
        if not value:
            _controller.selection_timer.stop()
            _controller.mini.hide()
        _controller.sync_menu_state()


def mouse_gestures_enabled():
    return False


def set_mouse_gestures_enabled(_value):
    return None


def _active_view():
    try:
        return Gui.ActiveDocument.ActiveView if Gui.ActiveDocument else None
    except Exception:
        return None


def _view_fit():
    view = _active_view()
    if view:
        view.fitAll()


def _view_axo():
    view = _active_view()
    if view:
        view.viewAxonometric()
        view.fitAll()


def _view_front():
    view = _active_view()
    if view:
        view.viewFront()
        view.fitAll()


def _view_top():
    view = _active_view()
    if view:
        view.viewTop()
        view.fitAll()


def _view_right():
    view = _active_view()
    if view:
        view.viewRight()
        view.fitAll()


def _command_object(name):
    try:
        return Gui.Command.get(name) if name else None
    except Exception:
        return None


def _command_action(name):
    try:
        command = _command_object(name)
        actions = command.getAction() if command else []
        return actions[0] if actions else None
    except Exception:
        return None


def _spec_enabled(spec):
    # Callback wrappers intentionally prepare their own context (e.g. leave
    # sketch edit mode before Pad). Their icon command is not a prerequisite.
    if spec.callback:
        return True
    if not spec.command or _command_object(spec.command) is None:
        return False
    action = _command_action(spec.command)
    return bool(action is not None and action.isEnabled())


def _place_near_cursor(widget, offset_x=18, offset_y=20):
    cursor = QtGui.QCursor.pos()
    screen = QtWidgets.QApplication.screenAt(cursor) or QtWidgets.QApplication.primaryScreen()
    x, y = cursor.x() + offset_x, cursor.y() + offset_y
    if screen:
        area = screen.availableGeometry()
        x = max(area.left() + 8, min(x, area.right() - widget.width() - 8))
        y = max(area.top() + 8, min(y, area.bottom() - widget.height() - 8))
    widget.move(x, y)


def _run_spec(spec):
    try:
        if not _spec_enabled(spec):
            Gui.getMainWindow().statusBar().showMessage(
                "SolidFlow: %s non disponibile per la selezione corrente." % spec.label, 5000)
            return
        if spec.callback:
            spec.callback()
        elif spec.command:
            Gui.runCommand(spec.command, 0)
    except Exception as exc:
        App.Console.PrintError("SolidFlow command %s: %s\n" % (spec.label, exc))


def _is_sketch_edit_mode():
    try:
        edit = Gui.ActiveDocument.getInEdit() if Gui.ActiveDocument else None
        return bool(edit and edit.isDerivedFrom("SketcherGui::ViewProviderSketch"))
    except Exception:
        return False


def _workbench_name():
    try:
        wb = Gui.activeWorkbench()
        return wb.name() if wb else ""
    except Exception:
        return ""


def _selection_kind():
    if selected_face():
        return "Face"
    sketch, subs = selected_profile()
    if sketch and not _is_sketch_edit_mode():
        return "SketchEdges" if subs else "Sketch"
    sketches = selected_sketch_objects()
    if len(sketches) == 2 and len(selection_snapshot()) == 2:
        return "TwoSketches"
    sel = selection_snapshot()
    if not sel:
        return "None"
    names = []
    for item in sel:
        names += list(getattr(item, "SubElementNames", []) or [])
    if any(str(name).startswith("Edge") for name in names):
        return "Edge"
    if selected_editable_feature():
        return "Feature"
    return "Object"


def current_context():
    if _is_sketch_edit_mode():
        return "Sketch"
    kind = _selection_kind()
    if kind in ("Face", "Sketch", "SketchEdges", "TwoSketches", "Edge", "Feature"):
        return "PartDesign"
    return "PartDesign" if "PartDesign" in _workbench_name() else "General"


def _launch_rev_best():
    try:
        import solidflow_beta5
        solidflow_beta5.launch_revolution_plus()
    except Exception:
        launch_revolution()


def _fillet_best():
    try:
        import solidflow_beta5
        solidflow_beta5.launch_fillet_doctor()
    except Exception:
        try:
            Gui.runCommand("PartDesign_Fillet", 0)
        except Exception:
            pass


def _run_beta6(name, *args):
    try:
        import solidflow_beta6
        getattr(solidflow_beta6, name)(*args)
    except Exception as exc:
        App.Console.PrintError("SolidFlow beta6 %s: %s\n" % (name, exc))


def _smart_constraint_specs():
    specs = []
    try:
        for suggestion in constraint_suggestions(limit=5):
            specs.append(
                ActionSpec(
                    suggestion["label"],
                    command=suggestion["command"],
                    tooltip=suggestion["reason"],
                    emphasis=True,
                )
            )
    except Exception as exc:
        App.Console.PrintWarning("SolidFlow Smart suggestions: %s\n" % exc)
    return specs


def _show_more_sketch_tools():
    menu = QtWidgets.QMenu(Gui.getMainWindow())
    constraints = menu.addMenu("Vincoli e quote")
    for label, command in all_constraint_actions():
        action = constraints.addAction(label)
        native = _command_action(command)
        if native:
            try:
                action.setIcon(native.icon())
            except Exception:
                pass
        action.triggered.connect(lambda _checked=False, cmd=command: Gui.runCommand(cmd, 0))

    geometry = menu.addMenu("Riferimenti")
    geometry.addAction("Proietta geometria esterna", lambda: Gui.runCommand("Sketcher_CompExternal", 0))
    geometry.addAction("Riferimenti automatici faccia", add_support_face_references_to_active_sketch)
    menu.exec_(QtGui.QCursor.pos())


def _sketch_groups():
    stable = [
        ActionSpec("Linea", "Sketcher_CreateLine"),
        ActionSpec("Rettangolo", "Sketcher_CreateRectangle"),
        ActionSpec("Cerchio", "Sketcher_CreateCircle"),
        ActionSpec("Polilinea", "Sketcher_CreatePolyline"),
        ActionSpec("Rifila", "Sketcher_Trimming", tooltip="Taglia una porzione di geometria dello schizzo"),
        ActionSpec("Costruzione", "Sketcher_ToggleConstruction", tooltip="Alterna geometria normale e di costruzione"),
        ActionSpec("Quote e vincoli…", callback=_show_more_sketch_tools,
                   tooltip="Accesso stabile a quote, vincoli e riferimenti esterni"),
    ]

    smart = _smart_constraint_specs()
    # Patterns are deliberately first-class commands.  They remain visible in
    # Sketch edit mode even before a selection so the user can find them; the
    # native Sketcher command decides whether the current selection is valid.
    smart += [
        ActionSpec(
            "Serie X/Y",
            command=SKETCH_RECTANGULAR,
            tooltip="Serie rettangolare nel piano dello Sketch",
            icon_command=SKETCH_RECTANGULAR,
            emphasis=True,
        ),
        ActionSpec(
            "Serie polare",
            command=SKETCH_POLAR,
            tooltip="Serie circolare della geometria selezionata",
            icon_command=SKETCH_POLAR,
            emphasis=True,
        ),
        ActionSpec("Estrusione", callback=launch_pad, icon_command="PartDesign_Pad"),
        ActionSpec("Taglio", callback=launch_pocket, icon_command="PartDesign_Pocket"),
        ActionSpec("Rivoluzione+", callback=_launch_rev_best, icon_command="PartDesign_Revolution"),
        ActionSpec("Altri…", callback=_show_more_sketch_tools, tooltip="Altri vincoli, quote e riferimenti"),
        ActionSpec("Chiudi Sketch", "Sketcher_LeaveSketch"),
    ]
    return "DISEGNA", stable, "SUGGERITI / AZIONI", smart


def _partdesign_groups():
    kind = _selection_kind()
    stable = [
        ActionSpec("Nuovo Sketch", "PartDesign_NewSketch"),
        ActionSpec("Importa", callback=show_import_assistant),
        ActionSpec("Isometrica", callback=_view_axo),
        ActionSpec("Adatta", callback=_view_fit),
    ]

    if kind == "Face":
        smart = [
            ActionSpec("Schizzo su faccia", callback=create_sketch_on_face, icon_command="PartDesign_NewSketch", emphasis=True),
            ActionSpec("Fillet Doctor", callback=_fillet_best, icon_command="PartDesign_Fillet"),
            ActionSpec("Smusso", "PartDesign_Chamfer"),
            ActionSpec("Foro", "PartDesign_Hole"),
        ]
    elif kind in ("Sketch", "SketchEdges"):
        smart = [
            ActionSpec("Profili chiusi", callback=show_closed_profiles, emphasis=True),
            ActionSpec("Estrusione", callback=launch_pad, icon_command="PartDesign_Pad"),
            ActionSpec("Taglio", callback=launch_pocket, icon_command="PartDesign_Pocket"),
            ActionSpec("Rivoluzione+", callback=_launch_rev_best, icon_command="PartDesign_Revolution"),
            ActionSpec("Elica", callback=lambda: _run_beta6("_launch_helix", False)),
        ]
    elif kind == "TwoSketches":
        smart = [
            ActionSpec("Sweep", callback=launch_sweep, emphasis=True),
            ActionSpec("Loft", callback=lambda: _run_beta6("_launch_loft", False)),
        ]
    elif kind == "Edge":
        smart = [
            ActionSpec("Fillet Doctor", callback=_fillet_best, icon_command="PartDesign_Fillet"),
            ActionSpec("Smusso", "PartDesign_Chamfer"),
        ]
    elif kind == "Feature":
        smart = [
            ActionSpec("Modifica rapida", callback=launch_edit_selected_feature, emphasis=True),
            ActionSpec("Serie lineare 3D", command=PD_LINEAR, icon_command=PD_LINEAR, tooltip="Serie lungo X/Y/Z o riferimento"),
            ActionSpec("Serie polare 3D", command=PD_POLAR, icon_command=PD_POLAR, tooltip="Serie attorno a un asse/riferimento"),
            ActionSpec("Fillet Doctor", callback=_fillet_best, icon_command="PartDesign_Fillet"),
            ActionSpec("Smusso", "PartDesign_Chamfer"),
        ]
    else:
        smart = [
            ActionSpec("Estrusione", callback=launch_pad, icon_command="PartDesign_Pad"),
            ActionSpec("Taglio", callback=launch_pocket, icon_command="PartDesign_Pocket"),
            ActionSpec("Rivoluzione+", callback=_launch_rev_best, icon_command="PartDesign_Revolution"),
        ]
    return "PRINCIPALI", stable, "PER LA SELEZIONE", smart


def _general_groups():
    return (
        "PRINCIPALI",
        [
            ActionSpec("Importa", callback=show_import_assistant),
            ActionSpec("Adatta", callback=_view_fit),
            ActionSpec("Isometrica", callback=_view_axo),
        ],
        "VISTA",
        [
            ActionSpec("Frontale", callback=_view_front),
            ActionSpec("Alto", callback=_view_top),
            ActionSpec("Destra", callback=_view_right),
        ],
    )


def _groups_for_context(context):
    if context == "Sketch":
        return _sketch_groups()
    if context == "PartDesign":
        return _partdesign_groups()
    return _general_groups()


def _is_text_entry_widget(widget):
    editable = (
        QtWidgets.QLineEdit,
        QtWidgets.QTextEdit,
        QtWidgets.QPlainTextEdit,
        QtWidgets.QAbstractSpinBox,
    )
    current = widget
    depth = 0
    while current is not None and depth < 20:
        try:
            if isinstance(current, editable):
                return True
            if isinstance(current, QtWidgets.QComboBox) and current.isEditable():
                return True
            cls = str(current.metaObject().className()).lower() if hasattr(current, "metaObject") else ""
            if any(token in cls for token in ("lineedit", "textedit", "spinbox")):
                return True
            if "combobox" in cls and hasattr(current, "isEditable") and current.isEditable():
                return True
            current = current.parentWidget()
            depth += 1
        except Exception:
            break
    return False


class ShortcutPalette(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setObjectName("SolidFlowShortcutPalette")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.setStyleSheet(
            "QFrame#SolidFlowShortcutPalette{border:1px solid palette(mid);border-radius:8px;background:palette(window);}"
            "QLabel#SolidFlowTitle{font-weight:700;padding:4px 5px;font-size:13px;}"
            "QLabel#SolidFlowSection{font-weight:600;padding:4px 3px 1px 3px;}"
            "QToolButton{min-width:120px;min-height:56px;padding:5px 8px;border-radius:6px;font-size:13px;text-align:left;}"
            "QToolButton:hover{background:palette(highlight);color:palette(highlighted-text);}"
            "QToolButton[solidflowSuggested='true']{font-weight:700;}"
        )
        self.outer = QtWidgets.QVBoxLayout(self)
        self.outer.setContentsMargins(8, 8, 8, 8)
        self.outer.setSpacing(4)
        self.title = QtWidgets.QLabel()
        self.title.setObjectName("SolidFlowTitle")
        self.outer.addWidget(self.title)
        self.host = QtWidgets.QWidget()
        self.content = QtWidgets.QVBoxLayout(self.host)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setWidget(self.host)
        self.outer.addWidget(self.scroll)

    def _clear(self):
        while self.content.count():
            item = self.content.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _section(self, title, specs):
        available = [spec for spec in specs if not spec.command or _command_object(spec.command) is not None]
        if not available:
            return
        label = QtWidgets.QLabel(title)
        label.setObjectName("SolidFlowSection")
        self.content.addWidget(label)
        host = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        cols = min(4, max(1, len(available)))
        for index, spec in enumerate(available):
            button = QtWidgets.QToolButton()
            button.setText(spec.label)
            button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            button.setIconSize(QtCore.QSize(28, 28))
            button.setProperty("solidflowSuggested", bool(spec.emphasis))
            enabled = _spec_enabled(spec)
            button.setEnabled(enabled)
            button.setToolTip(spec.tooltip or (spec.label if enabled else
                "Non disponibile: verifica documento, selezione e operazione in corso."))
            action = _command_action(spec.command or spec.icon_command)
            if action:
                try:
                    button.setIcon(action.icon())
                except Exception:
                    pass
            button.clicked.connect(lambda _checked=False, current=spec: self._trigger(current))
            grid.addWidget(button, index // cols, index % cols)
        self.content.addWidget(host)

    def _trigger(self, spec):
        self.hide()
        QtCore.QTimer.singleShot(0, lambda: _run_spec(spec))

    def rebuild(self):
        self._clear()
        context = current_context()
        self.title.setText("SolidFlow • " + context)
        title_a, stable, title_b, smart = _groups_for_context(context)
        self._section(title_a, stable)
        self._section(title_b, smart)
        self._section("DOCUMENTO", [
            ActionSpec("Salva", "Std_Save"),
            ActionSpec("Annulla", "Std_Undo"),
            ActionSpec("Ripeti", "Std_Redo"),
            ActionSpec("Esporta…", "Std_Export", tooltip="Esporta la selezione usando i formati disponibili in FreeCAD"),
        ])
        self.content.activate()
        desired = self.host.sizeHint()
        screen = QtWidgets.QApplication.screenAt(QtGui.QCursor.pos()) or QtWidgets.QApplication.primaryScreen()
        width, height = desired.width() + 44, desired.height() + 70
        if screen:
            area = screen.availableGeometry()
            width, height = min(width, area.width() - 16), min(height, area.height() - 16)
        self.resize(max(1, width), max(1, height))

    def show_near_cursor(self):
        self.rebuild()
        _place_near_cursor(self, 38, 30)
        self.show()
        self.raise_()
        self.activateWindow()


class ContextMiniToolbar(QtWidgets.QFrame):
    """3D-context toolbar. Sketch hints are handled by solidflow_sketch."""

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.layout = QtWidgets.QHBoxLayout(self)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.hide_timer = QtCore.QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def _trigger(self, spec):
        self.hide_timer.stop()
        self.hide()
        QtCore.QTimer.singleShot(0, lambda: _run_spec(spec))

    def show_for_selection(self):
        self.hide_timer.stop()
        self.hide()
        if not auto_mini_toolbar_enabled() or _is_sketch_edit_mode():
            return
        app = QtWidgets.QApplication.instance()
        if (not Gui.ActiveDocument or not app or app.activeModalWidget() is not None
                or app.activePopupWidget() is not None or Gui.Control.activeDialog()):
            return
        while self.layout.count():
            widget = self.layout.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        kind = _selection_kind()
        specs = []
        if kind == "Face":
            specs = [ActionSpec("Sketch", callback=create_sketch_on_face), ActionSpec("Fillet", callback=_fillet_best)]
        elif kind == "Edge":
            specs = [ActionSpec("Fillet", callback=_fillet_best), ActionSpec("Smusso", "PartDesign_Chamfer")]
        elif kind in ("Sketch", "SketchEdges"):
            specs = [ActionSpec("Pad", callback=launch_pad), ActionSpec("Pocket", callback=launch_pocket), ActionSpec("Rivolvi", callback=_launch_rev_best)]
        elif kind == "Feature":
            specs = [ActionSpec("Serie 3D", command=PD_LINEAR), ActionSpec("Raccordo", callback=_fillet_best)]
            if selected_editable_feature() is not None:
                specs.insert(0, ActionSpec("Modifica", callback=launch_edit_selected_feature))
        if not specs:
            return
        for spec in specs:
            button = QtWidgets.QToolButton()
            button.setText(spec.label)
            button.setEnabled(_spec_enabled(spec))
            button.setToolTip(spec.tooltip or spec.label)
            button.clicked.connect(lambda _checked=False, current=spec: self._trigger(current))
            self.layout.addWidget(button)
        self.adjustSize()
        _place_near_cursor(self)
        self.show()
        self.raise_()
        self.hide_timer.start(3500)


class _SelectionObserver:
    def __init__(self, controller):
        self.controller = controller

    def addSelection(self, *args):
        self.controller.selection_changed()

    def removeSelection(self, *args):
        self.controller.selection_changed()

    def clearSelection(self, *args):
        self.controller.selection_changed()

    def setSelection(self, *args):
        self.controller.selection_changed()


class SolidFlowController(QtCore.QObject):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.palette = ShortcutPalette(main_window)
        self.mini = ContextMiniToolbar(main_window)
        self.hint = self.mini
        self.viewbar = DisplayStyleBar(main_window)
        self.enable_action = None
        self.snap_action = None
        self.viewbar_action = None
        self.mini_action = None
        self.references_action = None
        self.view_timer = QtCore.QTimer(self)
        self.view_timer.setInterval(450)
        self.view_timer.timeout.connect(self.viewbar.sync_position)
        self.selection_timer = QtCore.QTimer(self)
        self.selection_timer.setSingleShot(True)
        self.selection_timer.timeout.connect(self.mini.show_for_selection)
        self.observer = _SelectionObserver(self)

    def install(self):
        app = QtWidgets.QApplication.instance()
        if app:
            app.installEventFilter(self)
        self._install_menu()
        self.view_timer.start()
        Gui.Selection.addObserver(self.observer)
        if not _prefs().GetBool("Beta10ViewbarMigration", False):
            set_display_bar_enabled(True)
            _prefs().SetBool("Beta10ViewbarMigration", True)
        if smart_snap_enabled():
            try:
                apply_smart_snap_preferences()
            except Exception:
                pass
        QtCore.QTimer.singleShot(350, self.viewbar.sync_position)

    def _install_menu(self):
        global _menu
        bar = self.main_window.menuBar()
        existing = None
        for action in bar.actions():
            if action.text().replace("&", "") == "SolidFlow":
                existing = action.menu()
                break
        _menu = existing or bar.addMenu("SolidFlow")
        _menu.clear()
        action = _menu.addAction("Mostra palette")
        action.setShortcut(QtGui.QKeySequence("Ctrl+Space"))
        action.triggered.connect(show_palette)
        _menu.addAction("Import Assistant", show_import_assistant)
        _menu.addAction("Impostazioni Smart Sketch…", show_smart_sketch_settings)
        _menu.addSeparator()
        self.enable_action = _menu.addAction("Usa tasto S")
        self.enable_action.setCheckable(True)
        self.enable_action.triggered.connect(set_shortcut_enabled)
        self.snap_action = _menu.addAction("Smart Snap")
        self.snap_action.setCheckable(True)
        self.snap_action.triggered.connect(set_smart_snap_enabled)
        self.mini_action = _menu.addAction("Mini-toolbar contestuale 3D")
        self.mini_action.setCheckable(True)
        self.mini_action.triggered.connect(set_auto_mini_toolbar_enabled)
        self.references_action = _menu.addAction("Auto-riferimenti faccia / fori")
        self.references_action.setCheckable(True)
        self.references_action.triggered.connect(set_auto_face_references_enabled)
        self.viewbar_action = _menu.addAction("Barra vista sotto cubo")
        self.viewbar_action.setCheckable(True)
        self.viewbar_action.triggered.connect(self._toggle_viewbar)
        _menu.addSeparator()
        _menu.addAction("Diagnostica SolidFlow…", show_diagnostics)
        self.sync_menu_state()

    def _toggle_viewbar(self, value):
        set_display_bar_enabled(value)
        self.viewbar.set_enabled(value)
        self.sync_menu_state()

    def sync_menu_state(self):
        for action, value in (
            (self.enable_action, shortcut_enabled()),
            (self.snap_action, smart_snap_enabled()),
            (self.mini_action, auto_mini_toolbar_enabled()),
            (self.references_action, auto_face_references_enabled()),
            (self.viewbar_action, display_bar_enabled()),
        ):
            if action:
                old = action.blockSignals(True)
                action.setChecked(bool(value))
                action.blockSignals(old)

    def selection_changed(self):
        self.mini.hide()
        self.selection_timer.start(150)

    def eventFilter(self, watched, event):
        if event.type() != QtCore.QEvent.KeyPress:
            return False
        try:
            if event.isAutoRepeat():
                return False
        except Exception:
            pass
        if event.key() == QtCore.Qt.Key_Escape and self.palette.isVisible():
            self.palette.hide()
            return True
        if event.key() != QtCore.Qt.Key_S or event.modifiers() != QtCore.Qt.NoModifier or not shortcut_enabled():
            return False
        app = QtWidgets.QApplication.instance()
        if not app:
            return False
        if _is_text_entry_widget(watched) or _is_text_entry_widget(app.focusWidget()):
            return False
        modal = app.activeModalWidget()
        popup = app.activePopupWidget()
        if modal is not None and modal is not self.palette:
            return False
        if popup is not None and popup is not self.palette:
            return False
        self.mini.hide()
        if self.palette.isVisible():
            self.palette.hide()
        else:
            self.palette.show_near_cursor()
        return True


def show_palette():
    if not _controller:
        return
    if _controller.palette.isVisible():
        _controller.palette.hide()
    else:
        _controller.palette.show_near_cursor()


def show_diagnostics():
    from solidflow_diagnostics import show_report
    show_report()


def install():
    global _controller
    if _controller is not None:
        return _controller
    main_window = Gui.getMainWindow()
    if main_window is None:
        return None
    _controller = SolidFlowController(main_window)
    _controller.install()
    App.Console.PrintMessage("SolidFlow UX %s loaded. Press S.\n" % VERSION)
    return _controller
