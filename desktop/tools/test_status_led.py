import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from bumble import hci
from engine import Engine
from status_led import StatusLed


def response(data=b'\0LEDS\x01'):
    return hci.HCI_GenericReturnParameters(data=data)


class LedControl(unittest.IsolatedAsyncioTestCase):
    async def test_old_firmware_keeps_audio_compatible(self):
        device = SimpleNamespace(host=SimpleNamespace(send_sync_command=AsyncMock(return_value=response(b'\x01'))))
        led = StatusLed(device, lambda *args: None)
        await led.start('streaming')
        self.assertFalse(led.enabled)
        self.assertIsNone(led.task)
        await led.close()
        device.host.send_sync_command.assert_awaited_once()

    async def test_state_change_and_manual_disconnect(self):
        device = SimpleNamespace(host=SimpleNamespace(send_sync_command=AsyncMock(return_value=response())))
        led = StatusLed(device, lambda *args: None)
        await led.start('connecting')
        led.update('streaming')
        await asyncio.sleep(.02)
        await led.close()
        payloads = [call.args[0].parameters for call in device.host.send_sync_command.await_args_list]
        self.assertEqual(payloads, [b'LEDS\x02', b'LEDS\x04', b'LEDS\x00'])

    async def test_error_precedes_idle_and_usb_loss_skips_io(self):
        device = SimpleNamespace(host=SimpleNamespace(send_sync_command=AsyncMock(return_value=response())))
        led = StatusLed(device, lambda *args: None)
        await led.start('paired')
        await led.close(failed=True)
        payloads = [call.args[0].parameters for call in device.host.send_sync_command.await_args_list]
        self.assertEqual(payloads[-2:], [b'LEDS\x05', b'LEDS\x00'])
        device.host.send_sync_command.reset_mock()
        await led.close(transport_lost=True)
        device.host.send_sync_command.assert_not_awaited()

    async def test_automatic_connection_uses_reconnecting_pattern(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine('.', Path(directory))
            engine.led = SimpleNamespace(update=lambda state: states.append(state))
            engine.reconnecting = True
            states = []
            with patch('engine.emit'):
                engine.state('connecting')
                engine.state('paired')
                engine.state('streaming')
            self.assertEqual(states, ['reconnecting', 'paired', 'streaming'])


if __name__ == '__main__':
    unittest.main()
