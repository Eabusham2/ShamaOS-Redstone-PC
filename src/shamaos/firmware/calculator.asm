; ShamaOS integer Calculator
; Decimal keyboard input, + - * /, Enter evaluates, B clears.

define EVT_KEY 0x01
define EVT_B 0x15
define KEY_ENTER 0x0d
define GPU_BASE 0x02000000
define GPU_TEXT 0x02000400

LDI r8 0
LDI r9 0
LDI r10 0
LDI r11 0

.redraw
MOV r1 r11
MOV r2 r10
SYS SYS_UI_CALCULATOR_VIEW
CALL .draw_value

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
BR.EQ .clear
LDI r14 EVT_KEY
CMP r12 r14
BR.NE .universal

; digit?
LDI r14 0x30
CMP r13 r14
BR.LT .operator
LDI r14 0x39
CMP r13 r14
BR.GT .operator
LDI r14 0x30
SUB r13 r13 r14
LDI r14 10
CMP r11 r0
BR.NE .digit_second
MUL r8 r8 r14
ADD r8 r8 r13
JMP .redraw
.digit_second
MUL r9 r9 r14
ADD r9 r9 r13
JMP .redraw

.operator
LDI r14 KEY_ENTER
CMP r13 r14
BR.EQ .evaluate
LDI r14 0x2b
CMP r13 r14
BR.EQ .op_add
LDI r14 0x2d
CMP r13 r14
BR.EQ .op_sub
LDI r14 0x2a
CMP r13 r14
BR.EQ .op_mul
LDI r14 0x2f
CMP r13 r14
BR.EQ .op_div
JMP .redraw

.op_add
LDI r10 0
LDI r11 1
JMP .redraw
.op_sub
LDI r10 1
LDI r11 1
JMP .redraw
.op_mul
LDI r10 2
LDI r11 1
JMP .redraw
.op_div
LDI r10 3
LDI r11 1
JMP .redraw

.evaluate
CMP r11 r0
BR.EQ .redraw
CMP r10 r0
BR.EQ .eval_add
LDI r14 1
CMP r10 r14
BR.EQ .eval_sub
LDI r14 2
CMP r10 r14
BR.EQ .eval_mul
CMP r9 r0
BR.EQ .eval_zero
DIV r8 r8 r9
JMP .eval_done
.eval_add
ADD r8 r8 r9
JMP .eval_done
.eval_sub
SUB r8 r8 r9
JMP .eval_done
.eval_mul
MUL r8 r8 r9
JMP .eval_done
.eval_zero
LDI r8 0
.eval_done
LDI r9 0
LDI r10 0
LDI r11 0
JMP .redraw

.clear
LDI r8 0
LDI r9 0
LDI r10 0
LDI r11 0
JMP .redraw

.universal
SYS SYS_APP_EVENT
JMP .redraw

.draw_value
; Render current active operand/result as up to ten decimal digits.
PUSH r1
PUSH r2
PUSH r3
PUSH r4
PUSH r12
PUSH r13
PUSH r14

LDI r12 GPU_TEXT
LDI r1 16
LDI r2 0x20
.clear_line
CMP r1 r0
BR.EQ .choose_value
STB r12 r2 0
INC r12
DEC r1
JMP .clear_line

.choose_value
CMP r11 r0
BR.EQ .value_first
MOV r3 r9
JMP .value_ready
.value_first
MOV r3 r8
.value_ready
LDI r4 15
CMP r3 r0
BR.NE .digits
LDI r12 GPU_TEXT
ADD r12 r12 r4
LDI r13 0x30
STB r12 r13 0
JMP .submit

.digits
LDI r14 10
.digit_loop
CMP r3 r0
BR.EQ .submit
MOD r1 r3 r14
DIV r3 r3 r14
LDI r2 0x30
ADD r1 r1 r2
LDI r12 GPU_TEXT
ADD r12 r12 r4
STB r12 r1 0
DEC r4
JMP .digit_loop

.submit
LDI r12 GPU_BASE
LDI r14 0x13
STW r12 r14 4
LDI r14 1
STW r12 r14 40
LDI r14 0
STW r12 r14 8
LDI r14 16
STW r12 r14 12
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

POP r14
POP r13
POP r12
POP r4
POP r3
POP r2
POP r1
RET

.idle
SYS SYS_YIELD
JMP .loop
