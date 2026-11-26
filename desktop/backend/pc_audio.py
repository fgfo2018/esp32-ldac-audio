"""Experimental Windows LDAC source using ESP32 as a USB-UART HCI controller.

Uses Google's Bumble host stack and Sony's libldac. No kernel driver changes.
Only connects to an explicitly selected WH-1000XM4; scan never auto-connects.
"""
import argparse
import asyncio
import ctypes as C
import json
import logging
import math
from pathlib import Path
import struct
import time
import numpy as np
import soxr

import serial
import serial_asyncio
from bumble.a2dp import VendorSpecificMediaCodecInformation, SbcMediaCodecInformation, make_audio_source_service_sdp_records
from bumble.avdtp import MediaCodecCapabilities, MediaPacketPump, Protocol, RealtimeClock
from bumble.core import PhysicalTransport, AdvertisingData
from bumble.device import Device
from bumble import avrcp, avc
from bumble.rtp import MediaPacket
from bumble.transport.common import Transport, StreamPacketSink
from bumble.transport.serial import SerialPacketSource

ROOT = Path(__file__).resolve().parent
PCM_BLOCK = 128 * 2 * 4


class MonotonicClock(RealtimeClock):
    def now(self):
        return time.perf_counter()


class CaptureBuffer:
    """Prime on startup or after depletion while keeping the RTP clock running."""

    def __init__(self, queue, rate, delay_ms, block_size=PCM_BLOCK):
        self.queue = queue
        self.block_size = block_size
        self.target = math.ceil(rate * delay_ms * 8 / 1000 / block_size) * block_size
        self.prime_timeout = delay_ms / 1000 + 0.08
        self.buffer = bytearray()
        self.priming = True
        self.first_chunk_at = None
        self.rebuffers = 0
        self.removed_bytes = 0

    def read(self, now):
        size = self.block_size
        self.removed_bytes = 0
        required = self.target if self.priming else size
        while len(self.buffer) < required and not self.queue.empty():
            chunk = self.queue.get_nowait()
            self.removed_bytes += len(chunk)
            self.buffer.extend(chunk)
        if self.priming:
            if self.first_chunk_at is None and self.buffer:
                self.first_chunk_at = now
            if len(self.buffer) < required and (
                self.first_chunk_at is None or now - self.first_chunk_at < self.prime_timeout
            ):
                return bytes(size)
            self.priming = False
        if len(self.buffer) < size:
            self.priming = True
            self.first_chunk_at = None
            self.rebuffers += 1
            tail = bytes(self.buffer).ljust(size, b'\0')
            self.buffer.clear()
            return tail
        block = bytes(self.buffer[:size])
        del self.buffer[:size]
        return block


class Encoder:
    frame_samples = 128

    def __init__(self, rate=48000, quality=2, mtu=679):
        self.rate = rate
        self.dll = C.CDLL(str(ROOT / 'ldacBT_enc.dll'))
        self.dll.ldacBT_get_handle.restype = C.c_void_p
        self.dll.ldacBT_init_handle_encode.argtypes = [C.c_void_p] + [C.c_int] * 5
        self.dll.ldacBT_encode.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(C.c_int), C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int)]
        self.dll.ldacBT_get_error_code.argtypes = [C.c_void_p]
        self.dll.ldacBT_free_handle.argtypes = [C.c_void_p]
        self.dll.ldacBT_close_handle.argtypes = [C.c_void_p]
        self.handle = self.dll.ldacBT_get_handle()
        if not self.handle:
            raise RuntimeError('LDAC allocation failed')
        result = self.dll.ldacBT_init_handle_encode(self.handle, mtu, quality, 1, 5, self.rate)
        if result:
            error = self.dll.ldacBT_get_error_code(self.handle)
            self.close()
            raise RuntimeError(f'LDAC init: {error:x}')
        self.output = C.create_string_buffer(4096)

    def split_frames(self, payload, count):
        for _ in range(count):
            if len(payload) < 3 or payload[0] != 0xAA:
                raise ValueError('LDAC 封包缺少音訊框標頭')
            # Nine-bit body length, stored minus one, plus three header bytes.
            length = ((payload[1] & 7) << 6 | (payload[2] >> 2)) + 4
            if length > len(payload):
                raise ValueError('LDAC 音訊框長度錯誤')
            yield payload[:length]
            payload = payload[length:]
        if payload:
            raise ValueError('LDAC 音訊框數量錯誤')

    def encode(self, pcm):
        if len(pcm) != 128 * 2 * 4:
            raise ValueError('Expected 128 stereo float32 frames')
        used, length, frames = C.c_int(), C.c_int(), C.c_int()
        data = C.create_string_buffer(pcm)
        result = self.dll.ldacBT_encode(self.handle, data, C.byref(used), self.output, C.byref(length), C.byref(frames))
        if result:
            raise RuntimeError(f'LDAC encode: {self.dll.ldacBT_get_error_code(self.handle):x}')
        if used.value != len(pcm):
            raise RuntimeError(f'Unexpected PCM consumption: {used.value}')
        return self.output.raw[:length.value], frames.value

    def close(self):
        if self.handle:
            self.dll.ldacBT_close_handle(self.handle)
            self.dll.ldacBT_free_handle(self.handle)
            self.handle = None


async def transport(port, baud=921600):
    # Keep the dev board's auto-reset pins inactive. Reset once deliberately,
    # discard the ROM boot banner before handing bytes to the H4 parser.
    ser = serial.Serial(port=None, baudrate=baud, timeout=0)
    ser.port = port
    ser.dtr = False
    ser.rts = False
    tr=None
    try:
        ser.open()
        ser.rts = True
        await asyncio.sleep(0.1)
        ser.rts = False
        await asyncio.sleep(1.5)
        ser.reset_input_buffer()
        tr, source = await serial_asyncio.connection_for_serial(asyncio.get_running_loop(), SerialPacketSource, ser)
        await asyncio.wait_for(source.wait_until_ready(),5)
        return Transport(source, StreamPacketSink(tr))
    except BaseException:
        if tr:tr.close()
        if ser.is_open:ser.close()
        raise



def caps(rate):
    freq = {44100: 0x20, 48000: 0x10, 88200: 8, 96000: 4}[rate]
    return MediaCodecCapabilities(0, 0xff, VendorSpecificMediaCodecInformation(0x12d, 0xaa, bytes([freq, 1])))


async def scan(device, seconds):
    found = {}
    def result(address, cod, data, rssi):
        name = data.get(AdvertisingData.COMPLETE_LOCAL_NAME) or data.get(AdvertisingData.SHORTENED_LOCAL_NAME) or ''
        print(f'FOUND {address} name={name!r} rssi={rssi}', flush=True)
        found[str(address)] = {'name': name, 'rssi': rssi}
    device.on('inquiry_result', result)
    await device.start_discovery(auto_restart=False)
    await asyncio.sleep(seconds)
    await device.stop_discovery()
    (ROOT / 'scan-results.json').write_text(json.dumps(found, indent=2), encoding='utf-8')


async def packets(encoder, seconds, live=False, gain=0.1, buffer_ms=0, link_backlog=None):
    """Generate quiet test tone or WASAPI output loopback, never microphone."""
    total = seq = sent = frames_sent = 0
    pa = stream = None
    queue = asyncio.Queue(maxsize=128)
    frame_samples = encoder.frame_samples
    block_size = frame_samples * 8
    capture = CaptureBuffer(queue, encoder.rate, buffer_ms, block_size) if live and buffer_ms else None
    buffer = bytearray()
    dropped = 0
    queued_bytes = 0
    capture_peak = 0
    peak = 0.0
    serial_peak = acl_peak = last_sent = last_frames = 0
    loop = asyncio.get_running_loop()
    start = time.monotonic()
    if live:
        import pyaudiowpatch as p
        import numpy as np
        import soxr
        pa = p.PyAudio()
        selected = getattr(encoder, 'capture_index', None)
        info = pa.get_device_info_by_index(selected) if selected is not None else pa.get_default_wasapi_loopback()
        channels = int(info['maxInputChannels'])
        source_rate = int(info['defaultSampleRate'])
        resampler = soxr.ResampleStream(source_rate, encoder.rate, 2, dtype='float32') if source_rate != encoder.rate else None
        print(f'CAPTURE OUTPUT ONLY: {info["name"]}; gain={gain}', flush=True)
        def enqueue(data,chunk_peak):
            nonlocal dropped,peak,queued_bytes,capture_peak
            peak=max(peak,chunk_peak)
            meter=getattr(encoder,'meter',None)
            if meter:meter.push(chunk_peak)
            limit = (max(capture.target + encoder.rate * 8 * 60 // 1000
                         - len(capture.buffer), len(data)) if capture else None)
            while not queue.empty() and (queue.full() or (limit and queued_bytes + len(data) > limit)):
                queued_bytes -= len(queue.get_nowait())
                dropped += 1
            queue.put_nowait(data)
            queued_bytes += len(data)
            capture_peak = max(capture_peak, queued_bytes + (len(capture.buffer) if capture else len(buffer)))
        def callback(data, frame_count, time_info, status):
            floats = np.frombuffer(data, dtype=np.float32).reshape(-1, channels)
            if channels == 1:
                floats = np.repeat(floats, 2, axis=1)
            elif channels > 2:
                floats = floats[:, :2].copy()
            if resampler:
                floats = resampler.resample_chunk(floats)
            if len(floats):
                loop.call_soon_threadsafe(enqueue, floats.astype(np.float32).tobytes(),float(np.max(np.abs(floats))))
            return (None, p.paContinue)
        stream = pa.open(format=p.paFloat32, channels=channels, rate=source_rate,
                         input=True, input_device_index=info['index'],
                         frames_per_buffer=480, stream_callback=callback)
    try:
        while total < seconds * encoder.rate:
            if live:
                if capture:
                    raw = capture.read(loop.time())
                    queued_bytes -= capture.removed_bytes
                else:
                    while len(buffer) < block_size:
                        try:
                            chunk = await asyncio.wait_for(queue.get(), 0.03)
                            queued_bytes -= len(chunk)
                            buffer.extend(chunk)
                        except TimeoutError:
                            # WASAPI can stop delivering when no audio is playing.
                            buffer.extend(bytes(1440 * 2 * 4))
                    raw = bytes(buffer[:block_size]); del buffer[:block_size]
                actual_gain = getattr(encoder, 'gain', gain)
                pcm = np.clip(np.frombuffer(raw, dtype='<f4') * actual_gain, -1, 1).tobytes()
            else:
                samples = [gain * math.sin(2 * math.pi * 440 * (total + i) / encoder.rate) for i in range(frame_samples)]
                pcm = struct.pack(f'<{frame_samples * 2}f', *(x for v in samples for x in (v, v)))
            payload, nframes = encoder.encode(pcm)
            total += frame_samples
            if not payload:
                continue
            sent += len(payload)
            timestamp = frames_sent
            frames_sent += nframes * frame_samples
            mtu = getattr(encoder, 'media_mtu', 1000)
            if mtu <= 12:
                raise ValueError('耳機的音訊封包大小無效')
            if getattr(encoder, 'allow_fragmentation', False):
                size = mtu - 12
                chunks = ((timestamp, int(offset + size >= len(payload)), payload[offset:offset+size])
                          for offset in range(0, len(payload), size))
            elif len(payload) + 13 <= mtu:
                chunks = ((timestamp, 0, bytes([nframes]) + payload),)
            elif isinstance(encoder, Encoder):
                chunks = ((timestamp + index * frame_samples, 0, b'\x01' + frame)
                          for index, frame in enumerate(encoder.split_frames(payload, nframes)))
            else:
                raise ValueError('耳機封包大小不足，無法傳送選擇的編碼')
            for sample_time, marker, chunk in chunks:
                if len(chunk) + 12 > mtu:
                    raise ValueError('耳機封包大小不足，無法傳送選擇的編碼')
                packet = MediaPacket(2, 0, 0, marker, seq, sample_time, 1, [], 96, chunk)
                packet.timestamp_seconds = sample_time / encoder.rate
                seq += 1
                yield packet
            if link_backlog:
                serial_bytes, acl_pending = link_backlog()
                serial_peak = max(serial_peak, serial_bytes)
                acl_peak = max(acl_peak, acl_pending)
            if frames_sent - last_frames >= encoder.rate * 2:
                kbps = (sent - last_sent) * 8 * encoder.rate / ((frames_sent - last_frames) * 1000)
                print(f'STREAM packets={seq} encoded_bytes={sent} audio_seconds={frames_sent/encoder.rate:.2f} '
                      f'capture_drops={dropped} input_peak={peak:.4f} encoded_kbps={kbps:.1f} '
                      f'capture_rebuffers={capture.rebuffers if capture else 0} '
                      f'capture_peak_ms={capture_peak * 1000 / (encoder.rate * 8):.1f} '
                      f'serial_peak_bytes={serial_peak} acl_peak_packets={acl_peak}', flush=True)
                last_sent, last_frames = sent, frames_sent
                serial_peak = acl_peak = capture_peak = 0
                peak=0.0
        print(f'STREAM COMPLETE packets={seq} encoded_bytes={sent} elapsed={time.monotonic()-start:.2f}s capture_drops={dropped}', flush=True)
    finally:
        if stream:
            stream.stop_stream(); stream.close()
        if pa:
            pa.terminate()


async def run(args):
    async with await transport(args.port) as tr:
        config = ROOT / 'device.json'
        device = Device.from_config_file_with_hci(str(config), tr.source, tr.sink)
        device.classic_enabled = True
        device.le_enabled = False
        device.sdp_service_records = {0x10001: make_audio_source_service_sdp_records(0x10001)}
        remote_control = None
        if args.mode == 'volume':
            device.sdp_service_records[0x10002] = avrcp.ControllerServiceSdpRecord(0x10002).to_service_attributes()
            remote_control = avrcp.Protocol()
            remote_control.listen(device)
        await asyncio.wait_for(device.power_on(), 15)
        print(f'CONTROLLER READY {device.public_address}', flush=True)
        if args.mode == 'probe':
            return
        if args.mode == 'scan':
            await scan(device, args.seconds)
            return
        if not args.address:
            raise RuntimeError('--address is required; no auto-connect')
        print(f'CONNECTING {args.address}', flush=True)
        connection = await asyncio.wait_for(device.connect(args.address, transport=PhysicalTransport.BR_EDR), 30)
        print('ACL CONNECTED; authenticating', flush=True)
        await asyncio.wait_for(connection.authenticate(), 30)
        await connection.encrypt()
        print('AUTHENTICATED AND ENCRYPTED', flush=True)
        if args.mode == 'volume':
            await asyncio.wait_for(remote_control.connect(connection), 10)
            events = await asyncio.wait_for(remote_control.get_supported_events(), 5)
            print(f'AVRCP SUPPORTED EVENTS: {events}', flush=True)
            if avrcp.EventId.VOLUME_CHANGED in events:
                monitor = remote_control.monitor_volume()
                current = await asyncio.wait_for(anext(monitor), 5)
                print(f'HEADPHONE VOLUME {current}/127 ({current*100/127:.1f}%)', flush=True)
                await monitor.aclose()
            if args.volume is not None:
                response = await asyncio.wait_for(remote_control.send_avrcp_command(
                    avc.CommandFrame.CommandType.CONTROL,
                    avrcp.SetAbsoluteVolumeCommand(round(args.volume*127/100))), 5)
                if not isinstance(response.response, avrcp.SetAbsoluteVolumeResponse):
                    raise RuntimeError(f'Volume command rejected: {response}')
                value = response.response.volume
                print(f'HEADPHONE VOLUME ACCEPTED {value}/127 ({value*100/127:.1f}%)', flush=True)
            await connection.disconnect()
            return
        protocol = await Protocol.connect(connection)
        endpoints = await protocol.discover_remote_endpoints()
        for endpoint in endpoints:
            print(f'ENDPOINT seid={endpoint.seid} in_use={endpoint.in_use} capabilities={endpoint.capabilities}', flush=True)
        if args.mode == 'hold':
            sink = protocol.find_remote_sink_by_codec(0, 0)
            if sink is None:
                raise RuntimeError('No available SBC sink for silent connection check')
            S = SbcMediaCodecInformation
            codec = MediaCodecCapabilities(0, 0, S(
                sampling_frequency=S.SamplingFrequency.SF_48000,
                channel_mode=S.ChannelMode.JOINT_STEREO,
                block_length=S.BlockLength.BL_16,
                subbands=S.Subbands.S_8,
                allocation_method=S.AllocationMethod.LOUDNESS,
                minimum_bitpool_value=2, maximum_bitpool_value=53))
            async def no_audio():
                if False:
                    yield None
            source = protocol.add_source(codec, MediaPacketPump(no_audio()))
            stream = await protocol.create_stream(source, sink)
            await stream.open()
            print(f'A2DP MEDIA CHANNEL OPEN; holding {args.seconds}s; NO AUDIO SENT', flush=True)
            await asyncio.sleep(args.seconds)
            await stream.close()
            await connection.disconnect()
            print('HOLD TEST COMPLETE; disconnected', flush=True)
            return
        if args.mode == 'pair':
            print('PAIRING VERIFIED; no audio sent', flush=True)
            await connection.disconnect()
            return
        sink = protocol.find_remote_sink_by_codec(0, 0xff, 0x12d, 0xaa)
        if sink is None:
            raise RuntimeError('No available LDAC endpoint; check XM4 multipoint setting')
        print(f'LDAC SINK FOUND seid={sink.seid}', flush=True)
        encoder = Encoder(48000, args.quality)
        pump = MediaPacketPump(packets(encoder, args.seconds, args.mode == 'live', args.gain))
        source = protocol.add_source(caps(48000), pump)
        stream = await protocol.create_stream(source, sink)
        try:
            await stream.start()
            print('LDAC STREAM STARTED 48000Hz stereo', flush=True)
            await asyncio.wait_for(pump.wait_for_completion(), args.seconds + 15)
            if pump.pump_task and pump.pump_task.done():
                error = pump.pump_task.exception()
                if error:
                    raise error
            await stream.close()
        finally:
            await pump.stop()
            encoder.close()
            await connection.disconnect()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['probe', 'scan', 'pair', 'hold', 'tone', 'live', 'volume', 'encoder-test'])
    parser.add_argument('--port', default='COM6')
    parser.add_argument('--address')
    parser.add_argument('--seconds', type=int, default=15)
    parser.add_argument('--quality', type=int, choices=[0, 1, 2], default=2)
    parser.add_argument('--gain', type=float, default=0.05)
    parser.add_argument('--volume', type=int, choices=range(101), metavar='0..100')
    args = parser.parse_args()
    if not 0 <= args.gain <= 0.25:
        parser.error('gain must be between 0 and 0.25')
    logging.basicConfig(level=logging.WARNING)
    if args.mode == 'encoder-test':
        enc = Encoder()
        frames = size = 0
        for _ in range(375):
            payload, count = enc.encode(bytes(1024))
            frames += count; size += len(payload)
        enc.close()
        print(f'LDAC encoder test: frames={frames}, bytes={size}, kbps={size*8/1000:.1f}')
        assert frames > 350 and 30000 < size < 45000
    else:
        asyncio.run(run(args))
