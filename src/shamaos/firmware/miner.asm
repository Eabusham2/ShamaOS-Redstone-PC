; ShamaOS Bitcoin Miner
; Native DSHA256/HASHCMP/INCNONCE execute inside the CPU.
; r4 = header ptr, r5 = hash ptr, r6 = target ptr
.redraw
SYS SYS_UI_MINER_VIEW
.loop
SYS SYS_GET_EVENT
CMP r1 r0
BR.NE .event
; r7 is the app's running flag.
CMP r7 r0
BR.EQ .idle
DSHA256 r5 r4
HASHCMP r5 r6
BR.VALID .found
INCNONCE r4
INC r8
SYS SYS_UI_MINER_VIEW
JMP .loop
.found
SYS SYS_MINER_LOG_RESULT
LDI r7 0
SYS SYS_UI_MINER_VIEW
JMP .loop
.event
SYS SYS_APP_EVENT
SYS SYS_UI_MINER_VIEW
.idle
SYS SYS_YIELD
JMP .loop
