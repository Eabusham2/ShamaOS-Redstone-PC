; ShamaOS Paint
; D-pad moves cursor, A draws, B erases. Pixels persist in GPU VRAM.

define EVT_UP 0x10
define EVT_DOWN 0x11
define EVT_LEFT 0x12
define EVT_RIGHT 0x13
define EVT_A 0x14
define EVT_B 0x15
define GPU_BASE 0x02000000

LDI r8 160
LDI r9 90

.redraw
MOV r1 r8
MOV r2 r9
SYS SYS_UI_PAINT_VIEW

.loop
SYS SYS_GET_EVENT
CMP r1 r0
BR.EQ .idle

MOV r12 r1
LDI r14 10
SHR r12 r12 r14
LDI r14 0xff
AND r12 r12 r14

LDI r14 EVT_UP
CMP r12 r14
BR.EQ .up
LDI r14 EVT_DOWN
CMP r12 r14
BR.EQ .down
LDI r14 EVT_LEFT
CMP r12 r14
BR.EQ .left
LDI r14 EVT_RIGHT
CMP r12 r14
BR.EQ .right
LDI r14 EVT_A
CMP r12 r14
BR.EQ .draw
LDI r14 EVT_B
CMP r12 r14
BR.EQ .erase

SYS SYS_APP_EVENT
JMP .redraw

.up
CMP r9 r0
BR.EQ .redraw
DEC r9
JMP .loop
.down
LDI r14 179
CMP r9 r14
BR.GE .redraw
INC r9
JMP .loop
.left
CMP r8 r0
BR.EQ .redraw
DEC r8
JMP .loop
.right
LDI r14 319
CMP r8 r14
BR.GE .redraw
INC r8
JMP .loop

.draw
LDI r10 0x02
CALL .pixel_command
JMP .loop

.erase
LDI r10 0x03
CALL .pixel_command
JMP .loop

.pixel_command
PUSH r1
PUSH r2
PUSH r3
LDI r1 GPU_BASE

; Copy current visible frame into the back buffer.
LDI r2 0x13
STW r1 r2 4
LDI r2 1
STW r1 r2 40

STW r1 r8 8
STW r1 r9 12
STW r1 r10 4
LDI r2 1
STW r1 r2 40

; Swap modified back buffer to screen.
LDI r2 0x14
STW r1 r2 4
STW r1 r2 40
POP r3
POP r2
POP r1
RET

.idle
SYS SYS_YIELD
JMP .loop
