; Checked signed 32-bit Calculator. Decimal input, unary +/- before digits,
; + - * / % binary operations, Enter/equals evaluates, B clears, Backspace edits.
; Division truncates toward zero; remainder has the dividend's sign.
; Overflow rejects the operation/input digit and preserves accepted operands.
; r8/r9 operands, r10 ASCII operator, r11 first/second/result (0/1/2),
; r5 error (0/zero-divisor/overflow), r6 active sign, r7 has input digits.
define GPU_BASE 0x02000000
define GPU_TEXT 0x02000400
CALLS .clear_state
.redraw
MOV r1 r11
MOV r2 r10
MOV r3 r5
SYS SYS_UI_CALCULATOR_VIEW
CALLS .draw_value
.loop
SYS SYS_GET_EVENT
CMP r1 r0
BRS.EQ .idle
MOV r12 r1
LEA r14 r0 10
SHR r12 r12 r14
LEA r14 r0 255
AND r12 r12 r14
LEA r14 r0 0x15
CMP r12 r14
BRS.EQ .clear
LEA r14 r0 1
CMP r12 r14
BRS.NE .universal
MOV r13 r1
LEA r14 r0 18
SHR r13 r13 r14
LEA r14 r0 255
AND r13 r13 r14
LEA r14 r0 8
CMP r13 r14
BRS.EQ .backspace
; Errors remain visible until Backspace dismisses them or B clears everything.
CMP r5 r0
BRS.NE .loop
LEA r14 r0 '0'
CMP r13 r14
BRS.LT .operator
LEA r14 r0 '9'
CMP r13 r14
BRS.GT .operator
; A digit after a completed result starts a new calculation.
LEA r14 r0 2
CMP r11 r14
BRS.NE .digit
CALLS .clear_state
.digit
LEA r14 r0 '0'
SUB r13 r13 r14
CALLS .get_active
CMP r6 r0
BRS.EQ .digit_magnitude
NEG r3 r3
.digit_magnitude
; Unsigned magnitude comparisons retain the special magnitude 0x80000000.
LDI r14 214748364
CMP r3 r14
BRS.NC .append_digit
BRS.NE .input_overflow
LEA r14 r0 7
ADD r14 r14 r6
CMP r14 r13
BRS.NC .input_overflow
.append_digit
LEA r14 r0 10
MUL r3 r3 r14
ADD r3 r3 r13
CMP r6 r0
BRS.EQ .store_digit
NEG r3 r3
.store_digit
CALLS .put_active
LEA r7 r0 1
JMPS .redraw
.input_overflow
LEA r5 r0 2
JMPS .redraw

.operator
LEA r14 r0 13
CMP r13 r14
BRS.EQ .enter
LEA r14 r0 '='
CMP r13 r14
BRS.EQ .enter
LEA r14 r0 '+'
CMP r13 r14
BRS.EQ .operator_valid
LEA r14 r0 '-'
CMP r13 r14
BRS.EQ .operator_valid
LEA r14 r0 '*'
CMP r13 r14
BRS.EQ .operator_valid
LEA r14 r0 '/'
CMP r13 r14
BRS.EQ .operator_valid
LEA r14 r0 '%'
CMP r13 r14
BRS.NE .loop
.operator_valid
CMP r7 r0
BRS.NE .operator_after_value
; Before digits, + and - are unary signs; * / % may replace a pending operator.
LEA r14 r0 '-'
CMP r13 r14
BRS.EQ .toggle_sign
LEA r14 r0 '+'
CMP r13 r14
BRS.EQ .positive_sign
LEA r14 r0 1
CMP r11 r14
BRS.NE .loop
JMPS .select_operator
.toggle_sign
LEA r14 r0 1
XOR r6 r6 r14
JMPS .redraw
.positive_sign
MOV r6 r0
JMPS .redraw
.operator_after_value
LEA r14 r0 1
CMP r11 r14
BRS.NE .select_operator
; Immediate calculator chaining is left-to-right, with no hidden precedence.
PUSH r13
CALLS .calculate
POP r13
CMP r5 r0
BRS.NE .redraw
.select_operator
MOV r10 r13
MOV r9 r0
LEA r11 r0 1
MOV r6 r0
MOV r7 r0
JMPS .redraw
.enter
CMP r7 r0
BRS.EQ .redraw
LEA r14 r0 1
CMP r11 r14
BRS.NE .single_value
CALLS .calculate
JMPS .redraw
.single_value
LEA r11 r0 2
MOV r6 r0
CMP r8 r0
BRS.GE .redraw
LEA r6 r0 1
JMPS .redraw

.backspace
CMP r5 r0
BRS.EQ .backspace_value
MOV r5 r0
JMPS .redraw
.backspace_value
LEA r14 r0 2
CMP r11 r14
BRS.NE .backspace_digits
MOV r11 r0
.backspace_digits
CMP r7 r0
BRS.NE .remove_digit
MOV r6 r0
JMPS .redraw
.remove_digit
CALLS .get_active
CMP r6 r0
BRS.EQ .remove_magnitude
NEG r3 r3
.remove_magnitude
LEA r14 r0 10
DIV r3 r3 r14
CMP r3 r0
BRS.NE .remove_sign
MOV r7 r0
.remove_sign
CMP r6 r0
BRS.EQ .remove_store
NEG r3 r3
.remove_store
CALLS .put_active
JMPS .redraw

; Compute into r3 and commit r8 only after signed range validation.
.calculate
LEA r14 r0 '+'
CMP r10 r14
BRS.EQ .calc_add
LEA r14 r0 '-'
CMP r10 r14
BRS.EQ .calc_sub
CALLS .magnitudes
LEA r14 r0 '*'
CMP r10 r14
BRS.EQ .calc_mul
CMP r2 r0
BRS.EQ .zero_divisor
LEA r14 r0 '%'
CMP r10 r14
BRS.EQ .calc_mod
DIV r3 r1 r2
CMP r4 r0
BRS.NE .negate_result
CMP r3 r0
BRS.NEG .arithmetic_overflow
JMPS .calculated
.calc_mod
MOD r3 r1 r2
CMP r8 r0
BRS.NEG .negate_result
JMPS .calculated
.calc_mul
MOV r3 r0
CMP r1 r0
BRS.EQ .calculated
CMP r2 r0
BRS.EQ .calculated
LDI r12 0x7fffffff
CMP r4 r0
BRS.EQ .mul_limit
LDI r12 0x80000000
.mul_limit
DIV r12 r12 r1
CMP r12 r2
BRS.NC .arithmetic_overflow
MUL r3 r1 r2
CMP r4 r0
BRS.EQ .calculated
.negate_result
NEG r3 r3
JMPS .calculated
.calc_add
ADD r3 r8 r9
XOR r1 r8 r9
NOT r1 r1
XOR r2 r8 r3
AND r1 r1 r2
BRS.NEG .arithmetic_overflow
JMPS .calculated
.calc_sub
SUB r3 r8 r9
XOR r1 r8 r9
XOR r2 r8 r3
AND r1 r1 r2
BRS.NEG .arithmetic_overflow
.calculated
MOV r8 r3
MOV r9 r0
MOV r10 r0
LEA r11 r0 2
MOV r5 r0
MOV r6 r0
LEA r7 r0 1
CMP r8 r0
BRS.GE .calculation_return
LEA r6 r0 1
.calculation_return
RET
.zero_divisor
LEA r5 r0 1
RET
.arithmetic_overflow
LEA r5 r0 2
RET
.magnitudes
MOV r1 r8
MOV r2 r9
MOV r4 r0
CMP r1 r0
BRS.GE .magnitude_right
NEG r1 r1
LEA r4 r0 1
.magnitude_right
CMP r2 r0
BRS.GE .magnitude_return
NEG r2 r2
LEA r14 r0 1
XOR r4 r4 r14
.magnitude_return
RET
.get_active
MOV r3 r8
LEA r14 r0 1
CMP r11 r14
BRS.NE .active_return
MOV r3 r9
.active_return
RET
.put_active
LEA r14 r0 1
CMP r11 r14
BRS.EQ .put_second
MOV r8 r3
RET
.put_second
MOV r9 r3
RET
.clear_state
MOV r5 r0
MOV r6 r0
MOV r7 r0
MOV r8 r0
MOV r9 r0
MOV r10 r0
MOV r11 r0
RET
.clear
CALLS .clear_state
JMPS .redraw
.universal
SYS SYS_APP_EVENT
JMPS .loop

.draw_value
; Signed decimal operand/result, or an explicit arithmetic error. The current
; binary operator remains visible next to the second operand.
LDI r12 GPU_TEXT
LEA r1 r0 32
LEA r2 r0 ' '
.clear_line
STB r12 r2 0
INC r12
DEC r1
BRS.NE .clear_line
CMP r5 r0
BRS.EQ .draw_number
LEA r14 r0 1
CMP r5 r14
BRS.EQ .draw_zero_error
LDI r12 GPU_TEXT
LDI r1 0x5245564f
STW r12 r1 0
LDI r1 0x574f4c46
STW r12 r1 4
JMPS .submit
.draw_zero_error
LDI r12 GPU_TEXT
LDI r1 0x49564944
STW r12 r1 0
LDI r1 0x4e4f4953
STW r12 r1 4
LDI r1 0x20594220
STW r12 r1 8
LDI r1 0x4f52455a
STW r12 r1 12
JMPS .submit
.draw_number
CALLS .get_active
CMP r6 r0
BRS.EQ .draw_magnitude
NEG r3 r3
.draw_magnitude
LEA r4 r0 15
LEA r14 r0 10
.digit_loop
MOD r1 r3 r14
DIV r3 r3 r14
LEA r2 r0 '0'
ADD r1 r1 r2
LDI r12 GPU_TEXT
ADD r12 r12 r4
STB r12 r1 0
DEC r4
CMP r3 r0
BRS.NE .digit_loop
CMP r6 r0
BRS.EQ .draw_operator
LDI r12 GPU_TEXT
ADD r12 r12 r4
LEA r1 r0 '-'
STB r12 r1 0
.draw_operator
LEA r14 r0 1
CMP r11 r14
BRS.NE .submit
LDI r12 GPU_TEXT
LEA r1 r0 '('
STB r12 r1 18
STB r12 r10 19
LEA r1 r0 ')'
STB r12 r1 20
.submit
LDI r12 GPU_BASE
LEA r14 r0 0x13
STW r12 r14 4
LEA r14 r0 1
STW r12 r14 40
STW r12 r0 8
LEA r14 r0 32
STW r12 r14 12
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
RET
.idle
SYS SYS_YIELD
JMPS .loop
