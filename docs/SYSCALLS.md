# ShamaOS System Call ABI

## Calling convention

- syscall instruction: `SYS id`
- `r1-r6`: arguments.
- `r1`: primary return value.
- `r2`: secondary result/error detail.
- `r13`: stack pointer convention.
- `r14`: frame/temp convention.
- `r15`: reserved kernel/scratch by ABI when required.

Exact register preservation rules are finalized in the executable ISA metadata.

## IDs

Initial symbolic syscall set:

### Process

- `SYS_EXIT`
- `SYS_YIELD`
- `SYS_GET_EVENT`
- `SYS_GET_TIME`
- `SYS_GET_COUNTER`

### Memory

- `SYS_ALLOC`
- `SYS_FREE`
- `SYS_RAM_USAGE`
- `SYS_CACHE_USAGE`
- `SYS_CACHE_FLUSH_APP`

### Filesystem

- `SYS_FILE_CREATE`
- `SYS_FILE_OPEN`
- `SYS_FILE_CLOSE`
- `SYS_FILE_READ`
- `SYS_FILE_WRITE`
- `SYS_FILE_TRUNCATE`
- `SYS_FILE_RENAME`
- `SYS_FILE_DELETE`
- `SYS_FILE_STAT`
- `SYS_FILE_LIST`
- `SYS_FLASH_USAGE`

### Input

- `SYS_GET_KEY`
- `SYS_GET_CONTROLLER`

### Graphics/UI

- `SYS_GPU_SUBMIT`
- `SYS_DRAW_TEXT`
- `SYS_DRAW_RECT`
- `SYS_MESSAGE_BOX`
- `SYS_GPU_FENCE`

### Miner

- `SYS_MINER_LOG_RESULT`
- `SYS_MINER_SAVE_STATE`
- `SYS_MINER_LOAD_HISTORY`

## File handles

Open returns a small integer handle. Handles are per process/app. Exit closes all remaining handles.

## Errors

Syscalls return success/failure in the ABI-defined result convention. Source code should use symbolic error constants rather than numeric literals.

## Security model

This is a Minecraft redstone OS, not a hostile multi-user security boundary. The kernel still enforces handle ownership and allocator boundaries to prevent accidental corruption by normal apps.
