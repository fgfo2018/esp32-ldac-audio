"""AAC-LC and SBC encoder setup based on the remote A2DP capabilities."""
import math

import av
import numpy as np
from bumble.a2dp import AacMediaCodecInformation as Aac, SbcMediaCodecInformation as Sbc
from bumble.avdtp import MediaCodecCapabilities
from bumble.codecs import AacAudioRtpPacket

CODEC_IDS = {'LDAC': (255, 301, 170), 'AAC': (2, 0, 0), 'SBC': (0, 0, 0)}


class PcmEncoder:
    def __init__(self, name, rate, bitrate, format):
        self.rate = rate
        self.format = format
        self.context = av.CodecContext.create(name.lower(), 'w')
        self.context.sample_rate = rate
        self.context.layout = 'stereo'
        self.context.format = format
        self.context.bit_rate = bitrate
        self.context.open()
        self.frame_samples = self.context.frame_size

    def encode_frame(self, pcm):
        samples = np.frombuffer(pcm, dtype='<f4')
        if self.format == 's16':
            samples = np.clip(samples * 32768, -32768, 32767).astype(np.int16)[None, :]
        else:
            samples = np.ascontiguousarray(samples.reshape(-1, 2).T)
        frame = av.AudioFrame.from_ndarray(samples, format=self.format, layout='stereo')
        frame.sample_rate = self.rate
        return self.context.encode(frame)

    def close(self):
        self.context = None


class AacEncoder(PcmEncoder):
    media_header = False
    allow_fragmentation = True

    def __init__(self, rate, bitrate):
        super().__init__('AAC', rate, bitrate, 'fltp')

    def encode(self, pcm):
        frames = self.encode_frame(pcm)
        if not frames:
            return b'', 0
        (frame,) = frames
        return bytes(AacAudioRtpPacket.for_simple_aac(self.rate, 2, bytes(frame))), 1


class SbcEncoder(PcmEncoder):
    def __init__(self, rate, bitrate):
        super().__init__('SBC', rate, bitrate, 's16')
        self.group = bytearray()
        self.count = 0

    def encode(self, pcm):
        (frame,) = self.encode_frame(pcm)
        self.group.extend(bytes(frame))
        self.count += 1
        if self.count < self.frames_per_packet:
            return b'', 0
        payload, count = bytes(self.group), self.count
        self.group.clear()
        self.count = 0
        return payload, count


def configure_aac(info):
    objects = info.object_type & (Aac.ObjectType.MPEG_2_AAC_LC | Aac.ObjectType.MPEG_4_AAC_LC)
    if not objects or not info.channels & Aac.Channels.STEREO:
        return None
    for rate in (48000, 44100):
        frequency = Aac.SamplingFrequency.from_int(rate)
        if not info.sampling_frequency & frequency:
            continue
        bitrate = min(info.bitrate or 192000, 192000)
        object_type = (objects & Aac.ObjectType.MPEG_4_AAC_LC) or Aac.ObjectType.MPEG_2_AAC_LC
        chosen = Aac(object_type, frequency, Aac.Channels.STEREO, 0, bitrate)
        return AacEncoder(rate, bitrate), MediaCodecCapabilities(0, 2, chosen)
    return None


def configure_sbc(info):
    for rate in (48000, 44100, 32000, 16000):
        frequency = Sbc.SamplingFrequency.from_int(rate)
        if not info.sampling_frequency & frequency:
            continue
        min_pool = max(2, info.minimum_bitpool_value)
        max_pool = min(53, info.maximum_bitpool_value)
        for pool in range(max_pool, min_pool - 1, -1):
            # Estimate bitrate for 16 blocks and 8 subbands, then verify the actual frame.
            bitrate = math.ceil((12 + 2 * pool) * 8 * rate / 128)
            encoder = SbcEncoder(rate, bitrate)
            (frame,) = encoder.encode_frame(bytes(encoder.frame_samples * 8))
            _, flags, actual_pool = bytes(frame)[:3]
            mode = Sbc.ChannelMode(8 >> ((flags >> 2) & 3))
            blocks = Sbc.BlockLength(8 >> ((flags >> 4) & 3))
            subbands = Sbc.Subbands.S_8 if flags & 1 else Sbc.Subbands.S_4
            allocation = Sbc.AllocationMethod.SNR if flags & 2 else Sbc.AllocationMethod.LOUDNESS
            if (min_pool <= actual_pool <= max_pool and info.channel_mode & mode
                    and info.block_length & blocks and info.subbands & subbands
                    and info.allocation_method & allocation):
                # Fresh encoder so the capability probe does not enter the stream.
                frame_length = frame.size
                encoder.close()
                encoder = SbcEncoder(rate, bitrate)
                encoder.frames_per_packet = min(4, (672 - 13) // frame_length)
                encoder.frame_length = frame_length
                chosen = Sbc(frequency, mode, blocks, subbands, allocation, actual_pool, actual_pool)
                return encoder, MediaCodecCapabilities(0, 0, chosen)
            encoder.close()
    return None
