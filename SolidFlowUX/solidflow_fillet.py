# -*- coding: utf-8 -*-
"""SolidFlow interactive Fillet Doctor.

The user can launch the tool from a selected feature/face/edge and then pick
edges directly in the 3D view. Picks are accumulated even with normal clicks,
kept highlighted, validated against OCC, and finally stored in a native
PartDesign::Fillet feature.
"""
from __future__ import annotations

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

VERSION = "0.4.0-beta.11-fillet"


def _main_window():
    return Gui.getMainWindow()


def _message(title, text, icon=QtWidgets.QMessageBox.Information):
    box = QtWidgets.QMessageBox(_main_window())
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(icon)
    box.exec_()


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


def _shape_element(shape, name):
    try:
        return shape.getElement(name)
    except Exception:
        try:
            if name.startswith("Edge"):
                return shape.Edges[int(name[4:]) - 1]
            if name.startswith("Face"):
                return shape.Faces[int(name[4:]) - 1]
        except Exception:
            pass
    return None


def _shape_ok(shape):
    try:
        if shape is None or shape.isNull():
            return False
        if hasattr(shape, "isValid") and not shape.isValid():
            return False
        return True
    except Exception:
        return False


def _try_fillet(shape, edges, radius):
    if radius <= 0 or not edges:
        return False, "Nessuno spigolo selezionato"
    try:
        result = shape.makeFillet(float(radius), list(edges))
        if not _shape_ok(result):
            return False, "Il kernel ha prodotto una forma non valida"
        try:
            if len(shape.Solids) and not len(result.Solids):
                return False, "Il raccordo elimina il solido"
        except Exception:
            pass
        return True, "OK"
    except Exception as exc:
        return False, str(exc) or exc.__class__.__name__


def _suggest_upper(shape, edges):
    values = []
    try:
        bb = shape.BoundBox
        dims = [v for v in (bb.XLength, bb.YLength, bb.ZLength) if v > 1e-6]
        if dims:
            values.append(min(dims) * 0.499)
    except Exception:
        pass
    try:
        lengths = [float(e.Length) for e in edges if float(e.Length) > 1e-6]
        if lengths:
            values.append(min(lengths) * 0.499)
    except Exception:
        pass
    return max(0.01, min(values)) if values else 100.0


def _maximum_valid_radius(shape, edges):
    if not edges:
        return 0.0
    upper = _suggest_upper(shape, edges)
    tiny = max(1e-5, min(0.01, upper * 0.1))
    if not _try_fillet(shape, edges, tiny)[0]:
        return 0.0
    if _try_fillet(shape, edges, upper)[0]:
        return upper
    lo, hi = tiny, upper
    for _ in range(18):
        mid = (lo + hi) * 0.5
        if _try_fillet(shape, edges, mid)[0]:
            lo = mid
        else:
            hi = mid
    return lo


def _selection_seed():
    """Return base object and selected EdgeN/FaceN names, if any."""
    try:
        sx = Gui.Selection.getSelectionEx()
    except Exception:
        sx = []
    base = None
    names = []
    for item in sx:
        obj = getattr(item, "Object", None)
        if obj is None or not hasattr(obj, "Shape"):
            continue
        if base is None:
            base = obj
        elif obj != base:
            continue
        for name in list(getattr(item, "SubElementNames", []) or []):
            name = str(name)
            if name.startswith("Edge") or name.startswith("Face"):
                if name not in names:
                    names.append(name)
    if base is None:
        try:
            objs = Gui.Selection.getSelection()
            if len(objs) == 1 and hasattr(objs[0], "Shape"):
                base = objs[0]
        except Exception:
            pass
    return base, names


class _SelectionObserver:
    def __init__(self, dialog):
        self.dialog = dialog

    def addSelection(self, doc, obj, sub, pnt):
        try:
            self.dialog._on_view_pick(doc, obj, sub)
        except Exception:
            pass

    def removeSelection(self, doc, obj, sub):
        pass

    def clearSelection(self, doc):
        # Deliberately ignored while picking: a normal click in FreeCAD clears
        # the previous GUI selection before adding the new edge. SolidFlow keeps
        # its own accumulated edge set and re-highlights it afterwards.
        pass

    def setSelection(self, doc):
        pass


class FilletDoctorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or _main_window())
        self.setWindowTitle("SolidFlow — Fillet Doctor")
        self.setMinimumWidth(540)
        self.setModal(False)
        self.base, initial_names = _selection_seed()
        self.names = []
        self.edge_pairs = []
        self._syncing = False
        self._observer = _SelectionObserver(self)
        self._observer_installed = False

        root = QtWidgets.QVBoxLayout(self)
        root.addWidget(QtWidgets.QLabel("<b>Raccordo — selezione diretta degli spigoli</b>"))
        hint = QtWidgets.QLabel(
            "Clicca gli spigoli direttamente sul modello. Non serve tenere premuto Ctrl: "
            "SolidFlow accumula i bordi scelti e li mantiene evidenziati. Una faccia selezionata "
            "è una scorciatoia per raccordarne l'intero perimetro."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.base_label = QtWidgets.QLabel()
        self.base_label.setWordWrap(True)
        root.addWidget(self.base_label)

        self.picks = QtWidgets.QListWidget()
        self.picks.setMaximumHeight(120)
        root.addWidget(self.picks)

        pick_row = QtWidgets.QHBoxLayout()
        self.remove_btn = QtWidgets.QPushButton("Rimuovi selezionato")
        self.clear_btn = QtWidgets.QPushButton("Azzera spigoli")
        pick_row.addWidget(self.remove_btn)
        pick_row.addWidget(self.clear_btn)
        pick_row.addStretch(1)
        root.addLayout(pick_row)

        form = QtWidgets.QFormLayout()
        self.radius = QtWidgets.QDoubleSpinBox()
        self.radius.setDecimals(3)
        self.radius.setRange(0.001, 100000.0)
        self.radius.setSuffix(" mm")
        self.radius.setSingleStep(0.25)
        self.radius.setValue(1.0)
        form.addRow("Raggio", self.radius)
        self.use_all = QtWidgets.QCheckBox("Raccorda tutti gli spigoli del solido")
        form.addRow("", self.use_all)
        root.addLayout(form)

        self.status = QtWidgets.QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMinimumHeight(140)
        root.addWidget(self.status)

        row = QtWidgets.QHBoxLayout()
        self.analyse_btn = QtWidgets.QPushButton("Analizza")
        self.max_btn = QtWidgets.QPushButton("Trova massimo sicuro")
        self.create_btn = QtWidgets.QPushButton("Crea raccordo")
        self.close_btn = QtWidgets.QPushButton("Chiudi")
        self.create_btn.setDefault(True)
        row.addWidget(self.analyse_btn)
        row.addWidget(self.max_btn)
        row.addStretch(1)
        row.addWidget(self.create_btn)
        row.addWidget(self.close_btn)
        root.addLayout(row)

        self.remove_btn.clicked.connect(self._remove_selected)
        self.clear_btn.clicked.connect(self._clear_picks)
        self.analyse_btn.clicked.connect(self.analyse)
        self.max_btn.clicked.connect(self.find_maximum)
        self.create_btn.clicked.connect(self.create_fillet)
        self.close_btn.clicked.connect(self.reject)
        self.use_all.toggled.connect(self._refresh)
        self.radius.valueChanged.connect(self._schedule_analyse)

        self._install_observer()
        for name in initial_names:
            self._add_name(name, sync=False)
        self._refresh(sync=True)

    def _install_observer(self):
        if self._observer_installed:
            return
        try:
            Gui.Selection.addObserver(self._observer)
            self._observer_installed = True
        except Exception as exc:
            App.Console.PrintWarning("SolidFlow Fillet observer: %s\n" % exc)

    def _remove_observer(self):
        if not self._observer_installed:
            return
        try:
            Gui.Selection.removeObserver(self._observer)
        except Exception:
            pass
        self._observer_installed = False

    def _base_from_name(self, doc_name, obj_name):
        try:
            doc = App.getDocument(doc_name)
            return doc.getObject(obj_name) if doc else None
        except Exception:
            return None

    def _on_view_pick(self, doc_name, obj_name, sub_name):
        if self._syncing:
            return
        if not sub_name or not (str(sub_name).startswith("Edge") or str(sub_name).startswith("Face")):
            return
        obj = self._base_from_name(doc_name, obj_name)
        if obj is None or not hasattr(obj, "Shape"):
            return
        if self.base is None:
            self.base = obj
        if obj != self.base:
            self.status.setPlainText("Gli spigoli devono appartenere alla stessa feature/solido.")
            self._sync_gui_selection()
            return
        self._add_name(str(sub_name), sync=True)
        self._schedule_analyse()

    def _add_name(self, name, sync=True):
        # A face means its complete perimeter. Once individual edges are picked,
        # switch back to explicit-edge mode to avoid ambiguous mixed inputs.
        if name.startswith("Face"):
            self.names = [name]
        else:
            if any(n.startswith("Face") for n in self.names):
                self.names = []
            if name not in self.names:
                self.names.append(name)
        self._refresh(sync=sync)

    def _edge_pairs_from_names(self):
        pairs = []
        if self.base is None:
            return pairs
        for name in self.names:
            element = _shape_element(self.base.Shape, name)
            if element is None:
                continue
            if name.startswith("Edge"):
                pairs.append((name, element))
            elif name.startswith("Face"):
                for index, edge in enumerate(list(getattr(element, "Edges", []) or []), 1):
                    pairs.append(("%s/bordo%d" % (name, index), edge))
        return pairs

    def _sync_gui_selection(self):
        if self.base is None:
            return
        self._syncing = True
        try:
            Gui.Selection.clearSelection()
            for name in self.names:
                Gui.Selection.addSelection(self.base, name)
        except Exception:
            pass
        finally:
            self._syncing = False

    def _refresh(self, sync=False):
        self.edge_pairs = self._edge_pairs_from_names()
        self.picks.clear()
        for name in self.names:
            self.picks.addItem(name)
        if self.base is None:
            self.base_label.setText("Base: —  • clicca uno spigolo del modello")
        else:
            label = getattr(self.base, "Label", getattr(self.base, "Name", "?"))
            self.base_label.setText("Base: %s  • spigoli effettivi: %d" % (label, len(self.edge_pairs)))
        valid = self.base is not None and (bool(self.edge_pairs) or self.use_all.isChecked())
        self.analyse_btn.setEnabled(valid)
        self.max_btn.setEnabled(valid)
        self.create_btn.setEnabled(valid)
        self.remove_btn.setEnabled(bool(self.names))
        self.clear_btn.setEnabled(bool(self.names))
        if sync:
            self._sync_gui_selection()

    def _remove_selected(self):
        row = self.picks.currentRow()
        if 0 <= row < len(self.names):
            del self.names[row]
            self._refresh(sync=True)
            self._schedule_analyse()

    def _clear_picks(self):
        self.names = []
        self._refresh(sync=True)
        self.status.setPlainText("Selezione azzerata. Clicca gli spigoli da raccordare.")

    def _edges_for_test(self):
        if self.base is None:
            return []
        if self.use_all.isChecked():
            try:
                return list(self.base.Shape.Edges)
            except Exception:
                return []
        return [edge for _label, edge in self.edge_pairs]

    def _schedule_analyse(self, *_args):
        QtCore.QTimer.singleShot(100, self.analyse)

    def analyse(self):
        if self.base is None:
            self.status.setPlainText("Clicca almeno uno spigolo del solido.")
            return
        edges = self._edges_for_test()
        if not edges:
            self.status.setPlainText("Nessuno spigolo scelto. Clicca gli spigoli direttamente nella vista 3D.")
            return
        radius = float(self.radius.value())
        ok, reason = _try_fillet(self.base.Shape, edges, radius)
        lines = ["Raggio: %.3f mm" % radius, "Spigoli verificati: %d" % len(edges)]
        if ok:
            lines.append("✓ Combinazione valida.")
        else:
            lines.append("✗ Combinazione non valida.")
            if reason:
                lines.append("Kernel: " + reason)
            if not self.use_all.isChecked() and len(self.edge_pairs) > 1:
                bad = []
                for label, edge in self.edge_pairs:
                    if not _try_fillet(self.base.Shape, [edge], radius)[0]:
                        bad.append(label)
                if bad:
                    lines.append("Falliscono già singolarmente: " + ", ".join(bad))
                else:
                    suspects = []
                    raw = [e for _l, e in self.edge_pairs]
                    for i, (label, _e) in enumerate(self.edge_pairs):
                        subset = raw[:i] + raw[i + 1:]
                        if subset and _try_fillet(self.base.Shape, subset, radius)[0]:
                            suspects.append(label)
                    if suspects:
                        lines.append("La combinazione torna valida rimuovendo: " + ", ".join(suspects))
                    else:
                        lines.append("I bordi sono validi singolarmente: il limite nasce dalla combinazione.")
        maximum = _maximum_valid_radius(self.base.Shape, edges)
        if maximum > 0:
            lines.append("Massimo sicuro stimato: %.3f mm" % maximum)
        self.status.setPlainText("\n".join(lines))

    def find_maximum(self):
        if self.base is None:
            return
        edges = self._edges_for_test()
        maximum = _maximum_valid_radius(self.base.Shape, edges)
        if maximum <= 0:
            self.status.setPlainText("Non ho trovato un raggio stabile per questa selezione.")
            return
        self.radius.setValue(max(0.001, maximum * 0.995))
        self.analyse()

    def create_fillet(self):
        if self.base is None:
            return
        body = _body_for(self.base)
        if body is None:
            _message("SolidFlow Fillet Doctor", "La feature selezionata non appartiene a un Body Part Design.", QtWidgets.QMessageBox.Warning)
            return
        edges = self._edges_for_test()
        radius = float(self.radius.value())
        ok, reason = _try_fillet(self.base.Shape, edges, radius)
        if not ok:
            _message("Raccordo non valido", "La geometria non accetta questo raccordo.\n\n" + reason, QtWidgets.QMessageBox.Warning)
            return
        doc = self.base.Document
        doc.openTransaction("SolidFlow - Fillet Doctor")
        try:
            feature = body.newObject("PartDesign::Fillet", "Fillet")
            if self.use_all.isChecked():
                feature.Base = (self.base, [""])
                feature.UseAllEdges = True
            else:
                feature.Base = (self.base, list(self.names))
                feature.UseAllEdges = False
            feature.Radius = radius
            doc.recompute()
            if not feature.isValid() or not _shape_ok(feature.Shape):
                raise RuntimeError("FreeCAD non ha prodotto un PartDesign::Fillet valido")
            doc.commitTransaction()
            try:
                self.base.ViewObject.Visibility = False
            except Exception:
                pass
            self._remove_observer()
            self._syncing = True
            try:
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(feature)
            finally:
                self._syncing = False
            super().accept()
        except Exception as exc:
            try:
                doc.abortTransaction()
            except Exception:
                pass
            _message("SolidFlow Fillet Doctor", "Creazione del raccordo fallita:\n" + str(exc), QtWidgets.QMessageBox.Critical)

    def reject(self):
        self._remove_observer()
        super().reject()

    def closeEvent(self, event):
        self._remove_observer()
        super().closeEvent(event)


_dialog = None


def launch_fillet_doctor():
    global _dialog
    base, _names = _selection_seed()
    if base is None:
        _message(
            "SolidFlow Fillet Doctor",
            "Seleziona il solido/feature, una faccia oppure un primo spigolo, poi avvia Fillet Doctor.\n\n"
            "Una volta aperto potrai aggiungere gli spigoli con normali click nella vista 3D.",
            QtWidgets.QMessageBox.Information,
        )
        return
    _dialog = FilletDoctorDialog()
    _dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()


def install():
    class _Command:
        def GetResources(self):
            return {
                "MenuText": "Fillet Doctor interattivo...",
                "ToolTip": "Seleziona gli spigoli direttamente sul modello e crea un raccordo Part Design",
            }

        def IsActive(self):
            return App.ActiveDocument is not None

        def Activated(self):
            launch_fillet_doctor()

    try:
        Gui.addCommand("SolidFlow_FilletDoctorInteractive", _Command())
    except Exception:
        pass
