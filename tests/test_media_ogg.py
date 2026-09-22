from pathlib import Path
import struct
import pytest
from wiki_voice_downloader.media import validate_media
from wiki_voice_downloader.media.ogg import ogg_crc
from wiki_voice_downloader.errors import InvalidMedia,UnsupportedMedia
from .koumakan_helpers import MEDIA


def crc_slow(data):
    crc=0
    for n in data:
        crc ^= n<<24
        for _ in range(8):
            crc=((crc<<1)^(0x04c11db7 if crc&0x80000000 else 0))&0xffffffff
    return crc


def ogg_page(body,laces,seq,flags=0,granule=0,serial=1234):
    head=b'OggS'+bytes([0,flags])+int(granule).to_bytes(8,'little',signed=True)+struct.pack('<III',serial,seq,0)+bytes([len(laces)])
    raw=head+bytes(laces)+body
    crc=crc_slow(raw)
    return raw[:22]+struct.pack('<I',crc)+raw[26:]


def from_packets(packets,seq,flags=0,granule=0):
    laces=[]
    for p in packets: laces.extend([255]*(len(p)//255)+[len(p)%255])
    return ogg_page(b''.join(packets),laces,seq,flags,granule)


def pages(data):
    out=[];pos=0
    while pos<len(data):
        n=data[pos+26]; length=27+n+sum(data[pos+27:pos+27+n]);out.append(data[pos:pos+length]);pos+=length
    return out


def packets(data):
    out=[];buf=bytearray()
    for page in pages(data):
        n=page[26];pos=27+n
        for size in page[27:27+n]:
            buf.extend(page[pos:pos+size]);pos+=size
            if size<255: out.append(bytes(buf));buf.clear()
    return out

@pytest.mark.parametrize('name,codec',[('vorbis','vorbis'),('opus','opus')])
def test_real_encoded_short_fixtures(name,codec):
    info=validate_media(MEDIA/(name+'.ogg'),'application/octet-stream')
    assert info.format=='ogg' and info.codec==codec and info.frame_count is None
    assert info.page_count==3 and info.size_bytes<10000

@pytest.mark.parametrize('name',['vorbis','opus'])
def test_crc_matches_independent_bitwise_implementation_and_encoder(name):
    for page in pages((MEDIA/(name+'.ogg')).read_bytes()):
        cleared=page[:22]+b'\0'*4+page[26:]
        assert ogg_crc(cleared)==crc_slow(cleared)==int.from_bytes(page[22:26],'little')

@pytest.mark.parametrize('remove',[1,12,100,1000])
def test_truncated_ogg_is_not_complete(tmp_path,remove):
    file=tmp_path/'audio';file.write_bytes((MEDIA/'vorbis.ogg').read_bytes()[:-remove])
    with pytest.raises(InvalidMedia): validate_media(file)

@pytest.mark.parametrize('body',[b'OggS',b'OggS'+b'\0'*100,b'<html>Error</html>',b'{"error":true}',b''])
def test_magic_and_error_payloads_fail(tmp_path,body):
    file=tmp_path/'audio';file.write_bytes(body)
    with pytest.raises(InvalidMedia): validate_media(file)


def test_challenge_is_site_stopping(tmp_path):
    f=tmp_path/'media';f.write_bytes(b'<html><title>Verify you are human</title><form id="challenge">captcha</form></html>')
    with pytest.raises(InvalidMedia) as e: validate_media(f)
    assert e.value.stop_site


def test_bad_crc_and_page_gap(tmp_path):
    raw=(MEDIA/'opus.ogg').read_bytes(); seq=pages(raw)
    f=tmp_path/'x'; f.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
    with pytest.raises(InvalidMedia,match='CRC'):validate_media(f)
    f.write_bytes(seq[0]+seq[-1])
    with pytest.raises(InvalidMedia,match='sequence'):validate_media(f)


def test_required_headers_and_audio_not_fabricated(tmp_path):
    p=packets((MEDIA/'vorbis.ogg').read_bytes())
    f=tmp_path/'x';f.write_bytes(from_packets([p[0]],0,2)+from_packets(p[1:3],1,4,1))
    with pytest.raises(InvalidMedia,match='no audio'):validate_media(f)
    p[2]=b'\x05vorbisgarbage'; f.write_bytes(from_packets([p[0]],0,2)+from_packets(p[1:3],1)+from_packets(p[3:],2,4,960))
    with pytest.raises(InvalidMedia):validate_media(f)

@pytest.mark.parametrize('size',[255,80000])
def test_valid_cross_page_packet_and_exact_255_termination(tmp_path,size):
    p=packets((MEDIA/'opus.ogg').read_bytes())
    vendor=b'A'*(size-16)
    tag=b'OpusTags'+struct.pack('<I',len(vendor))+vendor+struct.pack('<I',0)
    # Split the large comment header into full segments, leaving an explicit
    # zero-length terminator when its packet length is a multiple of 255.
    chunk=min((len(tag)//255)*255,65025)
    remainder=tag[chunk:]
    first=ogg_page(tag[:chunk],[255]*(chunk//255),1,0,-1)
    laces=[255]*(len(remainder)//255)+[len(remainder)%255]
    second=ogg_page(remainder,laces,2,1,0)
    raw=from_packets([p[0]],0,2)+first+second+from_packets(p[2:],3,4,1272)
    f=tmp_path/'x';f.write_bytes(raw)
    assert validate_media(f).codec=='opus'
    broken=bytearray(raw);offset=len(from_packets([p[0]],0,2))+len(first);broken[offset+5]=0
    f.write_bytes(broken)
    with pytest.raises(InvalidMedia,match='continuation'):validate_media(f)

@pytest.mark.parametrize('packet',[b'\x80theora'+b'\0'*35,b'fLaC'+b'\0'*25])
def test_unknown_or_video_ogg_is_unsupported_not_mp3(tmp_path,packet):
    f=tmp_path/'x';f.write_bytes(from_packets([packet],0,2)+from_packets([b'x'],1,4,100))
    with pytest.raises(UnsupportedMedia):validate_media(f)


def test_chained_ogg_not_silently_accepted(tmp_path):
    raw=(MEDIA/'opus.ogg').read_bytes();f=tmp_path/'x';f.write_bytes(raw+raw)
    with pytest.raises(UnsupportedMedia,match='chained'):validate_media(f)


def test_expected_length_applies_and_no_suffix_assumption(tmp_path):
    f=tmp_path/'not_really.mp3';raw=(MEDIA/'opus.ogg').read_bytes();f.write_bytes(raw)
    assert validate_media(f,expected_size=len(raw)).extension=='ogg'
    with pytest.raises(InvalidMedia) as e:validate_media(f,expected_size=len(raw)+1)
    assert e.value.retryable
