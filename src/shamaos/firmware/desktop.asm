; ShamaOS Desktop
; App IDs:
; 0 Desktop, 1 Editor, 2 File Explorer, 3 Bitcoin Miner,
; 4 System Monitor, 5 Terminal, 6 Calculator, 7 Paint, 8 Settings.

define EVT_UP 0x10
define EVT_DOWN 0x11
define EVT_LEFT 0x12
define EVT_RIGHT 0x13
define EVT_A 0x14

LDI r8 1

.redraw
MOV r1 r8
SYS SYS_UI_REDRAW
SYS SYS_UI_STATUSBAR

.loop
SYS SYS_GET_EVENT
CMP r1 r0
BR.EQ .idle

; Extract event code: (r1 >> 10) & 0xff.
MOV r9 r1
LDI r10 10
SHR r9 r9 r10
LDI r10 0xff
AND r9 r9 r10

LDI r10 EVT_UP
CMP r9 r10
BR.EQ .prev
LDI r10 EVT_LEFT
CMP r9 r10
BR.EQ .prev
LDI r10 EVT_DOWN
CMP r9 r10
BR.EQ .next
LDI r10 EVT_RIGHT
CMP r9 r10
BR.EQ .next
LDI r10 EVT_A
CMP r9 r10
BR.EQ .open

; Kernel handles universal Home/Exit/Editor/Files.
SYS SYS_APP_EVENT
JMP .redraw

.prev
LDI r10 1
CMP r8 r10
BR.LE .redraw
DEC r8
JMP .redraw

.next
LDI r10 8
CMP r8 r10
BR.GE .redraw
INC r8
JMP .redraw

.open
MOV r1 r8
SYS SYS_APP_LAUNCH
HLT

.idle
; Keep RAM/cache/flash indicators live even without controller input.
MOV r1 r8
SYS SYS_UI_STATUSBAR
SYS SYS_YIELD
JMP .loop
