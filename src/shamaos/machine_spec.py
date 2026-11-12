"""Owner-approved physical capacities, amended on 2026-11-12.

RAM, flash, cache and VRAM were explicitly reduced to 192 KiB, 500 KiB,
8 KiB and 16 KiB. Display and CPU features remain unchanged.
"""

RAM_BYTES = 192 << 10
FLASH_BYTES = 500 << 10
FLASH_WRITE_LIMIT_PERCENT = 95
FLASH_WRITE_LIMIT_BYTES = FLASH_BYTES * FLASH_WRITE_LIMIT_PERCENT // 100
CACHE_BYTES = 8 << 10
VRAM_BYTES = 16 << 10
BANK_BYTES = 4 << 10
RAM_BANKS = RAM_BYTES // BANK_BYTES
FLASH_BANKS = FLASH_BYTES // BANK_BYTES
