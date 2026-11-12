; Settings are persisted before the updated state is presented. Paint reads the
; same versioned record on launch. Flags: bit0 cursor enabled, bit1 single pixel.
define CONFIG 0x000b4000
define CONFIG_NAME 0x000b4100
define GPU 0x02000000
LDI r8 0
LDI r9 0
LDI r10 0
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
BR.NE .create
MOV r10 r1
LDI r2 CONFIG
LDI r3 16
SYS SYS_FILE_READ
CMP r2 r0
BR.NE .defaults
LDI r12 CONFIG
LDW r1 r12 0
LDI r2 0x53434647
CMP r1 r2
BR.NE .defaults
LDW r1 r12 8
LDI r2 1
CMP r1 r2
BR.NE .defaults
LDW r11 r12 4
LDI r1 3
AND r11 r11 r1
JMP .redraw
.create
LDI r1 CONFIG_NAME
LDI r2 6
SYS SYS_FILE_CREATE
CMP r2 r0
BR.NE .failed
MOV r10 r1
.defaults
LDI r12 CONFIG
LDI r1 0x53434647
STW r12 r1 0
LDI r11 1
STW r12 r11 4
LDI r1 1
STW r12 r1 8
STW r12 r0 12
CALL .save
JMP .redraw
.failed
LDI r9 1
.redraw
MOV r1 r8
MOV r2 r11
MOV r3 r9
SYS SYS_UI_SETTINGS_VIEW
CALL .preview
.loop
SYS SYS_GET_EVENT
CMP r1 r0
BR.EQ .idle
MOV r12 r1
LDI r14 10
SHR r12 r12 r14
LDI r14 255
AND r12 r12 r14
LDI r14 0x10
CMP r12 r14
BR.EQ .up
LDI r14 0x11
CMP r12 r14
BR.EQ .down
LDI r14 0x15
CMP r12 r14
BR.EQ .exit
LDI r14 0x14
CMP r12 r14
BR.NE .universal
CMP r8 r0
BR.EQ .toggle_enabled
LDI r14 1
CMP r8 r14
BR.EQ .toggle_shape
LDI r14 2
CMP r8 r14
BR.EQ .save_action
LDI r14 3
CMP r8 r14
BR.EQ .monitor
JMP .exit
.up
CMP r8 r0
BR.EQ .redraw
DEC r8
JMP .redraw
.down
LDI r14 4
CMP r8 r14
BR.GE .redraw
INC r8
JMP .redraw
.toggle_enabled
LDI r14 1
XOR r11 r11 r14
JMP .save_action
.toggle_shape
LDI r14 2
XOR r11 r11 r14
.save_action
LDI r12 CONFIG
STW r12 r11 4
CALL .save
CMP r9 r0
BR.NE .redraw
JMP .redraw
.preview
; Show the chosen cursor immediately; subsequent Paint launches restore it.
LDI r12 GPU
LDI r1 160
STW r12 r1 8
LDI r1 90
STW r12 r1 12
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
.save
MOV r1 r10
LDI r2 CONFIG
LDI r3 16
SYS SYS_FILE_WRITE
LDI r9 0
CMP r2 r0
BR.EQ .save_return
LDI r9 1
.save_return
RET
.monitor
LDI r1 4
SYS SYS_APP_LAUNCH
JMP .loop
.exit
SYS SYS_EXIT
JMP .loop
.universal
SYS SYS_APP_EVENT
JMP .redraw
.idle
SYS SYS_YIELD
JMP .loop
