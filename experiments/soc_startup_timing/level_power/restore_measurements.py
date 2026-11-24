"""Restore a losslessly deduplicated collection and check every original byte fingerprint."""
from array import array
import base64, hashlib, json, os, struct, sys, tempfile, tarfile, zlib
from pathlib import Path

def safe(name):
    p=Path(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name:
        raise ValueError(f'unsafe archive name {name!r}')
    return p

def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()

def offsets(file):
    positions=array('Q')
    pos=0
    for row in file:
        positions.append(pos);pos+=len(row)
    positions.append(pos)
    return positions

def decode(container,dest):
    dest.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory() as tmp:
        t=Path(tmp)
        with tarfile.open(container,'r|*') as stream:
            seen=set()
            for item in stream:
                rel=safe(item.name)
                if not item.isfile() or item.name in seen:raise ValueError('nonregular or duplicate member')
                seen.add(item.name);p=t/rel;p.parent.mkdir(parents=True,exist_ok=True)
                with stream.extractfile(item) as inf,p.open('xb') as out:
                    while chunk:=inf.read(1<<20):out.write(chunk)
        recipes=json.loads((t/'packing.json').read_bytes())
        for name,kind in recipes['ordinary'].items():
            p=dest/safe(name);p.parent.mkdir(parents=True,exist_ok=True)
            if kind=='literal':
                os.replace(t/'literal'/safe(name),p)
            else:
                import shutil
                shutil.copyfile(dest/safe(kind),p)
        with (t/'chunks.nbt').open('rb') as nbt:
            sizes=recipes['sizes'];pos=[0]
            for length in sizes:pos.append(pos[-1]+length)
            for name,region in recipes['regions'].items():
                data=bytearray(region['bytes']);data[:8192]=base64.b64decode(region['header'])
                for slot,idx in region['records']:
                    loc=struct.unpack_from('>I',data,slot*4)[0];offset=(loc>>8)*4096
                    nbt.seek(pos[idx]);raw=nbt.read(sizes[idx])
                    body=b'\2'+zlib.compress(raw)
                    if len(body)+4>(loc&255)*4096:raise ValueError('reconstructed chunk overflows its sectors')
                    struct.pack_into('>I',data,offset,len(body));data[offset+4:offset+4+len(body)]=body
                p=dest/safe(name);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        for rel in recipes['streams']:
            p=dest/safe(rel['name']);p.parent.mkdir(parents=True,exist_ok=True)
            ref=dest/safe(rel['base'])
            with ref.open('rb') as src,(t/rel['extras']).open('rb') as extras,p.open('wb') as out:
                starts=offsets(src)
                refs=array('i');refs.frombytes((t/rel['refs']).read_bytes())
                if sys.byteorder!='little':refs.byteswap()
                for a in refs:
                    if a<0: line=extras.readline()
                    else:
                        src.seek(starts[a]);line=src.read(starts[a+1]-starts[a])
                        if rel.get('sorted'):
                            line=(json.dumps(json.loads(line),sort_keys=True,separators=(',',':'))+'\n').encode()
                    if not line.endswith(b'\n'):raise ValueError('missing complete line during reconstruction')
                    out.write(line)
                if extras.read(1):raise ValueError('unconsumed literal lines')
        expected=recipes['members']
        if set(str(x.relative_to(dest)) for x in dest.rglob('*') if x.is_file())!=set(expected):
            raise ValueError('restored member inventory differs')
        for k,spec in expected.items():
            p=dest/safe(k)
            if p.stat().st_size!=spec['bytes'] or digest(p)!=spec['sha256']:
                raise ValueError('byte mismatch: '+k)
        print(f"Restored and verified every byte of {len(expected)} original members.")

if __name__=='__main__':decode(Path(sys.argv[1]),Path(sys.argv[2]))
