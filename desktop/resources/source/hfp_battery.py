"""Experimental HFP Audio Gateway: control-only headset battery reporting.

No calls, codec negotiation, or SCO audio are initiated. AG CIND battchg is the
gateway's battery, NOT the headset's. Only HF BIEV/Apple accessory reports count.
"""
import asyncio
import re
from bumble import hfp, rfcomm


def configuration():
    return hfp.AgConfiguration(
        supported_ag_features=[hfp.AgFeature.HF_INDICATORS],
        supported_ag_indicators=[hfp.AgIndicatorState.service(),
            hfp.AgIndicatorState.call(), hfp.AgIndicatorState.callsetup(),
            hfp.AgIndicatorState.callheld(), hfp.AgIndicatorState.signal(),
            hfp.AgIndicatorState.roam(), hfp.AgIndicatorState.battchg()],
        supported_hf_indicators=[hfp.HfIndicator.BATTERY_LEVEL],
        supported_ag_call_hold_operations=[],
        supported_audio_codecs=[hfp.AudioCodec.CVSD])


class BatteryGateway(hfp.AgProtocol):
    def __init__(self, dlc, notify, log):
        self.notify_battery = notify
        self.log = log
        self.ready = asyncio.Event()
        self.reported = asyncio.Event()
        self.closed = asyncio.Event()
        self.last_battery = None
        self.cmer_received = False
        # Install handlers before binding the sink: DLC may hold queued AT data.
        super().__init__(dlc, configuration())
        self.on(self.EVENT_HF_INDICATOR, self.indicator)
        dlc.on('close', self.closed.set)

    def _read_at(self, data):
        for command in re.findall(rb'AT\+([A-Z]+)', data):
            self.log('HFP AT', command.decode('ascii'))
        if len(self.read_buffer) + len(data) > 4096:
            self.read_buffer.clear()
            self.send_error()
            return
        try:
            super()._read_at(data)
        except (ValueError, TypeError, IndexError, KeyError):
            self.read_buffer.clear()
            self.send_error()

    def _on_brsf(self, features):
        self.log('HFP HF features', int(features))
        super()._on_brsf(features)

    def _check_remained_slc_commands(self):
        if self.cmer_received and not self._remained_slc_setup_features:
            super()._check_remained_slc_commands()
            self.ready.set()

    def _on_cmer(self, mode, keypad=None, display=None, indicator=b''):
        if int(mode) != 3 or (keypad and int(keypad)) or (display and int(display)) or int(indicator) not in (0, 1):
            self.send_error()
            return
        super()._on_cmer(mode, keypad, display, indicator)
        self.cmer_received = True
        self._check_remained_slc_commands()

    def report(self, percent, source):
        if not 0 <= percent <= 100:
            return
        self.last_battery = {'percent': percent, 'charging': None, 'source': source}
        self.reported.set()
        self.notify_battery(self.last_battery)

    def indicator(self, state):
        if state.indicator == hfp.HfIndicator.BATTERY_LEVEL:
            self.report(state.current_status, 'HFP HF indicator')

    def _on_biev(self, index, value):
        # BIEV=2 is the HEADSET battery, in percent.
        if int(index) == 2 and not 0 <= int(value) <= 100:
            self.send_error()
            return
        super()._on_biev(index, value)
        # Also handles a report queued while the parent constructor bound sink.
        if int(index) == 2 and hfp.HfIndicator.BATTERY_LEVEL in self.hf_indicators:
            if not self.reported.is_set():
                self.report(int(value), 'HFP HF indicator')

    def _on_xapl(self, device_id, features):
        # Apple accessory extension: advertise battery reporting (bit 1) only.
        int(features)
        # XM4 interoperability: this exact legacy product token is used by
        # PipeWire/BlueZ traces; it does not change our Bluetooth device name.
        self.send_response('+XAPL=iPhone,2')
        self.send_ok()

    def _on_iphoneaccev(self, count, *fields):
        count = int(count)
        if count < 0 or len(fields) != count * 2:
            self.send_error()
            return
        pairs = [(int(fields[i]), int(fields[i+1])) for i in range(0, len(fields), 2)]
        if any(key == 1 and not 0 <= value <= 9 for key, value in pairs):
            self.send_error()
            return
        for key, value in pairs:
            if key == 1:
                self.report((value + 1) * 10, 'HFP Apple accessory (10% steps)')
        self.send_ok()


class HfpBatteryService:
    def __init__(self, device, log=lambda *args: None):
        self.log = log
        self.sessions = {}
        self.listeners = {}
        self.server = rfcomm.Server(device)
        channel = self.server.listen(self.accept)
        device.sdp_service_records[0x10003] = hfp.make_ag_sdp_records(
            0x10003, channel, configuration())

    def accept(self, dlc):
        connection = dlc.multiplexer.l2cap_channel.connection
        def notify(value):
            if callback := self.listeners.get(connection):
                callback(value)
        protocol = BatteryGateway(dlc, notify, self.log)
        self.sessions[connection] = protocol
        def closed():
            if self.sessions.get(connection) is protocol:
                self.sessions.pop(connection, None)
        dlc.on('close', closed)
        self.log('HFP control RFCOMM connected; no SCO requested')
        return protocol

    async def monitor(self, connection, notify, stop):
        client = None
        protocol = None
        self.listeners[connection] = notify
        try:
            protocol = self.sessions.get(connection)
            if protocol is None:
                record = await asyncio.wait_for(hfp.find_hf_sdp_record(connection), 6)
                if not record:
                    raise RuntimeError('耳機沒有提供 HFP Hands-Free 服務')
                channel, version, features = record
                self.log('HFP service', 'version', version, 'features', features)
                client = rfcomm.Client(connection)
                mux = await asyncio.wait_for(client.start(), 5)
                dlc = await asyncio.wait_for(mux.open_dlc(channel), 5)
                protocol = self.accept(dlc)
            if protocol.last_battery:
                notify(protocol.last_battery)
            await asyncio.wait_for(protocol.ready.wait(), 15)
            self.log('HFP SLC complete; waiting for headset battery')
            await asyncio.wait_for(protocol.reported.wait(), 15)
            stopped = asyncio.create_task(stop.wait())
            closed = asyncio.create_task(protocol.closed.wait())
            try:
                await asyncio.wait([stopped, closed], return_when=asyncio.FIRST_COMPLETED)
            finally:
                stopped.cancel(); closed.cancel()
                await asyncio.gather(stopped, closed, return_exceptions=True)
            if not stop.is_set():
                raise RuntimeError('HFP 電量控制通道已中斷')
        finally:
            self.listeners.pop(connection, None)
            if protocol:
                self.sessions.pop(connection, None)
                try:
                    await asyncio.wait_for(protocol.dlc.disconnect(), 2)
                except Exception:
                    pass
            if client:
                try:
                    await asyncio.wait_for(client.shutdown(), 2)
                except Exception:
                    pass
