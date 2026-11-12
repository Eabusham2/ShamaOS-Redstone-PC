; Paint uses a non-destructive scanout cursor. Movement never copies a cursor
; mark into the artwork. D-pad/arrow keys move, A draws, B erases, C clears.
; Settings restores cursor visibility/shape from the persistent versioned file.
define GPU 0x02000000
define CONFIG 0x000b5000
define CONFIG_NAME 0x000b5100
LDI r8 160
LDI r9 90
LDI r11 1
LDI r12 CONFIG_NAME
LDI r13 0x74746573
STW r12 r13 0
LDI r13 0x73676e69
STW r12 r13 4
LDI r13 0x6766632e
STW r12 r13 8
STB r12 r0 12
LDI r1 CONFIG_NAME
SYS SYS_FILE_OPEN
CMP r2 r0
BR.NE .redraw
PUSH r1
LDI r2 CONFIG
LDI r3 16
SYS SYS_FILE_READ
POP r1
SYS SYS_FILE_CLOSE
LDI r12 CONFIG
LDW r1 r12 0
LDI r2 0x53434647
CMP r1 r2
BR.NE .redraw
LDW r11 r12 4
LDI r1 3
AND r11 r11 r1
.redraw
MOV r1 r8
MOV r2 r9
SYS SYS_UI_PAINT_VIEW
CALL .cursor
.loop
SYS SYS_GET_EVENT
CMP r1 r0
BR.EQ .idle
MOV r12 r1
LDI r14 10
SHR r12 r12 r14
LDI r14 255
AND r12 r12 r14
LDI r14 1
CMP r12 r14
BR.EQ .keyboard
LDI r14 0x10
CMP r12 r14
BR.EQ .up
LDI r14 0x11
CMP r12 r14
BR.EQ .down
LDI r14 0x12
CMP r12 r14
BR.EQ .left
LDI r14 0x13
CMP r12 r14
BR.EQ .right
LDI r14 0x14
CMP r12 r14
BR.EQ .draw
LDI r14 0x15
CMP r12 r14
BR.EQ .erase
SYS SYS_APP_EVENT
JMP .loop
.keyboard
MOV r13 r1
LDI r14 18
SHR r13 r13 r14
LDI r14 255
AND r13 r13 r14
LDI r14 0x80
CMP r13 r14
BR.EQ .up
LDI r14 0x81
CMP r13 r14
BR.EQ .down
LDI r14 0x82
CMP r13 r14
BR.EQ .left
LDI r14 0x83
CMP r13 r14
BR.EQ .right
LDI r14 0x43
CMP r13 r14
BR.EQ .clear
LDI r14 0x63
CMP r13 r14
BR.EQ .clear
JMP .loop
.up
CMP r9 r0
BR.EQ .loop
DEC r9
JMP .moved
.down
LDI r14 179
CMP r9 r14
BR.GE .loop
INC r9
JMP .moved
.left
CMP r8 r0
BR.EQ .loop
DEC r8
JMP .moved
.right
LDI r14 319
CMP r8 r14
BR.GE .loop
INC r8
.moved
CALL .cursor
JMP .loop
.draw
LDI r10 2
CALL .pixel
JMP .loop
.erase
LDI r10 3
CALL .pixel
JMP .loop
.clear
LDI r12 GPU
STW r12 r0 8
LDI r1 1
STW r12 r1 4
STW r12 r1 40
LDI r1 0x14
STW r12 r1 4
LDI r1 1
STW r12 r1 40
SYS SYS_GPU_FENCE
CALL .cursor
JMP .loop
.pixel
LDI r12 GPU
LDI r1 0x13
STW r12 r1 4
LDI r1 1
STW r12 r1 40
STW r12 r8 8
STW r12 r9 12
STW r12 r10 4
STW r12 r1 40
LDI r1 0x14
STW r12 r1 4
LDI r1 1
STW r12 r1 40
SYS SYS_GPU_FENCE
CALL .cursor
RET
.cursor
LDI r12 GPU
STW r12 r8 8
STW r12 r9 12
STW r12 r11 16
LDI r1 0x12
STW r12 r1 4
LDI r1 1
STW r12 r1 40
LDI r1 0x15
STW r12 r1 4
LDI r1 1
STW r12 r1 40
SYS SYS_GPU_FENCE
RET
.idle
SYS SYS_YIELD
JMP .loop
