import asyncio,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from meter import PeakMeter
from engine import Engine

class WindowedPeak(unittest.TestCase):
    def test_windows_reset_and_quiet_audio_is_not_stuck_at_old_peak(self):
        meter=PeakMeter();meter.push(.8);meter.push(.2)
        self.assertEqual(meter.take(),.8)
        meter.push(.05);self.assertEqual(meter.take(),.05)
        self.assertEqual(meter.take(),0)
    def test_nonfinite_samples_and_overload(self):
        meter=PeakMeter()
        for value in [float('nan'),float('inf'),-2]:meter.push(value)
        self.assertEqual(meter.take(),0)
        meter.push(2);self.assertEqual(meter.take(),1)

class IndependentMeter(unittest.IsolatedAsyncioTestCase):
    async def test_updates_at_least_15_hz_and_resets_on_stop(self):
        meter=PeakMeter();meter.push(.8);stop=asyncio.Event();events=[]
        loop=asyncio.get_running_loop()
        with patch('engine.emit',lambda kind,**data:events.append((loop.time(),kind,data))):
            task=asyncio.create_task(Engine.meter_loop(None,meter,stop))
            await asyncio.sleep(.35);stop.set();await task
        self.assertGreaterEqual(len(events)-1,5)
        self.assertEqual(events[0][2]['peak'],.8)
        self.assertTrue(all(e[2]['peak']==0 for e in events[1:]))

if __name__=='__main__':unittest.main()
