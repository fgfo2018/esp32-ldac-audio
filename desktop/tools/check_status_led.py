"""Read GPIO2 back from a real bridge and check every LED pattern."""
import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import pc_audio as audio
from bumble import hci
from bumble.device import Device
from status_led import OPCODE, STATES, StatusLed


def expected(state, elapsed):
    phase = elapsed % 1000
    return {
        0: elapsed % 3000 < 100,
        1: elapsed % 400 < 200,
        2: phase < 100 or 200 <= phase < 300,
        3: True,
        4: elapsed % 2000 < 1900,
        5: phase < 100 or 200 <= phase < 300 or 400 <= phase < 500,
        6: phase < 500,
    }[state]


async def read_status(device):
    result = await device.host.send_sync_command(
        hci.HCI_Command(op_code=OPCODE, parameters=b'LED?'), response_timeout=2)
    data = bytes(result)
    assert data[:6] == b'\0LEDS\x01' and len(data) == 10, data.hex()
    return data[6], data[7], int.from_bytes(data[8:], 'little')


async def main(port):
    async with await audio.transport(port, 2000000) as transport:
        device = Device.with_hci('ESP32 LED check', hci.Address('F0:F1:F2:F3:F4:F5'),
                                 transport.source, transport.sink)
        await device.power_on()
        led = StatusLed(device, print)
        try:
            # Direct commands allow testing the firmware's no-heartbeat recovery too.
            for name, duration in [('idle', 3.2), ('scanning', 1.3), ('connecting', 1.3),
                                   ('paired', .6), ('streaming', 2.2), ('reconnecting', 1.3),
                                   ('error', 1.3)]:
                assert await led.send(name)
                samples = 0
                levels = set()
                until = time.monotonic() + duration
                while time.monotonic() < until:
                    state, level, elapsed = await read_status(device)
                    assert state == STATES[name], (name, state)
                    # The independent LED task updates every 10 ms.
                    if elapsed > 20 and expected(state, elapsed - 20) == expected(state, elapsed + 20):
                        assert bool(level) == expected(state, elapsed), (name, level, elapsed)
                        samples += 1
                        levels.add(level)
                    await asyncio.sleep(.025)
                assert samples >= 10, (name, samples)
                assert levels == ({1} if name == 'paired' else {0, 1}), (name, levels)
                print(f'PASS {name}: GPIO2 readback, {samples} samples')
            await led.send('idle')
            await asyncio.sleep(4)
            assert (await read_status(device))[0] == 0, 'error did not expire after 5 seconds'
            await led.send('streaming')
            await asyncio.sleep(8.2)
            assert (await read_status(device))[0] == 5, 'watchdog did not signal error'
            await asyncio.sleep(5.2)
            assert (await read_status(device))[0] == 0, 'watchdog did not recover to idle'
            print('PASS five-second error overlay and eight-second heartbeat watchdog')
        finally:
            await led.send('idle')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='COM6')
    asyncio.run(main(parser.parse_args().port))
