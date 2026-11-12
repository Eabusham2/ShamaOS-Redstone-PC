; ShamaOS Terminal
; Commands: LS, EDIT, MEM, FLASH, GPU, MINER, PAINT, CALC,
; RUN/CAT/RM/MV (open File Explorer for the selected file workflow), CLEAR.

define EVT_KEY 0x01
define EVT_B 0x15
define KEY_ENTER 0x0d
define KEY_BACKSPACE 0x08

define TERM_BUF 0x0002e000
define GPU_BASE 0x02000000
define GPU_TEXT 0x02000400

MOV r8 r0
MOV r9 r0

.redraw
MOV r1 r9
SYS SYS_UI_TERMINAL_VIEW
CALL .draw_command

.loop
SYS SYS_GET_EVENT
CMP r1 r0
BR.EQ .idle

MOV r12 r1
LEA r14 r0 10
SHR r12 r12 r14
LEA r14 r0 0xff
AND r12 r12 r14

MOV r13 r1
LEA r14 r0 18
SHR r13 r13 r14
LEA r14 r0 0xff
AND r13 r13 r14

LEA r14 r0 EVT_B
CMP r12 r14
BR.EQ .desktop

LEA r14 r0 EVT_KEY
CMP r12 r14
BR.NE .universal

LEA r14 r0 KEY_ENTER
CMP r13 r14
BR.EQ .execute
LEA r14 r0 KEY_BACKSPACE
CMP r13 r14
BR.EQ .backspace

LEA r14 r0 0x20
CMP r13 r14
BR.LT .redraw
LEA r14 r0 0x7e
CMP r13 r14
BR.GT .redraw
LEA r14 r0 31
CMP r8 r14
BR.GE .redraw
LEA r14 r0 97
CMP r13 r14
BR.LT .append_key
LEA r14 r0 122
CMP r13 r14
BR.GT .append_key
LEA r14 r0 32
SUB r13 r13 r14
.append_key
MOV r9 r0
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
LEA r14 r0 2
CMP r8 r14
BR.NE .command_next_0
LDI r14 TERM_BUF
LDB r1 r14 0
LEA r2 r0 76
CMP r1 r2
BR.NE .command_next_0
LDB r1 r14 1
LEA r2 r0 83
CMP r1 r2
BR.NE .command_next_0
JMP .files
.command_next_0
LEA r14 r0 2
CMP r8 r14
BR.NE .command_next_1
LDI r14 TERM_BUF
LDB r1 r14 0
LEA r2 r0 82
CMP r1 r2
BR.NE .command_next_1
LDB r1 r14 1
LEA r2 r0 77
CMP r1 r2
BR.NE .command_next_1
JMP .files
.command_next_1
LEA r14 r0 2
CMP r8 r14
BR.NE .command_next_2
LDI r14 TERM_BUF
LDB r1 r14 0
LEA r2 r0 77
CMP r1 r2
BR.NE .command_next_2
LDB r1 r14 1
LEA r2 r0 86
CMP r1 r2
BR.NE .command_next_2
JMP .files
.command_next_2
LEA r14 r0 4
CMP r8 r14
BR.NE .command_next_3
LDI r14 TERM_BUF
LDB r1 r14 0
LEA r2 r0 69
CMP r1 r2
BR.NE .command_next_3
LDB r1 r14 1
LEA r2 r0 68
CMP r1 r2
BR.NE .command_next_3
LDB r1 r14 2
LEA r2 r0 73
CMP r1 r2
BR.NE .command_next_3
LDB r1 r14 3
LEA r2 r0 84
CMP r1 r2
BR.NE .command_next_3
JMP .editor
.command_next_3
LEA r14 r0 4
CMP r8 r14
BR.NE .command_next_4
LDI r14 TERM_BUF
LDB r1 r14 0
LEA r2 r0 67
CMP r1 r2
BR.NE .command_next_4
LDB r1 r14 1
LEA r2 r0 65
CMP r1 r2
BR.NE .command_next_4
LDB r1 r14 2
LEA r2 r0 76
CMP r1 r2
BR.NE .command_next_4
LDB r1 r14 3
LEA r2 r0 67
CMP r1 r2
BR.NE .command_next_4
JMP .calc
.command_next_4
LEA r14 r0 3
CMP r8 r14
BR.NE .command_next_5
LDI r14 TERM_BUF
LDB r1 r14 0
LEA r2 r0 77
CMP r1 r2
BR.NE .command_next_5
LDB r1 r14 1
LEA r2 r0 69
CMP r1 r2
BR.NE .command_next_5
LDB r1 r14 2
LEA r2 r0 77
CMP r1 r2
BR.NE .command_next_5
JMP .monitor
.command_next_5
LEA r14 r0 3
CMP r8 r14
BR.NE .command_next_6
LDI r14 TERM_BUF
LDB r1 r14 0
LEA r2 r0 71
CMP r1 r2
BR.NE .command_next_6
LDB r1 r14 1
LEA r2 r0 80
CMP r1 r2
BR.NE .command_next_6
LDB r1 r14 2
LEA r2 r0 85
CMP r1 r2
BR.NE .command_next_6
JMP .monitor
.command_next_6
LEA r14 r0 3
CMP r8 r14
BR.NE .command_next_7
LDI r14 TERM_BUF
LDB r1 r14 0
LEA r2 r0 82
CMP r1 r2
BR.NE .command_next_7
LDB r1 r14 1
LEA r2 r0 85
CMP r1 r2
BR.NE .command_next_7
LDB r1 r14 2
LEA r2 r0 78
CMP r1 r2
BR.NE .command_next_7
JMP .files
.command_next_7
LEA r14 r0 3
CMP r8 r14
BR.NE .command_next_8
LDI r14 TERM_BUF
LDB r1 r14 0
LEA r2 r0 67
CMP r1 r2
BR.NE .command_next_8
LDB r1 r14 1
LEA r2 r0 65
CMP r1 r2
BR.NE .command_next_8
LDB r1 r14 2
LEA r2 r0 84
CMP r1 r2
BR.NE .command_next_8
JMP .files
.command_next_8
LEA r14 r0 5
CMP r8 r14
BR.NE .command_next_9
LDI r14 TERM_BUF
LDB r1 r14 0
LEA r2 r0 70
CMP r1 r2
BR.NE .command_next_9
LDB r1 r14 1
LEA r2 r0 76
CMP r1 r2
BR.NE .command_next_9
LDB r1 r14 2
LEA r2 r0 65
CMP r1 r2
BR.NE .command_next_9
LDB r1 r14 3
LEA r2 r0 83
CMP r1 r2
BR.NE .command_next_9
LDB r1 r14 4
LEA r2 r0 72
CMP r1 r2
BR.NE .command_next_9
JMP .monitor
.command_next_9
LEA r14 r0 5
CMP r8 r14
BR.NE .command_next_10
LDI r14 TERM_BUF
LDB r1 r14 0
LEA r2 r0 77
CMP r1 r2
BR.NE .command_next_10
LDB r1 r14 1
LEA r2 r0 73
CMP r1 r2
BR.NE .command_next_10
LDB r1 r14 2
LEA r2 r0 78
CMP r1 r2
BR.NE .command_next_10
LDB r1 r14 3
LEA r2 r0 69
CMP r1 r2
BR.NE .command_next_10
LDB r1 r14 4
LEA r2 r0 82
CMP r1 r2
BR.NE .command_next_10
JMP .miner
.command_next_10
LEA r14 r0 5
CMP r8 r14
BR.NE .command_next_11
LDI r14 TERM_BUF
LDB r1 r14 0
LEA r2 r0 80
CMP r1 r2
BR.NE .command_next_11
LDB r1 r14 1
LEA r2 r0 65
CMP r1 r2
BR.NE .command_next_11
LDB r1 r14 2
LEA r2 r0 73
CMP r1 r2
BR.NE .command_next_11
LDB r1 r14 3
LEA r2 r0 78
CMP r1 r2
BR.NE .command_next_11
LDB r1 r14 4
LEA r2 r0 84
CMP r1 r2
BR.NE .command_next_11
JMP .paint
.command_next_11
LEA r14 r0 5
CMP r8 r14
BR.NE .command_next_12
LDI r14 TERM_BUF
LDB r1 r14 0
LEA r2 r0 67
CMP r1 r2
BR.NE .command_next_12
LDB r1 r14 1
LEA r2 r0 76
CMP r1 r2
BR.NE .command_next_12
LDB r1 r14 2
LEA r2 r0 69
CMP r1 r2
BR.NE .command_next_12
LDB r1 r14 3
LEA r2 r0 65
CMP r1 r2
BR.NE .command_next_12
LDB r1 r14 4
LEA r2 r0 82
CMP r1 r2
BR.NE .command_next_12
JMP .clear_input
.command_next_12
LEA r9 r0 1
JMP .clear_input

.clear_input
MOV r8 r0
JMP .redraw

.files
LEA r1 r0 2
SYS SYS_APP_LAUNCH
HLT
.editor
LEA r1 r0 1
SYS SYS_APP_LAUNCH
HLT
.monitor
LEA r1 r0 4
SYS SYS_APP_LAUNCH
HLT
.miner
LEA r1 r0 3
SYS SYS_APP_LAUNCH
HLT
.paint
LEA r1 r0 7
SYS SYS_APP_LAUNCH
HLT
.calc
LEA r1 r0 6
SYS SYS_APP_LAUNCH
HLT
.desktop
MOV r1 r0
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
LEA r14 r0 0x13
STW r12 r14 4
LEA r14 r0 1
STW r12 r14 40

MOV r1 r0
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
MOV r14 r0
STW r12 r14 8
STW r12 r8 12
LEA r14 r0 4
STW r12 r14 16
LEA r14 r0 48
STW r12 r14 20
LEA r14 r0 0x0c
STW r12 r14 4
LEA r14 r0 1
STW r12 r14 40
LEA r14 r0 0x14
STW r12 r14 4
LEA r14 r0 1
STW r12 r14 40
SYS SYS_GPU_FENCE
.draw_done
RET

.idle
SYS SYS_YIELD
JMP .loop
