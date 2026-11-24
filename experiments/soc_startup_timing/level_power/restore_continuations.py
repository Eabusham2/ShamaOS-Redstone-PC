"""Restore every actual continuation byte against the verified nominal measurement payload."""
import hashlib,json,sys,tarfile,tempfile
from pathlib import Path

def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()
def safe(name):
    p=Path(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name:raise ValueError(name)
    return p

def restore(package,reference,dest):
    dest.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory() as temp:
        temp=Path(temp)
        with tarfile.open(package,'r|*') as archive:
            for item in archive:
                rel=safe(item.name)
                if not item.isfile():raise ValueError(item.name)
                p=temp/rel
                with archive.extractfile(item) as src,p.open('xb') as out:
                    while block:=src.read(1<<20):out.write(block)
        manifest=json.loads((temp/'continuations.json').read_bytes())
        hashes={}
        with (temp/'literals').open('rb') as lit:
            for name,rec in manifest['files'].items():
                path=dest/safe(name);path.parent.mkdir(parents=True,exist_ok=True);src=None
                if rec['reference']:
                    pin=reference/safe(rec['reference'])
                    if pin not in hashes:hashes[pin]=digest(pin)
                    assert hashes[pin]==rec['reference_sha256'],rec['reference']
                    src=pin.open('rb')
                try:
                    with path.open('xb') as out:
                        for kind,offset,n in rec['ranges']:
                            feed=src if kind=='ref' else lit
                            feed.seek(offset)
                            while n:
                                chunk=feed.read(min(n,1<<20));assert chunk
                                out.write(chunk);n-=len(chunk)
                finally:
                    if src:src.close()
                assert path.stat().st_size==rec['bytes'] and digest(path)==rec['sha256'],name
    print('Restored and verified',len(manifest['files']),'continuation files.')

if __name__=='__main__':restore(*map(Path,sys.argv[1:]))
