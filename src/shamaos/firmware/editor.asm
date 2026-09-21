; ShamaOS Editor
; Modes:
; 0 menu, 1 new TXT name, 2 new ASM name, 3 edit,
; 4 action menu, 5 delete confirm, 6 rename, 7 save-as name.
;
; r10 handle, r11 file type, r7 text length, r6 name length,
; r8 mode, r9 action selection, r5 view offset.

define EVT_KEY 0x01
define EVT_UP 0x10
define EVT_DOWN 0x11
define EVT_LEFT 0x12
define EVT_RIGHT 0x13
define EVT_A 0x14
define EVT_B 0x15

define KEY_ENTER 0x0d
define KEY_BACKSPACE 0x08
define KEY_DELETE 0x7f

define TYPE_TXT 1
define TYPE_ASM 2
define TYPE_BIN 3

define FILE_NAME_PTR 0x000b0000
define BIN_NAME_PTR 0x000b0100
define EDIT_CTX 0x000b1000
define EDIT_BUF 0x000c0000
define RUN_BUF 0x000e0000
define EDIT_MAGIC 0x5348414d

define GPU_BASE 0x02000000
define GPU_TEXT_CONTENT 0x020004a0

LDI r8 0
LDI r9 0
LDI r10 0
LDI r11 TYPE_TXT
LDI r7 0
LDI r6 0
LDI r5 0

; Check for a handoff from File Explorer.
LDI r12 EDIT_CTX
LDW r13 r12 12
LDI r14 EDIT_MAGIC
CMP r13 r14
BR.NE .redraw

LDW r10 r12 0
LDW r11 r12 4
LDW r7 r12 8
LDW r6 r12 16
STW r12 r0 12
LDI r8 3

.redraw
MOV r1 r8
MOV r2 r9
SYS SYS_UI_EDITOR_VIEW
CALL .draw_editor_content
SYS SYS_UI_STATUSBAR

.loop
SYS SYS_GET_EVENT
CMP r1 r0
BR.EQ .idle

; event code -> r12
MOV r12 r1
LDI r14 10
SHR r12 r12 r14
LDI r14 0xff
AND r12 r12 r14

; key code -> r13
MOV r13 r1
LDI r14 18
SHR r13 r13 r14
LDI r14 0xff
AND r13 r13 r14

CMP r8 r0
BR.EQ .menu_event
LDI r14 1
CMP r8 r14
BR.EQ .name_event
LDI r14 2
CMP r8 r14
BR.EQ .name_event
LDI r14 3
CMP r8 r14
BR.EQ .edit_event
LDI r14 4
CMP r8 r14
BR.EQ .action_event
LDI r14 5
CMP r8 r14
BR.EQ .delete_event
LDI r14 6
CMP r8 r14
BR.EQ .name_event
LDI r14 7
CMP r8 r14
BR.EQ .name_event
JMP .redraw

.menu_event
LDI r14 EVT_UP
CMP r12 r14
BR.EQ .menu_up
LDI r14 EVT_DOWN
CMP r12 r14
BR.EQ .menu_down
LDI r14 EVT_A
CMP r12 r14
BR.EQ .menu_open
SYS SYS_APP_EVENT
JMP .redraw

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

.menu_open
CMP r9 r0
BR.EQ .new_text
LDI r14 1
CMP r9 r14
BR.EQ .new_program
; Open uses File Explorer.
LDI r1 2
SYS SYS_APP_LAUNCH
HLT

.new_text
LDI r11 TYPE_TXT
LDI r10 0
LDI r7 0
LDI r6 0
LDI r5 0
LDI r8 1
JMP .redraw

.new_program
LDI r11 TYPE_ASM
LDI r10 0
LDI r7 0
LDI r6 0
LDI r5 0
LDI r8 2
JMP .redraw

.name_event
LDI r14 EVT_B
CMP r12 r14
BR.EQ .name_cancel
LDI r14 EVT_KEY
CMP r12 r14
BR.NE .universal_event

LDI r14 KEY_ENTER
CMP r13 r14
BR.EQ .name_commit
LDI r14 KEY_BACKSPACE
CMP r13 r14
BR.EQ .name_backspace

; Accept printable ASCII while filename remains short enough for ShamaFS.
LDI r14 0x20
CMP r13 r14
BR.LT .redraw
LDI r14 0x7e
CMP r13 r14
BR.GT .redraw
LDI r14 35
CMP r6 r14
BR.GE .redraw
CALL .append_name_key
JMP .redraw

.name_backspace
CMP r6 r0
BR.EQ .redraw
DEC r6
JMP .redraw

.name_cancel
LDI r14 6
CMP r8 r14
BR.EQ .back_to_edit
LDI r14 7
CMP r8 r14
BR.EQ .back_to_edit
LDI r8 0
JMP .redraw

.name_commit
CMP r6 r0
BR.EQ .redraw
CALL .append_extension_and_zero

LDI r14 6
CMP r8 r14
BR.EQ .commit_rename
LDI r14 7
CMP r8 r14
BR.EQ .commit_save_as

; New file.
LDI r1 FILE_NAME_PTR
MOV r2 r11
SYS SYS_FILE_CREATE
MOV r10 r1
LDI r7 0
LDI r5 0
LDI r8 3
JMP .redraw

.commit_rename
MOV r1 r10
LDI r2 FILE_NAME_PTR
SYS SYS_FILE_RENAME
LDI r8 3
JMP .redraw

.commit_save_as
LDI r1 FILE_NAME_PTR
MOV r2 r11
SYS SYS_FILE_CREATE
CMP r1 r0
BR.EQ .redraw
MOV r10 r1
CALL .save_source
LDI r8 3
JMP .redraw

.edit_event
LDI r14 EVT_KEY
CMP r12 r14
BR.EQ .edit_key
LDI r14 EVT_A
CMP r12 r14
BR.EQ .save_and_redraw
LDI r14 EVT_B
CMP r12 r14
BR.EQ .open_actions
LDI r14 EVT_UP
CMP r12 r14
BR.EQ .scroll_up
LDI r14 EVT_DOWN
CMP r12 r14
BR.EQ .scroll_down
SYS SYS_APP_EVENT
JMP .redraw

.edit_key
LDI r14 KEY_BACKSPACE
CMP r13 r14
BR.EQ .text_backspace
LDI r14 KEY_DELETE
CMP r13 r14
BR.EQ .ask_delete
LDI r14 KEY_ENTER
CMP r13 r14
BR.EQ .append_newline

LDI r14 0x20
CMP r13 r14
BR.LT .redraw
LDI r14 0x7e
CMP r13 r14
BR.GT .redraw
LDI r14 4095
CMP r7 r14
BR.GE .redraw
CALL .append_text_key
JMP .redraw

.append_newline
LDI r13 0x0a
CALL .append_text_key
JMP .redraw

.text_backspace
CMP r7 r0
BR.EQ .redraw
DEC r7
JMP .redraw

.scroll_up
CMP r5 r0
BR.EQ .redraw
LDI r14 32
SUB r5 r5 r14
JMP .redraw

.scroll_down
MOV r14 r5
LDI r12 32
ADD r14 r14 r12
CMP r14 r7
BR.GE .redraw
ADD r5 r5 r12
JMP .redraw

.save_and_redraw
CALL .save_source
JMP .redraw

.open_actions
LDI r8 4
LDI r9 0
JMP .redraw

.action_event
LDI r14 EVT_B
CMP r12 r14
BR.EQ .back_to_edit
LDI r14 EVT_UP
CMP r12 r14
BR.EQ .action_up
LDI r14 EVT_DOWN
CMP r12 r14
BR.EQ .action_down
LDI r14 EVT_A
CMP r12 r14
BR.EQ .action_run
SYS SYS_APP_EVENT
JMP .redraw

.action_up
CMP r9 r0
BR.EQ .redraw
DEC r9
JMP .redraw

.action_down
LDI r14 6
CMP r9 r14
BR.GE .redraw
INC r9
JMP .redraw

.action_run
CMP r9 r0
BR.EQ .action_save
LDI r14 1
CMP r9 r14
BR.EQ .action_save_as
LDI r14 2
CMP r9 r14
BR.EQ .action_rename
LDI r14 3
CMP r9 r14
BR.EQ .action_delete
LDI r14 4
CMP r9 r14
BR.EQ .action_compile_run
LDI r14 5
CMP r9 r14
BR.EQ .new_text
JMP .new_program

.action_save
CALL .save_source
JMP .back_to_edit

.action_save_as
LDI r6 0
LDI r8 7
JMP .redraw

.action_rename
LDI r6 0
LDI r8 6
JMP .redraw

.action_delete
LDI r8 5
JMP .redraw

.action_compile_run
LDI r14 TYPE_ASM
CMP r11 r14
BR.NE .back_to_edit
CALL .save_source

LDI r1 EDIT_BUF
MOV r2 r7
LDI r3 RUN_BUF
LDI r4 0x4000
SYS SYS_ASSEMBLE

; r2 is assembler error code, r1 is emitted byte count.
CMP r2 r0
BR.NE .back_to_edit
CMP r1 r0
BR.EQ .back_to_edit

MOV r12 r1
CALL .save_binary_copy

LDI r1 RUN_BUF
MOV r2 r12
SYS SYS_RUN_BUFFER
HLT

.delete_event
LDI r14 EVT_A
CMP r12 r14
BR.EQ .delete_yes
LDI r14 EVT_B
CMP r12 r14
BR.EQ .back_to_edit
SYS SYS_APP_EVENT
JMP .redraw

.delete_yes
CMP r10 r0
BR.EQ .delete_done
MOV r1 r10
SYS SYS_FILE_DELETE
.delete_done
LDI r10 0
LDI r7 0
LDI r6 0
LDI r5 0
LDI r8 0
LDI r9 0
JMP .redraw

.back_to_edit
LDI r8 3
LDI r9 0
JMP .redraw

.ask_delete
LDI r8 5
JMP .redraw

.universal_event
SYS SYS_APP_EVENT
JMP .redraw

.append_name_key
LDI r14 FILE_NAME_PTR
ADD r14 r14 r6
STB r14 r13 0
INC r6
RET

.append_text_key
LDI r14 EDIT_BUF
ADD r14 r14 r7
STB r14 r13 0
INC r7
RET

.append_extension_and_zero
; Append extension based on current file type.
LDI r14 FILE_NAME_PTR
ADD r14 r14 r6
LDI r12 0x2e
STB r14 r12 0
INC r14
INC r6

LDI r12 TYPE_ASM
CMP r11 r12
BR.EQ .ext_asm

LDI r12 0x54
STB r14 r12 0
INC r14
LDI r12 0x58
STB r14 r12 0
INC r14
LDI r12 0x54
STB r14 r12 0
INC r14
JMP .ext_zero

.ext_asm
LDI r12 0x41
STB r14 r12 0
INC r14
LDI r12 0x53
STB r14 r12 0
INC r14
LDI r12 0x4d
STB r14 r12 0
INC r14

.ext_zero
STB r14 r0 0
LDI r12 4
ADD r6 r6 r12
RET

.save_source
CMP r10 r0
BR.EQ .save_source_done
MOV r1 r10
LDI r2 EDIT_BUF
MOV r3 r7
SYS SYS_FILE_WRITE
.save_source_done
RET

.save_binary_copy
; Input r12 = assembled byte count. Copy current name to BIN_NAME_PTR and
; replace the final extension with BIN.
LDI r1 0
.copy_bin_name
CMP r1 r6
BR.GE .bin_name_done
LDI r2 FILE_NAME_PTR
ADD r2 r2 r1
LDB r3 r2 0
LDI r4 BIN_NAME_PTR
ADD r4 r4 r1
STB r4 r3 0
INC r1
JMP .copy_bin_name

.bin_name_done
; Replace last three extension characters if name is long enough.
LDI r2 BIN_NAME_PTR
MOV r3 r6
LDI r4 3
SUB r3 r3 r4
ADD r2 r2 r3
LDI r4 0x42
STB r2 r4 0
INC r2
LDI r4 0x49
STB r2 r4 0
INC r2
LDI r4 0x4e
STB r2 r4 0
INC r2
STB r2 r0 0

LDI r1 BIN_NAME_PTR
SYS SYS_FILE_OPEN
CMP r1 r0
BR.NE .bin_handle_ready
LDI r1 BIN_NAME_PTR
LDI r2 TYPE_BIN
SYS SYS_FILE_CREATE
.bin_handle_ready
MOV r2 r1
; handle in r2, output length still in r12.
MOV r1 r2
LDI r2 RUN_BUF
MOV r3 r12
SYS SYS_FILE_WRITE
RET

.draw_editor_content
; Mode menu/action shells are drawn by the kernel. Name/edit modes additionally
; draw the typed filename or a 32-byte source window.
LDI r14 1
CMP r8 r14
BR.EQ .draw_name
LDI r14 2
CMP r8 r14
BR.EQ .draw_name
LDI r14 6
CMP r8 r14
BR.EQ .draw_name
LDI r14 7
CMP r8 r14
BR.EQ .draw_name
LDI r14 3
CMP r8 r14
BR.EQ .draw_source
LDI r14 4
CMP r8 r14
BR.EQ .draw_source
LDI r14 5
CMP r8 r14
BR.EQ .draw_source
RET

.draw_name
MOV r4 r6
LDI r3 FILE_NAME_PTR
JMP .draw_buffer

.draw_source
MOV r4 r7
SUB r4 r4 r5
LDI r14 32
CMP r4 r14
BR.LE .source_len_ok
MOV r4 r14
.source_len_ok
LDI r3 EDIT_BUF
ADD r3 r3 r5

.draw_buffer
CMP r4 r0
BR.LE .draw_return
LDI r12 GPU_BASE

; COPY_BUFFER
LDI r14 0x13
STW r12 r14 4
LDI r14 1
STW r12 r14 40

LDI r2 0
.draw_copy_loop
CMP r2 r4
BR.GE .draw_submit
MOV r14 r3
ADD r14 r14 r2
LDB r1 r14 0
LDI r13 GPU_TEXT_CONTENT
ADD r13 r13 r2
STB r13 r1 0
INC r2
JMP .draw_copy_loop

.draw_submit
LDI r14 0xa0
STW r12 r14 8
STW r12 r4 12
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
.draw_return
RET

.idle
SYS SYS_YIELD
JMP .loop
