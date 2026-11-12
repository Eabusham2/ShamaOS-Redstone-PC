"""Resource totals reconcile independent filesystem and physical-map evidence."""
from dataclasses import replace

import pytest

from shamaos.flashfs import FileType, ShamaFS
from shamaos.machine_spec import CACHE_BYTES, FLASH_BYTES, RAM_BYTES, VRAM_BYTES
from shamaos.memory_map import APP_HEAP_RESERVED_SPANS, HEAP_BASE, HEAP_PAGE_BYTES
from shamaos.os_image import APP_IDS, BUNDLE_ORDER, FIRMWARE, build_default_os_image
from shamaos.software_resources import software_resource_usage


@pytest.fixture(scope="module")
def image():
    return build_default_os_image()


def test_flash_categories_and_program_shares_reconcile_allocated_image(image):
    fs = ShamaFS.deserialize(image.image)
    report = software_resource_usage(image)
    details = report["flash_details"]
    metadata = fs.data_start * fs.block_size
    file_allocation = sum(entry.block_count * fs.block_size for entry in fs.list_files())
    assert report["flash"]["used_bytes"] == metadata + file_allocation == fs.used_bytes
    assert sum(row["allocated_bytes"] for row in details["categories"]) == fs.used_bytes
    assert sum(row["allocated_bytes"] for row in details["files"]) == file_allocation
    assert sum(row["attributed_bytes"] for row in details["programs"].values()) + details["shared_and_other_bytes"] == fs.used_bytes
    loader_records = 0
    for name, (source_name, binary_name, _) in FIRMWARE.items():
        source, binary = fs.stat(source_name), fs.stat(binary_name)
        program = details["programs"][name]
        assert program["source_logical_bytes"] == len(source.data)
        assert program["source_allocated_bytes"] == source.block_count * fs.block_size
        assert program["executable_logical_bytes"] == len(binary.data)
        assert program["executable_allocated_bytes"] == binary.block_count * fs.block_size
        record = len(binary.data) + 4 if name in BUNDLE_ORDER else 0
        assert program["packed_loader_record_bytes"] == record
        loader_records += record
    assert loader_records == image.bundle_bytes
    assert loader_records + details["bundle_allocation_padding_bytes"] == fs.stat("boot.bundle").block_count * fs.block_size


def test_flash_report_counts_new_files_and_block_rounding(image):
    fs = ShamaFS.deserialize(image.image)
    before = software_resource_usage(image)
    fs.create_file("documents/new.txt", b"x" * 257, FileType.TXT)
    after = software_resource_usage(replace(image, image=fs.serialize()))
    assert after["flash"]["used_bytes"] == before["flash"]["used_bytes"] + 512
    assert after["flash"]["files"] == before["flash"]["files"] + 1
    assert after["flash_details"]["shared_and_other_bytes"] == before["flash_details"]["shared_and_other_bytes"] + 512
    assert after["flash_details"]["programs"] == before["flash_details"]["programs"]
    other = next(row for row in after["flash_details"]["categories"] if row["category"] == "other")
    assert (other["logical_file_bytes"], other["allocated_bytes"]) == (257, 512)


def test_every_app_fits_with_peak_workspace_and_all_eligible_heap_pages(image):
    report = software_resource_usage(image)
    assert set(row["app"] for row in report["apps"]) == set(APP_IDS)
    assert report["capacity_bytes"] == {
        "ram": RAM_BYTES, "flash": FLASH_BYTES, "fast": CACHE_BYTES,
        "vram": VRAM_BYTES, "combined": RAM_BYTES + FLASH_BYTES + CACHE_BYTES + VRAM_BYTES,
    }
    for app in report["apps"]:
        reserved = APP_HEAP_RESERVED_SPANS.get(app["app"], ())
        # Determine eligible pages directly from ownership, independently of the
        # report's coalescing helper. A partially occupied page is unavailable.
        eligible = [base for base in range(HEAP_BASE, RAM_BYTES, HEAP_PAGE_BYTES)
                    if not any(base < start + size and start < base + HEAP_PAGE_BYTES
                               for start, size in reserved)]
        reported = [base for span in app["heap_spans"]
                    for base in range(span["base"], span["base"] + span["bytes"], HEAP_PAGE_BYTES)]
        assert reported == eligible
        assert app["available_heap_bytes"] == len(eligible) * HEAP_PAGE_BYTES
        assert app["peak_ram_bytes_with_all_available_heap"] <= RAM_BYTES
        assert app["fast_bytes"] <= CACHE_BYTES
        assert app["vram_shared_reserved_bytes"] <= VRAM_BYTES
        expected = app["ram_bytes"] + app["fast_bytes"] + report["flash"]["used_bytes"] + report["vram"]["total_reserved_bytes"]
        assert app["all_memory_pools_used_bytes"] == expected
        assert app["ram_percent"] == pytest.approx(100 * app["ram_bytes"] / RAM_BYTES)
        assert app["installed_flash_attributed_bytes"] == report["flash_details"]["programs"][app["app"]]["attributed_bytes"]


def test_resource_report_rejects_an_image_for_a_different_physical_capacity(image):
    with pytest.raises(ValueError, match="agreed flash capacity"):
        software_resource_usage(replace(image, image=image.image[:-256]))
