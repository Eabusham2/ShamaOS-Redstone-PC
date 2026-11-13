"""Independent cycle/active-DMA miter for the shared immediate resolver."""
from __future__ import annotations

import sys
if not __debug__:
    raise SystemExit('assembler review requires assertions')

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from shamaos.assembler import assemble

ROOT=Path(__file__).resolve().parents[2]
BASELINE='c3315eb35b3d52c1828e0d5c0c93dc0139ebd6e9'


def sha(data): return hashlib.sha256(data).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True,
                        help='disposable directory for compiled models and raw case logs')
    parser.add_argument('--baseline',default=BASELINE)
    args=parser.parse_args()
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    baseline=subprocess.check_output(['git','rev-parse',args.baseline],cwd=ROOT,text=True).strip()
    before=subprocess.check_output(['git','show',f'{baseline}:rtl/shama_asm_accel.sv'],cwd=ROOT)
    after=(ROOT/'rtl/shama_asm_accel.sv').read_bytes()
    header=(ROOT/'rtl/shama_boot_params.svh').read_bytes()
    old_header=subprocess.check_output(['git','show',f'{baseline}:rtl/shama_boot_params.svh'],cwd=ROOT)
    macros=sorted(set(re.findall(rb'`(SHAMA_\w+)',before+after)))
    for macro in macros:
        pattern=rb'^`define\s+'+macro+rb'\s+([^\n]+)'
        old=re.search(pattern,old_header,re.M)
        new=re.search(pattern,header,re.M)
        assert old and new and old.group(1)==new.group(1),('effective macro mismatch',macro)
    testbench=Path(__file__).with_name('tb_asm_differential.sv')
    for name,data in [('reference',before),('candidate',after)]:
        (output/f'{name}.sv').write_text(data.decode().replace('module shama_asm_accel(',f'module shama_asm_accel_{name}(',1))
    binary=output/'differential.vvp'
    command=['iverilog','-g2012','-I',str(ROOT/'rtl'),'-s','tb_asm_differential','-o',str(binary),
             str(output/'reference.sv'),str(output/'candidate.sv'),str(testbench)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert compiled.returncode==0,compiled.stderr
    (output/'compile.stderr.txt').write_text(compiled.stderr)
    cases=[]
    for name in ['editor','files','miner','paint','settings','terminal','calculator','desktop','monitor']:
        cases.append((f'app-{name}',(ROOT/f'src/shamaos/firmware/{name}.asm').read_text(),True))
    formats={
      'RI32':'LDI r1 {value}', 'J32':'JMP {value}', 'BR32':'BR.NE {value}',
      'BRCOND':'BR NE {value}', 'MEM':'LDW r1 r2 {value}', 'SYS':'SYS {value}', 'RSYS':'RDPMC r1 {value}',
    }
    for fmt,template in formats.items():
        for kind,value in [('number','17'),('definition','VALUE'),('label','target'),('syscall','SYS_YIELD')]:
            for labeled in (False,True):
                instruction=('here: ' if labeled else '')+template.format(value=value)
                source=f'define VALUE 17\nseed: LDW r8 r9 555\n{instruction}\ntarget: HLT\n'
                cases.append((f'{fmt}-{kind}-label{int(labeled)}',source,True))
        tokens=template.format(value='17').split()
        for missing in range(1,len(tokens)):
            for labeled in (False,True):
                instruction=('here: ' if labeled else '')+' '.join(tokens[:-missing])
                source=f'seed: LDW r8 r9 555\n{instruction}\nHLT\n'
                cases.append((f'{fmt}-missing{missing}-label{int(labeled)}',source,False))
        cases += [(f'{fmt}-extra',template.format(value='17')+' extra\n',False),
                  (f'{fmt}-unknown',template.format(value='UNRESOLVED')+'\n',False)]
    for name,source,valid in [
        ('no-operand-after-number','LDI r5 4294967295\nNOP\nHLT\n',True),
        ('label-no-operand','seed: LDW r5 r6 555\nonly: NOP\nend: HLT\n',True),
        ('four-register-after-immediate','LDI r5 12\nCH r1 r2 r3 r4\nMAJ r5 r6 r7 r8\nHLT\n',True),
        ('mem-min','LDW r1 r2 -2048\n',True),('mem-max','STW r1 r2 2047\n',True),
        ('mem-too-negative','LDW r1 r2 -2049\n',False),('mem-too-positive','STW r1 r2 2048\n',False),
        ('sys-max','SYS 4095\nRDPMC r3 4095\n',True),('sys-too-large','SYS 4096\n',False),
        ('rsys-negative','RDPMC r3 -1\n',False),('condition-invalid','BR INVALID 17\n',False),
        ('register-invalid','LDI r16 17\n',False),('label-only','only:\nHLT\n',True),
    ]: cases.append((name,source,valid))
    reports=[]
    started=time.monotonic()
    for index,(name,source,valid) in enumerate(cases):
        encoded=source.encode()
        words=assemble(source).words if valid else ()
        folder=output/'cases'/name
        folder.mkdir(parents=True,exist_ok=True)
        src=folder/'source.hex'; expected=folder/'expected.hex'
        src.write_text('\n'.join(f'{b:02x}' for b in encoded)+'\n')
        expected.write_text('\n'.join(f'{w:08x}' for w in words)+'\n')
        for mode in (0,1):
            run=subprocess.run(['vvp',str(binary),f'+SRC={src}',f'+EXPECT={expected}',f'+LEN={len(encoded)}',
                                f'+OUT={len(words)*4}',f'+VALID={int(valid)}',f'+MODE={mode}'],
                                capture_output=True,text=True,timeout=90)
            (folder/f'mode-{mode}.stdout.txt').write_text(run.stdout)
            (folder/f'mode-{mode}.stderr.txt').write_text(run.stderr)
            assert run.returncode==0,(name,mode,run.stdout[-8000:],run.stderr[-8000:])
            match=re.search(r'DIFFERENTIAL PASS cycles=(\d+) reads=(\d+) writes=(\d+) return=([0-9a-f]{16})',run.stdout)
            assert match,(name,mode,run.stdout[-2000:])
            cycles,reads,writes,returned=match.groups()
            actual_words=[int(m.group(1),16) for m in re.finditer(r'^WRITE [0-9a-f]{8} ([0-9a-f]{8})$',run.stdout,re.M)]
            if valid: assert tuple(actual_words)==tuple(words),(name,'host word mismatch')
            reports.append({'name':name,'source_sha256':sha(encoded),'valid':valid,'dma_backpressure':bool(mode),
                            'cycles':int(cycles),'reads':int(reads),'writes':int(writes),'return_hex':returned,
                            'output_words_sha256':sha(b''.join(w.to_bytes(4,'little') for w in actual_words)),
                            'cycle_state_and_active_dma_equal':True,'host_binary_equal':True if valid else None})
        if index%15==0: print(json.dumps({'completed_sources':index+1,'total_sources':len(cases)}),flush=True)
    assert sha((ROOT/'rtl/shama_asm_accel.sv').read_bytes())==sha(after),'candidate changed during review'
    assert sha((ROOT/'rtl/shama_boot_params.svh').read_bytes())==sha(header),'boot header changed during review'
    report={'status':'passed','baseline_commit':baseline,'baseline_source_sha256':sha(before),
            'candidate_source_sha256':sha(after),'boot_header_sha256':sha(header),
            'effective_macros':{macro.decode():re.search(rb'^`define\s+'+macro+rb'\s+([^\n]+)',header,re.M).group(1).decode() for macro in macros},
            'testbench_sha256':sha(testbench.read_bytes()),
            'script_sha256':sha(Path(__file__).read_bytes()),'sources':len(cases),'runs':len(reports),
            'shipped_apps':9,'dma_modes':['always ready','deterministic stalls every 7th/11th cycle'],
            'elapsed_seconds':time.monotonic()-started,'cases':reports,
            'scope':'RTL clock/state and active DMA equivalence; no native redstone timing, area or complete-machine claim'}
    (output/'equivalence-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},indent=2))


if __name__=='__main__':main()
