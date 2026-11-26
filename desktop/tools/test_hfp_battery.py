import asyncio
import sys
import unittest
from pathlib import Path
from bumble import hfp
from bumble.utils import EventEmitter
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from hfp_battery import BatteryGateway


class Dlc(EventEmitter):
    def __init__(self):
        super().__init__()
        self.sink = None
        self.writes = []
    def write(self, value):
        self.writes.append(value)


class ProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_hf_battery_after_slc_and_fragmented_at(self):
        dlc = Dlc(); values = []
        gateway = BatteryGateway(dlc, values.append, lambda *args: None)
        for command in [b'AT+BRSF=256\r', b'AT+CIND=?\r', b'AT+CIND?\r',
                        b'AT+CMER=3,0,0,1\r', b'AT+BIND=2\r',
                        b'AT+BIND=?\r', b'AT+BIND?\r', b'AT+BIE', b'V=2,80\r']:
            dlc.sink(command)
        self.assertTrue(gateway.ready.is_set())
        self.assertEqual(values[-1]['percent'], 80)
        self.assertTrue(gateway.reported.is_set())
        self.assertEqual(gateway.supported_ag_features, int(hfp.AgFeature.HF_INDICATORS))

    async def test_gateway_cind_is_not_headphone_battery(self):
        dlc = Dlc(); values = []
        gateway = BatteryGateway(dlc, values.append, lambda *args: None)
        dlc.sink(b'AT+CIND?\r')
        self.assertFalse(values)
        self.assertFalse(gateway.ready.is_set())

    async def test_real_xm4_handshake_without_standard_battery_feature(self):
        dlc = Dlc(); values = []
        gateway = BatteryGateway(dlc, values.append, lambda *args: None)
        dlc.sink(b'AT+BRSF=765\rAT+CIND=?\rAT+CIND?\r')
        self.assertFalse(gateway.ready.is_set())
        dlc.sink(b'AT+CMER=3,0,0,1\rAT+XAPL=054C-0D58-0250,10\r')
        self.assertTrue(gateway.ready.is_set())
        dlc.sink(b'AT+IPHONEACCEV=1,1,0\r')
        self.assertEqual(values[-1]['percent'], 10)
        dlc.sink(b'AT+IPHONEACCEV=1,1,9\r')
        self.assertEqual(values[-1]['percent'], 100)

    async def test_apple_extension_and_malformed_data(self):
        dlc = Dlc(); values = []
        BatteryGateway(dlc, values.append, lambda *args: None)
        dlc.sink(b'AT+XAPL=0000-0000-0100,2\rAT+IPHONEACCEV=1,1,7\r')
        self.assertIn('\r\n+XAPL=iPhone,2\r\n', dlc.writes)
        self.assertEqual(values[-1]['percent'], 80)
        count = len(values)
        dlc.sink(b'AT+IPHONEACCEV=2,1,99\rAT+BIEV=2,101\rAT+BIEV=x,y\r')
        self.assertEqual(len(values), count)
        self.assertIn('\r\nERROR\r\n', dlc.writes)


if __name__ == '__main__':
    unittest.main()
