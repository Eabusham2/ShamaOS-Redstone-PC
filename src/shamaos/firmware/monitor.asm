; ShamaOS System Monitor
; r1 cycle counter, r2 retired-instruction counter are passed to the GUI kernel.

.loop
RDPMC r1 0
RDPMC r2 1
SYS SYS_UI_MONITOR_VIEW

SYS SYS_GET_EVENT
CMP r1 r0
BR.EQ .idle
SYS SYS_APP_EVENT
JMP .loop

.idle
SYS SYS_YIELD
JMP .loop
