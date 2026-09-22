"""Ogg page/CRC/packet and Vorbis/Opus header inspection.

Only one unchained logical audio stream is accepted. Opus mapping family 0
(mono/stereo) is supported; other mappings are explicitly unsupported. This is
structural validation, NOT decoding, listening or a guarantee of playability.
"""
from __future__ import annotations
import hashlib
import struct
from pathlib import Path
from ..errors import InvalidMedia, UnsupportedMedia
from .vorbis_headers import Bits, setup_modes


def _table():
    values=[]
    for n in range(256):
        crc=n<<24
        for _ in range(8): crc=((crc<<1) ^ (0x04c11db7 if crc&0x80000000 else 0)) & 0xffffffff
        values.append(crc)
    return tuple(values)
CRC_TABLE=_table()

def ogg_crc(data: bytes) -> int:
    crc=0
    for byte in data: crc=((crc<<8)&0xffffffff)^CRC_TABLE[((crc>>24)&255)^byte]
    return crc

def need(condition,message):
    if not condition: raise InvalidMedia('invalid_media: '+message)


def _comments(packet: bytes, start: int, framing: bool):
    pos=start
    def number():
        nonlocal pos
        need(pos+4<=len(packet),'truncated comment length')
        result=int.from_bytes(packet[pos:pos+4],'little'); pos+=4; return result
    length=number(); need(length<=len(packet)-pos,'truncated vendor comment'); pos+=length
    count=number(); need(count<=(len(packet)-pos)//4,'invalid comment count')
    for _ in range(count):
        length=number(); need(length<=len(packet)-pos,'truncated comment'); pos+=length
    if framing: need(pos<len(packet) and packet[pos]&1==1,'missing Vorbis comment framing')


def opus_packet(packet):
    need(bool(packet),'empty Opus packet')
    code=packet[0]&3; config=packet[0]>>3
    duration=([10,20,40,60][config&3] if config<12 else [10,20][config&1] if config<16 else [2.5,5,10,20][config&3])
    pos=1; end=len(packet)
    def length():
        nonlocal pos
        need(pos<end,'truncated Opus size'); first=packet[pos]; pos+=1
        if first<252: return first
        need(pos<end,'truncated Opus size'); second=packet[pos]; pos+=1
        return first+4*second
    if code==0: sizes=[end-pos]
    elif code==1:
        need((end-pos)%2==0,'unequal CBR Opus frames'); sizes=[(end-pos)//2]*2
    elif code==2:
        first=length(); sizes=[first,end-pos-first]
    else:
        need(pos<end,'truncated Opus frame count'); flags=packet[pos]; pos+=1; count=flags&63
        need(count>0 and count*duration<=120,'invalid Opus packet duration/frame count')
        if flags&64:
            padding=0
            while True:
                need(pos<end,'truncated Opus padding'); value=packet[pos]; pos+=1
                padding+=254 if value==255 else value
                if value!=255: break
            end-=padding; need(end>=pos,'Opus padding exceeds packet')
        if flags&128:
            sizes=[length() for _ in range(count-1)]
            sizes.append(end-pos-sum(sizes))
        else:
            need((end-pos)%count==0,'invalid Opus CBR packet length'); sizes=[(end-pos)//count]*count
    need(len(sizes)*duration<=120 and all(0<=n<=1275 for n in sizes),'invalid Opus frame lengths')


def validate_ogg(path: Path,content_type='',expected_size=None):
    from ..validation import MediaInfo
    size=path.stat().st_size
    need(size>0,'empty Ogg')
    if expected_size is not None: need(size==expected_size,'Ogg transfer length mismatch')
    serial=None; sequence=0; pages=0; packets=0; audio_packets=0
    codec=None; channels=None; modes=None; preskip=0; header_end_page=None
    pending=bytearray(); continued=False; eos=False; last_granule=0; digest=hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            header=stream.read(27)
            if not header: break
            if eos: raise UnsupportedMedia('unsupported_media_format: chained/trailing Ogg streams are not supported')
            need(len(header)==27 and header[:4]==b'OggS','truncated/invalid Ogg page header')
            need(header[4]==0 and not header[5]&~7,'unsupported Ogg version/flags')
            flags=header[5]; granule=int.from_bytes(header[6:14],'little',signed=True)
            stream_id,seq,crc=struct.unpack('<III',header[14:26]); segments=header[26]
            if serial is None:
                need(bool(flags&2) and not flags&1 and seq==0,'missing Ogg BOS/initial sequence')
                serial=stream_id
            elif stream_id!=serial or flags&2:
                raise UnsupportedMedia('unsupported_media_format: multiplexed/chained Ogg streams are not supported')
            need(seq==sequence,'Ogg page sequence gap'); sequence=(sequence+1)&0xffffffff
            need(bool(flags&1)==continued,'Ogg continuation flag mismatch')
            lace=stream.read(segments); need(len(lace)==segments,'truncated Ogg lacing table')
            body=stream.read(sum(lace)); need(len(body)==sum(lace),'truncated Ogg page body')
            page=header+lace+body
            need(ogg_crc(page[:22]+b'\0\0\0\0'+page[26:])==crc,'Ogg CRC mismatch')
            digest.update(page); pages+=1; pos=0; complete=0
            for segment in lace:
                pending.extend(body[pos:pos+segment]); pos+=segment; continued=segment==255
                if len(pending)>16*1024*1024:
                    raise UnsupportedMedia('unsupported_media_format: Ogg packet exceeds 16 MiB validation budget')
                if continued: continue
                packet=bytes(pending); pending.clear(); complete+=1
                if packets==0:
                    if packet.startswith(b'\x01vorbis'):
                        need(len(packet)==30,'Vorbis identification length')
                        need(int.from_bytes(packet[7:11],'little')==0,'Vorbis version')
                        channels=packet[11]; rate=int.from_bytes(packet[12:16],'little')
                        small=packet[28]&15; large=packet[28]>>4
                        need(channels>0 and rate>0 and 6<=small<=large<=13 and packet[29]&1,'Vorbis identification fields')
                        codec='vorbis'
                    elif packet.startswith(b'OpusHead'):
                        need(len(packet)>=19 and packet[8]!=0 and packet[8]<16 and packet[9]>0,'OpusHead fields')
                        if packet[18]!=0:
                            raise UnsupportedMedia('unsupported_media_format: only Opus mapping family 0 is supported')
                        need(packet[9] in {1,2} and (packet[8]!=1 or len(packet)==19),'Opus mono/stereo identification length')
                        channels=packet[9]; preskip=int.from_bytes(packet[10:12],'little'); codec='opus'
                    else:
                        raise UnsupportedMedia('unsupported_media_format: Ogg codec is not Vorbis/Opus audio')
                    need(pages==1,'identification header must complete on first page')
                elif codec=='vorbis' and packets==1:
                    need(packet.startswith(b'\x03vorbis'),'missing Vorbis comment header'); _comments(packet,7,True)
                elif codec=='vorbis' and packets==2:
                    modes=setup_modes(packet,channels); header_end_page=pages
                elif codec=='opus' and packets==1:
                    need(packet.startswith(b'OpusTags'),'missing OpusTags'); _comments(packet,8,False); header_end_page=pages
                else:
                    need(header_end_page is not None and pages>header_end_page, 'audio must start after the final codec-header page')
                    if codec=='vorbis':
                        bits=Bits(packet); need(bits.read(1)==0,'unexpected Vorbis header in audio payload')
                        mode=bits.read((len(modes)-1).bit_length()); need(mode<len(modes),'invalid Vorbis audio mode')
                        if modes[mode]: bits.skip(2)
                    else: opus_packet(packet)
                    audio_packets+=1
                packets+=1
            if pages==1:
                need(packets==1 and not continued and granule==0,'BOS must contain only the identification packet')
            if complete:
                need(granule>=0,'negative Ogg completed-page granule')
                need(granule>=last_granule,'Ogg granule moved backwards')
                last_granule=granule
            else:
                need(granule==-1,'unfinished packet requires granule -1')
            if flags&4:
                need(not continued and complete>0,'EOS has incomplete packet')
                eos=True
    need(eos and not continued,'missing complete Ogg EOS')
    need(audio_packets>0,'Ogg headers contain no audio packets')
    need(last_granule>preskip,'Ogg has no output samples')
    return MediaInfo(size,digest.hexdigest(),None,format='ogg',codec=codec,extension='ogg',
                     validation_level='ogg-pages-crc-packets-codec-headers',page_count=pages)
