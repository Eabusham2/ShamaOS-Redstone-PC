; ShamaOS File Explorer
; r8 = selected file-table index
; r9 = mode: 0 list, 1 delete confirm, 2 text viewer, 3 rename
; r10 = selected metadata low word: [15:8] name length, [7:0] type
; r11 = selected file size
; r7 = text viewer byte offset

define EVT_UP 0x10
define EVT_DOWN 0x11
define EVT_LEFT 0x12
define EVT_RIGHT 0x13
define EVT_A 0x14
define EVT_B 0x15
define EVT_KEY 0x01
define KEY_ENTER 0x0d
define KEY_BACKSPACE 0x08

define TYPE_TXT 1
define TYPE_ASM 2
define TYPE_BIN 3

define FILE_NAME_PTR 0x000b0000
define RENAME_PTR 0x000b0080
define EDIT_CTX 0x000b1000
define EDIT_BUF 0x000c0000
define RUN_BUF 0x000e0000
define EDIT_MAGIC 0x5348414d

define GPU_BASE 0x02000000
define GPU_TEXT_CONTENT 0x020004a0

LDI r8 0
LDI r9 0
LDI r7 0

.redraw
CMP r9 r0
BR.NE .redraw_mode

MOV r1 r8
LDI r2 FILE_NAME_PTR
SYS SYS_FILE_LIST
MOV r10 r1
MOV r11 r2
MOV r1 r8
MOV r2 r9
SYS SYS_UI_FILE_LIST
CALL .draw_selected_name
SYS SYS_UI_STATUSBAR
JMP .loop

.redraw_mode
LDI r12 2
CMP r9 r12
BR.EQ .redraw_text

; Delete/rename modes still use the File Explorer shell; r2 tells the
; UI/kernel which state is active.
MOV r1 r8
MOV r2 r9
SYS SYS_UI_FILE_LIST
LDI r12 3
CMP r9 r12
BR.NE .redraw_mode_selected
CALL .draw_rename_name
JMP .redraw_mode_status
.redraw_mode_selected
CALL .draw_selected_name
.redraw_mode_status
SYS SYS_UI_STATUSBAR
JMP .loop

.redraw_text
MOV r1 r8
MOV r2 r9
SYS SYS_UI_FILE_LIST
CALL .draw_rename_name
CMP r7 r0
BR.EQ .draw_done
LDI r12 GPU_BASE
LDI r4 0x13
STW r12 r4 4
LDI r4 1
STW r12 r4 40

LDI r5 0
.rename_draw_copy
CMP r5 r7
BR.GE .rename_draw_submit
LDI r13 RENAME_PTR
ADD r13 r13 r5
LDB r4 r13 0
LDI r14 GPU_TEXT_CONTENT
ADD r14 r14 r5
STB r14 r4 0
INC r5
JMP .rename_draw_copy

.rename_draw_submit
LDI r4 0xa0
STW r12 r4 8
STW r12 r7 12
LDI r4 4
STW r12 r4 16
LDI r4 48
STW r12 r4 20
LDI r4 0x0c
STW r12 r4 4
LDI r4 1
STW r12 r4 40
LDI r4 0x14
STW r12 r4 4
LDI r4 1
STW r12 r4 40
RET

.draw_text_page
SYS SYS_UI_STATUSBAR

.loop
SYS SYS_GET_EVENT
CMP r1 r0
BR.EQ .idle

; event = (r1 >> 10) & 0xff
MOV r12 r1
LDI r13 10
SHR r12 r12 r13
LDI r13 0xff
AND r12 r12 r13

; key code = (event >> 18) & ff
MOV r14 r1
LDI r13 18
SHR r14 r14 r13
LDI r13 0xff
AND r14 r14 r13

; Mode-specific handling first.
LDI r13 1
CMP r9 r13
BR.EQ .confirm_event
LDI r13 2
CMP r9 r13
BR.EQ .viewer_event
LDI r13 3
CMP r9 r13
BR.EQ .rename_event

LDI r13 EVT_KEY
CMP r12 r13
BR.EQ .list_key
LDI r13 EVT_UP
CMP r12 r13
BR.EQ .list_key
; R starts File Explorer rename mode.
LDI r13 0x52
CMP r14 r13
BR.NE .redraw
LDI r7 0
LDI r9 3
JMP .redraw

.prev
LDI r13 EVT_DOWN
CMP r12 r13
BR.EQ .next
LDI r13 EVT_LEFT
CMP r12 r13
BR.EQ .edit_selected
LDI r13 EVT_RIGHT
CMP r12 r13
BR.EQ .ask_delete
LDI r13 EVT_A
CMP r12 r13
BR.EQ .open_selected
LDI r13 EVT_B
CMP r12 r13
BR.EQ .desktop

; Universal Home/Exit/Editor/Files.
SYS SYS_APP_EVENT
JMP .redraw

.prev
CMP r8 r0
BR.EQ .redraw
DEC r8
JMP .redraw

.next
LDI r13 63
CMP r8 r13
BR.GE .redraw
INC r8
JMP .redraw

.desktop
LDI r1 0
SYS SYS_APP_LAUNCH
HLT

.ask_delete
CMP r10 r0
BR.EQ .redraw
LDI r9 1
JMP .redraw

.confirm_event
LDI r13 EVT_A
CMP r12 r13
BR.EQ .delete_yes
LDI r13 EVT_B
CMP r12 r13
BR.EQ .delete_no
SYS SYS_APP_EVENT
JMP .redraw

.delete_yes
MOV r1 r8
INC r1
SYS SYS_FILE_DELETE
LDI r9 0
JMP .redraw

.delete_no
LDI r9 0
JMP .redraw

.viewer_event
LDI r13 EVT_B
CMP r12 r13
BR.EQ .rename_event
LDI r13 EVT_B
CMP r12 r13
BR.EQ .rename_cancel
LDI r13 EVT_KEY
CMP r12 r13
BR.NE .universal_rename

LDI r13 KEY_ENTER
CMP r14 r13
BR.EQ .rename_commit
LDI r13 KEY_BACKSPACE
CMP r14 r13
BR.EQ .rename_backspace

LDI r13 0x20
CMP r14 r13
BR.LT .redraw
LDI r13 0x7e
CMP r14 r13
BR.GT .redraw
LDI r13 42
CMP r7 r13
BR.GE .redraw
LDI r13 RENAME_PTR
ADD r13 r13 r7
STB r13 r14 0
INC r7
JMP .redraw

.rename_backspace
CMP r7 r0
BR.EQ .redraw
DEC r7
JMP .redraw

.rename_commit
CMP r7 r0
BR.EQ .redraw
LDI r13 RENAME_PTR
ADD r13 r13 r7
STB r13 r0 0
MOV r1 r8
INC r1
LDI r2 RENAME_PTR
SYS SYS_FILE_RENAME
LDI r7 0
LDI r9 0
JMP .redraw

.rename_cancel
LDI r7 0
LDI r9 0
JMP .redraw

.universal_rename
SYS SYS_APP_EVENT
JMP .redraw

.viewer_close
LDI r13 EVT_UP
CMP r12 r13
BR.EQ .viewer_up
LDI r13 EVT_DOWN
CMP r12 r13
BR.EQ .viewer_down
LDI r13 EVT_LEFT
CMP r12 r13
BR.EQ .viewer_edit
SYS SYS_APP_EVENT
JMP .redraw

.viewer_close
LDI r9 0
LDI r7 0
JMP .redraw

.viewer_up
CMP r7 r0
BR.EQ .redraw
LDI r13 32
SUB r7 r7 r13
JMP .redraw

.viewer_down
MOV r13 r7
LDI r14 32
ADD r13 r13 r14
CMP r13 r11
BR.GE .redraw
ADD r7 r7 r14
JMP .redraw

.viewer_edit
CALL .cap_edit_length
LDI r13 4095
CMP r11 r13
BR.LE .cap_edit_done
MOV r11 r13
.cap_edit_done
RET

.prepare_editor_context
LDI r1 1
SYS SYS_APP_LAUNCH
HLT

.open_selected
; type = metadata & 0xff
MOV r12 r10
LDI r13 0xff
AND r12 r12 r13

LDI r13 TYPE_TXT
CMP r12 r13
BR.EQ .open_text
LDI r13 TYPE_ASM
CMP r12 r13
BR.EQ .open_editor
LDI r13 TYPE_BIN
CMP r12 r13
BR.EQ .run_binary
JMP .redraw

.open_text
CALL .cap_edit_length
MOV r1 r8
INC r1
LDI r2 EDIT_BUF
MOV r3 r11
SYS SYS_FILE_READ
LDI r7 0
LDI r9 2
JMP .redraw

.open_editor
CALL .cap_edit_length
MOV r1 r8
INC r1
LDI r2 EDIT_BUF
MOV r3 r11
SYS SYS_FILE_READ
CALL .prepare_editor_context
LDI r1 1
SYS SYS_APP_LAUNCH
HLT

.edit_selected
MOV r12 r10
LDI r13 0xff
AND r12 r12 r13
LDI r13 TYPE_TXT
CMP r12 r13
BR.EQ .edit_read
LDI r13 TYPE_ASM
CMP r12 r13
BR.NE .redraw
.edit_read
CALL .cap_edit_length
MOV r1 r8
INC r1
LDI r2 EDIT_BUF
MOV r3 r11
SYS SYS_FILE_READ
CALL .prepare_editor_context
LDI r1 1
SYS SYS_APP_LAUNCH
HLT

.run_binary
MOV r1 r8
INC r1
LDI r2 RUN_BUF
MOV r3 r11
SYS SYS_FILE_READ
LDI r1 RUN_BUF
MOV r2 r11
SYS SYS_RUN_BUFFER
HLT

.prepare_editor_context
; Context survives the File Explorer app-slot flush because it is in main RAM.
LDI r12 EDIT_CTX
MOV r13 r8
INC r13
STW r12 r13 0

MOV r13 r10
LDI r14 0xff
AND r13 r13 r14
STW r12 r13 4

STW r12 r11 8
LDI r13 EDIT_MAGIC
STW r12 r13 12

; name length
MOV r13 r10
LDI r14 8
SHR r13 r13 r14
LDI r14 0xff
AND r13 r13 r14
STW r12 r13 16
RET

.draw_selected_name
; name_len = (metadata >> 8) & 0xff
MOV r6 r10
LDI r13 8
SHR r6 r6 r13
LDI r13 0xff
AND r6 r6 r13
CMP r6 r0
BR.EQ .draw_done

LDI r12 GPU_BASE

; Preserve clean shell: queue COPY_BUFFER.
LDI r4 0x13
STW r12 r4 4
LDI r4 1
STW r12 r4 40

LDI r5 0
.copy_name
CMP r5 r6
BR.GE .submit_name
LDI r13 FILE_NAME_PTR
ADD r13 r13 r5
LDB r4 r13 0
LDI r14 GPU_TEXT_CONTENT
ADD r14 r14 r5
STB r14 r4 0
INC r5
JMP .copy_name

.submit_name
LDI r4 0xa0
STW r12 r4 8
STW r12 r6 12
LDI r4 4
STW r12 r4 16
LDI r4 48
STW r12 r4 20
LDI r4 0x0c
STW r12 r4 4
LDI r4 1
STW r12 r4 40

; Swap the updated back buffer.
LDI r4 0x14
STW r12 r4 4
LDI r4 1
STW r12 r4 40
.draw_done
RET

.draw_text_page
; Draw one 32-byte page from the current text document.
MOV r6 r11
SUB r6 r6 r7
LDI r13 32
CMP r6 r13
BR.LE .text_len_ok
MOV r6 r13
.text_len_ok
CMP r6 r0
BR.LE .text_done

LDI r12 GPU_BASE
LDI r4 0x13
STW r12 r4 4
LDI r4 1
STW r12 r4 40

LDI r5 0
.copy_text
CMP r5 r6
BR.GE .submit_text
LDI r13 EDIT_BUF
ADD r13 r13 r7
ADD r13 r13 r5
LDB r4 r13 0
LDI r14 GPU_TEXT_CONTENT
ADD r14 r14 r5
STB r14 r4 0
INC r5
JMP .copy_text

.submit_text
LDI r4 0xa0
STW r12 r4 8
STW r12 r6 12
LDI r4 4
STW r12 r4 16
LDI r4 48
STW r12 r4 20
LDI r4 0x0c
STW r12 r4 4
LDI r4 1
STW r12 r4 40
LDI r4 0x14
STW r12 r4 4
LDI r4 1
STW r12 r4 40
.text_done
RET

.idle
SYS SYS_YIELD
JMP .loop
