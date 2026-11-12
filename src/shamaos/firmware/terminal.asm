; ShamaOS Terminal
; Commands: LS, EDIT, MEM, FLASH, GPU, MINER, PAINT, CALC,
; RUN/CAT/RM/MV (open File Explorer for the selected file workflow), CLEAR.

define EVT_KEY 0x01
define EVT_B 0x15
define KEY_ENTER 0x0d
define KEY_BACKSPACE 0x08

define TERM_BUF 0x000b3000
define GPU_BASE 0x02000000
define GPU_TEXT 0x02000400

LDI r8 0
LDI r9 0

.redraw
MOV r1 r9
SYS SYS_UI_TERMINAL_VIEW
CALL .draw_command

.loop
SYS SYS_GET_EVENT
CMP r1 r0
BR.EQ .idle

MOV r12 r1
LDI r14 10
SHR r12 r12 r14
LDI r14 0xff
AND r12 r12 r14

MOV r13 r1
LDI r14 18
SHR r13 r13 r14
LDI r14 0xff
AND r13 r13 r14

LDI r14 EVT_B
CMP r12 r14
BR.EQ .desktop

LDI r14 EVT_KEY
CMP r12 r14
BR.NE .universal

LDI r14 KEY_ENTER
CMP r13 r14
BR.EQ .execute
LDI r14 KEY_BACKSPACE
CMP r13 r14
BR.EQ .backspace

LDI r14 0x20
CMP r13 r14
BR.LT .redraw
LDI r14 0x7e
CMP r13 r14
BR.GT .redraw
LDI r14 31
CMP r8 r14
BR.GE .redraw
LDI r14 97
CMP r13 r14
BR.LT .append_key
LDI r14 122
CMP r13 r14
BR.GT .append_key
LDI r14 32
SUB r13 r13 r14
.append_key
LDI r9 0
LDI r14 TERM_BUF
ADD r14 r14 r8
STB r14 r13 0
INC r8
JMP .redraw

.backspace
CMP r8 r0
BR.EQ .redraw
DEC r8
JMP .redraw

.execute
LDI r14 2
CMP r8 r14
BR.NE .command_next_0
LDI r14 TERM_BUF
LDB r1 r14 0
LDI r2 76
CMP r1 r2
BR.NE .command_next_0
LDB r1 r14 1
LDI r2 83
CMP r1 r2
BR.NE .command_next_0
JMP .files
.command_next_0
LDI r14 2
CMP r8 r14
BR.NE .command_next_1
LDI r14 TERM_BUF
LDB r1 r14 0
LDI r2 82
CMP r1 r2
BR.NE .command_next_1
LDB r1 r14 1
LDI r2 77
CMP r1 r2
BR.NE .command_next_1
JMP .files
.command_next_1
LDI r14 2
CMP r8 r14
BR.NE .command_next_2
LDI r14 TERM_BUF
LDB r1 r14 0
LDI r2 77
CMP r1 r2
BR.NE .command_next_2
LDB r1 r14 1
LDI r2 86
CMP r1 r2
BR.NE .command_next_2
JMP .files
.command_next_2
LDI r14 4
CMP r8 r14
BR.NE .command_next_3
LDI r14 TERM_BUF
LDB r1 r14 0
LDI r2 69
CMP r1 r2
BR.NE .command_next_3
LDB r1 r14 1
LDI r2 68
CMP r1 r2
BR.NE .command_next_3
LDB r1 r14 2
LDI r2 73
CMP r1 r2
BR.NE .command_next_3
LDB r1 r14 3
LDI r2 84
CMP r1 r2
BR.NE .command_next_3
JMP .editor
.command_next_3
LDI r14 4
CMP r8 r14
BR.NE .command_next_4
LDI r14 TERM_BUF
LDB r1 r14 0
LDI r2 67
CMP r1 r2
BR.NE .command_next_4
LDB r1 r14 1
LDI r2 65
CMP r1 r2
BR.NE .command_next_4
LDB r1 r14 2
LDI r2 76
CMP r1 r2
BR.NE .command_next_4
LDB r1 r14 3
LDI r2 67
CMP r1 r2
BR.NE .command_next_4
JMP .calc
.command_next_4
LDI r14 3
CMP r8 r14
BR.NE .command_next_5
LDI r14 TERM_BUF
LDB r1 r14 0
LDI r2 77
CMP r1 r2
BR.NE .command_next_5
LDB r1 r14 1
LDI r2 69
CMP r1 r2
BR.NE .command_next_5
LDB r1 r14 2
LDI r2 77
CMP r1 r2
BR.NE .command_next_5
JMP .monitor
.command_next_5
LDI r14 3
CMP r8 r14
BR.NE .command_next_6
LDI r14 TERM_BUF
LDB r1 r14 0
LDI r2 71
CMP r1 r2
BR.NE .command_next_6
LDB r1 r14 1
LDI r2 80
CMP r1 r2
BR.NE .command_next_6
LDB r1 r14 2
LDI r2 85
CMP r1 r2
BR.NE .command_next_6
JMP .monitor
.command_next_6
LDI r14 3
CMP r8 r14
BR.NE .command_next_7
LDI r14 TERM_BUF
LDB r1 r14 0
LDI r2 82
CMP r1 r2
BR.NE .command_next_7
LDB r1 r14 1
LDI r2 85
CMP r1 r2
BR.NE .command_next_7
LDB r1 r14 2
LDI r2 78
CMP r1 r2
BR.NE .command_next_7
JMP .files
.command_next_7
LDI r14 3
CMP r8 r14
BR.NE .command_next_8
LDI r14 TERM_BUF
LDB r1 r14 0
LDI r2 67
CMP r1 r2
BR.NE .command_next_8
LDB r1 r14 1
LDI r2 65
CMP r1 r2
BR.NE .command_next_8
LDB r1 r14 2
LDI r2 84
CMP r1 r2
BR.NE .command_next_8
JMP .files
.command_next_8
LDI r14 5
CMP r8 r14
BR.NE .command_next_9
LDI r14 TERM_BUF
LDB r1 r14 0
LDI r2 70
CMP r1 r2
BR.NE .command_next_9
LDB r1 r14 1
LDI r2 76
CMP r1 r2
BR.NE .command_next_9
LDB r1 r14 2
LDI r2 65
CMP r1 r2
BR.NE .command_next_9
LDB r1 r14 3
LDI r2 83
CMP r1 r2
BR.NE .command_next_9
LDB r1 r14 4
LDI r2 72
CMP r1 r2
BR.NE .command_next_9
JMP .monitor
.command_next_9
LDI r14 5
CMP r8 r14
BR.NE .command_next_10
LDI r14 TERM_BUF
LDB r1 r14 0
LDI r2 77
CMP r1 r2
BR.NE .command_next_10
LDB r1 r14 1
LDI r2 73
CMP r1 r2
BR.NE .command_next_10
LDB r1 r14 2
LDI r2 78
CMP r1 r2
BR.NE .command_next_10
LDB r1 r14 3
LDI r2 69
CMP r1 r2
BR.NE .command_next_10
LDB r1 r14 4
LDI r2 82
CMP r1 r2
BR.NE .command_next_10
JMP .miner
.command_next_10
LDI r14 5
CMP r8 r14
BR.NE .command_next_11
LDI r14 TERM_BUF
LDB r1 r14 0
LDI r2 80
CMP r1 r2
BR.NE .command_next_11
LDB r1 r14 1
LDI r2 65
CMP r1 r2
BR.NE .command_next_11
LDB r1 r14 2
LDI r2 73
CMP r1 r2
BR.NE .command_next_11
LDB r1 r14 3
LDI r2 78
CMP r1 r2
BR.NE .command_next_11
LDB r1 r14 4
LDI r2 84
CMP r1 r2
BR.NE .command_next_11
JMP .paint
.command_next_11
LDI r14 5
CMP r8 r14
BR.NE .command_next_12
LDI r14 TERM_BUF
LDB r1 r14 0
LDI r2 67
CMP r1 r2
BR.NE .command_next_12
LDB r1 r14 1
LDI r2 76
CMP r1 r2
BR.NE .command_next_12
LDB r1 r14 2
LDI r2 69
CMP r1 r2
BR.NE .command_next_12
LDB r1 r14 3
LDI r2 65
CMP r1 r2
BR.NE .command_next_12
LDB r1 r14 4
LDI r2 82
CMP r1 r2
BR.NE .command_next_12
JMP .clear_input
.command_next_12
LDI r9 1
JMP .clear_input

.clear_input
LDI r8 0
JMP .redraw

.files
LDI r1 2
SYS SYS_APP_LAUNCH
HLT
.editor
LDI r1 1
SYS SYS_APP_LAUNCH
HLT
.monitor
LDI r1 4
SYS SYS_APP_LAUNCH
HLT
.miner
LDI r1 3
SYS SYS_APP_LAUNCH
HLT
.paint
LDI r1 7
SYS SYS_APP_LAUNCH
HLT
.calc
LDI r1 6
SYS SYS_APP_LAUNCH
HLT
.desktop
LDI r1 0
SYS SYS_APP_LAUNCH
HLT

.universal
SYS SYS_APP_EVENT
JMP .redraw

.draw_command
CMP r8 r0
BR.EQ .draw_done
LDI r12 GPU_BASE
; copy front to back
LDI r14 0x13
STW r12 r14 4
LDI r14 1
STW r12 r14 40

LDI r1 0
.copy
CMP r1 r8
BR.GE .submit
LDI r2 TERM_BUF
ADD r2 r2 r1
LDB r3 r2 0
LDI r4 GPU_TEXT
ADD r4 r4 r1
STB r4 r3 0
INC r1
JMP .copy
.submit
LDI r14 0
STW r12 r14 8
STW r12 r8 12
LDI r14 4
STW r12 r14 16
LDI r14 48
STW r12 r14 20
LDI r14 0x0c
STW r12 r14 4
LDI r14 1
STW r12 r14 40
LDI r14 0x14
STW r12 r14 4
LDI r14 1
STW r12 r14 40
SYS SYS_GPU_FENCE
.draw_done
RET

.idle
SYS SYS_YIELD
JMP .loop
