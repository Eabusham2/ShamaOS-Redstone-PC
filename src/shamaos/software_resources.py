"""Reproducible software occupancy for the actual assembled machine image.

This is allocation accounting, not CPU/GPU load or physical gate utilization.
Only one foreground app runs at a time. Fixed VRAM reservations are shared by
all apps and must not be summed once per application.
"""
from __future__ import annotations

from .flashfs import MAX_FILES, FileType, ShamaFS
from .machine_spec import CACHE_BYTES, FLASH_BYTES, FLASH_WRITE_LIMIT_BYTES, RAM_BYTES, VRAM_BYTES
from .os_image import APP_IDS, APP_SLOT_BYTES, BUNDLE_ORDER, OSImage, build_default_os_image


# The same fixed reservations are owned/cleared by shama_services.sv.
# Include the complete source buffer. Editor uses dedicated assembly staging
# only when the source leaves insufficient room for output in the same buffer.
# These are additional to resident kernel and foreground code bytes.
APP_WORKSPACE_BYTES = {
    "desktop": 0,
    "editor": 512 + 68 + 65536,
    "files": 512 + 68 + 65536,
    "miner": 4096 + 1280,
    "monitor": 0,
    "terminal": 1024,
    "calculator": 0,
    "paint": 272,
    "settings": 272,
}


def software_resource_usage(image: OSImage | None = None) -> dict[str, object]:
    """Account for the initial image and each built-in foreground app.

    RAM follows the services' public counter: kernel bytes + app executable
    backing + app workspace, with no extra SYS_ALLOC pages. The software-managed
    fast memory holds the same executable and is counted in its separate pool.
    A custom image must still match the owner's current physical flash size.
    """
    image = image or build_default_os_image()
    if len(image.image) != FLASH_BYTES:
        raise ValueError("software resource report requires the agreed flash capacity")
    fs = ShamaFS.deserialize(image.image)
    entries = fs.list_files()
    frame_bytes = (320 * 180 + 7) // 8
    vram_reserved = 2 * frame_bytes + 256 + 1024
    combined_capacity = RAM_BYTES + FLASH_BYTES + CACHE_BYTES + VRAM_BYTES
    kernel_bytes = image.slot_binary_lengths["kernel"]
    rows = []
    for app in APP_IDS:
        executable = image.slot_binary_lengths[app]
        workspace = APP_WORKSPACE_BYTES[app]
        ram_used = kernel_bytes + executable + workspace
        extra_staging = APP_SLOT_BYTES if app == "editor" else 0
        peak_ram = ram_used + extra_staging
        if peak_ram > RAM_BYTES or executable > CACHE_BYTES:
            raise ValueError(f"{app} does not fit the agreed physical memory")
        combined_used = ram_used + executable + fs.used_bytes + vram_reserved
        rows.append({
            "app": app,
            "ram_bytes": ram_used,
            "ram_kib": ram_used / 1024,
            "ram_percent": 100 * ram_used / RAM_BYTES,
            "workspace_bytes": workspace,
            "conditional_assembly_staging_bytes": extra_staging,
            "peak_ram_bytes_without_heap": peak_ram,
            "peak_ram_percent_without_heap": 100 * peak_ram / RAM_BYTES,
            "fast_bytes": executable,
            "fast_kib": executable / 1024,
            "fast_percent": 100 * executable / CACHE_BYTES,
            "vram_shared_reserved_bytes": vram_reserved,
            "vram_shared_reserved_percent": 100 * vram_reserved / VRAM_BYTES,
            "all_memory_pools_used_bytes": combined_used,
            "all_memory_pools_used_percent": 100 * combined_used / combined_capacity,
        })
    return {
        "scope": "assembled initial image and hardware fixed-workspace accounting; "
                 "one foreground app, no extra heap allocations; "
                 "shared VRAM reservations, not live GPU load",
        "capacity_bytes": {
            "ram": RAM_BYTES, "flash": FLASH_BYTES, "fast": CACHE_BYTES,
            "vram": VRAM_BYTES, "combined": combined_capacity,
        },
        "flash": {
            "used_bytes": fs.used_bytes,
            "free_bytes": fs.free_bytes,
            "used_percent": 100 * fs.used_bytes / FLASH_BYTES,
            "write_limit_bytes": FLASH_WRITE_LIMIT_BYTES,
            "write_limit_percent": 100 * FLASH_WRITE_LIMIT_BYTES / FLASH_BYTES,
            "remaining_before_write_limit_bytes": max(0, FLASH_WRITE_LIMIT_BYTES - fs.used_bytes),
            "bundle_bytes": image.bundle_bytes,
            "bundle_padding_removed_bytes": len(BUNDLE_ORDER) * APP_SLOT_BYTES - image.bundle_bytes,
            "metadata_bytes": fs.data_start * fs.block_size,
            "entries": len(entries),
            "files": sum(entry.file_type != FileType.DIR for entry in entries),
            "directories": sum(entry.file_type == FileType.DIR for entry in entries),
            "maximum_entries": MAX_FILES,
        },
        "vram": {
            "frame_bytes": frame_bytes,
            "two_frames_bytes": 2 * frame_bytes,
            "two_frames_percent": 100 * 2 * frame_bytes / VRAM_BYTES,
            "scroll_scratch_bytes": 0,
            "framebuffer_offsets": [0, frame_bytes],
            "text_staging_offset": 2 * frame_bytes,
            "sprite_staging_offset": 2 * frame_bytes + 256,
            "text_staging_bytes": 256,
            "sprite_staging_bytes": 1024,
            "total_reserved_bytes": vram_reserved,
            "reserved_percent": 100 * vram_reserved / VRAM_BYTES,
            "unused_bytes": VRAM_BYTES - vram_reserved,
        },
        "heap": {
            "base": 0xC000, "end_exclusive": 0x18000,
            "bytes": 48 << 10, "ram_percent": 100 * (48 << 10) / RAM_BYTES,
            "page_bytes": 4096, "pages": 12, "maximum_allocations": 16,
        },
        "kernel_bytes": kernel_bytes,
        "apps": rows,
    }
