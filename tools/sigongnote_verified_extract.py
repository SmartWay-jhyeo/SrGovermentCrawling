"""Offline, passive document extraction for the approved local files only."""
import collections, csv, hashlib, io, json, re, struct, sys, warnings, zipfile, zlib
from pathlib import Path
import xml.etree.ElementTree as ET
from extract_hwp_notice_text import CompoundFile
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'outputs/sigongnote_market_stage2_documents'
CACHE=ROOT/'.local/sigongnote_stage2_verified'
OUT=ROOT/'outputs/sigongnote_market_stage2_verified'

def col(n):
    s=''
    while n: n,m=divmod(n-1,26); s=chr(65+m)+s
    return s

def number(v):
    return str(int(v)) if isinstance(v,float) and v.is_integer() else str(v)

def clean(s):
    s=s.encode('utf-16le','surrogatepass').decode('utf-16le','replace')
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',' ',s).strip()

def records(raw):
    p=0
    while p+4<=len(raw):
        tag,n=struct.unpack_from('<HH',raw,p); q=p; p+=4
        if p+n>len(raw):raise ValueError('Truncated BIFF record')
        yield q,tag,raw[p:p+n];p+=n

class Segments:
    def __init__(self,segs):self.segs=segs;self.i=0;self.p=0
    def read(self,n):
        out=b''
        while n:
            if self.p==len(self.segs[self.i]):self.i+=1;self.p=0
            a=self.segs[self.i][self.p:self.p+n];self.p+=len(a);n-=len(a);out+=a
        return out
    def string(self,n,wide):
        parts=[]
        while n:
            if self.p==len(self.segs[self.i]):self.i+=1;self.p=0;wide=bool(self.read(1)[0]&1)
            width=2 if wide else 1
            k=min(n,(len(self.segs[self.i])-self.p)//width)
            if k==0:raise ValueError('Invalid split string')
            parts.append(self.read(k*width).decode('utf-16le' if wide else 'latin1','replace'));n-=k
        return ''.join(parts)

def xls_blocks(raw):
    streams=CompoundFile(raw).streams(); book=streams.get('Workbook',streams.get('Book'))
    if book is None:raise ValueError('No BIFF workbook stream')
    recs=list(records(book));sst=[];sheets={}
    for i,(pos,tag,b) in enumerate(recs):
        if tag==0x85:
            off=struct.unpack_from('<I',b)[0];n=b[6];wide=b[7]&1
            sheets[off]=b[8:8+n*(2 if wide else 1)].decode('utf-16le' if wide else 'latin1','replace')
        if tag==0xfc:
            segs=[b[8:]];j=i+1
            while j<len(recs) and recs[j][1]==0x3c:segs.append(recs[j][2]);j+=1
            cur=Segments(segs)
            for _ in range(struct.unpack_from('<I',b,4)[0]):
                n=struct.unpack('<H',cur.read(2))[0];flags=cur.read(1)[0]
                runs=struct.unpack('<H',cur.read(2))[0] if flags&8 else 0
                ext=struct.unpack('<I',cur.read(4))[0] if flags&4 else 0
                s=cur.string(n,bool(flags&1));cur.read(runs*4+ext);sst.append(s)
    def rk(x):
        v=(struct.unpack('<i',struct.pack('<I',x))[0]>>2) if x&2 else struct.unpack('<d',struct.pack('<II',0,x&~3))[0]
        return v/100 if x&1 else v
    rows=collections.defaultdict(list);sheet='';pending=None
    for pos,tag,b in recs:
        if pos in sheets:sheet=sheets[pos]
        if not sheet:continue
        vals=[]
        if tag in (0xfd,0x203,0x27e,0x6,0x204) and len(b)>=6:
            r,c,xf=struct.unpack_from('<HHH',b);v=None;suffix=''
            if tag==0xfd:v=sst[struct.unpack_from('<I',b,6)[0]]
            elif tag==0x203:v=struct.unpack_from('<d',b,6)[0]
            elif tag==0x27e:v=rk(struct.unpack_from('<I',b,6)[0])
            elif tag==0x6:
                suffix=' [저장된 수식결과; 재계산 안 함]'
                if b[12:14]!=b'\xff\xff':v=struct.unpack_from('<d',b,6)[0]
                else:v='[수식 결과 미확인]'
            elif tag==0x204:
                n=struct.unpack_from('<H',b,6)[0];wide=b[8]&1
                v=b[9:9+n*(2 if wide else 1)].decode('utf-16le' if wide else 'latin1','replace')
            if v is not None:vals=[(r,c,number(v)+suffix)]
        elif tag==0xbd:
            r,c=struct.unpack_from('<HH',b);last=struct.unpack_from('<H',b,len(b)-2)[0]
            vals=[(r,c+j,number(rk(struct.unpack_from('<I',b,6+j*6)[0]))) for j in range(last-c+1)]
        for r,c,v in vals:
            if str(v).strip():rows[(sheet,r+1)].append((c+1,v))
    return [{'loc':f'시트 {s} / 행 {r}', 'text':' | '.join(f'{col(c)}{r}={clean(v)}' for c,v in sorted(vals))} for (s,r),vals in rows.items()]

def xlsx_blocks(z):
    ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    shared=[]
    if 'xl/sharedStrings.xml' in z.namelist():
        shared=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml'))]
    rels={e.attrib['Id']:e.attrib['Target'] for e in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
    blocks=[]
    for sheet in ET.fromstring(z.read('xl/workbook.xml')).findall('x:sheets/x:sheet',ns):
        target=rels[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
        path=target.lstrip('/') if target.startswith('/') else 'xl/'+target
        tree=ET.fromstring(z.read(path));name=sheet.attrib['name']
        for row in tree.findall('x:sheetData/x:row',ns):
            parts=[]
            for c in row.findall('x:c',ns):
                typ=c.get('t');v=c.find('x:v',ns);val=v.text if v is not None and v.text else ''
                if typ=='s':val=shared[int(val)] if val else ''
                elif typ=='inlineStr':val=''.join(c.find('x:is',ns).itertext())
                if c.find('x:f',ns) is not None:val=(val or '[미저장]')+' [저장된 수식결과; 재계산 안 함]'
                if val:parts.append(c.attrib['r']+'='+clean(val))
            if parts:blocks.append({'loc':f'시트 {name} / 행 {row.attrib["r"]}','text':' | '.join(parts)})
    return blocks

def hwp_blocks(raw):
    streams=CompoundFile(raw).streams();fh=streams.get('FileHeader',b'')
    if not fh.startswith(b'HWP Document File'):return None
    flags=struct.unpack_from('<I',fh,36)[0]
    if flags&6:raise ValueError('Encrypted/distribution HWP unsupported')
    blocks=[]
    for name,payload in sorted(streams.items()):
        if not re.fullmatch('Section[0-9]+',name):continue
        if flags&1:
            try:body=zlib.decompress(payload,-15)
            except zlib.error:body=zlib.decompress(payload)
        else:body=payload
        pos=0;n=0
        while pos+4<=len(body):
            h=struct.unpack_from('<I',body,pos)[0];pos+=4;size=h>>20;tag=h&1023
            if size==4095:size=struct.unpack_from('<I',body,pos)[0];pos+=4
            b=body[pos:pos+size];pos+=size
            if len(b)!=size:raise ValueError('Truncated HWP record')
            if tag!=67:continue
            n+=1;units=struct.unpack('<'+'H'*(len(b)//2),b);i=0;chars=[]
            while i<len(units):
                c=units[i]
                if c in (1,2,3,4,5,6,7,8,9,11,12,14,15,16,17,18,19,20,21,22,23):chars.append(' ');i+=8
                else:chars.append(chr(c) if c>=32 or c in (10,13) else ' ');i+=1
            text=clean(''.join(chars))
            if text:blocks.append({'loc':f'{name} / 본문 문단 {n}','text':text})
    return blocks

def decode_zipname(info):
    if info.flag_bits&0x800:return info.filename
    try:return info.filename.encode('cp437').decode('cp949')
    except UnicodeError:return info.filename

def extract(raw,name):
    if raw.lstrip().startswith(b'<?xml'):
        root=ET.fromstring(raw)
        if root.tag=='HWPML':
            blocks=[]
            for i,p in enumerate(root.iter('P'),1):
                text=''.join(''.join(c.itertext()) for c in p.iter('CHAR'))
                if text.strip():blocks.append({'loc':f'HWPML / paragraph {i}','text':clean(text)})
            return 'HWPML',blocks,[]
    if raw.startswith(b'%PDF'):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore');r=PdfReader(io.BytesIO(raw))
            return 'PDF',[{'loc':f'페이지 {i+1}','text':p.extract_text() or ''} for i,p in enumerate(r.pages)],[]
    if raw[:8]==bytes.fromhex('D0CF11E0A1B11AE1'):
        result=hwp_blocks(raw)
        return ('HWP',result,[]) if result is not None else ('XLS',xls_blocks(raw),[])
    if zipfile.is_zipfile(io.BytesIO(raw)):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if sum(i.file_size for i in z.infolist())>300*1024**2:raise ValueError('Uncompressed archive >300MiB')
            if 'xl/workbook.xml' in z.namelist():return 'XLSX',xlsx_blocks(z),[]
            sections=sorted(n for n in z.namelist() if re.fullmatch('Contents/section[0-9]+.xml',n))
            if sections:
                blocks=[]
                for s in sections:
                    root=ET.fromstring(z.read(s));n=0
                    for p in root.iter():
                        if p.tag.split('}')[-1]!='p':continue
                        n+=1;text=''.join(''.join(t.itertext()) for t in p.iter() if t.tag.split('}')[-1]=='t')
                        if text.strip():blocks.append({'loc':f'{s} / 문단 {n}','text':clean(text)})
                return 'HWPX',blocks,[]
            children=[]
            for info in z.infolist():
                if info.is_dir():continue
                if info.file_size>50*1024**2:continue
                children.append((decode_zipname(info),z.read(info)))
            return 'ZIP',[],children
    return 'UNKNOWN',[],[]

def main():
    sys.stdout.reconfigure(encoding='utf-8');CACHE.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
    meta={r['document_id']:r for r in csv.DictReader((BASE/'attachment_metadata.csv').open(encoding='utf-8-sig'))}
    rows=list(csv.DictReader((BASE/'attachment_fetch_results.csv').open(encoding='utf-8-sig')))
    ledger=[json.loads(s) for s in (ROOT/'.local/sigongnote_stage2_documents/fetch_ledger.jsonl').read_text(encoding='utf-8').splitlines()]
    results={e['document_id']:e for e in ledger if e['event']=='result'}
    inventory=[];audit=[]
    def process(row,raw,name,member='',depth=0):
        key=row['document_id']+(':'+hashlib.sha256(member.encode()).hexdigest()[:10] if member else '')
        item={'document_key':key,'document_id':row['document_id'],'priority_id':row['priority_id'],'bid_ntce_no':row['bid_ntce_no'],'bid_ntce_ord':row['bid_ntce_ord'],
              'document_name':name,'member_path':member,'source_local_path':row['local_path'],'source_kind':row.get('source_kind','DOWNLOADED'),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        try:
            kind,blocks,children=extract(raw,name);item.update(actual_format=kind,blocks=len(blocks),characters=sum(len(b['text']) for b in blocks))
            item['read_status']='READ_TEXT' if item['characters'] else 'ARCHIVE_EXPANDED' if children else 'NO_TEXT_OR_UNSUPPORTED'
            if blocks:
                (CACHE/(key.replace(':','_')+'.json')).write_text(json.dumps(blocks,ensure_ascii=False),encoding='utf-8');item['text_cache']=str((CACHE/(key.replace(':','_')+'.json')).relative_to(ROOT))
            inventory.append(item)
            for cname,craw in children:
                if depth<2:process(row,craw,cname,(member+'/' if member else '')+cname,depth+1)
        except Exception as e:item.update(read_status='PARSE_FAILED',error=type(e).__name__+': '+str(e)[:180]);inventory.append(item)
        print(row['priority_id'],key,item['read_status'],item.get('actual_format',''),item.get('blocks',''),flush=True)
    for row in rows:
        item={'document_id':row['document_id'],'priority_id':row['priority_id'],'fetch_status':row['fetch_status'],'ledger_agrees':results[row['document_id']]['fetch_status']==row['fetch_status']}
        if row['fetch_status']=='DOWNLOADED':
            path=ROOT/row['local_path'];item['file_exists']=path.is_file()
            if item['file_exists']:
                raw=path.read_bytes();item.update(size_matches=len(raw)==int(row['received_bytes']),sha_matches=hashlib.sha256(raw).hexdigest()==row['sha256'],bytes=len(raw))
                assert item['size_matches'] and item['sha_matches'] and item['ledger_agrees']
                process(row,raw,meta[row['document_id']]['attachment_filename_from_notice'])
        audit.append(item)
    for path in (ROOT/'outputs').glob('*.hwp'):
        if hashlib.sha256(path.read_bytes()).hexdigest()=='8a8a1a7f5fcae6214f7e7282e510da30af960d40e54ebf7125f58d3d60442800':
            row=dict(next(r for r in rows if r['document_id']=='fb414d38ad6d2162'));row.update(local_path=str(path.relative_to(ROOT)),source_kind='USER_PROVIDED_LOCAL')
            process(row,path.read_bytes(),path.name)
    (CACHE/'inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'source_inventory.json').write_text(json.dumps({'file_audit':audit,'documents':inventory},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'downloaded':sum(a['fetch_status']=='DOWNLOADED' for a in audit),'downloaded_bytes':sum(a.get('bytes',0) for a in audit),'documents':len(inventory),'statuses':dict(collections.Counter(i['read_status'] for i in inventory))}))

if __name__=='__main__':main()
