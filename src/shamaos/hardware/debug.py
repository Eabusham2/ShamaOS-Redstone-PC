"""Architectural debug wall with open, independently routable input faces."""
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

DEBUG_COLUMNS = 32
DEBUG_ROWS = sum((width+DEBUG_COLUMNS-1)//DEBUG_COLUMNS for _,width in DEBUG_FIELDS)
DEBUG_BOUNDS = ((0,0,-3),((DEBUG_COLUMNS-1)*4,(DEBUG_ROWS-1)*4+1,0))


def debug_ports(origin: Vec3, *, fields=DEBUG_FIELDS, columns: int = DEBUG_COLUMNS) -> dict[str,tuple[Vec3,...]]:
    if columns < 1:
        raise ValueError("debug panel needs at least one column")
    ports={};row=0
    for name,width in fields:
        ports[name]=tuple(origin.offset((bit%columns)*4,1+(row+bit//columns)*4,0) for bit in range(width))
        row+=(width+columns-1)//columns
    return ports


def iter_debug_panel(origin: Vec3,component: str='debug-panel', *, fields=DEBUG_FIELDS, columns: int = DEBUG_COLUMNS) -> Iterator[Placement]:
    """Lamps face north; all input pins escape south into empty space.

    Four-block X/Y pitch isolates each strong lamp driver. Putting rows above
    one another leaves their +Z routing corridors open; a flat four-block Z
    pitch made every input route cross the next lamp's wiring.
    """
    lamp=BlockState.of('minecraft:redstone_lamp',lit='false')
    for name,pins in debug_ports(origin,fields=fields,columns=columns).items():
        for pin in pins:
            yield Placement(pin.offset(dy=-1),SUPPORT,f'{component}:{name}')
            yield Placement(pin,DUST,f'{component}:{name}')
            yield Placement(pin.offset(dy=-1,dz=-1),SUPPORT,f'{component}:{name}')
            yield Placement(pin.offset(dz=-1),repeater('north'),f'{component}:{name}')
            yield Placement(pin.offset(dz=-2),SUPPORT,f'{component}:{name}')
            yield Placement(pin.offset(dz=-3),lamp,f'{component}:{name}')
