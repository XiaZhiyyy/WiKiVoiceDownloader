"""Bounded Vorbis I setup-header validation, not a waveform decoder.

Checks required configuration sections and references so a forged magic string
is insufficient. Entropy-coded audio payloads are not decoded.
"""
from ..errors import InvalidMedia, UnsupportedMedia

class Bits:
    def __init__(self,data): self.data=data; self.pos=0
    def read(self,n):
        if n<0 or self.pos+n>len(self.data)*8: raise InvalidMedia('invalid_media: truncated Vorbis setup')
        start=self.pos; self.pos+=n
        if not n: return 0
        value=int.from_bytes(self.data[start//8:(self.pos+7)//8],'little')
        return (value >> (start%8)) & ((1<<n)-1)
    def skip(self,n):
        if n<0 or self.pos+n>len(self.data)*8: raise InvalidMedia('invalid_media: truncated Vorbis setup values')
        self.pos+=n

def _need(ok,detail):
    if not ok: raise InvalidMedia('invalid_media: Vorbis '+detail)

def setup_modes(packet,channels):
    _need(packet.startswith(b'\x05vorbis'),'setup signature')
    b=Bits(packet[7:]); books=b.read(8)+1
    for _ in range(books):
        _need(b.read(24)==0x564342,'codebook sync')
        dimensions=b.read(16); entries=b.read(24)
        _need(dimensions>0 and entries>0,'empty codebook')
        if entries>262144: raise UnsupportedMedia('unsupported_media_format: Vorbis codebook exceeds validation resource budget')
        lengths=[]
        if b.read(1):
            length=b.read(5)+1; consumed=0
            while consumed<entries:
                _need(length<=32,'codeword length')
                count=b.read((entries-consumed).bit_length())
                _need(count<=entries-consumed,'ordered codebook count')
                lengths.extend([length]*count); consumed+=count; length+=1
        else:
            sparse=b.read(1)
            for _ in range(entries):
                if not sparse or b.read(1): lengths.append(b.read(5)+1)
        _need(bool(lengths),'empty codeword list')
        _need(sum(1<<(32-l) for l in lengths)<=1<<32,'oversubscribed Huffman codebook')
        lookup=b.read(4); _need(lookup<=2,'codebook lookup type')
        if lookup:
            b.skip(64); value_bits=b.read(4)+1; b.skip(1)
            if lookup==1:
                # Exact integer root; no float approximation of lookup1_values.
                lo,hi=0,entries
                while lo<hi:
                    mid=(lo+hi+1)//2
                    if dimensions>entries.bit_length() and mid>=2: hi=mid-1
                    elif pow(mid,dimensions)<=entries: lo=mid
                    else: hi=mid-1
                values=lo
            else: values=entries*dimensions
            b.skip(values*value_bits)
    for _ in range(b.read(6)+1): _need(b.read(16)==0,'reserved time transform')
    floors=b.read(6)+1
    for _ in range(floors):
        kind=b.read(16)
        if kind==0:
            b.skip(8+16+16+6+8)
            for _ in range(b.read(4)+1): _need(b.read(8)<books,'floor0 codebook index')
        elif kind==1:
            partitions=[b.read(4) for _ in range(b.read(5))]
            dims=[]
            for _ in range(max(partitions,default=-1)+1):
                dims.append(b.read(3)+1); subs=b.read(2)
                if subs: _need(b.read(8)<books,'floor1 masterbook')
                for _ in range(1<<subs): _need(b.read(8)-1<books,'floor1 subclass book')
            b.skip(2); bits=b.read(4)
            xs=[0,1<<bits]
            for part in partitions:
                xs.extend(b.read(bits) for _ in range(dims[part]))
            _need(len(xs)==len(set(xs)),'duplicate floor1 X coordinate')
        else: raise InvalidMedia('invalid_media: unsupported Vorbis floor type')
    residues=b.read(6)+1
    for _ in range(residues):
        _need(b.read(16)<=2,'residue type')
        begin=b.read(24); end=b.read(24); _need(end>=begin,'residue range')
        b.skip(24); classes=b.read(6)+1; _need(b.read(8)<books,'residue classbook')
        cascades=[]
        for _ in range(classes):
            low=b.read(3); high=b.read(5) if b.read(1) else 0
            cascades.append((high<<3)|low)
        for mask in cascades:
            for bit in range(8):
                if mask & (1<<bit): _need(b.read(8)<books,'residue book index')
    mappings=b.read(6)+1
    for _ in range(mappings):
        _need(b.read(16)==0,'mapping type')
        submaps=b.read(4)+1 if b.read(1) else 1
        if b.read(1):
            for _ in range(b.read(8)+1):
                width=(channels-1).bit_length(); magnitude=b.read(width); angle=b.read(width)
                _need(magnitude!=angle and magnitude<channels and angle<channels,'coupling channels')
        _need(b.read(2)==0,'mapping reserved bits')
        if submaps>1:
            for _ in range(channels): _need(b.read(4)<submaps,'mapping mux')
        for _ in range(submaps):
            b.skip(8); _need(b.read(8)<floors,'mapping floor'); _need(b.read(8)<residues,'mapping residue')
    modes=[]
    for _ in range(b.read(6)+1):
        modes.append(b.read(1))
        _need(b.read(16)==0 and b.read(16)==0,'reserved mode transforms')
        _need(b.read(8)<mappings,'mode mapping')
    _need(b.read(1)==1,'setup framing bit')
    return modes
