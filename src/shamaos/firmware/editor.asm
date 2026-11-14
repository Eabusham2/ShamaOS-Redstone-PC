; ShamaOS Editor. Fully editable 64 KiB sources, insertion cursor, named files,
; visible menus, Cancel-first delete, checked assembly and durable close state.
; r5 view byte, r6 filename length, r7 source length, r8 mode, r9 selection,
; r10 open handle, r11 file type, r15 insertion cursor. Syscalls preserve r3-r15.
; Context +0 handle,+4 type,+8 length,+12 handoff magic,+16 name length,
; +20 dirty,+24 cursor,+28 view,+32 diagnostic,+40 return mode,+44 name input
; length,+48 assembled size,+52 cursor x,+56 cursor y,+64 assembly output pointer.
define CTX 0x00002600
define NAME 0x00002400
define NAME_EDIT 0x00002480
define BIN_NAME 0x00002500
define TEXT 0x00004000
define CAPACITY 65535
define RUN 0x00014000
define GPU 0x02000000
define GPU_TEXT 0x020004a0
define MAGIC 0x5348414d
MOV r5 r0
MOV r6 r0
MOV r7 r0
MOV r8 r0
MOV r9 r0
MOV r10 r0
LEA r11 r0 1
MOV r15 r0
LDIS r14 CTX
LDW r1 r14 12
LDI r2 MAGIC
CMP r1 r2
BRS.NE .fresh
LDW r10 r14 0
LDW r11 r14 4
LDW r7 r14 8
LDW r6 r14 16
LDW r15 r14 24
STW r14 r0 12
LEA r8 r0 3
JMPS .redraw
.fresh
STW r14 r0 20
STW r14 r0 32
STW r14 r0 36
.redraw
CALLS .sync
MOV r1 r8
MOV r2 r9
LDIS r14 CTX
LDW r3 r14 32
SYS SYS_UI_EDITOR_VIEW
CALLS .draw_content
.loop
CALLS .sync
SYS SYS_GET_EVENT
CMP r1 r0
BRS.EQ .idle
; Publish a sequence-locked source transaction for asynchronous close/save.
LDIS r14 CTX
LDW r12 r14 36
LEA r13 r0 1
OR r12 r12 r13
STW r14 r12 36
LEA r14 r0 10
SHR r12 r1 r14
LEA r14 r0 255
AND r12 r12 r14
LEA r14 r0 18
SHR r13 r1 r14
LEA r14 r0 255
AND r13 r13 r14
CMP r8 r0
BRS.EQ .menu
LEA r14 r0 3
CMP r8 r14
BRS.EQ .edit
LEA r14 r0 4
CMP r8 r14
BRS.EQ .actions
LEA r14 r0 5
CMP r8 r14
BRS.EQ .confirm
LEA r14 r0 8
CMP r8 r14
BRS.EQ .diagnostic
JMPS .name_input
.menu
LEA r14 r0 0x10
CMP r12 r14
BRS.EQ .menu_up
LEA r14 r0 0x11
CMP r12 r14
BRS.EQ .menu_down
LEA r14 r0 0x14
CMP r12 r14
BRS.NE .universal
CMP r9 r0
BRS.EQ .new_text
LEA r14 r0 1
CMP r9 r14
BRS.EQ .new_program
LEA r1 r0 2
CALLS .sync
SYS SYS_APP_LAUNCH
JMPS .loop
.menu_up
CMP r9 r0
BRS.EQ .redraw
DEC r9
JMPS .redraw
.menu_down
LEA r14 r0 2
CMP r9 r14
BRS.GE .redraw
INC r9
JMPS .redraw
.new_text
LEA r4 r0 1
JMPS .new_document
.new_program
LEA r4 r0 2
.new_document
; Keep the current mode and file type until saving the old document succeeds.
CALLS .save
CMP r2 r0
BRS.NE .error
LDIS r14 CTX
STW r14 r11 60
MOV r11 r4
MOV r8 r4
.new_name
LDIS r14 CTX
STW r14 r0 44
JMPS .redraw
.name_input
LEA r14 r0 0x15
CMP r12 r14
BRS.EQ .name_cancel
LEA r14 r0 1
CMP r12 r14
BRS.NE .universal
LEA r14 r0 13
CMP r13 r14
BRS.EQ .name_enter
LDIS r14 CTX
LDW r4 r14 44
LEA r14 r0 8
CMP r13 r14
BRS.EQ .name_backspace
LEA r14 r0 32
CMP r13 r14
BRS.LT .redraw
LEA r14 r0 126
CMP r13 r14
BRS.GT .redraw
LEA r14 r0 43
CMP r4 r14
BRS.GE .capacity_error
LDIS r14 NAME_EDIT
ADD r14 r14 r4
STB r14 r13 0
INC r4
JMPS .name_length
.name_backspace
CMP r4 r0
BRS.EQ .redraw
DEC r4
.name_length
LDIS r14 CTX
STW r14 r4 44
JMPS .redraw
.name_cancel
LEA r14 r0 2
CMP r8 r14
BRS.GT .name_cancel_ready
LDIS r14 CTX
LDW r11 r14 60
.name_cancel_ready
CMP r10 r0
BRS.GT .back_edit
MOV r8 r0
MOV r9 r0
JMPS .redraw
.name_enter
LDIS r14 CTX
LDW r4 r14 44
CMP r4 r0
BRS.EQ .redraw
CALLS .finish_name
CMP r2 r0
BRS.NE .error
LEA r14 r0 6
CMP r8 r14
BRS.EQ .rename
LEA r14 r0 7
CMP r8 r14
BRS.EQ .save_as
; Create is exclusive. A rejected name never destroys the previous file.
LDIS r1 NAME_EDIT
MOV r2 r11
SYS SYS_FILE_CREATE
CMP r2 r0
BRS.NE .error
PUSH r1
MOV r1 r10
CMP r1 r0
BRS.LE .new_handle
SYS SYS_FILE_CLOSE
.new_handle
POP r10
MOV r6 r4
CALLS .commit_name
MOV r7 r0
MOV r15 r0
MOV r5 r0
LDIS r14 CTX
STW r14 r0 20
JMPS .back_edit
.rename
MOV r1 r10
LDIS r2 NAME_EDIT
SYS SYS_FILE_RENAME
CMP r2 r0
BRS.NE .error
MOV r6 r4
CALLS .commit_name
JMPS .back_edit
.save_as
LDIS r1 NAME_EDIT
MOV r2 r11
SYS SYS_FILE_CREATE
CMP r2 r0
BRS.NE .error
PUSH r10
MOV r10 r1
CALLS .write_current
CMP r2 r0
BRS.NE .save_as_fail
POP r1
CMP r1 r0
BRS.LE .save_as_done
SYS SYS_FILE_CLOSE
.save_as_done
MOV r6 r4
CALLS .commit_name
JMPS .back_edit
.save_as_fail
PUSH r2
MOV r1 r10
SYS SYS_FILE_DELETE
POP r2
POP r10
JMPS .error
.edit
LEA r14 r0 1
CMP r12 r14
BRS.EQ .key
LEA r14 r0 0x14
CMP r12 r14
BRS.EQ .save_action
LEA r14 r0 0x15
CMP r12 r14
BRS.EQ .open_actions
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
JMPS .universal
.key
LEA r14 r0 8
CMP r13 r14
BRS.EQ .backspace
LEA r14 r0 127
CMP r13 r14
BRS.EQ .delete_char
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
LEA r14 r0 13
CMP r13 r14
BRS.NE .key_tab
LEA r13 r0 10
JMPS .insert
.key_tab
LEA r14 r0 9
CMP r13 r14
BRS.EQ .insert
LEA r14 r0 32
CMP r13 r14
BRS.LT .redraw
LEA r14 r0 126
CMP r13 r14
BRS.GT .redraw
.insert
LDIS r14 CAPACITY
CMP r7 r14
BRS.GE .capacity_error
MOV r1 r7
.insert_shift
CMP r1 r15
BRS.LE .insert_here
LDIS r2 TEXT
ADD r2 r2 r1
LDB r3 r2 -1
STB r2 r3 0
DEC r1
JMPS .insert_shift
.insert_here
LDIS r2 TEXT
ADD r2 r2 r15
STB r2 r13 0
INC r7
INC r15
CALLS .dirty
JMPS .show_cursor
.backspace
CMP r15 r0
BRS.EQ .redraw
DEC r15
.delete_char
CMP r15 r7
BRS.GE .redraw
MOV r1 r15
.delete_shift
INC r1
CMP r1 r7
BRS.GE .delete_done
LDIS r2 TEXT
ADD r2 r2 r1
LDB r3 r2 0
STB r2 r3 -1
JMPS .delete_shift
.delete_done
DEC r7
CALLS .dirty
JMPS .show_cursor
.left
CMP r15 r0
BRS.EQ .redraw
DEC r15
JMPS .show_cursor
.right
CMP r15 r7
BRS.GE .redraw
INC r15
JMPS .show_cursor
.up
MOV r1 r15
CALLS .line_start
MOV r3 r1
SUB r4 r15 r1
CMP r1 r0
BRS.EQ .redraw
DEC r1
CALLS .line_start
ADD r15 r1 r4
DEC r3
CMP r15 r3
BRS.LE .show_cursor
MOV r15 r3
JMPS .show_cursor
.down
MOV r1 r15
CALLS .line_start
SUB r4 r15 r1
MOV r1 r15
CALLS .line_end
CMP r1 r7
BRS.GE .redraw
INC r1
MOV r3 r1
CALLS .line_end
ADD r15 r3 r4
CMP r15 r1
BRS.LE .show_cursor
MOV r15 r1
.show_cursor
MOV r1 r15
CALLS .line_start
; Long lines scroll horizontally in 48-character segments.
MOV r5 r1
SUB r1 r15 r1
LEA r2 r0 48
DIV r1 r1 r2
MUL r1 r1 r2
ADD r5 r5 r1
JMPS .redraw
.line_start
CMP r1 r0
BRS.EQ .line_return
LDIS r2 TEXT
ADD r2 r2 r1
LDB r2 r2 -1
LEA r12 r0 10
CMP r2 r12
BRS.EQ .line_return
DEC r1
JMPS .line_start
.line_end
CMP r1 r7
BRS.GE .line_return
LDIS r2 TEXT
ADD r2 r2 r1
LDB r2 r2 0
LEA r12 r0 10
CMP r2 r12
BRS.EQ .line_return
INC r1
JMPS .line_end
.line_return
RET
.open_actions
LEA r8 r0 4
MOV r9 r0
JMPS .redraw
.actions
LEA r14 r0 0x15
CMP r12 r14
BRS.EQ .back_edit
LEA r14 r0 0x10
CMP r12 r14
BRS.EQ .menu_up
LEA r14 r0 0x11
CMP r12 r14
BRS.EQ .action_down
LEA r14 r0 0x14
CMP r12 r14
BRS.NE .universal
CMP r9 r0
BRS.EQ .save_action
LEA r14 r0 1
CMP r9 r14
BRS.EQ .save_as_action
LEA r14 r0 2
CMP r9 r14
BRS.EQ .rename_action
LEA r14 r0 3
CMP r9 r14
BRS.EQ .delete_action
LEA r14 r0 5
CMP r9 r14
BRS.LE .assemble_action
LEA r14 r0 6
CMP r9 r14
BRS.EQ .new_text
LEA r14 r0 7
CMP r9 r14
BRS.EQ .new_program
CALLS .sync
SYS SYS_EXIT
JMPS .loop
.action_down
LEA r14 r0 8
CMP r9 r14
BRS.GE .redraw
INC r9
JMPS .redraw
.save_action
CALLS .save
CMP r2 r0
BRS.NE .error
JMPS .back_edit
.save_as_action
LEA r8 r0 7
JMPS .new_name
.rename_action
LEA r8 r0 6
JMPS .new_name
.delete_action
LEA r8 r0 5
MOV r9 r0
JMPS .redraw
.assemble_action
LEA r14 r0 2
CMP r11 r14
BRS.NE .type_error
CALLS .save
CMP r2 r0
BRS.NE .error
; Reuse unused source-buffer tail for normal documents. Long source keeps the
; separate staging fallback, preserving the full 65,535-byte editing capacity.
LDIS r3 RUN
LDIS r14 49152
CMP r7 r14
BRS.GT .assembly_buffer_ready
LDIS r3 TEXT
ADD r3 r3 r7
LEA r3 r3 3
ANDIS r3 0xfffffffc
.assembly_buffer_ready
LDIS r14 CTX
STW r14 r3 64
LDIS r1 TEXT
MOV r2 r7
LDIS r4 0x3ffc
SYS SYS_ASSEMBLE
CMP r2 r0
BRS.NE .error
LDIS r14 CTX
STW r14 r1 48
CALLS .save_binary
CMP r2 r0
BRS.NE .error
LEA r14 r0 4
CMP r9 r14
BRS.EQ .back_edit
LDIS r14 CTX
LDW r2 r14 48
LDW r1 r14 64
CALLS .sync
SYS SYS_RUN_BUFFER
JMPS .loop
.confirm
LEA r14 r0 0x15
CMP r12 r14
BRS.EQ .back_edit
LEA r14 r0 0x12
CMP r12 r14
BRS.EQ .cancel_select
LEA r14 r0 0x10
CMP r12 r14
BRS.EQ .cancel_select
LEA r14 r0 0x13
CMP r12 r14
BRS.EQ .delete_select
LEA r14 r0 0x11
CMP r12 r14
BRS.EQ .delete_select
LEA r14 r0 0x14
CMP r12 r14
BRS.NE .universal
CMP r9 r0
BRS.EQ .back_edit
MOV r1 r10
SYS SYS_FILE_DELETE
CMP r2 r0
BRS.NE .error
MOV r10 r0
MOV r6 r0
MOV r7 r0
MOV r15 r0
MOV r5 r0
MOV r8 r0
MOV r9 r0
LDIS r14 CTX
STW r14 r0 20
JMPS .redraw
.cancel_select
MOV r9 r0
JMPS .redraw
.delete_select
LEA r9 r0 1
JMPS .redraw
.back_edit
LEA r8 r0 3
MOV r9 r0
JMPS .redraw
.capacity_error
LEA r2 r0 9
JMPS .error
.type_error
LEA r2 r0 10
.error
LDIS r14 CTX
STW r14 r2 32
STW r14 r8 40
LEA r8 r0 8
JMPS .redraw
.diagnostic
LEA r14 r0 0x14
CMP r12 r14
BRS.EQ .diagnostic_back
LEA r14 r0 0x15
CMP r12 r14
BRS.NE .universal
.diagnostic_back
LDIS r14 CTX
LDW r8 r14 40
STW r14 r0 32
JMPS .redraw
.universal
CALLS .sync
SYS SYS_APP_EVENT
JMPS .redraw
.idle
SYS SYS_YIELD
JMPS .loop

.sync
PUSH r12
PUSH r13
LDIS r14 CTX
STW r14 r10 0
STW r14 r11 4
STW r14 r7 8
STW r14 r6 16
STW r14 r15 24
STW r14 r5 28
LDW r12 r14 36
LEA r13 r0 1
AND r13 r12 r13
CMP r13 r0
BRS.EQ .sync_done
INC r12
STW r14 r12 36
.sync_done
POP r13
POP r12
RET
.dirty
LEA r1 r0 1
LDIS r14 CTX
STW r14 r1 20
JMPS .sync
.save
LDIS r14 CTX
LDW r1 r14 20
CMP r1 r0
BRS.NE .write_current
MOV r2 r0
RET
.write_current
CMP r10 r0
BRS.LE .save_no_name
MOV r1 r10
LDIS r2 TEXT
MOV r3 r7
SYS SYS_FILE_WRITE
CMP r2 r0
BRS.NE .save_return
LDIS r14 CTX
STW r14 r0 20
.save_return
RET
.save_no_name
LEA r2 r0 10
RET
.finish_name
; r4 is typed length. Existing '.xxx' is preserved; otherwise add .TXT/.ASM.
MOV r2 r0
LDIS r1 NAME_EDIT
ADD r1 r1 r4
LEA r14 r0 4
CMP r4 r14
BRS.LT .append_extension
LDB r3 r1 -4
LEA r14 r0 46
CMP r3 r14
BRS.EQ .name_terminate
.append_extension
LEA r14 r0 39
CMP r4 r14
BRS.GT .name_too_long
LDI r3 0x5458542e
LEA r14 r0 2
CMP r11 r14
BRS.NE .extension_ready
LDI r3 0x4d53412e
.extension_ready
; Write bytes so every filename length remains valid on the aligned-word bus.
LEA r2 r0 4
LEA r14 r0 8
.extension_byte
STB r1 r3 0
SHR r3 r3 r14
INC r1
DEC r2
BRS.NE .extension_byte
LEA r4 r4 4
.name_terminate
STB r1 r0 0
RET
.name_too_long
LEA r2 r0 9
RET
.commit_name
LDIS r1 NAME_EDIT
LDIS r2 NAME
MOV r3 r6
.commit_loop
LDB r14 r1 0
STB r2 r14 0
INC r1
INC r2
CMP r3 r0
BRS.EQ .commit_done
DEC r3
JMPS .commit_loop
.commit_done
JMPS .sync
.save_binary
; Only reached after a successful complete assembly. Last good BIN is retained
; if compilation or replacement write fails. Name length excludes its NUL.
LDIS r1 NAME
LDIS r2 BIN_NAME
MOV r3 r6
.binary_name_copy
LDB r14 r1 0
STB r2 r14 0
INC r1
INC r2
DEC r3
CMP r3 r0
BRS.GT .binary_name_copy
LDIS r1 BIN_NAME
ADD r1 r1 r6
LEA r14 r0 4
CMP r6 r14
BRS.LT .binary_append
LDB r3 r1 -4
LEA r14 r0 46
CMP r3 r14
BRS.EQ .binary_replace
.binary_append
LEA r14 r0 39
CMP r6 r14
BRS.GT .binary_conflict
LEA r14 r0 46
STB r1 r14 0
LEA r1 r1 4
JMPS .binary_suffix
.binary_replace
; Never replace the source itself when an ASM file was named with .BIN.
LDB r3 r1 -3
LEA r14 r0 66
CMP r3 r14
BRS.NE .binary_suffix
LDB r3 r1 -2
LEA r14 r0 73
CMP r3 r14
BRS.NE .binary_suffix
LDB r3 r1 -1
LEA r14 r0 78
CMP r3 r14
BRS.EQ .binary_conflict
.binary_suffix
LEA r14 r0 66
STB r1 r14 -3
LEA r14 r0 73
STB r1 r14 -2
LEA r14 r0 78
STB r1 r14 -1
STB r1 r0 0
LDIS r1 BIN_NAME
SYS SYS_FILE_OPEN
CMP r2 r0
BRS.EQ .binary_check_type
LDIS r1 BIN_NAME
LEA r2 r0 3
SYS SYS_FILE_CREATE
CMP r2 r0
BRS.NE .binary_return
JMPS .binary_handle
.binary_check_type
PUSH r1
SYS SYS_FILE_STAT
LEA r3 r0 255
AND r3 r1 r3
LEA r4 r0 3
CMP r3 r4
BRS.EQ .binary_type_ok
POP r1
SYS SYS_FILE_CLOSE
JMPS .binary_conflict
.binary_type_ok
POP r1
.binary_handle
PUSH r1
LDIS r14 CTX
LDW r3 r14 48
LDW r2 r14 64
SYS SYS_FILE_WRITE
MOV r4 r2
POP r1
SYS SYS_FILE_CLOSE
MOV r2 r4
.binary_return
RET
.binary_conflict
LEA r2 r0 11
RET

.draw_content
; UI kernel has fenced its full redraw. All following text writes are fenced
; before reusing the 96-byte content scratch at GPU text offset 160.
LDI r12 GPU
LEA r1 r0 0x13
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
LEA r2 r0 44
LDIS r3 NAME
MOV r4 r6
LEA r1 r0 1
CMP r8 r1
BRS.EQ .draw_candidate
LEA r1 r0 2
CMP r8 r1
BRS.EQ .draw_candidate
LEA r1 r0 6
CMP r8 r1
BRS.EQ .draw_candidate
LEA r1 r0 7
CMP r8 r1
BRS.NE .draw_name_ready
.draw_candidate
LDIS r3 NAME_EDIT
LDIS r14 CTX
LDW r4 r14 44
.draw_name_ready
CALLS .draw_span
LEA r1 r0 8
CMP r8 r1
BRS.EQ .draw_diagnostic
LEA r1 r0 3
CMP r8 r1
BRS.NE .draw_finish
; Eight readable logical lines, at most 48 characters each.
MOV r1 r5
LEA r2 r0 56
.draw_line
MOV r3 r1
MOV r4 r0
.draw_count
CMP r1 r15
BRS.NE .draw_not_cursor
LDIS r12 CTX
LEA r13 r0 6
MUL r13 r4 r13
LEA r13 r13 4
STW r12 r13 52
STW r12 r2 56
.draw_not_cursor
CMP r1 r7
BRS.GE .draw_line_ready
LEA r14 r0 48
CMP r4 r14
BRS.GE .draw_line_ready
LDIS r12 TEXT
ADD r12 r12 r1
LDB r13 r12 0
LEA r14 r0 10
CMP r13 r14
BRS.EQ .draw_newline
INC r4
INC r1
JMPS .draw_count
.draw_newline
INC r1
.draw_line_ready
LDIS r14 TEXT
ADD r3 r3 r14
CALLS .draw_span
CMP r1 r7
BRS.GE .draw_cursor
LEA r2 r2 10
LEA r14 r0 136
CMP r2 r14
BRS.LT .draw_line
.draw_cursor
LDIS r12 CTX
LDW r1 r12 52
LDW r2 r12 56
LDI r12 GPU
STW r12 r1 8
STW r12 r2 12
LEA r1 r0 1
STW r12 r1 16
LEA r1 r0 0x12
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
JMPS .draw_finish
.draw_diagnostic
; Packed hardware error -> explicit decimal source line and hex error code.
LDI r3 GPU_TEXT
LDI r12 0x454e494c
STW r3 r12 0
LDI r12 0x45444f43
STW r3 r12 12
LEA r12 r0 32
STB r3 r12 4
STB r3 r12 10
STB r3 r12 11
STB r3 r12 16
LDIS r14 CTX
LDW r4 r14 32
LEA r2 r0 8
SHR r1 r4 r2
LEA r2 r0 9
LEA r13 r0 10
.diag_decimal
MOD r12 r1 r13
LEA r12 r12 48
ADD r14 r3 r2
STB r14 r12 0
DIV r1 r1 r13
DEC r2
LEA r14 r0 5
CMP r2 r14
BRS.GE .diag_decimal
LEA r2 r0 4
LEA r3 r3 17
.diag_hex
SHR r12 r4 r2
LEA r13 r0 15
AND r12 r12 r13
LEA r13 r0 10
CMP r12 r13
BRS.LT .diag_digit
LEA r13 r0 55
JMPS .diag_emit
.diag_digit
LEA r13 r0 48
.diag_emit
ADD r12 r12 r13
STB r3 r12 0
INC r3
LEA r13 r0 4
SUB r2 r2 r13
CMP r2 r0
BRS.GE .diag_hex
LDI r12 GPU
LEA r1 r0 0xa0
STW r12 r1 8
LEA r1 r0 19
STW r12 r1 12
LEA r1 r0 4
STW r12 r1 16
LEA r1 r0 56
STW r12 r1 20
LEA r1 r0 0x0c
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40

.draw_finish
LDI r12 GPU
LEA r1 r0 0x14
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
SYS SYS_GPU_FENCE
RET
.draw_span
; RAM pointer r3, count r4, y r2. Only r1/r2 carry caller row/index state.
; The private renderer may overwrite r3/r4 and r12-r14.
PUSH r1
PUSH r2
CMP r4 r0
BRS.LE .span_return
MOV r1 r0
.span_copy
CMP r1 r4
BRS.GE .span_submit
ADD r12 r3 r1
LDB r13 r12 0
LEA r14 r0 9
CMP r13 r14
BRS.NE .span_char
LEA r13 r0 32
.span_char
LDI r12 GPU_TEXT
ADD r12 r12 r1
STB r12 r13 0
INC r1
JMPS .span_copy
.span_submit
LDI r12 GPU
LEA r1 r0 0xa0
STW r12 r1 8
STW r12 r4 12
LEA r1 r0 4
STW r12 r1 16
STW r12 r2 20
LEA r1 r0 0x0c
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
SYS SYS_GPU_FENCE
.span_return
POP r2
POP r1
RET
