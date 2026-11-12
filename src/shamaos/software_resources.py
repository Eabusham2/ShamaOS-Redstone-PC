"""Reproducible software occupancy for the actual assembled machine image.

This is allocation accounting, not CPU/GPU load or physical gate utilization.
Only one foreground app runs at a time. Fixed VRAM reservations are shared by
all apps and must not be summed once per application.
"""
from __future__ import annotations

from .flashfs import MAX_FILES, FileType, ShamaFS
from .machine_spec import CACHE_BYTES, FLASH_BYTES, FLASH_WRITE_LIMIT_BYTES, RAM_BYTES, VRAM_BYTES
from .memory_map import HEAP_BASE, HEAP_PAGE_BYTES, HEAP_PAGES, app_heap_spans
from .os_image import APP_IDS, APP_SLOT_BYTES, BUNDLE_ORDER, FIRMWARE, OSImage, build_default_os_image


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


def flash_resource_usage(image: OSImage) -> dict[str, object]:
    """Explain every allocated initial-image byte without double counting.

    Each ordinary file consumes whole filesystem blocks. Boot records share one
    packed file, so per-program attribution uses its exact record length and
    reports the final allocation padding separately. Metadata is shared, not
    charged again to every application.
    """
    fs = ShamaFS.deserialize(image.image)
    entries = {entry.name: entry for entry in fs.list_files()}
    source_names = {source for source, _, _ in FIRMWARE.values()}
    executable_names = {executable for _, executable, _ in FIRMWARE.values()}
    labels = {
        "metadata": "Filesystem metadata and journal",
        "sources": "Complete editable program sources",
        "executables": "Separate program executable files",
        "boot_bundle": "Packed boot and application loader bundle",
        "miner_history": "Miner history: 256 complete records",
        "miner_state": "Miner saved state",
        "documents": "Welcome documents",
        "other": "Other allocated files",
    }
    groups = {key: {"category": key, "description": label, "allocated_bytes": 0,
                    "logical_file_bytes": 0, "files": 0}
              for key, label in labels.items()}
    groups["metadata"]["allocated_bytes"] = fs.data_start * fs.block_size
    inventory = []
    for entry in entries.values():
        allocated = entry.block_count * fs.block_size
        inventory.append({"name": entry.name, "type": entry.file_type.name,
                          "logical_bytes": len(entry.data), "allocated_bytes": allocated,
                          "capacity_percent": 100 * allocated / len(image.image)})
        if entry.file_type == FileType.DIR:
            continue
        category = ("sources" if entry.name in source_names else
                    "executables" if entry.name in executable_names else
                    "boot_bundle" if entry.name == "boot.bundle" else
                    "miner_history" if entry.name == "miner-history.log" else
                    "miner_state" if entry.name == "miner-state.cfg" else
                    "documents" if entry.name in ("welcome.txt", "documents/welcome.txt") else
                    "other")
        group = groups[category]
        group["allocated_bytes"] += allocated
        group["logical_file_bytes"] += len(entry.data)
        group["files"] += 1
    for group in groups.values():
        group["capacity_percent"] = 100 * group["allocated_bytes"] / len(image.image)
    programs = {}
    for name, (source_name, executable_name, _) in FIRMWARE.items():
        source = entries[source_name]
        binary = entries[executable_name]
        source_bytes = source.block_count * fs.block_size
        executable_bytes = binary.block_count * fs.block_size
        bundle_bytes = 4 + image.slot_binary_lengths[name] if name in BUNDLE_ORDER else 0
        persistent_bytes = sum(entries[path].block_count * fs.block_size
                               for path in ("miner-state.cfg", "miner-history.log")
                               if name == "miner" and path in entries)
        attributed = source_bytes + executable_bytes + bundle_bytes + persistent_bytes
        programs[name] = {
            "source_logical_bytes": len(source.data), "source_allocated_bytes": source_bytes,
            "executable_logical_bytes": len(binary.data), "executable_allocated_bytes": executable_bytes,
            "packed_loader_record_bytes": bundle_bytes, "persistent_allocated_bytes": persistent_bytes,
            "attributed_bytes": attributed, "capacity_percent": 100 * attributed / len(image.image),
        }
    attributed = sum(program["attributed_bytes"] for program in programs.values())
    return {
        "categories": [group for group in groups.values() if group["allocated_bytes"]],
        "files": inventory, "programs": programs,
        "program_attributed_bytes": attributed,
        "shared_and_other_bytes": fs.used_bytes - attributed,
        "bundle_allocation_padding_bytes": entries["boot.bundle"].block_count * fs.block_size - image.bundle_bytes,
        "attribution_note": "Program shares include their complete editable source, separate executable, "
                            "exact packed loader record and installed persistent data. Shared metadata, "
                            "documents and final bundle block padding are counted once separately.",
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
    flash_details = flash_resource_usage(image)
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
        heap_spans = app_heap_spans(app)
        heap_bytes = sum(size for _, size in heap_spans)
        if peak_ram + heap_bytes > RAM_BYTES or executable > CACHE_BYTES:
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
            "available_heap_bytes": heap_bytes,
            "largest_initial_heap_allocation_bytes": max((size for _, size in heap_spans), default=0),
            "heap_spans": [{"base": base, "bytes": size} for base, size in heap_spans],
            "peak_ram_bytes_with_all_available_heap": peak_ram + heap_bytes,
            "peak_ram_percent_with_all_available_heap": 100 * (peak_ram + heap_bytes) / RAM_BYTES,
            "fast_bytes": executable,
            "fast_kib": executable / 1024,
            "fast_percent": 100 * executable / CACHE_BYTES,
            "vram_shared_reserved_bytes": vram_reserved,
            "vram_shared_reserved_percent": 100 * vram_reserved / VRAM_BYTES,
            "all_memory_pools_used_bytes": combined_used,
            "all_memory_pools_used_percent": 100 * combined_used / combined_capacity,
            "peak_all_memory_pools_used_bytes_without_heap": combined_used + extra_staging,
            "peak_all_memory_pools_used_percent_without_heap": 100 * (combined_used + extra_staging) / combined_capacity,
            "installed_flash_attributed_bytes": flash_details["programs"][app]["attributed_bytes"],
            "installed_flash_capacity_percent": flash_details["programs"][app]["capacity_percent"],
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
            "base": HEAP_BASE, "end_exclusive": RAM_BYTES,
            "bytes": HEAP_PAGES * HEAP_PAGE_BYTES,
            "ram_percent": 100 * HEAP_PAGES * HEAP_PAGE_BYTES / RAM_BYTES,
            "page_bytes": HEAP_PAGE_BYTES, "pages": HEAP_PAGES, "maximum_allocations": 16,
            "availability_note": "This is the page arena before per-app workspace exclusions. "
                                 "Use each app's available_heap_bytes and heap_spans; "
                                 "Editor staging remains excluded even before assembly begins.",
        },
        "kernel_bytes": kernel_bytes,
        "flash_details": flash_details,
        "apps": rows,
    }
