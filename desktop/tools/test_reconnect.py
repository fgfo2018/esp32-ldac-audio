import asyncio,io,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import engine,pc_audio

class Reconnect(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.e=engine.Engine('.',Path(self.temp.name))
    def tearDown(self):self.temp.cleanup()
    async def test_usb_loss_cancels_pending_pairing_and_cleans_session(self):
        future=asyncio.get_running_loop().create_future();cleaned=asyncio.Event()
        async def session():
            try:await asyncio.Event().wait()
            finally:cleaned.set()
        task=asyncio.create_task(self.e.transport_session(SimpleNamespace(source=SimpleNamespace(terminated=future)),session()))
        await asyncio.sleep(0);future.set_result(None)
        with self.assertRaises(engine.UsbDisconnected):await task
        self.assertTrue(cleaned.is_set());self.assertIsNone(self.e.checked_port)
    async def test_manual_stop_does_not_cancel_shared_transport_future(self):
        future=asyncio.get_running_loop().create_future()
        task=asyncio.create_task(self.e.transport_session(SimpleNamespace(source=SimpleNamespace(terminated=future)),asyncio.Event().wait()))
        await asyncio.sleep(0);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(future.cancelled())
    async def test_usb_error_survives_snapshot_state_and_clears_after_recovery(self):
        async def failed():raise engine.UsbDisconnected('USB lost')
        with patch.object(engine,'OUT',io.StringIO()):
            await self.e._guard(failed())
            self.assertEqual(self.e.status,'waiting');self.assertIn('USB',self.e.problem['message'])
            self.e.state('streaming');self.assertIsNone(self.e.problem)
    async def test_cancelled_serial_open_releases_windows_port(self):
        serial=MagicMock();serial.is_open=True
        with patch.object(pc_audio.serial,'Serial',return_value=serial):
            task=asyncio.create_task(pc_audio.transport('COM6'));await asyncio.sleep(.01);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        serial.close.assert_called_once()

if __name__=='__main__':unittest.main()
