; ShamaOS boot loader.
; Mount persistent flash, ask the system service to load resident OS pages,
; then launch desktop app id 0.
SYS SYS_BOOT_MOUNT
SYS SYS_BOOT_LOAD_OS
LDI r1 0
SYS SYS_APP_LAUNCH
.loop
SYS SYS_YIELD
JMP .loop
