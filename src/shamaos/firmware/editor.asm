; ShamaOS Editor. Fully editable 64 KiB sources, insertion cursor, named files,
; visible menus, Cancel-first delete, checked assembly and durable close state.
; r5 view byte, r6 filename length, r7 source length, r8 mode, r9 selection,
; r10 open handle, r11 file type, r15 insertion cursor. Syscalls preserve r3-r15.
; Context +0 handle,+4 type,+8 length,+12 handoff magic,+16 name length,
; +20 dirty,+24 cursor,+28 view,+32 diagnostic,+40 return mode,+44 name input
; length,+48 assembled size,+52 cursor x,+56 cursor y.
define CTX 0x000b1000
define NAME 0x000b0000
define NAME_EDIT 0x000b0080
define BIN_NAME 0x000b0100
define TEXT 0x000c0000
define CAPACITY 65535
define RUN 0x000e0000
define GPU 0x02000000
define GPU_TEXT 0x020004a0
define MAGIC 0x5348414d
LDI r5 0
LDI r6 0
LDI r7 0
LDI r8 0
LDI r9 0
LDI r10 0
LDI r11 1
LDI r15 0
LDI r14 CTX
LDW r1 r14 12
LDI r2 MAGIC
CMP r1 r2
BR.NE .fresh
LDW r10 r14 0
LDW r11 r14 4
LDW r7 r14 8
LDW r6 r14 16
LDW r15 r14 24
STW r14 r0 12
LDI r8 3
JMP .redraw
.fresh
STW r14 r0 20
STW r14 r0 32
STW r14 r0 36
.redraw
CALL .sync
MOV r1 r8
MOV r2 r9
LDI r14 CTX
LDW r3 r14 32
SYS SYS_UI_EDITOR_VIEW
CALL .draw_content
.loop
CALL .sync
SYS SYS_GET_EVENT
CMP r1 r0
BR.EQ .idle
; Publish a sequence-locked source transaction for asynchronous close/save.
LDI r14 CTX
LDW r12 r14 36
LDI r13 1
OR r12 r12 r13
STW r14 r12 36
MOV r12 r1
LDI r14 10
SHR r12 r12 r14
LDI r14 255
AND r12 r12 r14
MOV r13 r1
LDI r14 18
SHR r13 r13 r14
LDI r14 255
AND r13 r13 r14
CMP r8 r0
BR.EQ .menu
LDI r14 3
CMP r8 r14
BR.EQ .edit
LDI r14 4
CMP r8 r14
BR.EQ .actions
LDI r14 5
CMP r8 r14
BR.EQ .confirm
LDI r14 8
CMP r8 r14
BR.EQ .diagnostic
JMP .name_input
.menu
LDI r14 0x10
CMP r12 r14
BR.EQ .menu_up
LDI r14 0x11
CMP r12 r14
BR.EQ .menu_down
LDI r14 0x14
CMP r12 r14
BR.NE .universal
CMP r9 r0
BR.EQ .new_text
LDI r14 1
CMP r9 r14
BR.EQ .new_program
LDI r1 2
CALL .sync
SYS SYS_APP_LAUNCH
JMP .loop
.menu_up
CMP r9 r0
BR.EQ .redraw
DEC r9
JMP .redraw
.menu_down
LDI r14 2
CMP r9 r14
BR.GE .redraw
INC r9
JMP .redraw
.new_text
CALL .save
CMP r2 r0
BR.NE .error
LDI r14 CTX
STW r14 r11 60
LDI r11 1
LDI r8 1
JMP .new_name
.new_program
CALL .save
CMP r2 r0
BR.NE .error
LDI r14 CTX
STW r14 r11 60
LDI r11 2
LDI r8 2
.new_name
LDI r14 CTX
STW r14 r0 44
JMP .redraw
.name_input
LDI r14 0x15
CMP r12 r14
BR.EQ .name_cancel
LDI r14 1
CMP r12 r14
BR.NE .universal
LDI r14 13
CMP r13 r14
BR.EQ .name_enter
LDI r14 CTX
LDW r4 r14 44
LDI r14 8
CMP r13 r14
BR.EQ .name_backspace
LDI r14 32
CMP r13 r14
BR.LT .redraw
LDI r14 126
CMP r13 r14
BR.GT .redraw
LDI r14 43
CMP r4 r14
BR.GE .capacity_error
LDI r14 NAME_EDIT
ADD r14 r14 r4
STB r14 r13 0
INC r4
JMP .name_length
.name_backspace
CMP r4 r0
BR.EQ .redraw
DEC r4
.name_length
LDI r14 CTX
STW r14 r4 44
JMP .redraw
.name_cancel
LDI r14 2
CMP r8 r14
BR.GT .name_cancel_ready
LDI r14 CTX
LDW r11 r14 60
.name_cancel_ready
CMP r10 r0
BR.GT .back_edit
LDI r8 0
LDI r9 0
JMP .redraw
.name_enter
LDI r14 CTX
LDW r4 r14 44
CMP r4 r0
BR.EQ .redraw
CALL .finish_name
CMP r2 r0
BR.NE .error
LDI r14 6
CMP r8 r14
BR.EQ .rename
LDI r14 7
CMP r8 r14
BR.EQ .save_as
; Create is exclusive. A rejected name never destroys the previous file.
LDI r1 NAME_EDIT
MOV r2 r11
SYS SYS_FILE_CREATE
CMP r2 r0
BR.NE .error
PUSH r1
MOV r1 r10
CMP r1 r0
BR.LE .new_handle
SYS SYS_FILE_CLOSE
.new_handle
POP r10
MOV r6 r4
CALL .commit_name
LDI r7 0
LDI r15 0
LDI r5 0
LDI r14 CTX
STW r14 r0 20
JMP .back_edit
.rename
MOV r1 r10
LDI r2 NAME_EDIT
SYS SYS_FILE_RENAME
CMP r2 r0
BR.NE .error
MOV r6 r4
CALL .commit_name
JMP .back_edit
.save_as
LDI r1 NAME_EDIT
MOV r2 r11
SYS SYS_FILE_CREATE
CMP r2 r0
BR.NE .error
PUSH r10
MOV r10 r1
CALL .write_current
CMP r2 r0
BR.NE .save_as_fail
POP r1
CMP r1 r0
BR.LE .save_as_done
SYS SYS_FILE_CLOSE
.save_as_done
MOV r6 r4
CALL .commit_name
JMP .back_edit
.save_as_fail
PUSH r2
MOV r1 r10
SYS SYS_FILE_DELETE
POP r2
POP r10
JMP .error
.edit
LDI r14 1
CMP r12 r14
BR.EQ .key
LDI r14 0x14
CMP r12 r14
BR.EQ .save_action
LDI r14 0x15
CMP r12 r14
BR.EQ .open_actions
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
JMP .universal
.key
LDI r14 8
CMP r13 r14
BR.EQ .backspace
LDI r14 127
CMP r13 r14
BR.EQ .delete_char
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
LDI r14 13
CMP r13 r14
BR.NE .key_tab
LDI r13 10
JMP .insert
.key_tab
LDI r14 9
CMP r13 r14
BR.EQ .insert
LDI r14 32
CMP r13 r14
BR.LT .redraw
LDI r14 126
CMP r13 r14
BR.GT .redraw
.insert
LDI r14 CAPACITY
CMP r7 r14
BR.GE .capacity_error
MOV r1 r7
.insert_shift
CMP r1 r15
BR.LE .insert_here
LDI r2 TEXT
ADD r2 r2 r1
LDB r3 r2 -1
STB r2 r3 0
DEC r1
JMP .insert_shift
.insert_here
LDI r2 TEXT
ADD r2 r2 r15
STB r2 r13 0
INC r7
INC r15
CALL .dirty
JMP .show_cursor
.backspace
CMP r15 r0
BR.EQ .redraw
DEC r15
.delete_char
CMP r15 r7
BR.GE .redraw
MOV r1 r15
.delete_shift
INC r1
CMP r1 r7
BR.GE .delete_done
LDI r2 TEXT
ADD r2 r2 r1
LDB r3 r2 0
STB r2 r3 -1
JMP .delete_shift
.delete_done
DEC r7
CALL .dirty
JMP .show_cursor
.left
CMP r15 r0
BR.EQ .redraw
DEC r15
JMP .show_cursor
.right
CMP r15 r7
BR.GE .redraw
INC r15
JMP .show_cursor
.up
MOV r1 r15
CALL .line_start
MOV r3 r1
SUB r4 r15 r1
CMP r1 r0
BR.EQ .redraw
DEC r1
CALL .line_start
ADD r15 r1 r4
DEC r3
CMP r15 r3
BR.LE .show_cursor
MOV r15 r3
JMP .show_cursor
.down
MOV r1 r15
CALL .line_start
SUB r4 r15 r1
MOV r1 r15
CALL .line_end
CMP r1 r7
BR.GE .redraw
INC r1
MOV r3 r1
CALL .line_end
ADD r15 r3 r4
CMP r15 r1
BR.LE .show_cursor
MOV r15 r1
.show_cursor
MOV r1 r15
CALL .line_start
; Long lines scroll horizontally in 48-character segments.
MOV r5 r1
SUB r1 r15 r1
LDI r2 48
DIV r1 r1 r2
MUL r1 r1 r2
ADD r5 r5 r1
JMP .redraw
.line_start
CMP r1 r0
BR.EQ .line_return
LDI r2 TEXT
ADD r2 r2 r1
LDB r2 r2 -1
LDI r12 10
CMP r2 r12
BR.EQ .line_return
DEC r1
JMP .line_start
.line_end
CMP r1 r7
BR.GE .line_return
LDI r2 TEXT
ADD r2 r2 r1
LDB r2 r2 0
LDI r12 10
CMP r2 r12
BR.EQ .line_return
INC r1
JMP .line_end
.line_return
RET
.open_actions
LDI r8 4
LDI r9 0
JMP .redraw
.actions
LDI r14 0x15
CMP r12 r14
BR.EQ .back_edit
LDI r14 0x10
CMP r12 r14
BR.EQ .menu_up
LDI r14 0x11
CMP r12 r14
BR.EQ .action_down
LDI r14 0x14
CMP r12 r14
BR.NE .universal
CMP r9 r0
BR.EQ .save_action
LDI r14 1
CMP r9 r14
BR.EQ .save_as_action
LDI r14 2
CMP r9 r14
BR.EQ .rename_action
LDI r14 3
CMP r9 r14
BR.EQ .delete_action
LDI r14 5
CMP r9 r14
BR.LE .assemble_action
LDI r14 6
CMP r9 r14
BR.EQ .new_text
LDI r14 7
CMP r9 r14
BR.EQ .new_program
CALL .sync
SYS SYS_EXIT
JMP .loop
.action_down
LDI r14 8
CMP r9 r14
BR.GE .redraw
INC r9
JMP .redraw
.save_action
CALL .save
CMP r2 r0
BR.NE .error
JMP .back_edit
.save_as_action
LDI r8 7
JMP .new_name
.rename_action
LDI r8 6
JMP .new_name
.delete_action
LDI r8 5
LDI r9 0
JMP .redraw
.assemble_action
LDI r14 2
CMP r11 r14
BR.NE .type_error
CALL .save
CMP r2 r0
BR.NE .error
LDI r1 TEXT
MOV r2 r7
LDI r3 RUN
LDI r4 0x3ffc
SYS SYS_ASSEMBLE
CMP r2 r0
BR.NE .error
LDI r14 CTX
STW r14 r1 48
CALL .save_binary
CMP r2 r0
BR.NE .error
LDI r14 4
CMP r9 r14
BR.EQ .back_edit
LDI r14 CTX
LDW r2 r14 48
LDI r1 RUN
CALL .sync
SYS SYS_RUN_BUFFER
JMP .loop
.confirm
LDI r14 0x15
CMP r12 r14
BR.EQ .back_edit
LDI r14 0x12
CMP r12 r14
BR.EQ .cancel_select
LDI r14 0x10
CMP r12 r14
BR.EQ .cancel_select
LDI r14 0x13
CMP r12 r14
BR.EQ .delete_select
LDI r14 0x11
CMP r12 r14
BR.EQ .delete_select
LDI r14 0x14
CMP r12 r14
BR.NE .universal
CMP r9 r0
BR.EQ .back_edit
MOV r1 r10
SYS SYS_FILE_DELETE
CMP r2 r0
BR.NE .error
LDI r10 0
LDI r6 0
LDI r7 0
LDI r15 0
LDI r5 0
LDI r8 0
LDI r9 0
LDI r14 CTX
STW r14 r0 20
JMP .redraw
.cancel_select
LDI r9 0
JMP .redraw
.delete_select
LDI r9 1
JMP .redraw
.back_edit
LDI r8 3
LDI r9 0
JMP .redraw
.capacity_error
LDI r2 9
JMP .error
.type_error
LDI r2 10
.error
LDI r14 CTX
STW r14 r2 32
STW r14 r8 40
LDI r8 8
JMP .redraw
.diagnostic
LDI r14 0x14
CMP r12 r14
BR.EQ .diagnostic_back
LDI r14 0x15
CMP r12 r14
BR.NE .universal
.diagnostic_back
LDI r14 CTX
LDW r8 r14 40
STW r14 r0 32
JMP .redraw
.universal
CALL .sync
SYS SYS_APP_EVENT
JMP .redraw
.idle
SYS SYS_YIELD
JMP .loop

.sync
PUSH r12
PUSH r13
LDI r14 CTX
STW r14 r10 0
STW r14 r11 4
STW r14 r7 8
STW r14 r6 16
STW r14 r15 24
STW r14 r5 28
LDW r12 r14 36
LDI r13 1
AND r13 r12 r13
CMP r13 r0
BR.EQ .sync_done
INC r12
STW r14 r12 36
.sync_done
POP r13
POP r12
RET
.dirty
LDI r1 1
LDI r14 CTX
STW r14 r1 20
CALL .sync
RET
.save
LDI r14 CTX
LDW r1 r14 20
CMP r1 r0
BR.NE .write_current
LDI r2 0
RET
.write_current
CMP r10 r0
BR.LE .save_no_name
MOV r1 r10
LDI r2 TEXT
MOV r3 r7
SYS SYS_FILE_WRITE
CMP r2 r0
BR.NE .save_return
LDI r14 CTX
STW r14 r0 20
.save_return
RET
.save_no_name
LDI r2 10
RET
.finish_name
; r4 is typed length. Existing '.xxx' is preserved; otherwise add .TXT/.ASM.
LDI r2 0
LDI r14 4
CMP r4 r14
BR.LT .append_extension
LDI r1 NAME_EDIT
ADD r1 r1 r4
LDB r3 r1 -4
LDI r14 46
CMP r3 r14
BR.EQ .name_terminate
.append_extension
LDI r14 39
CMP r4 r14
BR.GT .name_too_long
LDI r1 NAME_EDIT
ADD r1 r1 r4
LDI r3 46
STB r1 r3 0
LDI r14 2
CMP r11 r14
BR.EQ .asm_extension
LDI r3 84
STB r1 r3 1
LDI r3 88
STB r1 r3 2
LDI r3 84
STB r1 r3 3
JMP .extension_done
.asm_extension
LDI r3 65
STB r1 r3 1
LDI r3 83
STB r1 r3 2
LDI r3 77
STB r1 r3 3
.extension_done
LDI r3 4
ADD r4 r4 r3
.name_terminate
LDI r1 NAME_EDIT
ADD r1 r1 r4
STB r1 r0 0
RET
.name_too_long
LDI r2 9
RET
.commit_name
LDI r1 NAME_EDIT
LDI r2 NAME
MOV r3 r6
.commit_loop
LDB r14 r1 0
STB r2 r14 0
INC r1
INC r2
CMP r3 r0
BR.EQ .commit_done
DEC r3
JMP .commit_loop
.commit_done
CALL .sync
RET
.save_binary
; Only reached after a successful complete assembly. Last good BIN is retained
; if compilation or replacement write fails. Name length excludes its NUL.
LDI r1 NAME
LDI r2 BIN_NAME
MOV r3 r6
.binary_name_copy
LDB r14 r1 0
STB r2 r14 0
INC r1
INC r2
DEC r3
CMP r3 r0
BR.GT .binary_name_copy
LDI r1 BIN_NAME
ADD r1 r1 r6
LDI r14 4
CMP r6 r14
BR.LT .binary_append
LDB r3 r1 -4
LDI r14 46
CMP r3 r14
BR.EQ .binary_replace
.binary_append
LDI r14 39
CMP r6 r14
BR.GT .binary_conflict
LDI r14 46
STB r1 r14 0
LDI r14 4
ADD r1 r1 r14
JMP .binary_suffix
.binary_replace
; Never replace the source itself when an ASM file was named with .BIN.
LDB r3 r1 -3
LDI r14 66
CMP r3 r14
BR.NE .binary_suffix
LDB r3 r1 -2
LDI r14 73
CMP r3 r14
BR.NE .binary_suffix
LDB r3 r1 -1
LDI r14 78
CMP r3 r14
BR.EQ .binary_conflict
.binary_suffix
LDI r14 66
STB r1 r14 -3
LDI r14 73
STB r1 r14 -2
LDI r14 78
STB r1 r14 -1
STB r1 r0 0
LDI r1 BIN_NAME
SYS SYS_FILE_OPEN
CMP r2 r0
BR.EQ .binary_check_type
LDI r1 BIN_NAME
LDI r2 3
SYS SYS_FILE_CREATE
CMP r2 r0
BR.NE .binary_return
JMP .binary_handle
.binary_check_type
PUSH r1
SYS SYS_FILE_STAT
LDI r3 255
AND r3 r1 r3
LDI r4 3
CMP r3 r4
BR.EQ .binary_type_ok
POP r1
SYS SYS_FILE_CLOSE
JMP .binary_conflict
.binary_type_ok
POP r1
.binary_handle
PUSH r1
LDI r14 CTX
LDW r3 r14 48
LDI r2 RUN
SYS SYS_FILE_WRITE
PUSH r2
POP r4
POP r1
PUSH r4
SYS SYS_FILE_CLOSE
POP r2
.binary_return
RET
.binary_conflict
LDI r2 11
RET

.draw_content
; UI kernel has fenced its full redraw. All following text writes are fenced
; before reusing the 96-byte content scratch at GPU text offset 160.
LDI r12 GPU
LDI r1 0x13
STW r12 r1 4
LDI r1 1
STW r12 r1 40
LDI r2 44
LDI r3 NAME
MOV r4 r6
LDI r1 1
CMP r8 r1
BR.EQ .draw_candidate
LDI r1 2
CMP r8 r1
BR.EQ .draw_candidate
LDI r1 6
CMP r8 r1
BR.EQ .draw_candidate
LDI r1 7
CMP r8 r1
BR.NE .draw_name_ready
.draw_candidate
LDI r3 NAME_EDIT
LDI r14 CTX
LDW r4 r14 44
.draw_name_ready
CALL .draw_span
LDI r1 8
CMP r8 r1
BR.EQ .draw_diagnostic
LDI r1 3
CMP r8 r1
BR.NE .draw_finish
; Eight readable logical lines, at most 48 characters each.
MOV r1 r5
LDI r2 56
.draw_line
MOV r3 r1
LDI r4 0
.draw_count
CMP r1 r15
BR.NE .draw_not_cursor
LDI r12 CTX
LDI r13 6
MUL r13 r4 r13
LDI r14 4
ADD r13 r13 r14
STW r12 r13 52
STW r12 r2 56
.draw_not_cursor
CMP r1 r7
BR.GE .draw_line_ready
LDI r14 48
CMP r4 r14
BR.GE .draw_line_ready
LDI r12 TEXT
ADD r12 r12 r1
LDB r13 r12 0
LDI r14 10
CMP r13 r14
BR.EQ .draw_newline
INC r4
INC r1
JMP .draw_count
.draw_newline
INC r1
.draw_line_ready
LDI r14 TEXT
ADD r3 r3 r14
CALL .draw_span
CMP r1 r7
BR.GE .draw_cursor
LDI r14 10
ADD r2 r2 r14
LDI r14 136
CMP r2 r14
BR.LT .draw_line
.draw_cursor
LDI r12 CTX
LDW r1 r12 52
LDW r2 r12 56
LDI r12 GPU
STW r12 r1 8
STW r12 r2 12
LDI r1 1
STW r12 r1 16
LDI r1 0x12
STW r12 r1 4
LDI r1 1
STW r12 r1 40
JMP .draw_finish
.draw_diagnostic
; Packed hardware error -> explicit decimal source line and hex error code.
LDI r3 GPU_TEXT
LDI r12 0x454e494c
STW r3 r12 0
LDI r12 0x45444f43
STW r3 r12 12
LDI r12 32
STB r3 r12 4
STB r3 r12 10
STB r3 r12 11
STB r3 r12 16
LDI r14 CTX
LDW r4 r14 32
LDI r2 8
SHR r1 r4 r2
LDI r2 9
LDI r13 10
.diag_decimal
MOD r12 r1 r13
LDI r14 48
ADD r12 r12 r14
ADD r14 r3 r2
STB r14 r12 0
DIV r1 r1 r13
DEC r2
LDI r14 5
CMP r2 r14
BR.GE .diag_decimal
LDI r2 4
LDI r14 17
ADD r3 r3 r14
.diag_hex
SHR r12 r4 r2
LDI r13 15
AND r12 r12 r13
LDI r13 10
CMP r12 r13
BR.LT .diag_digit
LDI r13 55
JMP .diag_emit
.diag_digit
LDI r13 48
.diag_emit
ADD r12 r12 r13
STB r3 r12 0
INC r3
LDI r13 4
SUB r2 r2 r13
CMP r2 r0
BR.GE .diag_hex
LDI r12 GPU
LDI r1 0xa0
STW r12 r1 8
LDI r1 19
STW r12 r1 12
LDI r1 4
STW r12 r1 16
LDI r1 56
STW r12 r1 20
LDI r1 0x0c
STW r12 r1 4
LDI r1 1
STW r12 r1 40

.draw_finish
LDI r12 GPU
LDI r1 0x14
STW r12 r1 4
LDI r1 1
STW r12 r1 40
SYS SYS_GPU_FENCE
RET
.draw_span
; RAM pointer r3, count r4, y r2. Preserve caller row/index state.
PUSH r1
PUSH r2
PUSH r3
PUSH r4
PUSH r12
PUSH r13
PUSH r14
CMP r4 r0
BR.LE .span_return
LDI r1 0
.span_copy
CMP r1 r4
BR.GE .span_submit
ADD r12 r3 r1
LDB r13 r12 0
LDI r14 9
CMP r13 r14
BR.NE .span_char
LDI r13 32
.span_char
LDI r12 GPU_TEXT
ADD r12 r12 r1
STB r12 r13 0
INC r1
JMP .span_copy
.span_submit
LDI r12 GPU
LDI r1 0xa0
STW r12 r1 8
STW r12 r4 12
LDI r1 4
STW r12 r1 16
STW r12 r2 20
LDI r1 0x0c
STW r12 r1 4
LDI r1 1
STW r12 r1 40
SYS SYS_GPU_FENCE
.span_return
POP r14
POP r13
POP r12
POP r4
POP r3
POP r2
POP r1
RET
