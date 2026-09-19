from array import array
import json
from pathlib import Path
import struct
import tempfile
import unittest
import wave

from voice_tools.audio.rtp import decode_g711, parse_rtp, reconstruct, segments
from voice_tools.tools.report.rtcp import parse_compound, enrich
from voice_tools.tools.report.service import build
from tests.sessions.fixtures import pcap, packet, T0


def rtp(seq, stamp=None, pt=0, payload=None, ssrc=111):
    return struct.pack('!BBHII',0x80,pt,seq,seq*160 if stamp is None else stamp,ssrc)+(b'\xff'*160 if payload is None else payload)


class MediaV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def test_g711_known_codewords(self):
        def values(data):return struct.unpack('<'+'h'*(len(data)//2),data)
        self.assertEqual(values(decode_g711(bytes([255,127,0,128]),'PCMU')),(0,0,-32124,32124))
        self.assertEqual(values(decode_g711(bytes([213,85,170,42]),'PCMA')),(8,-8,32256,-32256))

    def test_rtp_header_extensions_and_padding(self):
        raw=bytearray(rtp(1,payload=b'abc'));raw[0]=0x90
        raw=raw[:12]+bytes.fromhex('bede0001')+b'1234'+raw[12:]
        self.assertEqual(parse_rtp(raw)['payload'],b'abc')
        raw[0]|=32;raw+=b'\x00\x02'
        self.assertEqual(parse_rtp(raw)['payload'],b'abc')
        self.assertIsNone(parse_rtp(b'\x80'))

    def test_audio_reconstruction_has_two_distinct_timing_models(self):
        packets=[]
        for seq in (1,2,4,4):
            value=parse_rtp(rtp(seq));value['codec']={'name':'PCMU','clock_rate':8000};packets.append(value)
        result,used=reconstruct(segments(packets)[0],self.root,'test',1048576)
        self.assertEqual(result['inserted_silence_samples'],160)
        self.assertEqual(result['duplicates_ignored'],1)
        counts=[]
        for file in result['files']:
            with wave.open(str(self.root/file['file'])) as stream:counts.append(stream.getnframes())
        self.assertEqual(counts,[480,640])
        result,_=reconstruct(segments(packets)[0],self.root,'limited',10)
        self.assertEqual(result['status'],'limited');self.assertEqual(result['files'],[])

    def test_srtp_and_unknown_codecs_never_become_fake_audio(self):
        p=parse_rtp(rtp(1));p['codec']={'name':'PCMU','clock_rate':8000};p['encrypted']=True
        value,_=reconstruct(segments([p])[0],self.root,'secret',1048576)
        self.assertEqual(value['status'],'unsupported');self.assertEqual(value['files'],[])
        p['encrypted']=False;p['codec']={'name':'OPUS','clock_rate':48000}
        value,_=reconstruct(segments([p])[0],self.root,'opus',1048576)
        self.assertEqual(value['status'],'unsupported')

    def test_rtcp_sr_rr_clock_units_and_rtt(self):
        msw,lsw=3900000000,0x12340000
        sr=struct.pack('!BBHIIIIII',0x80,200,6,111,msw,lsw,320,2,320)
        lsr=((msw&65535)<<16)|(lsw>>16)
        block=struct.pack('!I',111)+bytes([64])+(-2).to_bytes(3,'big',signed=True)+struct.pack('!IIII',4,80,lsr,32768)
        rr=struct.pack('!BBHI',0x81,201,7,222)+block
        result=enrich(parse_compound(sr,T0)+parse_compound(rr,T0+1.5),{111:8000})
        report=result[1]['report_blocks'][0]
        self.assertEqual(report['fraction_lost'],.25)
        self.assertEqual(report['cumulative_lost'],-2)
        self.assertEqual(report['jitter_ms'],10)
        self.assertEqual(report['capture_point_rtt_estimate_ms'],1000)
        unknown=enrich(parse_compound(rr,T0+1.5),{})[0]['report_blocks'][0]
        self.assertIsNone(unknown['jitter_ms'])
        self.assertNotIn('capture_point_rtt_estimate_ms',unknown)
        with self.assertRaises(ValueError):parse_compound(rr[:-1],T0)

    def test_rtcp_xr_mos_is_endpoint_report_not_prediction(self):
        payload=bytearray(32);struct.pack_into('!I',payload,0,111);payload[22]=42;payload[23]=127
        xr=struct.pack('!BBHI',0x80,207,10,222)+struct.pack('!BBH',7,0,8)+payload
        result=parse_compound(xr,T0)[0]['xr_blocks'][0]
        self.assertEqual(result['endpoint_mos_lq'],4.2);self.assertIsNone(result['endpoint_mos_cq'])

    def test_continuous_files_fix_boundary_loss_without_merging_sensors(self):
        a,b=self.root/'a.pcap',self.root/'b.pcap'
        pcap(a,[(0,packet(rtp(1),16000,24000)),(.02,packet(rtp(2),16000,24000))])
        pcap(b,[(.06,packet(rtp(4),16000,24000)),(.08,packet(rtp(5),16000,24000))])
        grouped=build(self.root/'continuous',rtp_ports=[16000,24000],pcap_groups=[('fs-a',[a,b])],decode_rtp=True)
        self.assertFalse(grouped['partial'],grouped)
        self.assertEqual(len(grouped['pcaps']),1)
        analysis=grouped['pcaps'][0]['analysis']
        self.assertEqual(analysis['streams'][0]['sequence_gap_candidates'],1)
        self.assertTrue(analysis['continuous'])
        self.assertEqual(len(analysis['media']['rtp_audio'][0]['files']),2)
        independent=build(self.root/'independent',pcap_paths=[a,b],rtp_ports=[16000,24000])
        self.assertEqual(len(independent['pcaps']),2)
        self.assertEqual(sum(p['analysis']['streams'][0]['sequence_gap_candidates'] for p in independent['pcaps']),0)
        self.assertIn('RTP 音频重建',(self.root/'continuous/report.html').read_text())

    def test_real_tshark_rtcp_compound_and_audio(self):
        source=self.root/'control.pcap'
        sr=struct.pack('!BBHIIIIII',0x80,200,6,111,3900000000,0,320,2,320)
        rr=struct.pack('!BBHI',0x81,201,7,222)+struct.pack('!I',111)+b'\x00\x00\x00\x01'+struct.pack('!IIII',4,80,0,0)
        pcap(source,[(0,packet(rtp(1),16000,24000)),(.1,packet(sr+rr,16001,24001))])
        data=build(self.root/'report',pcap_paths=[source],rtp_ports=[16000,24000],rtcp_ports=[16001,24001],decode_rtp=True)
        self.assertEqual(data['errors'],0,data)
        media=data['pcaps'][0]['analysis']['media']
        self.assertEqual([r['packet_type'] for r in media['rtcp']],[200,201])
        self.assertEqual(media['rtcp'][1]['report_blocks'][0]['jitter_ms'],10)
        self.assertEqual(media['rtp_audio'][0]['status'],'ok')

    def test_mux_dynamic_codec_and_encryption_mapping(self):
        from voice_tools.tools.report.pcap import analyze_group
        source = self.root/'mux.pcap'
        sr = struct.pack('!BBHIIIIII', 0x80, 200, 6, 111, 3900000000, 0, 320, 2, 320)
        pcap(source, [(0, packet(rtp(1, pt=96), 16000, 24000)),
                      (.01, packet(sr, 16000, 24000)), (.02, packet(rtp(2, pt=96), 16000, 24000))])
        mappings = [{'from_epoch': T0, 'media': {'ip': '192.0.2.2', 'port': 24000, 'protocol': 'RTP/AVP',
                     'rtcp_mux': True, 'codecs': {'96': {'name': 'PCMA', 'clock_rate': 8000, 'channels': 1}}}}]
        result = analyze_group([source], [16000, 24000], rtcp_ports=[16000, 24000], mappings=mappings,
                               audio_output=self.root/'plain')
        self.assertEqual(result['streams'][0]['packets'], 2)
        self.assertEqual(result['streams'][0]['clock_rate'], 8000)
        self.assertEqual(len(result['media']['rtcp']), 1)
        self.assertEqual(result['media']['rtp_audio'][0]['codecs'], ['PCMA'])
        mappings[0]['media']['protocol'] = 'RTP/SAVP'
        result = analyze_group([source], [16000, 24000], rtcp_ports=[16000, 24000], mappings=mappings,
                               audio_output=self.root/'encrypted')
        self.assertEqual(result['media']['rtp_audio'][0]['status'], 'unsupported')
        self.assertFalse((self.root/'encrypted').exists())
