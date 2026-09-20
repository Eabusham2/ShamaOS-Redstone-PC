; Bitcoin miner control-flow example.
; Memory layout used by this example:
; r1 = 80-byte header pointer
; r2 = 32-byte hash output pointer
; r3 = 32-byte target pointer

.start
LDI r1 0x2000
LDI r2 0x2100
LDI r3 0x2200

.loop
DSHA256 r2 r1
HASHCMP r2 r3
BR.VALID .found
INCNONCE r1
JMP .loop

.found
SYS SYS_MINER_LOG_RESULT
HLT
