; ShamaOS boot loader.
; Mount persistent flash, ask the system service to load resident OS pages,
; then launch desktop app id 0. A rejected mount/load halts with its r1/r2
; diagnostics intact; reset after replacing/repairing the flash image.
SYS SYS_BOOT_MOUNT
CMP r2 r0
BR.NE .failed
SYS SYS_BOOT_LOAD_OS
CMP r2 r0
BR.NE .failed
LDI r1 0
SYS SYS_APP_LAUNCH
CMP r2 r0
BR.NE .failed
.loop
SYS SYS_YIELD
JMP .loop
.failed
HLT
