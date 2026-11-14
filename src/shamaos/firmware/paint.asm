; Paint uses a non-destructive scanout cursor. Movement never copies a cursor
; mark into the artwork. D-pad/arrow keys move, A draws, B erases, C clears.
; Settings restores cursor visibility/shape from the persistent versioned file.
define GPU 0x02000000
define CONFIG 0x00004200
define CONFIG_NAME 0x00004300
LEA r8 r0 160
LEA r9 r0 90
LEA r11 r0 1
LDIS r12 CONFIG_NAME
LDI r13 0x74746573
STW r12 r13 0
LDI r13 0x73676e69
STW r12 r13 4
LDI r13 0x6766632e
STW r12 r13 8
STB r12 r0 12
LDIS r1 CONFIG_NAME
SYS SYS_FILE_OPEN
CMP r2 r0
BRS.NE .redraw
PUSH r1
LDIS r2 CONFIG
LEA r3 r0 16
SYS SYS_FILE_READ
MOV r10 r1
MOV r14 r2
POP r1
SYS SYS_FILE_CLOSE
CMP r14 r0
BRS.NE .redraw
LEA r1 r0 16
CMP r10 r1
BRS.NE .redraw
LDIS r12 CONFIG
LDW r1 r12 0
LDI r2 0x53434647
CMP r1 r2
BRS.NE .redraw
LDW r1 r12 8
LEA r2 r0 1
CMP r1 r2
BRS.NE .redraw
LDW r11 r12 4
LEA r1 r0 3
AND r11 r11 r1
.redraw
MOV r1 r8
MOV r2 r9
SYS SYS_UI_PAINT_VIEW
CALLS .cursor
.loop
SYS SYS_GET_EVENT
CMP r1 r0
BRS.EQ .idle
MOV r12 r1
LEA r14 r0 10
SHR r12 r12 r14
LEA r14 r0 255
AND r12 r12 r14
LEA r14 r0 1
CMP r12 r14
BRS.EQ .keyboard
LEA r14 r0 0x10
CMP r12 r14
BRS.EQ .up
LEA r14 r0 0x11
CMP r12 r14
BRS.EQ .down
LEA r14 r0 0x12
CMP r12 r14
BRS.EQ .left
LEA r14 r0 0x13
CMP r12 r14
BRS.EQ .right
LEA r14 r0 0x14
CMP r12 r14
BRS.EQ .draw
LEA r14 r0 0x15
CMP r12 r14
BRS.EQ .erase
SYS SYS_APP_EVENT
JMPS .loop
.keyboard
MOV r13 r1
LEA r14 r0 18
SHR r13 r13 r14
LEA r14 r0 255
AND r13 r13 r14
LEA r14 r0 0x80
CMP r13 r14
BRS.EQ .up
LEA r14 r0 0x81
CMP r13 r14
BRS.EQ .down
LEA r14 r0 0x82
CMP r13 r14
BRS.EQ .left
LEA r14 r0 0x83
CMP r13 r14
BRS.EQ .right
LEA r14 r0 0x43
CMP r13 r14
BRS.EQ .clear
LEA r14 r0 0x63
CMP r13 r14
BRS.EQ .clear
JMPS .loop
.up
CMP r9 r0
BRS.EQ .loop
DEC r9
JMPS .moved
.down
LEA r14 r0 179
CMP r9 r14
BRS.GE .loop
INC r9
JMPS .moved
.left
CMP r8 r0
BRS.EQ .loop
DEC r8
JMPS .moved
.right
LEA r14 r0 319
CMP r8 r14
BRS.GE .loop
INC r8
.moved
CALLS .cursor
JMPS .loop
.draw
LEA r10 r0 2
CALLS .pixel
JMPS .loop
.erase
LEA r10 r0 3
CALLS .pixel
JMPS .loop
.clear
LDI r12 GPU
STW r12 r0 8
LEA r1 r0 1
STW r12 r1 4
STW r12 r1 40
LEA r1 r0 0x14
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
SYS SYS_GPU_FENCE
CALLS .cursor
JMPS .loop
.pixel
LDI r12 GPU
LEA r1 r0 0x13
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
STW r12 r8 8
STW r12 r9 12
STW r12 r10 4
STW r12 r1 40
LEA r1 r0 0x14
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
SYS SYS_GPU_FENCE
JMPS .cursor
.cursor
LDI r12 GPU
STW r12 r8 8
STW r12 r9 12
STW r12 r11 16
LEA r1 r0 0x12
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
LEA r1 r0 0x15
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
SYS SYS_GPU_FENCE
RET
.idle
SYS SYS_YIELD
JMPS .loop
