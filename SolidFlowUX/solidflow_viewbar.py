# -*- coding: utf-8 -*-
"""SolidFlow always-available vertical display/style bar below the navigation cube.

Includes an adaptive modelling grid available both in normal 3D view and while
editing a Sketch. In Sketch edit mode the grid follows FreeCAD's actual editing
placement; outside Sketcher it returns to the global XY base plane. The grid is
a visual reference only: it does not create or modify geometry and it does not
add constraints.
"""

import math

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

try:
    from pivy import coin
except Exception:
    coin = None

PREF_PATH = "User parameter:BaseApp/Preferences/Mod/SolidFlowUX"


def _prefs():
    return App.ParamGet(PREF_PATH)


def display_bar_enabled():
    return _prefs().GetBool("DisplayStyleBar", True)


def set_display_bar_enabled(enabled):
    _prefs().SetBool("DisplayStyleBar", bool(enabled))


def grid_enabled():
    return _prefs().GetBool("AdaptiveGrid", False)


def set_grid_enabled(enabled):
    _prefs().SetBool("AdaptiveGrid", bool(enabled))


def _home_view():
    """SolidFlow Home: Shift keeps orientation; normal click returns to axonometric and fits all."""
    try:
        view = Gui.ActiveDocument.ActiveView if Gui.ActiveDocument else None
    except Exception:
        view = None
    if view is None:
        return
    try:
        modifiers = QtWidgets.QApplication.keyboardModifiers()
    except Exception:
        modifiers = QtCore.Qt.NoModifier
    try:
        if not (modifiers & QtCore.Qt.ShiftModifier):
            view.viewAxonometric()
        view.fitAll()
    except Exception:
        try:
            Gui.runCommand("Std_ViewAxonometric")
            Gui.runCommand("Std_ViewFitAll")
        except Exception:
            pass


def _run_draw_style(index, fallback_name=None):
    try:
        Gui.runCommand("Std_DrawStyle", int(index))
        return True
    except Exception:
        pass
    if fallback_name:
        try:
            if Gui.ActiveDocument:
                Gui.ActiveDocument.ActiveView.setDrawStyle(fallback_name)
                return True
        except Exception:
            pass
    return False


def _studio_controller():
    try:
        import solidflow_beta5
        return solidflow_beta5._studio
    except Exception:
        return None


def _toggle_shadows():
    studio = _studio_controller()
    if studio is not None:
        try:
            studio.toggle()
            return
        except Exception as exc:
            App.Console.PrintWarning("SolidFlow Ombre/Piano: %s\n" % exc)
    try:
        QtWidgets.QMessageBox.information(
            Gui.getMainWindow(), "SolidFlow - Ombre/Piano",
            "Il modulo Studio non è disponibile. Controlla SolidFlow > Diagnostica SolidFlow.",
        )
    except Exception:
        pass


def _set_studio_plane(plane):
    studio = _studio_controller()
    if studio is None:
        return
    try:
        studio.set_plane(str(plane))
        if not getattr(studio, "enabled", False):
            studio.set_enabled(True)
    except Exception as exc:
        App.Console.PrintWarning("SolidFlow piano %s: %s\n" % (plane, exc))


def _material_editor():
    try:
        import solidflow_appearance
        solidflow_appearance.launch_appearance_studio()
        return
    except Exception as exc:
        App.Console.PrintWarning("SolidFlow Materiale: %s\n" % exc)
    try:
        cmd = Gui.Command.get("Std_SetMaterial")
        if cmd:
            Gui.runCommand("Std_SetMaterial")
            return
    except Exception:
        pass
    QtWidgets.QMessageBox.information(
        Gui.getMainWindow(),
        "SolidFlow - Materiale",
        "Seleziona prima un oggetto solido, poi riprova.",
    )


def _studio_view():
    """Fast modelling/material preview, deliberately not a ray-traced renderer."""
    _run_draw_style(5, "Shaded")
    try:
        Gui.runCommand("Std_PerspectiveCamera")
    except Exception:
        pass
    try:
        if Gui.ActiveDocument:
            Gui.ActiveDocument.ActiveView.fitAll()
    except Exception:
        pass


def _active_view_host(main_window):
    try:
        mdi = main_window.findChild(QtWidgets.QMdiArea)
        if mdi:
            sub = mdi.activeSubWindow()
            if sub:
                return sub.widget() or sub
    except Exception:
        pass
    try:
        w = main_window.activeWindow()
        if isinstance(w, QtWidgets.QWidget):
            return w
    except Exception:
        pass
    return None


def _active_view():
    try:
        if Gui.ActiveDocument:
            return Gui.ActiveDocument.ActiveView
    except Exception:
        pass
    return None


def _nice_grid_step(raw_step):
    """Return a 1-2-5 adaptive spacing in millimetres."""
    try:
        raw_step = float(raw_step)
    except Exception:
        raw_step = 10.0
    raw_step = max(0.05, min(raw_step, 100000.0))
    power = 10.0 ** math.floor(math.log10(raw_step))
    fraction = raw_step / power
    if fraction <= 1.0:
        nice = 1.0
    elif fraction <= 2.0:
        nice = 2.0
    elif fraction <= 5.0:
        nice = 5.0
    else:
        nice = 10.0
    return max(0.1, min(nice * power, 10000.0))


def _view_size(view):
    try:
        size = view.getSize()
        if isinstance(size, (tuple, list)) and len(size) >= 2:
            return max(1, int(size[0])), max(1, int(size[1]))
        if hasattr(size, "width") and hasattr(size, "height"):
            w = size.width() if callable(size.width) else size.width
            h = size.height() if callable(size.height) else size.height
            return max(1, int(w)), max(1, int(h))
    except Exception:
        pass
    return 1000, 700


def _camera_visible_height(view):
    """Approximate vertical size of the current camera view, in model units (mm)."""
    try:
        camera = view.getCameraNode()
        if camera is None:
            return None
        # Orthographic camera: Coin exposes the world-space height directly.
        if hasattr(camera, "height"):
            h = float(camera.height.getValue())
            if h > 0.0:
                return h
        # Perspective camera: visible height at focal plane.
        if hasattr(camera, "heightAngle") and hasattr(camera, "focalDistance"):
            angle = float(camera.heightAngle.getValue())
            focal = float(camera.focalDistance.getValue())
            h = 2.0 * focal * math.tan(angle * 0.5)
            if h > 0.0:
                return h
    except Exception:
        pass
    return None


def _edited_sketch_context():
    """Return ``(SketchObject, ViewProvider)`` for the sketch currently in edit.

    ``Gui.ActiveDocument.getInEdit()`` returns a ViewProvider, not the
    underlying document object. Older SolidFlow code treated that return value
    as if it were the SketchObject itself, so the adaptive grid fell back to
    global XY while editing sketches on XZ/YZ or on mapped faces.
    """
    try:
        gdoc = Gui.ActiveDocument
        if gdoc is None:
            return None, None
    except Exception:
        return None, None

    result = None
    for getter_name in ("getInEdit", "getInEditInfo"):
        try:
            getter = getattr(gdoc, getter_name, None)
            if getter:
                candidate = getter()
                if candidate:
                    result = candidate
                    break
        except Exception:
            continue

    if isinstance(result, (tuple, list)) and result:
        result = result[0]

    view_provider = None
    obj = None

    # FreeCAD's normal Python API returns the ViewProvider here.
    if result is not None:
        try:
            obj = getattr(result, "Object", None)
            if obj is not None:
                view_provider = result
        except Exception:
            obj = None

        # Compatibility fallback for wrappers exposing getObject().
        if obj is None:
            try:
                getter = getattr(result, "getObject", None)
                if getter:
                    obj = getter()
                    view_provider = result
            except Exception:
                obj = None

    # Some builds/helpers can return the object name instead.
    if obj is None and isinstance(result, str):
        try:
            obj = App.ActiveDocument.getObject(result)
        except Exception:
            obj = None

    # Or, exceptionally, the App object itself.
    if obj is None:
        try:
            if result is not None and hasattr(result, "TypeId"):
                obj = result
        except Exception:
            obj = None

    try:
        if obj is not None and "Sketcher::SketchObject" in str(obj.TypeId):
            return obj, view_provider
    except Exception:
        pass

    return None, None


def _edited_sketch():
    """Return the Sketch currently being edited, if any."""
    sketch, _view_provider = _edited_sketch_context()
    return sketch


def _grid_placement():
    """Return the plane used by the adaptive grid.

    While a sketch is being edited we deliberately use FreeCAD's editing
    placement. This is the same transform Sketcher uses for its edit geometry,
    so it also works for sketches on XZ/YZ, datum planes, mapped faces and
    objects edited through Body/Part hierarchies.

    Outside Sketch edit mode the modelling grid returns to global XY.
    """
    sketch, view_provider = _edited_sketch_context()
    if sketch is None:
        return App.Placement()

    # Best source: Sketcher's own edit placement.
    if view_provider is not None:
        try:
            getter = getattr(view_provider, "getEditingPlacement", None)
            if getter:
                placement = getter()
                if placement is not None:
                    return placement
        except Exception:
            pass

    # Equivalent fallback for builds where getEditingPlacement() is not exposed.
    try:
        gdoc = Gui.ActiveDocument
        getter = getattr(gdoc, "getEditingTransform", None) if gdoc else None
        if getter:
            matrix = getter()
            if matrix is not None:
                return App.Placement(matrix)
    except Exception:
        pass

    # Last-resort fallbacks for older/simple documents.
    try:
        getter = getattr(sketch, "getGlobalPlacement", None)
        if getter:
            placement = getter()
            if placement is not None:
                return placement
    except Exception:
        pass
    try:
        return sketch.Placement
    except Exception:
        return App.Placement()


def _camera_forward(view):
    """World-space camera forward vector."""
    try:
        rot = view.getCameraOrientation()
        if hasattr(rot, "multVec"):
            v = rot.multVec(App.Vector(0, 0, -1))
            if v.Length > 1e-9:
                return v.normalize()
    except Exception:
        pass
    return App.Vector(0, 0, -1)


def _camera_position(view):
    try:
        camera = view.getCameraNode()
        p = camera.position.getValue()
        try:
            return App.Vector(float(p[0]), float(p[1]), float(p[2]))
        except Exception:
            return App.Vector(float(p.getValue()[0]), float(p.getValue()[1]), float(p.getValue()[2]))
    except Exception:
        return App.Vector(0, 0, 1000)


def _grid_center_on_plane(view, placement):
    """Intersect the camera centre ray with the grid plane, then return local XY."""
    origin = placement.multVec(App.Vector(0, 0, 0))
    normal = placement.Rotation.multVec(App.Vector(0, 0, 1))
    cam = _camera_position(view)
    forward = _camera_forward(view)
    denom = forward.dot(normal)
    point = origin
    if abs(denom) > 1e-6:
        try:
            t = (origin - cam).dot(normal) / denom
            point = cam + forward * t
        except Exception:
            point = origin
    try:
        local = placement.inverse().multVec(point)
        return local.x, local.y
    except Exception:
        return 0.0, 0.0


class AdaptiveGridController(QtCore.QObject):
    """Coin3D adaptive reference grid for both 3D modelling and Sketch edit mode."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = grid_enabled()
        self._scene = None
        self._root = None
        self._last_signature = None
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self.sync)
        self._timer.start()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        set_grid_enabled(self.enabled)
        if not self.enabled:
            self._detach()
        else:
            self.sync(force=True)

    def toggle(self):
        self.set_enabled(not self.enabled)

    def _detach(self):
        if self._scene is not None and self._root is not None:
            try:
                self._scene.removeChild(self._root)
            except Exception:
                pass
        self._scene = None
        self._root = None
        self._last_signature = None

    def sync(self, force=False):
        if not self.enabled or coin is None:
            if not self.enabled:
                self._detach()
            return
        view = _active_view()
        if view is None:
            self._detach()
            return
        try:
            scene = view.getSceneGraph()
        except Exception:
            scene = None
        if scene is None:
            self._detach()
            return

        if scene is not self._scene:
            self._detach()
            self._scene = scene
            force = True

        width, height = _view_size(view)
        visible_h = _camera_visible_height(view)
        if not visible_h or visible_h <= 0.0:
            visible_h = 200.0
        visible_w = visible_h * (float(width) / float(max(1, height)))

        # Aim for roughly 34 px between minor lines.  Spacing follows 1-2-5 x 10^n mm.
        world_per_px = visible_h / float(max(1, height))
        step = _nice_grid_step(world_per_px * 34.0)
        placement = _grid_placement()
        center_x, center_y = _grid_center_on_plane(view, placement)

        # Rebuild only after a meaningful zoom/pan/plane change.
        snap_x = round(center_x / step) * step
        snap_y = round(center_y / step) * step
        try:
            q = placement.Rotation.Q
            p = placement.Base
            placement_sig = tuple(round(float(v), 5) for v in (p.x, p.y, p.z, q[0], q[1], q[2], q[3]))
        except Exception:
            placement_sig = (0.0,) * 7
        signature = (
            round(step, 8),
            round(snap_x, 6),
            round(snap_y, 6),
            round(visible_w, 2),
            round(visible_h, 2),
            placement_sig,
        )
        if not force and signature == self._last_signature:
            return
        self._last_signature = signature
        self._build_grid(scene, placement, snap_x, snap_y, step, visible_w, visible_h)

    def _build_grid(self, scene, placement, center_x, center_y, step, visible_w, visible_h):
        if coin is None:
            return
        if self._root is not None:
            try:
                scene.removeChild(self._root)
            except Exception:
                pass

        root = coin.SoSeparator()
        root.setName("SolidFlowAdaptiveGrid")

        # A small local Z offset reduces z-fighting with coplanar sketch/model geometry.
        z_offset = max(0.001, step * 0.002)
        half_x = max(12.0 * step, visible_w * 0.72)
        half_y = max(12.0 * step, visible_h * 0.72)
        nx = min(90, max(12, int(math.ceil(half_x / step))))
        ny = min(90, max(12, int(math.ceil(half_y / step))))

        minor_vertices = []
        major_vertices = []

        def world_point(x, y):
            return placement.multVec(App.Vector(x, y, z_offset))

        # Vertical lines.
        ix0 = int(round(center_x / step))
        iy0 = int(round(center_y / step))
        x_min = (ix0 - nx) * step
        x_max = (ix0 + nx) * step
        y_min = (iy0 - ny) * step
        y_max = (iy0 + ny) * step

        for ix in range(ix0 - nx, ix0 + nx + 1):
            x = ix * step
            target = major_vertices if (ix % 5 == 0) else minor_vertices
            target.extend((world_point(x, y_min), world_point(x, y_max)))

        for iy in range(iy0 - ny, iy0 + ny + 1):
            y = iy * step
            target = major_vertices if (iy % 5 == 0) else minor_vertices
            target.extend((world_point(x_min, y), world_point(x_max, y)))

        self._add_line_group(root, minor_vertices, (0.58, 0.60, 0.63), 1.0)
        self._add_line_group(root, major_vertices, (0.36, 0.39, 0.43), 1.5)

        try:
            scene.addChild(root)
            self._root = root
        except Exception as exc:
            self._root = None
            App.Console.PrintWarning("SolidFlow Griglia: %s\n" % exc)

    @staticmethod
    def _add_line_group(parent, vertices, rgb, width):
        if not vertices:
            return
        sep = coin.SoSeparator()
        style = coin.SoDrawStyle()
        style.lineWidth = float(width)
        color = coin.SoBaseColor()
        color.rgb = rgb
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, len(vertices), [(v.x, v.y, v.z) for v in vertices])
        lines = coin.SoLineSet()
        lines.numVertices.setValues(0, len(vertices) // 2, [2] * (len(vertices) // 2))
        sep.addChild(style)
        sep.addChild(color)
        sep.addChild(coords)
        sep.addChild(lines)
        parent.addChild(sep)


_GRID_CONTROLLER = None


def _grid_controller():
    global _GRID_CONTROLLER
    if _GRID_CONTROLLER is None:
        _GRID_CONTROLLER = AdaptiveGridController(Gui.getMainWindow())
    return _GRID_CONTROLLER


class DisplayStyleBar(QtWidgets.QFrame):
    """Vertical modelling/view palette kept below FreeCAD's navigation cube."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self._host = None
        self.setObjectName("SolidFlowDisplayStyleBar")
        self.setStyleSheet("""
            QFrame#SolidFlowDisplayStyleBar {
                border: 1px solid palette(mid);
                border-radius: 7px;
                background: palette(window);
            }
            QToolButton {
                min-width: 92px;
                min-height: 28px;
                padding: 3px 7px;
                border-radius: 4px;
                font-size: 11px;
                text-align: left;
            }
            QToolButton:hover {
                background: palette(highlight);
                color: palette(highlighted-text);
            }
            QToolButton:checked {
                background: palette(highlight);
                color: palette(highlighted-text);
            }
        """)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        specs = [
            ("🏠  Home", "Isometrica + Inquadra tutto. Shift+clic: solo Inquadra tutto", _home_view),
            ("Wire", "Wireframe", lambda: _run_draw_style(2, "Wireframe")),
            ("Solido", "Solido ombreggiato", lambda: _run_draw_style(5, "Shaded")),
            ("Bordi", "Solido con bordi / Flat Lines", lambda: _run_draw_style(6, "Flat Lines")),
            ("Nascoste", "Linee nascoste", lambda: _run_draw_style(3, "Hidden Line")),
        ]
        for text, tip, callback in specs:
            b = QtWidgets.QToolButton(self)
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _checked=False, cb=callback: cb())
            layout.addWidget(b)

        # Adaptive millimetre grid: works in ordinary 3D view and follows the active Sketch plane.
        self.grid_button = QtWidgets.QToolButton(self)
        self.grid_button.setText("Griglia")
        self.grid_button.setToolTip(
            "Attiva/disattiva la griglia millimetrata adattiva. "
            "Il passo cambia automaticamente con lo zoom; in Schizzo segue il piano dello sketch."
        )
        self.grid_button.setCheckable(True)
        self.grid_button.setChecked(grid_enabled())
        self.grid_button.toggled.connect(self._set_grid)
        layout.addWidget(self.grid_button)

        shadow = QtWidgets.QToolButton(self)
        shadow.setText("Ombre/Piano")
        shadow.setToolTip("Attiva piano di appoggio e ombra; usa la freccia per scegliere XY/XZ/YZ")
        shadow.clicked.connect(_toggle_shadows)
        menu = QtWidgets.QMenu(shadow)
        menu.addAction("Piano XY", lambda: _set_studio_plane("XY"))
        menu.addAction("Piano XZ", lambda: _set_studio_plane("XZ"))
        menu.addAction("Piano YZ", lambda: _set_studio_plane("YZ"))
        shadow.setMenu(menu)
        try:
            shadow.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
        except Exception:
            pass
        layout.addWidget(shadow)

        studio = QtWidgets.QToolButton(self)
        studio.setText("Studio")
        studio.setToolTip("Vista rapida materiali/modello: shaded + prospettiva. Non è un ray tracer.")
        studio.clicked.connect(_studio_view)
        layout.addWidget(studio)

        material = QtWidgets.QToolButton(self)
        material.setText("Materiale")
        material.setToolTip("Appearance Studio: colore, materiale e texture")
        material.clicked.connect(_material_editor)
        layout.addWidget(material)

        # Ensure the controller exists after the UI is ready.  This also restores the saved ON state.
        try:
            _grid_controller().set_enabled(grid_enabled())
        except Exception as exc:
            App.Console.PrintWarning("SolidFlow Griglia init: %s\n" % exc)

        self.adjustSize()
        self.hide()

    def _set_grid(self, enabled):
        try:
            _grid_controller().set_enabled(bool(enabled))
        except Exception as exc:
            App.Console.PrintWarning("SolidFlow Griglia toggle: %s\n" % exc)
            try:
                self.grid_button.blockSignals(True)
                self.grid_button.setChecked(False)
                self.grid_button.blockSignals(False)
            except Exception:
                pass

    def set_enabled(self, enabled):
        set_display_bar_enabled(enabled)
        if not enabled:
            self.hide()
        else:
            self.sync_position()

    def sync_position(self):
        if not display_bar_enabled() or not Gui.ActiveDocument:
            self.hide()
            return
        host = _active_view_host(self.main_window)
        if host is None:
            self.hide()
            return
        if host is not self._host:
            self._host = host
            self.setParent(host)
            self.setWindowFlags(QtCore.Qt.Widget)
            self.show()
        try:
            # Keep the button state aligned with preferences/controller if the bar gets recreated.
            desired = grid_enabled()
            if self.grid_button.isChecked() != desired:
                self.grid_button.blockSignals(True)
                self.grid_button.setChecked(desired)
                self.grid_button.blockSignals(False)
        except Exception:
            pass
        self.adjustSize()
        x = max(4, host.width() - self.width() - 18)
        # Below the navigation cube, with enough room for the vertical stack.
        y = min(max(132, 8), max(8, host.height() - self.height() - 8))
        self.move(x, y)
        self.raise_()
        self.show()
