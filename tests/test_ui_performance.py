from types import SimpleNamespace

from nputop.gui import ui as ui_module
from nputop.gui.screens.main.process import ProcessPanel
from nputop.gui.ui import UI


class FakeSelection:
    def __init__(self):
        self.tagged = {}
        self.within_window = False

    def reset(self):
        pass

    def clear(self):
        pass

    def is_same(self, process):
        return False

    def is_same_on_host(self, process):
        return False

    def is_tagged(self, process):
        return False

    def owned(self):
        return False


class FakeDevice:
    def __init__(self, index):
        self.physical_index = index
        self.display_index = index
        self.snapshot = SimpleNamespace(display_color="green")


class FakeProcess:
    def __init__(self, pid, device, host_info, *, fail_host_info=False):
        self.pid = pid
        self.device = device
        self.username = "user"
        self.npu_memory_human = "1MiB"
        self.is_zombie = False
        self.no_permissions = False
        self.is_gone = False
        self.command = "cmd"
        self._host_info = host_info
        self._fail_host_info = fail_host_info

    @property
    def host_info(self):
        if self._fail_host_info:
            raise AssertionError("invisible rows should not format host_info")
        return self._host_info


class FakeWindow:
    def __init__(self, keys):
        self.keys = list(keys)

    def getch(self):
        if self.keys:
            return self.keys.pop(0)
        return ui_module.curses.ERR


def make_ui(keys):
    ui = object.__new__(UI)
    ui.win = FakeWindow(keys)
    ui.last_input_time = 0.0
    return ui


def test_handle_pending_inputs_drains_simple_keys_without_flush(monkeypatch):
    handled = []
    ui = make_ui([ord("j"), ord("k")])
    ui.handle_key = handled.append

    def fail_flushinp():
        raise AssertionError("simple input should not flush pending keys")

    monkeypatch.setattr(ui_module.curses, "flushinp", fail_flushinp)

    assert ui.handle_pending_inputs()
    assert handled == [ord("j"), ord("k")]


def test_handle_pending_inputs_respects_frame_limit():
    handled = []
    ui = make_ui([ord("a"), ord("b"), ord("c")])
    ui.handle_key = handled.append

    assert ui.handle_pending_inputs(max_events=2)
    assert handled == [ord("a"), ord("b")]

    assert ui.handle_pending_inputs(max_events=2)
    assert handled == [ord("a"), ord("b"), ord("c")]


def test_handle_input_reports_no_pending_key():
    ui = make_ui([])

    assert not ui.handle_input()


def test_process_panel_row_visibility_uses_terminal_viewport():
    panel = object.__new__(ProcessPanel)
    panel.root = SimpleNamespace(y=5, termsize=(10, 120))
    panel._width = 79

    assert not panel._is_row_visible(4)
    assert panel._is_row_visible(5)
    assert panel._is_row_visible(9)
    assert not panel._is_row_visible(10)


def test_process_panel_row_visibility_requires_min_width():
    panel = object.__new__(ProcessPanel)
    panel.root = SimpleNamespace(y=0, termsize=(10, 78))
    panel._width = 78

    assert not panel._is_row_visible(5)


def test_process_panel_orders_have_explicit_unique_bind_keys():
    assert "sm_utilization" not in ProcessPanel.ORDERS
    assert {order.bind_key for order in ProcessPanel.ORDERS.values()} == {
        "n",
        "p",
        "u",
        "g",
        "c",
        "m",
        "t",
    }


def test_process_panel_draw_skips_invisible_process_row_formatting():
    panel = object.__new__(ProcessPanel)
    panel.root = SimpleNamespace(y=10, termsize=(20, 120))
    panel.x = 0
    panel.y = 4
    panel._width = 100
    panel._compact = True
    panel._order = "natural"
    panel.reverse = False
    panel.host_offset = -1
    panel.host_headers = ["%CPU", "%MEM", "TIME", "COMMAND"]
    panel.y_mouse = None
    panel.selection = FakeSelection()
    panel.has_snapshots = True
    panel._need_redraw = False
    panel._snapshots = [
        FakeProcess(1, FakeDevice(0), "hidden", fail_host_info=True),
        FakeProcess(2, FakeDevice(0), "visible"),
    ]

    panel.color_reset = lambda: None
    panel.color_at = lambda *args, **kwargs: None
    panel.addstr = lambda *args, **kwargs: None

    panel.draw()
