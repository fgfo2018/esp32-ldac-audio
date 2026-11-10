import asyncio,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from battery import BatteryFrames,SonyFrames,sony_frame,sony_battery

class BatteryProtocol(unittest.TestCase):
    def test_fragmented_and_coalesced_fast_pair(self):
        result=[];p=BatteryFrames(result.append)
        p.feed(b'\x03\x03\x00');p.feed(b'\x01\xd0\x03\x03\x00\x03\x7f\x32\xe4')
        self.assertEqual(result[0]['percent'],80);self.assertTrue(result[0]['charging'])
        self.assertIsNone(result[1]['components']['left']['percent'])
        self.assertEqual(result[1]['components']['case']['percent'],100)
    def test_unrelated_and_invalid_fast_pair(self):
        result=[];p=BatteryFrames(result.append)
        p.feed(b'\x03\x01\x00\x01\x50\x03\x03\x00\x02\x50\x50')
        self.assertFalse(result)
    def test_sony_fragmentation_escaping_and_checksum(self):
        result=[];p=SonyFrames(lambda *x:result.append(x))
        frame=sony_frame(12,1,b'\x11\x00\x3c\x00')
        for value in frame:p.feed(bytes([value]))
        self.assertEqual(result,[(12,1,b'\x11\x00\x3c\x00')])
        corrupt=bytearray(frame);corrupt[-2]^=1;p.feed(corrupt)
        p.feed(b'>\x0c\x00\x00<')
        self.assertEqual(len(result),1)

class SonyReadOnly(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_battery_and_notifications(self):
        stop=asyncio.Event();values=[];commands=[]
        class Session:
            sink=None
            def write(self,frame):
                parsed=[];SonyFrames(lambda *x:parsed.append(x)).feed(frame)
                kind,seq,payload=parsed[0]
                if kind==1:return
                commands.append(payload)
                self.sink(sony_frame(1,1-seq))
                self.sink(sony_frame(12,seq,b'\x01\x00\x70\x00' if payload==b'\x00\x00' else b'\x11\x00\x50\x00'))
        def notify(value):values.append(value);stop.set()
        await sony_battery(Session(),notify,stop)
        self.assertEqual(values[0]['percent'],80)
        self.assertEqual(commands,[b'\x00\x00',b'\x10\x00'])

if __name__=='__main__':unittest.main()
