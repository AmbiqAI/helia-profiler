"""UART capture preserves output emitted while resetting the target."""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from helia_profiler.transport import uart
from helia_profiler.transport.protocol import HPX_END, HPX_START


@pytest.mark.parametrize("keep_attached", [False, True])
def test_capture_preserves_output_received_during_reset(monkeypatch, keep_attached):
    class SerialPort:
        def __init__(self):
            self.buffer = b"stale output\n"
            self.is_open = True

        @property
        def in_waiting(self):
            return len(self.buffer)

        def reset_input_buffer(self):
            self.buffer = b""

        def read(self, count):
            data, self.buffer = self.buffer[:count], self.buffer[count:]
            return data

        def close(self):
            self.is_open = False

    port = SerialPort()

    class ResetController:
        def swpoi_reset(self, **kwargs):
            raise AssertionError("unexpected SWPOI reset")

        def debug_reset(self, **kwargs):
            assert port.buffer == b""
            port.buffer = f"{HPX_START}\nHPX_TEST,1\n{HPX_END}\n".encode()

        @contextmanager
        def attached_reset_session(self, **kwargs):
            self.debug_reset(**kwargs)
            yield MagicMock()

    monkeypatch.setattr(uart, "find_jlink_vcom_port", lambda _: "test-port")
    monkeypatch.setattr(uart.serial, "Serial", lambda **kwargs: port)

    lines = uart.capture_uart_output(
        jlink_device="test-device",
        keep_attached=keep_attached,
        timeout_s=0.05,
        heartbeat_timeout_s=0.05,
        reset_controller=ResetController(),
    )

    assert lines == [HPX_START, "HPX_TEST,1", HPX_END]
    assert not port.is_open
