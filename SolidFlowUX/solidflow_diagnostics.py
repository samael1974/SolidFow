# -*- coding: utf-8 -*-
"""Read-only, shareable installation diagnostics, also embedded in a macro."""
import os
from pathlib import Path
import platform
import sys

VERSION = "0.4.0-beta.13"


def _clean(value):
    text = str(value)
    home = str(Path.home())
    if home and home != os.path.sep:
        text = text.replace(home, '<utente>')
        text = text.replace(home.replace('\\', '/'), '<utente>')
    return text


def collect_report(app, gui=None, runtime_dir=None):
    root = Path(app.getUserAppDataDir())
    target = root / 'Mod' / 'SolidFlowUX'
    lines = ['SolidFlow UX — diagnostica ' + VERSION,
             'FreeCAD: ' + '.'.join(str(x) for x in app.Version()[:3]),
             'Sistema: ' + platform.system() + ' ' + platform.release(),
             'Python: ' + platform.python_version(),
             'Profilo FreeCAD: ' + str(root),
             'Destinazione prevista: ' + str(target)]
    loaded = sys.modules.get('solidflow_ui')
    loaded_file = getattr(loaded, '__file__', None)
    lines.append('Interfaccia caricata da: ' + (str(loaded_file) if loaded_file else 'NON CARICATA'))
    lines.append('Controller avviato: ' + str(bool(getattr(loaded, '_controller', None))))
    active = Path(runtime_dir) if runtime_dir else (Path(loaded_file).parent if loaded_file else target)
    if loaded_file and active.resolve() != target.resolve():
        lines.append('ATTENZIONE: la copia caricata non coincide con il profilo utente attivo.')
    manifest = active / 'manifest.txt'
    if manifest.is_file():
        try:
            names = [n.strip() for n in manifest.read_text(encoding='utf-8-sig').splitlines()
                     if n.strip() and not n.lstrip().startswith('#')]
            missing = [name for name in names if not (active / name).is_file()]
            lines.append('File runtime: ' + ('MANCANTI: ' + ', '.join(missing) if missing else 'OK (%d)' % len(names)))
        except Exception as exc:
            lines.append('Manifest non leggibile: ' + str(exc))
    else:
        lines.append('Manifest: ASSENTE in ' + str(active))
    candidates = {target, active}
    for path in sys.path:
        if path:
            p = Path(path)
            if (p / 'solidflow_ui.py').is_file():
                candidates.add(p)
    appdata = os.environ.get('APPDATA')
    if appdata:
        base = Path(appdata) / 'FreeCAD'
        candidates.add(base / 'Mod' / 'SolidFlowUX')
        candidates.update(base.glob('v*/Mod/SolidFlowUX'))
    installed = sorted({str(p.resolve()) for p in candidates if p.is_dir()})
    lines.append('Copie rilevate: ' + str(len(installed)))
    lines.extend('  ' + p for p in installed)
    if len(installed) > 1:
        lines.append('Più copie: possono appartenere a profili diversi; non rimuoverle senza verifica.')
    status = getattr(app, '__solidflow_status__', {})
    lines.append('Avvio moduli:')
    if not status:
        lines.append('  NON RILEVATO: componente non avviato, disabilitato o riavvio necessario.')
    for key in sorted(status):
        lines.append('  %s: %s' % (key, status[key]))
    try:
        from PySide import QtCore
        lines.append('Qt: ' + QtCore.qVersion())
    except Exception as exc:
        lines.append('Qt: ' + str(exc))
    if gui is not None:
        try:
            lines.append('Workbench attivo: ' + gui.activeWorkbench().name())
        except Exception:
            lines.append('Workbench attivo: non disponibile')
    lines.append('Il rapporto non include modelli, contenuti dei documenti o variabili ambiente.')
    return _clean('\n'.join(lines))


def show_report():
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide import QtWidgets
    try:
        text = collect_report(App, Gui)
    except Exception as exc:
        text = 'Impossibile completare la diagnostica: ' + _clean(exc)
    dialog = QtWidgets.QDialog(Gui.getMainWindow())
    dialog.setWindowTitle('SolidFlow UX — Diagnostica installazione')
    dialog.resize(730, 520)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.addWidget(QtWidgets.QLabel('Copia o salva questo rapporto e allegalo alla segnalazione.'))
    editor = QtWidgets.QPlainTextEdit()
    editor.setReadOnly(True)
    editor.setPlainText(text)
    layout.addWidget(editor)
    buttons = QtWidgets.QDialogButtonBox()
    copy = buttons.addButton('Copia rapporto', QtWidgets.QDialogButtonBox.ActionRole)
    save = buttons.addButton('Salva rapporto…', QtWidgets.QDialogButtonBox.ActionRole)
    close = buttons.addButton('Chiudi', QtWidgets.QDialogButtonBox.RejectRole)
    copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(text))

    def save_report():
        path, _ = QtWidgets.QFileDialog.getSaveFileName(dialog, 'Salva diagnostica',
                                                      'SolidFlowUX-diagnostica.txt', 'Testo (*.txt)')
        if path:
            try:
                Path(path).write_text(text, encoding='utf-8')
            except OSError as exc:
                QtWidgets.QMessageBox.warning(dialog, 'Rapporto non salvato', str(exc))
    save.clicked.connect(save_report)
    close.clicked.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec_()
