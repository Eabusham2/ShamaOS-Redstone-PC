"""Physical RAM ownership shared by the image, services and resource reports.

The CPU maps addresses below CACHE_BYTES to its separate executable cache.
Services DMA can still address the underlying RAM there, so it holds the app's
write-through backing without consuming a second CPU-visible code slot.
Foreground workspaces may overlap only because app replacement drains saves,
frees allocations and clears the outgoing workspace before starting its peer.
"""

from .machine_spec import CACHE_BYTES, RAM_BYTES


APP_SLOT_BYTES = 16 << 10  # assembly/save capacity; execution is limited below
APP_RAM_BASE = 0
APP_BACKING_BYTES = CACHE_BYTES
KERNEL_RAM_BASE = 0x2000
KERNEL_SLOT_BYTES = 0x400
EDIT_NAME = 0x2400
EDIT_NAME_BYTES = 512
EDIT_CTX = 0x2600
EDIT_CTX_BYTES = 68
EDIT_BUF = 0x4000
EDIT_BUFFER_BYTES = 64 << 10
RUN_STAGING = 0x14000
MINER_BASE = 0x4000
MINER_RECORD = MINER_BASE + 0x200
MINER_SOFT_BASE = 0x5000
TERMINAL_BUF = MINER_SOFT_BASE
SETTINGS_BASE = 0x4000
PAINT_BASE = 0x4200
HEAP_BASE = 0x3000
HEAP_PAGE_BYTES = 4096
HEAP_PAGES = (RAM_BYTES - HEAP_BASE) // HEAP_PAGE_BYTES

# Page reservations include all addresses an app can use later in its lifetime.
# In particular Editor cannot lend its optional 16 KiB assembly staging to the
# heap before assembling a long source. Files needs only the shared source span.
APP_HEAP_RESERVED_SPANS = {
    "editor": ((EDIT_BUF, EDIT_BUFFER_BYTES), (RUN_STAGING, APP_SLOT_BYTES)),
    "files": ((EDIT_BUF, EDIT_BUFFER_BYTES),),
    "miner": ((MINER_BASE, 4096), (MINER_SOFT_BASE, 1280)),
    "terminal": ((TERMINAL_BUF, 1024),),
    "settings": ((SETTINGS_BASE, 272),),
    "paint": ((PAINT_BASE, 272),),
}


def app_heap_spans(app: str) -> tuple[tuple[int, int], ...]:
    """Return initially allocatable page runs as (base, byte count) pairs.

    This is page capacity, not unused physical byte occupancy: partially used
    workspace pages remain reserved until the foreground app closes.
    Apps without a built-in workspace, including staged user programs, receive
    the complete heap; this is the same fallback as the services app-ID switch.
    """
    reserved = APP_HEAP_RESERVED_SPANS.get(app, ())
    spans: list[tuple[int, int]] = []
    for page in range(HEAP_PAGES):
        base = HEAP_BASE + page * HEAP_PAGE_BYTES
        if any(base < start + size and start < base + HEAP_PAGE_BYTES
               for start, size in reserved):
            continue
        if spans and spans[-1][0] + spans[-1][1] == base:
            start, size = spans[-1]
            spans[-1] = (start, size + HEAP_PAGE_BYTES)
        else:
            spans.append((base, HEAP_PAGE_BYTES))
    return tuple(spans)


RTL_MEMORY_CONSTANTS = {
    "APP_RAM_BASE": APP_RAM_BASE,
    "APP_BACKING_BYTES": APP_BACKING_BYTES,
    "KERNEL_RAM_BASE": KERNEL_RAM_BASE,
    "KERNEL_SLOT_BYTES": KERNEL_SLOT_BYTES,
    "EDIT_NAME": EDIT_NAME,
    "EDIT_CTX": EDIT_CTX,
    "EDIT_BUF": EDIT_BUF,
    "RUN_STAGING": RUN_STAGING,
    "MINER_BASE": MINER_BASE,
    "MINER_RECORD": MINER_RECORD,
    "MINER_SOFT_BASE": MINER_SOFT_BASE,
    "TERMINAL_BUF": TERMINAL_BUF,
    "SETTINGS_BASE": SETTINGS_BASE,
    "PAINT_BASE": PAINT_BASE,
    "HEAP_BASE": HEAP_BASE,
    "HEAP_PAGE_BYTES": HEAP_PAGE_BYTES,
    "HEAP_PAGES": HEAP_PAGES,
}
