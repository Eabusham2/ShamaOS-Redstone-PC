"""Non-destructive architectural debug lamps; one isolated input per bit."""
from collections.abc import Iterator
from ..model import BlockState, Placement, Vec3
from .memory import DUST, SUPPORT, repeater

DEBUG_FIELDS = (
    ('debug_pc',32),('debug_instruction',32),('debug_flags',5),
    ('debug_state',6),('debug_stall',1),('debug_sha_round',6),
    ('debug_fault',8),('debug_app',4),('debug_lifecycle',32),
    ('debug_ram_used',32),('debug_cache_used',32),('debug_flash_used',32),
    ('input_overflow',1),('shutdown_ready',1),('halted',1),
    ('debug_registers',512),('debug_sha_state',256),
)

def debug_ports(origin: Vec3) -> dict[str,tuple[Vec3,...]]:
    ports={};row=0
    for name,width in DEBUG_FIELDS:
        ports[name]=tuple(origin.offset((bit%32)*4,1,(row+bit//32)*4) for bit in range(width))
        row+=(width+31)//32
    return ports


def iter_debug_panel(origin: Vec3,component: str='debug-panel') -> Iterator[Placement]:
    lamp=BlockState.of('minecraft:redstone_lamp',lit='false')
    for name,pins in debug_ports(origin).items():
        for pin in pins:
            yield Placement(pin.offset(dy=-1),SUPPORT,f'{component}:{name}')
            yield Placement(pin,DUST,f'{component}:{name}')
            yield Placement(pin.offset(dy=-1,dz=1),SUPPORT,f'{component}:{name}')
            yield Placement(pin.offset(dz=1),repeater('south'),f'{component}:{name}')
            yield Placement(pin.offset(dz=2),SUPPORT,f'{component}:{name}')
            yield Placement(pin.offset(dy=1,dz=2),lamp,f'{component}:{name}')
