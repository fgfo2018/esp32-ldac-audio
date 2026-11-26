import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from a2dp_codecs import Aac, Sbc, configure_aac, configure_sbc
from bumble.codecs import AacAudioRtpPacket
from pc_audio import Encoder, packets


class A2dpPackets(unittest.IsolatedAsyncioTestCase):
    async def test_aac_latm_fragments_share_timestamp(self):
        encoder, config = configure_aac(Aac.from_bytes(bytes.fromhex('c0010482ee00')))
        encoder.media_mtu = 80
        try:
            fragments = []
            async for packet in packets(encoder, .1):
                self.assertLessEqual(len(bytes(packet)), 80)
                fragments.append(packet)
                if packet.marker:
                    break
            self.assertEqual(config.media_codec_information.object_type, Aac.ObjectType.MPEG_4_AAC_LC)
            self.assertGreater(len(fragments), 1)
            self.assertEqual({packet.timestamp for packet in fragments}, {0})
            self.assertEqual([packet.marker for packet in fragments], [0] * (len(fragments) - 1) + [1])
            parsed = AacAudioRtpPacket.from_bytes(b''.join(packet.payload for packet in fragments))
            self.assertTrue(parsed.audio_mux_element.payload)
        finally:
            encoder.close()

    async def test_sbc_frame_matches_negotiated_bitpool(self):
        encoder, config = configure_sbc(Sbc.from_bytes(bytes.fromhex('3fff0223')))
        info = config.media_codec_information
        try:
            async for packet in packets(encoder, .035):
                count = packet.payload[0]
                self.assertEqual(len(packet.payload), 1 + count * encoder.frame_length)
                for offset in range(1, len(packet.payload), encoder.frame_length):
                    self.assertEqual(packet.payload[offset], 0x9c)
                    self.assertEqual(packet.payload[offset + 2], info.maximum_bitpool_value)
                    self.assertLessEqual(info.maximum_bitpool_value, 0x23)
        finally:
            encoder.close()

    async def test_ldac_990_splits_at_frame_boundary_for_672_byte_mtu(self):
        encoder = Encoder(48000, 0)
        encoder.media_mtu = 672
        try:
            timestamps = []
            async for packet in packets(encoder, .03):
                self.assertLessEqual(len(bytes(packet)), 672)
                self.assertEqual(packet.payload[:2], b'\x01\xaa')
                timestamps.append(packet.timestamp)
            self.assertGreater(len(timestamps), 1)
            self.assertEqual(timestamps, list(range(0, 128 * len(timestamps), 128)))
        finally:
            encoder.close()
