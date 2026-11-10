import asyncio,io,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import engine
from quality import AdaptiveQuality
engine.OUT=io.StringIO()

class FirmwareSafety(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.e=engine.Engine(self.root/'assets',self.root/'data');self.e.get_port=lambda _: 'COM6'
        self.calls=[]
    def tearDown(self):self.temp.cleanup()
    async def test_manual_disconnect_pauses_auto_but_retains_settings(self):
        self.e.settings={'autoConnect':True,'address':'AA:BB:CC:DD:EE:FF','port':'COM6'}
        await self.e.disconnect({})
        self.assertTrue(self.e.auto_paused)
        self.assertTrue(self.e.settings['autoConnect'])
        await self.e.settings_update({'volumeLimit':20,'autoConnect':True})
        self.assertTrue(self.e.auto_paused)
        await self.e.settings_update({'autoConnect':False})
        await self.e.settings_update({'autoConnect':True})
        self.assertFalse(self.e.auto_paused)
    async def test_startup_auto_connect_resolves_single_usb_port(self):
        from unittest.mock import patch,AsyncMock
        self.e.settings={'autoConnect':True,'address':'AA:BB:CC:DD:EE:FF','port':''}
        self.e.ports=lambda:[{'port':'COM6'}]
        self.e.connect=AsyncMock()
        count=0
        async def tick(_):
            nonlocal count
            count+=1
            if count>1:raise asyncio.CancelledError
        with patch.object(engine.asyncio,'sleep',tick):
            with self.assertRaises(asyncio.CancelledError):await self.e.autoconnect()
        self.e.connect.assert_awaited_once_with({'port':'COM6'})
    async def test_incomplete_backup_preserves_existing_file(self):
        dest=self.root/'backup.bin';dest.write_bytes(b'previous backup')
        async def tool(args):
            self.calls.append(args)
            if 'flash-id' in args:return 'Detected flash size: 4MB'
            Path(args[-1]).write_bytes(b'incomplete');return ''
        self.e.tool=tool
        with self.assertRaisesRegex(RuntimeError,'備份大小不符'):await self.e.backup({'path':str(dest)})
        self.assertEqual(dest.read_bytes(),b'previous backup')
    async def test_small_flash_never_written(self):
        async def tool(args):self.calls.append(args);return 'ESP32'
        async def backup(_):return {'size':2*1024*1024,'path':'saved.bin'}
        self.e.tool=tool;self.e.backup=backup
        with self.assertRaisesRegex(RuntimeError,'至少 4 MB'):await self.e.flash({})
        self.assertFalse(any('write-flash' in c for c in self.calls))
    async def test_corrupt_firmware_never_written(self):
        folder=self.root/'assets/firmware';folder.mkdir(parents=True)
        (folder/'firmware.bin').write_bytes(b'bad');(folder/'manifest.json').write_text(json.dumps({'firmware.bin':'0'*64}))
        async def tool(args):self.calls.append(args);return 'ESP32'
        async def backup(_):return {'size':4*1024*1024,'path':'saved.bin'}
        self.e.tool=tool;self.e.backup=backup
        with self.assertRaisesRegex(RuntimeError,'完整性'):await self.e.flash({})
        self.assertFalse(any('write-flash' in c for c in self.calls))
    async def test_wrong_capacity_restore_never_written(self):
        file=self.root/'image.bin';file.write_bytes(bytes(1024*1024))
        async def tool(args):self.calls.append(args);return 'Detected flash size: 4MB'
        self.e.tool=tool
        with self.assertRaisesRegex(ValueError,'容量不符'):await self.e.restore({'path':str(file)})
        self.assertFalse(any('write-flash' in c for c in self.calls))
    async def test_forget_removes_only_selected_headphone(self):
        a='AA:BB:CC:DD:EE:FF';b='11:22:33:44:55:66'
        f=self.e.data/'pairing.json';f.write_text(json.dumps({'controller':{a+'/P':{},b+'/P':{}}}))
        self.e.peers={a:{},b:{}}
        await self.e.forget({'address':a})
        db=json.loads(f.read_text());self.assertNotIn(a+'/P',db['controller']);self.assertIn(b+'/P',db['controller'])

class Adaptation(unittest.TestCase):
    def test_backpressure_reduces_quality_without_immediate_rebound(self):
        q=AdaptiveQuality(0)
        self.assertEqual(q.choose(0,True,2000000,0,0,9000,11),1)
        self.assertEqual(q.choose(0,True,2000000,0,0,0,12),1)
        self.assertEqual(q.choose(0,True,2000000,0,0,0,42),0)
    def test_legacy_transport_cannot_select_hq(self):
        self.assertEqual(AdaptiveQuality().choose(0,False,921600,0,0,0,100),1)
    def test_disabling_auto_restores_requested_quality(self):
        q=AdaptiveQuality(0);q.choose(0,True,2000000,-10,0,0,11)
        self.assertEqual(q.choose(0,False,2000000,0,0,0,12),0)

if __name__=='__main__':unittest.main()
