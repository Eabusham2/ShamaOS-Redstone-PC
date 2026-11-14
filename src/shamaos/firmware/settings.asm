; Settings are persisted before the updated state is presented. Paint reads the
; same versioned record on launch. Flags: bit0 cursor enabled, bit1 single pixel.
define CONFIG 0x00004000
define CONFIG_NAME 0x00004100
define GPU 0x02000000
MOV r8 r0
MOV r9 r0
MOV r10 r0
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
BRS.NE .create
MOV r10 r1
LDIS r2 CONFIG
LEA r3 r0 16
SYS SYS_FILE_READ
CMP r2 r0
BRS.NE .defaults
LEA r12 r0 16
CMP r1 r12
BRS.NE .defaults
LDIS r12 CONFIG
LDW r1 r12 0
LDI r2 0x53434647
CMP r1 r2
BRS.NE .defaults
LDW r1 r12 8
LEA r2 r0 1
CMP r1 r2
BRS.NE .defaults
LDW r11 r12 4
LEA r1 r0 3
AND r11 r11 r1
JMPS .redraw
.create
LDIS r1 CONFIG_NAME
LEA r2 r0 6
SYS SYS_FILE_CREATE
CMP r2 r0
BRS.NE .failed
MOV r10 r1
.defaults
LDIS r12 CONFIG
LDI r1 0x53434647
STW r12 r1 0
LEA r11 r0 1
STW r12 r11 4
LEA r1 r0 1
STW r12 r1 8
STW r12 r0 12
CALLS .save
JMPS .redraw
.failed
LEA r9 r0 1
.redraw
MOV r1 r8
MOV r2 r11
MOV r3 r9
SYS SYS_UI_SETTINGS_VIEW
CALLS .preview
.loop
SYS SYS_GET_EVENT
CMP r1 r0
BRS.EQ .idle
MOV r12 r1
LEA r14 r0 10
SHR r12 r12 r14
LEA r14 r0 255
AND r12 r12 r14
LEA r14 r0 0x10
CMP r12 r14
BRS.EQ .up
LEA r14 r0 0x11
CMP r12 r14
BRS.EQ .down
LEA r14 r0 0x15
CMP r12 r14
BRS.EQ .exit
LEA r14 r0 0x14
CMP r12 r14
BRS.NE .universal
CMP r8 r0
BRS.EQ .toggle_enabled
LEA r14 r0 1
CMP r8 r14
BRS.EQ .toggle_shape
LEA r14 r0 2
CMP r8 r14
BRS.EQ .save_action
LEA r14 r0 3
CMP r8 r14
BRS.EQ .monitor
JMPS .exit
.up
CMP r8 r0
BRS.EQ .redraw
DEC r8
JMPS .redraw
.down
LEA r14 r0 4
CMP r8 r14
BRS.GE .redraw
INC r8
JMPS .redraw
.toggle_enabled
LEA r14 r0 1
XOR r11 r11 r14
JMPS .save_action
.toggle_shape
LEA r14 r0 2
XOR r11 r11 r14
.save_action
LDIS r12 CONFIG
STW r12 r11 4
CALLS .save
CMP r9 r0
BRS.NE .redraw
JMPS .redraw
.preview
; Show the chosen cursor immediately; subsequent Paint launches restore it.
LDI r12 GPU
LEA r1 r0 160
STW r12 r1 8
LEA r1 r0 90
STW r12 r1 12
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
.save
MOV r1 r10
LDIS r2 CONFIG
LEA r3 r0 16
SYS SYS_FILE_WRITE
MOV r9 r0
CMP r2 r0
BRS.EQ .save_return
LEA r9 r0 1
.save_return
RET
.monitor
LEA r1 r0 4
SYS SYS_APP_LAUNCH
JMPS .loop
.exit
SYS SYS_EXIT
JMPS .loop
.universal
SYS SYS_APP_EVENT
JMPS .redraw
.idle
SYS SYS_YIELD
JMPS .loop
