"""Headless behavioral regression tests; FreeCAD/Qt integration is manual.

Compile the actual UI definitions with injected doubles, without importing
FreeCAD or executing the addon's bootstrap in the test process.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / 'SolidFlowUX' / 'solidflow_ui.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))


def load(name, env, owner=None):
    nodes = TREE.body
    if owner:
        nodes = next(n for n in nodes if isinstance(n, ast.ClassDef) and n.name == owner).body
    node = next(n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), env)
    return env[name]


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.action = Mock()
        self.action.isEnabled.return_value = False
        self.env = {'_command_object': Mock(return_value=object()),
                    '_command_action': Mock(return_value=self.action)}
        self.enabled = load('_spec_enabled', self.env)

    def test_native_disabled_and_enabled(self):
        spec = NS(callback=None, command='Std_Save')
        self.assertFalse(self.enabled(spec))
        self.action.isEnabled.return_value = True
        self.assertTrue(self.enabled(spec))

    def test_missing_command(self):
        self.env['_command_object'].return_value = None
        self.assertFalse(self.enabled(NS(callback=None, command='Missing')))

    def test_callback_can_prepare_context_even_if_icon_command_disabled(self):
        self.assertTrue(self.enabled(NS(callback=lambda: None, command=None)))

    def test_rechecks_state_before_dispatch(self):
        gui, app = Mock(), Mock()
        self.env.update(Gui=gui, App=app)
        run = load('_run_spec', self.env)
        run(NS(callback=None, command='Std_Save', label='Salva'))
        gui.runCommand.assert_not_called()
        gui.getMainWindow.return_value.statusBar.return_value.showMessage.assert_called_once()
        self.action.isEnabled.return_value = True
        run(NS(callback=None, command='Std_Save', label='Salva'))
        gui.runCommand.assert_called_once_with('Std_Save', 0)


class PlacementTests(unittest.TestCase):
    def test_secondary_monitor_negative_origin(self):
        area = NS(left=lambda:-1280, right=lambda:-1, top=lambda:0, bottom=lambda:719)
        screen = NS(availableGeometry=lambda:area)
        env = {'QtGui': NS(QCursor=NS(pos=lambda:NS(x=lambda:-5,y=lambda:715))),
               'QtWidgets': NS(QApplication=NS(screenAt=lambda p:screen))}
        widget = Mock()
        widget.width.return_value, widget.height.return_value = 400, 80
        load('_place_near_cursor',env)(widget)
        widget.move.assert_called_once_with(-409,631)

    def test_without_screen(self):
        env = {'QtGui': NS(QCursor=NS(pos=lambda:NS(x=lambda:10,y=lambda:20))),
               'QtWidgets': NS(QApplication=NS(screenAt=lambda p:None, primaryScreen=lambda:None))}
        widget = Mock()
        load('_place_near_cursor',env)(widget)
        widget.move.assert_called_once_with(28,40)


class SelectionTests(unittest.TestCase):
    def test_two_sketches_plus_other_object_is_not_two_sketch_context(self):
        selection = [NS(SubElementNames=[]) for _ in range(3)]
        env = dict(selected_face=lambda:None, selected_profile=lambda:(None,[]),
                   selected_sketch_objects=lambda:[1,2], selection_snapshot=lambda:selection,
                   selected_editable_feature=lambda:None)
        classify = load('_selection_kind',env)
        self.assertEqual(classify(),'Object')
        selection.pop()
        self.assertEqual(classify(),'TwoSketches')

    def test_disabling_toolbar_cancels_pending_show(self):
        controller = Mock()
        env = {'_controller':controller,'_prefs':Mock()}
        load('set_auto_mini_toolbar_enabled',env)(False)
        controller.selection_timer.stop.assert_called_once()
        controller.mini.hide.assert_called_once()

    def test_old_toolbar_disappears_before_debounce(self):
        controller = Mock()
        load('selection_changed',{},'SolidFlowController')(controller)
        self.assertEqual(controller.method_calls[0][0],'mini.hide')
        controller.selection_timer.start.assert_called_once_with(150)

    def test_click_hides_toolbar_before_queued_dispatch(self):
        controller, timer, dispatch = Mock(), Mock(), Mock()
        env={'QtCore':NS(QTimer=timer),'_run_spec':dispatch}
        spec=object()
        load('_trigger',env,'ContextMiniToolbar')(controller,spec)
        controller.hide_timer.stop.assert_called_once()
        controller.hide.assert_called_once()
        dispatch.assert_not_called()
        timer.singleShot.call_args.args[1]()
        dispatch.assert_called_once_with(spec)

    def test_feature_toolbar_restarts_timer_and_limits_quick_edit(self):
        for editable, expected in [(None, ['Serie 3D', 'Raccordo']),
                                   (object(), ['Modifica', 'Serie 3D', 'Raccordo'])]:
            controller = Mock()
            controller.layout.count.return_value = 0
            buttons = []
            def make_button():
                button = Mock()
                buttons.append(button)
                return button
            app = NS(activeModalWidget=lambda:None, activePopupWidget=lambda:None)
            env = dict(auto_mini_toolbar_enabled=lambda:True,
                       _is_sketch_edit_mode=lambda:False,
                       QtWidgets=NS(QApplication=NS(instance=lambda:app),QToolButton=make_button),
                       Gui=NS(ActiveDocument=object(),Control=NS(activeDialog=lambda:None)),
                       _selection_kind=lambda:'Feature',
                       selected_editable_feature=lambda:editable,
                       ActionSpec=lambda label, **kw:NS(label=label,tooltip='',**kw),
                       PD_LINEAR='PartDesign_LinearPattern', _fillet_best=Mock(),
                       launch_edit_selected_feature=Mock(), _spec_enabled=lambda s:True,
                       _place_near_cursor=Mock())
            load('show_for_selection',env,'ContextMiniToolbar')(controller)
            self.assertEqual([b.setText.call_args.args[0] for b in buttons], expected)
            controller.hide_timer.stop.assert_called_once()
            controller.hide_timer.start.assert_called_once_with(3500)
            controller.show.assert_called_once()

    def test_open_task_blocks_toolbar_and_stops_previous_timeout(self):
        controller=Mock()
        app=NS(activeModalWidget=lambda:None,activePopupWidget=lambda:None)
        env={'auto_mini_toolbar_enabled':lambda:True,'_is_sketch_edit_mode':lambda:False,
             'QtWidgets':NS(QApplication=NS(instance=lambda:app)),
             'Gui':NS(ActiveDocument=object(),Control=NS(activeDialog=lambda:object()))}
        load('show_for_selection',env,'ContextMiniToolbar')(controller)
        controller.hide_timer.stop.assert_called_once()
        controller.show.assert_not_called()
        controller.layout.count.assert_not_called()


if __name__ == '__main__':
    unittest.main()
