; ShamaOS hello-world skeleton.
; SYS_DRAW_TEXT expects pointer/length according to the OS ABI.

LDI r1 0x1000
LDI r2 13
SYS SYS_DRAW_TEXT
HLT
