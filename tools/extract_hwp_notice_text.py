"""Read text records from a local, unencrypted HWP5 file without running embedded content."""
import argparse
import struct
import zlib
from pathlib import Path

FREE, END, FAT, DIFAT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC


class CompoundFile:
    def __init__(self, data):
        if data[:8] != bytes.fromhex('D0CF11E0A1B11AE1'):
            raise ValueError('Not an OLE compound file')
        self.data = data
        self.sector_size = 1 << struct.unpack_from('<H', data, 30)[0]
        self.mini_size = 1 << struct.unpack_from('<H', data, 32)[0]
        self.cutoff = struct.unpack_from('<I', data, 56)[0]

        def u32(off): return struct.unpack_from('<I', data, off)[0]
        difat = [u32(76 + 4*i) for i in range(109)]
        difat = [x for x in difat if x not in (FREE, END)]
        first_difat, n_difat = u32(68), u32(72)
        sid = first_difat
        for _ in range(n_difat):
            sec = self.sector(sid)
            vals = struct.unpack('<'+'I'*(self.sector_size//4), sec)
            difat.extend(x for x in vals[:-1] if x not in (FREE, END))
            sid = vals[-1]
            if sid in (FREE, END): break
        n_fat = u32(44)
        fat_sectors = difat[:n_fat]
        self.fat = []
        for fsid in fat_sectors:
            sec = self.sector(fsid)
            self.fat.extend(struct.unpack('<'+'I'*(self.sector_size//4), sec))
        self.dir_start = u32(48)
        self.minifat_start, self.minifat_count = u32(60), u32(64)
        self.entries = self._directory()
        root = next(e for e in self.entries if e['type'] == 5)
        self.ministream = self._regular(root['start'], root['size'])
        raw = self._regular(self.minifat_start, self.minifat_count*self.sector_size)
        self.minifat = list(struct.unpack('<'+'I'*(len(raw)//4), raw)) if raw else []

    def sector(self, sid):
        if sid >= len(self.fat) and self.fat:
            # FAT sectors are not in the allocation table; physical bounds still apply.
            pass
        off = 512 + sid*self.sector_size
        block = self.data[off:off+self.sector_size]
        if len(block) != self.sector_size: raise ValueError('Truncated compound-file sector')
        return block

    def _chain(self, start, table):
        seen, out, sid = set(), [], start
        while sid not in (END, FREE) and sid < len(table):
            if sid in seen: raise ValueError('Cycle in compound-file sector chain')
            seen.add(sid); out.append(sid); sid = table[sid]
        return out

    def _regular(self, start, size):
        if size == 0 or start in (END, FREE): return b''
        return b''.join(self.sector(s) for s in self._chain(start, self.fat))[:size]

    def _directory(self):
        raw = self._regular(self.dir_start, len(self.data))
        entries=[]
        for off in range(0, len(raw)-127, 128):
            name_len=struct.unpack_from('<H',raw,off+64)[0]
            typ=raw[off+66]
            if typ == 0: continue
            name=raw[off:off+max(0,name_len-2)].decode('utf-16le','replace')
            start=struct.unpack_from('<I',raw,off+116)[0]
            size=struct.unpack_from('<Q',raw,off+120)[0]
            entries.append({'name':name,'type':typ,'start':start,'size':size})
        return entries

    def streams(self):
        out={}
        for e in self.entries:
            if e['type'] != 2: continue
            if e['size'] < self.cutoff:
                chunks=[]
                for sid in self._chain(e['start'], self.minifat):
                    off=sid*self.mini_size
                    chunks.append(self.ministream[off:off+self.mini_size])
                out[e['name']]=b''.join(chunks)[:e['size']]
            else:
                out[e['name']]=self._regular(e['start'],e['size'])
        return out


def read_hwp(path):
    streams=CompoundFile(Path(path).read_bytes()).streams()
    fh=streams.get('FileHeader')
    if not fh or not fh.startswith(b'HWP Document File'):
        raise ValueError('HWP FileHeader signature not found')
    flags=struct.unpack_from('<I',fh,36)[0]
    if flags & 2: raise ValueError('Password-encrypted HWP; text extraction unavailable')
    compressed=bool(flags & 1)
    paragraphs=[]
    for name,payload in streams.items():
        if not name.startswith('Section'): continue
        if compressed:
            try: body=zlib.decompress(payload)
            except zlib.error: body=zlib.decompress(payload,-zlib.MAX_WBITS)
        else: body=payload
        pos=0; parts=[]
        while pos+4 <= len(body):
            header=struct.unpack_from('<I',body,pos)[0];pos+=4
            tag=header&0x3ff; size=header>>20
            if size==0xfff:
                if pos+4>len(body): break
                size=struct.unpack_from('<I',body,pos)[0];pos+=4
            record=body[pos:pos+size];pos+=size
            if tag==67:
                # Paragraph text is UTF-16LE; HWP inline controls occupy 2-byte code units.
                text=record.decode('utf-16le','replace')
                text=''.join(ch if ch in '\t\n\r' or ord(ch)>=32 else ' ' for ch in text)
                parts.append(text)
        paragraphs.extend(parts)
    return paragraphs


def main():
    p=argparse.ArgumentParser();p.add_argument('path');args=p.parse_args()
    paras=read_hwp(args.path)
    print('paragraph_count',len(paras))
    for i,text in enumerate(paras,1):
        if text.strip(): print(f'{i}\t{text[:1200]}')


if __name__=='__main__': main()
