"""Owner-approved physical capacities, amended on 2026-11-14.

RAM, flash, cache and VRAM were explicitly reduced to 96 KiB, 200 KiB,
4 KiB and 16 KiB. The original 8 KiB program window retains its RAM backing;
only its first 4 KiB require duplicate physical cache cells.
"""

RAM_BYTES = 96 << 10
FLASH_BYTES = 200 << 10
FLASH_WRITE_LIMIT_PERCENT = 95
FLASH_WRITE_LIMIT_BYTES = FLASH_BYTES * FLASH_WRITE_LIMIT_PERCENT // 100
CACHE_BYTES = 4 << 10
VRAM_BYTES = 16 << 10
BANK_BYTES = 4 << 10
RAM_BANKS = RAM_BYTES // BANK_BYTES
FLASH_BANKS = (FLASH_BYTES + BANK_BYTES - 1) // BANK_BYTES
