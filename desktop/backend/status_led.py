"""Optional, versioned HCI bridge LED control, compatible with older firmware."""
import asyncio

from bumble import hci

OPCODE = 0xFCF0
STATES = {'idle': 0, 'scanning': 1, 'connecting': 2, 'paired': 3,
          'streaming': 4, 'error': 5, 'reconnecting': 6, 'waiting': 5}


class StatusLed:
    def __init__(self, device, log):
        self.device = device
        self.log = log
        self.state = 'idle'
        self.enabled = False
        self.changed = asyncio.Event()
        self.task = None
        self.stopped = False

    async def send(self, state):
        command = hci.HCI_Command(op_code=OPCODE, parameters=b'LEDS' + bytes([STATES[state]]))
        result = await self.device.host.send_sync_command(command, response_timeout=2)
        return bytes(result) == b'\0LEDS\x01'

    async def start(self, state):
        self.state = state if state in STATES else 'idle'
        try:
            self.enabled = await self.send(self.state)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.log('LED control unavailable:', type(error).__name__)
        self.log('LED feedback:', 'GPIO2 ready' if self.enabled else 'firmware update required')
        if self.enabled:
            self.task = asyncio.create_task(self.run())

    def update(self, state):
        if state in STATES:
            self.state = state
            self.changed.set()

    async def run(self):
        try:
            while not self.stopped:
                try:
                    await asyncio.wait_for(self.changed.wait(), 2)
                except TimeoutError:
                    pass
                self.changed.clear()
                if self.stopped:
                    break
                if not await self.send(self.state):
                    raise RuntimeError('LED command rejected')
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.log('LED feedback stopped:', type(error).__name__)

    async def close(self, failed=False, transport_lost=False):
        self.stopped = True
        self.changed.set()
        if self.task:
            if transport_lost:
                self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.enabled and not transport_lost:
            try:
                if failed:
                    await self.send('error')
                await self.send('idle')
            except Exception as error:
                self.log('LED cleanup:', type(error).__name__)
