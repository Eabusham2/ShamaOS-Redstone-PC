"""Bounded native input-packet RAM receiver. Only near/far data columns are driven."""
from dataclasses import dataclass, replace
from shamaos.hardware.clock import clock_ports, iter_clock
from shamaos.hardware.logic import TEMPLATES
from shamaos.hardware.memory import DUST, SUPPORT, MemoryBankSpec, bank_ports, iter_memory_bank, repeater
from shamaos.model import Placement, Vec3


@dataclass
class Receiver:
    blocks: list
    inputs: dict
    outputs: dict
    width: int
    words: int
    oscillator: bool


def west_driver(pin):
    # The actual harness writes on the south side of its declared reference.
    return pin.offset(-1,0,-1)


def receiver(width=32, words=4, oscillator=True, *, guard_extra=0):
    if guard_extra not in range(7): raise ValueError("guard_extra must be 0..6 redstone ticks")
    memory=Vec3(100,40,96)
    spec=MemoryBankSpec(words=words,word_bits=width,bit_pitch=5, word_pitch=16)
    blocks=list(iter_memory_bank(memory,spec=spec))
    ports=bank_ports(memory,spec)
    inputs,outputs={},{}
    def wire(x,y,z,block=DUST):
        pos=Vec3(x,y,z)
        blocks.extend((Placement(pos.offset(dy=-1),SUPPORT,"receiver-route"),Placement(pos,block,"receiver-route")))
    def register(name,origin):
        cell=TEMPLATES["DFF"]
        blocks.extend(cell.emitter(origin,name))
        pins={k:origin.offset(v.x,v.y,v.z) for k,v in cell.pins.items()}
        outputs.update({f"{name}_{k}":v for k,v in pins.items()})
        return pins
    last=5*(width-1)
    for name,offset in (("near",0),("far",last)):
        pins=register(name,Vec3(70+offset,42,80))
        inputs[name]=west_driver(pins["D"])
        for x in range(87,101):
            wire(x+offset,43,80,repeater("east") if x in (87,97) else DUST)
        for z in range(81,95):
            wire(100+offset,max(40,43-(z-80)),z,repeater("south") if z in (86,92) else DUST)
    root=Vec3(35,43,76)
    outputs["root"]=root
    taps={83,*[100+16*i for i in range(words)]}
    previous=75
    for z in range(76,101+16*(words-1)):
        refresh=z-previous>=11 and z not in taps
        wire(35,43,z,repeater("south") if refresh else DUST)
        if refresh:previous=z
    previous=24
    for x in range(36,77+last):
        refresh=x-previous>=11 and x not in (76,76+last)
        wire(x,43,83,repeater("east") if refresh else DUST)
        if refresh:previous=x
    for offset in (0,last):
        for z in (82,81):
            wire(76+offset,43,z,repeater("north") if z==81 else DUST)

    for i in range(words):
        gz=97+16*i
        pins=register(f"sel{i}",Vec3(44,42,gz))
        inputs[f"s{i}"]=west_driver(pins["D"])
        gate=Vec3(76,42,gz)
        blocks.extend(TEMPLATES["WRITE_GUARD"].emitter(gate,"phase-qualifier"))
        outputs[f"phase{i}"]=gate.offset(8,1,0)
        for x in range(61,76):
            wire(x,43,gz,repeater("east") if x in (61,72) else DUST)
        for x in range(36,85):
            wire(x,43,gz+3,repeater("east") if x in (36,47,59,71,82) else DUST)
        for x in (50,84):
            for z in (gz+2,gz+1):
                wire(x,43,z,repeater("north",delay=1+(min(guard_extra,3) if x==84 else 0)) if z==gz+1 else DUST)
        for x in range(91,94):
            wire(x,43,gz,repeater("east",delay=1+max(0,guard_extra-3)) if x==91 else DUST)
        for bit in (0,width-1):
            cz=99+16*i
            outputs[f"bit{i}_{bit}"]=Vec3(102+5*bit,43,cz)
            outputs[f"lock{i}_{bit}"]=Vec3(102+5*bit,43,cz-1)
            outputs[f"data{i}_{bit}"]=Vec3(101+5*bit,43,cz)
    if oscillator:
        origin=Vec3(-55,39,45)
        blocks.extend(iter_clock(origin,component="physical-input-clock"))
        src=clock_ports(origin)
        inputs["enable"]=west_driver(src.enable)
        outputs["oscillator"]=src.clock
        for z in range(46,76):
            wire(35,43,z,repeater("south") if z in (46,58,70) else DUST)
    else:
        inputs["clk"]=west_driver(root)
    # Preserve the same position deltas + block facings, including the actual
    # phase-generator geometry, in a positive-coordinate native plot.
    translate=lambda p:p.offset(dx=96)
    blocks=[replace(b,pos=translate(b.pos)) for b in blocks]
    inputs={name:translate(p) for name,p in inputs.items()}
    outputs={name:translate(p) for name,p in outputs.items()}
    occupied={b.pos for b in blocks}
    assert all(p.offset(dz=1) not in occupied for p in inputs.values())
    return Receiver(blocks,inputs,outputs,width,words,oscillator)
