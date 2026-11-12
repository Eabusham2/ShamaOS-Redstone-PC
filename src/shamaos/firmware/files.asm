; ShamaOS File Explorer
; r8 = selected file-table index
; r9 = mode: 0 directory list, 1 delete confirm, 2 text viewer, 3 rename,
; 4 error, 5 create folder. B goes to the parent, or Desktop at root.
; r10 = selected metadata low word: [15:8] name length, [7:0] type
; r11 = selected file size
; r7 = text-view offset / rename length

define EVT_KEY 0x01
define EVT_UP 0x10
define EVT_DOWN 0x11
define EVT_LEFT 0x12
define EVT_RIGHT 0x13
define EVT_A 0x14
define EVT_B 0x15

define KEY_ENTER 0x0d
define KEY_BACKSPACE 0x08

define TYPE_TXT 1
define TYPE_ASM 2
define TYPE_BIN 3

define FILE_NAME_PTR 0x0002c000
define RENAME_PTR 0x0002c080
define DIR_PATH 0x0002c140
define EDIT_CTX 0x0002c200
define EDIT_BUF 0x00018000
; BIN launch and full text viewing are mutually exclusive. Reuse source RAM.
define RUN_BUF 0x00018000
define EDIT_MAGIC 0x5348414d

define GPU_BASE 0x02000000
define GPU_TEXT_CONTENT 0x020004a0

MOV r8 r0
MOV r9 r0
MOV r7 r0
MOV r15 r0
LDI r14 EDIT_CTX
STW r14 r0 40
LDI r14 DIR_PATH
STB r14 r0 0

.redraw
CMP r9 r0
BR.NE .redraw_mode

; Skip empty table slots and entries outside the current directory.
LEA r4 r0 1
CALL .find_entry

MOV r1 r8
MOV r2 r9
LDI r14 EDIT_CTX
LDW r3 r14 36
LDW r4 r14 32
SYS SYS_UI_FILE_LIST
CALL .draw_selected_name
CALL .draw_metadata
CALL .draw_directory
SYS SYS_UI_STATUSBAR
JMP .loop

.redraw_mode
LEA r12 r0 2
CMP r9 r12
BR.EQ .redraw_text

MOV r1 r8
MOV r2 r9
LDI r14 EDIT_CTX
LDW r3 r14 36
LDW r4 r14 32
SYS SYS_UI_FILE_LIST

LEA r12 r0 3
CMP r9 r12
BR.EQ .redraw_rename
LEA r12 r0 5
CMP r9 r12
BR.EQ .redraw_rename

CALL .draw_selected_name
SYS SYS_UI_STATUSBAR
JMP .loop

.redraw_rename
CALL .draw_rename_name
SYS SYS_UI_STATUSBAR
JMP .loop

.redraw_text
MOV r1 r8
MOV r2 r9
LDI r14 EDIT_CTX
LDW r3 r14 36
LDW r4 r14 32
SYS SYS_UI_FILE_LIST
CALL .draw_selected_name
CALL .draw_text_page
SYS SYS_UI_STATUSBAR

.loop
SYS SYS_GET_EVENT
CMP r1 r0
BR.EQ .idle

; event code = (event >> 10) & 0xff
MOV r12 r1
LEA r13 r0 10
SHR r12 r12 r13
LEA r13 r0 0xff
AND r12 r12 r13

; key code = (event >> 18) & 0xff
MOV r14 r1
LEA r13 r0 18
SHR r14 r14 r13
LEA r13 r0 0xff
AND r14 r14 r13

; Mode-specific input.
LEA r13 r0 1
CMP r9 r13
BR.EQ .confirm_event
LEA r13 r0 2
CMP r9 r13
BR.EQ .viewer_event
LEA r13 r0 3
CMP r9 r13
BR.EQ .rename_event
LEA r13 r0 4
CMP r9 r13
BR.EQ .error_event
LEA r13 r0 5
CMP r9 r13
BR.EQ .rename_event

; List mode.
LEA r13 r0 EVT_KEY
CMP r12 r13
BR.EQ .list_key
LEA r13 r0 EVT_UP
CMP r12 r13
BR.EQ .prev
LEA r13 r0 EVT_DOWN
CMP r12 r13
BR.EQ .next
LEA r13 r0 EVT_LEFT
CMP r12 r13
BR.EQ .edit_selected
LEA r13 r0 EVT_RIGHT
CMP r12 r13
BR.EQ .ask_delete
LEA r13 r0 EVT_A
CMP r12 r13
BR.EQ .open_selected
LEA r13 r0 EVT_B
CMP r12 r13
BR.EQ .up_directory

; Hardware handles Home/Exit/Editor/Files even if this path is never reached.
SYS SYS_APP_EVENT
JMP .redraw

.list_key
; Keyboard N/R accept either case. New names are relative to this folder.
LEA r13 r0 97
CMP r14 r13
BR.LT .list_upper
LEA r13 r0 122
CMP r14 r13
BR.GT .list_upper
LEA r13 r0 32
SUB r14 r14 r13
.list_upper
LEA r13 r0 KEY_BACKSPACE
CMP r14 r13
BR.EQ .up_directory
LEA r13 r0 0x4e
CMP r14 r13
BR.EQ .new_folder
LEA r13 r0 0x52
CMP r14 r13
BR.NE .jump_initial
CMP r10 r0
BR.EQ .redraw
LEA r9 r0 3
CALL .start_name
JMP .redraw

.jump_initial
LEA r13 r0 65
CMP r14 r13
BR.LT .redraw
LEA r13 r0 90
CMP r14 r13
BR.GT .redraw
LDI r13 EDIT_CTX
STW r13 r14 44
CALL .close_selected
MOV r6 r8
MOV r8 r0
LEA r4 r0 1
.jump_scan
CALL .find_entry
CMP r10 r0
BR.EQ .jump_missing
LDI r13 EDIT_CTX
LDW r1 r13 40
LDI r13 FILE_NAME_PTR
ADD r13 r13 r1
LDB r1 r13 0
LEA r2 r0 97
CMP r1 r2
BR.LT .jump_compare
LEA r2 r0 122
CMP r1 r2
BR.GT .jump_compare
LEA r2 r0 32
SUB r1 r1 r2
.jump_compare
LDI r13 EDIT_CTX
LDW r2 r13 44
CMP r1 r2
BR.EQ .redraw
INC r8
JMP .jump_scan
.jump_missing
MOV r8 r6
JMP .redraw

.new_folder
LEA r9 r0 5
CALL .start_name
JMP .redraw

.prev
CALL .close_selected
MOV r6 r8
DEC r8
LEA r4 r0 -1
JMP .move_selection

.next
CALL .close_selected
MOV r6 r8
INC r8
LEA r4 r0 1
.move_selection
CALL .find_entry
CMP r10 r0
BR.NE .redraw
MOV r8 r6
JMP .redraw

.up_directory
LDI r13 EDIT_CTX
LDW r7 r13 40
CMP r7 r0
BR.EQ .desktop
CALL .close_selected
DEC r7
.parent_scan
CMP r7 r0
BR.EQ .parent_found
LDI r13 DIR_PATH
ADD r13 r13 r7
LDB r12 r13 -1
LEA r14 r0 47
CMP r12 r14
BR.EQ .parent_found
DEC r7
JMP .parent_scan
.parent_found
LDI r13 DIR_PATH
ADD r13 r13 r7
STB r13 r0 0
LDI r13 EDIT_CTX
STW r13 r7 40
MOV r8 r0
MOV r7 r0
JMP .redraw

.desktop
MOV r1 r0
SYS SYS_APP_LAUNCH
HLT

.ask_delete
CMP r10 r0
BR.EQ .redraw
LEA r9 r0 1
LDI r13 EDIT_CTX
STW r13 r0 36
JMP .redraw

.confirm_event
LEA r13 r0 EVT_LEFT
CMP r12 r13
BR.EQ .cancel_choice
LEA r13 r0 EVT_UP
CMP r12 r13
BR.EQ .cancel_choice
LEA r13 r0 EVT_RIGHT
CMP r12 r13
BR.EQ .delete_choice
LEA r13 r0 EVT_DOWN
CMP r12 r13
BR.EQ .delete_choice
LEA r13 r0 EVT_A
CMP r12 r13
BR.EQ .confirm_choice
LEA r13 r0 EVT_B
CMP r12 r13
BR.EQ .delete_no
SYS SYS_APP_EVENT
JMP .redraw

.cancel_choice
LDI r13 EDIT_CTX
STW r13 r0 36
JMP .redraw
.delete_choice
LDI r13 EDIT_CTX
LEA r14 r0 1
STW r13 r14 36
JMP .redraw
.confirm_choice
LDI r13 EDIT_CTX
LDW r13 r13 36
CMP r13 r0
BR.EQ .delete_no
.delete_yes
CALL .open_selected_handle
CMP r2 r0
BR.NE .error
MOV r1 r15
SYS SYS_FILE_DELETE
CMP r2 r0
BR.NE .error
MOV r15 r0
MOV r9 r0
MOV r8 r0
JMP .redraw

.delete_no
MOV r9 r0
JMP .redraw

.viewer_event
LEA r13 r0 EVT_B
CMP r12 r13
BR.EQ .viewer_close
LEA r13 r0 EVT_UP
CMP r12 r13
BR.EQ .viewer_up
LEA r13 r0 EVT_DOWN
CMP r12 r13
BR.EQ .viewer_down
LEA r13 r0 EVT_LEFT
CMP r12 r13
BR.EQ .viewer_edit
SYS SYS_APP_EVENT
JMP .redraw

.viewer_close
MOV r9 r0
MOV r7 r0
JMP .redraw

.viewer_up
CMP r7 r0
BR.EQ .redraw
MOV r13 r7
DEC r13
LDI r14 EDIT_BUF
ADD r14 r14 r13
LDB r1 r14 0
LEA r2 r0 10
CMP r1 r2
BR.NE .viewer_up_scan_start
CMP r13 r0
BR.EQ .viewer_up_done
DEC r13
LDI r14 EDIT_BUF
ADD r14 r14 r13
LDB r1 r14 0
CMP r1 r2
BR.NE .viewer_up_scan_start
INC r13
JMP .viewer_up_done
.viewer_up_scan_start
LEA r3 r0 1
.viewer_up_scan
CMP r13 r0
BR.EQ .viewer_up_done
LEA r14 r0 48
CMP r3 r14
BR.GE .viewer_up_done
LDI r14 EDIT_BUF
ADD r14 r14 r13
LDB r1 r14 -1
LEA r2 r0 10
CMP r1 r2
BR.EQ .viewer_up_done
DEC r13
INC r3
JMP .viewer_up_scan
.viewer_up_done
MOV r7 r13
JMP .redraw

.viewer_down
MOV r13 r7
MOV r3 r0
.viewer_down_scan
CMP r13 r11
BR.GE .redraw
LDI r14 EDIT_BUF
ADD r14 r14 r13
LDB r1 r14 0
INC r13
INC r3
LEA r2 r0 10
CMP r1 r2
BR.EQ .viewer_down_done
LEA r2 r0 48
CMP r3 r2
BR.LT .viewer_down_scan
.viewer_down_done
CMP r13 r11
BR.GE .redraw
MOV r7 r13
JMP .redraw

.viewer_edit
CALL .prepare_editor_context
CMP r2 r0
BR.NE .error
LEA r1 r0 1
SYS SYS_APP_LAUNCH
HLT

.rename_event
LEA r13 r0 EVT_B
CMP r12 r13
BR.EQ .rename_cancel
LEA r13 r0 EVT_KEY
CMP r12 r13
BR.NE .rename_universal

LEA r13 r0 KEY_ENTER
CMP r14 r13
BR.EQ .rename_commit
LEA r13 r0 KEY_BACKSPACE
CMP r14 r13
BR.EQ .rename_backspace

LEA r13 r0 0x20
CMP r14 r13
BR.LT .redraw
LEA r13 r0 0x7e
CMP r14 r13
BR.GT .redraw
LEA r13 r0 43
CMP r7 r13
BR.GE .redraw

LDI r13 RENAME_PTR
ADD r13 r13 r7
STB r13 r14 0
INC r7
JMP .redraw

.rename_backspace
LDI r13 EDIT_CTX
LDW r13 r13 40
CMP r7 r13
BR.LE .redraw
DEC r7
JMP .redraw

.rename_commit
LDI r13 EDIT_CTX
LDW r13 r13 40
CMP r7 r13
BR.LE .redraw
LDI r13 RENAME_PTR
ADD r13 r13 r7
STB r13 r0 0

LEA r13 r0 5
CMP r9 r13
BR.EQ .create_folder
CALL .open_selected_handle
CMP r2 r0
BR.NE .error
MOV r1 r15
LDI r2 RENAME_PTR
SYS SYS_FILE_RENAME
CMP r2 r0
BR.NE .error
CALL .close_selected
JMP .rename_success
.create_folder
LDI r1 RENAME_PTR
LEA r2 r0 7
SYS SYS_FILE_CREATE
CMP r2 r0
BR.NE .error
SYS SYS_FILE_CLOSE
.rename_success

MOV r7 r0
MOV r9 r0
MOV r8 r0
JMP .redraw

.rename_cancel
MOV r7 r0
MOV r9 r0
JMP .redraw

.rename_universal
SYS SYS_APP_EVENT
JMP .redraw

.open_selected
; type = metadata & 0xff
MOV r12 r10
LEA r13 r0 0xff
AND r12 r12 r13

LEA r13 r0 TYPE_TXT
CMP r12 r13
BR.EQ .open_text
LEA r13 r0 TYPE_ASM
CMP r12 r13
BR.EQ .open_editor
LEA r13 r0 TYPE_BIN
CMP r12 r13
BR.EQ .run_binary
LEA r13 r0 7
CMP r12 r13
BR.EQ .enter_directory
JMP .redraw

.enter_directory
CALL .close_selected
MOV r6 r10
LEA r13 r0 8
SHR r6 r6 r13
LEA r13 r0 255
AND r6 r6 r13
MOV r1 r0
.directory_copy
CMP r1 r6
BR.GE .directory_copied
LDI r13 FILE_NAME_PTR
ADD r13 r13 r1
LDB r2 r13 0
LDI r13 DIR_PATH
ADD r13 r13 r1
STB r13 r2 0
INC r1
JMP .directory_copy
.directory_copied
LDI r13 DIR_PATH
ADD r13 r13 r6
LEA r2 r0 47
STB r13 r2 0
STB r13 r0 1
INC r6
LDI r13 EDIT_CTX
STW r13 r6 40
MOV r8 r0
JMP .redraw

.open_text
CALL .check_edit_length
CMP r2 r0
BR.NE .error
CALL .open_selected_handle
CMP r2 r0
BR.NE .error
MOV r1 r15
LDI r2 EDIT_BUF
MOV r3 r11
SYS SYS_FILE_READ
CMP r2 r0
BR.NE .error
MOV r7 r0
LEA r9 r0 2
JMP .redraw

.open_editor
CALL .check_edit_length
CMP r2 r0
BR.NE .error
CALL .open_selected_handle
CMP r2 r0
BR.NE .error
MOV r1 r15
LDI r2 EDIT_BUF
MOV r3 r11
SYS SYS_FILE_READ
CMP r2 r0
BR.NE .error
CALL .prepare_editor_context
CMP r2 r0
BR.NE .error
LEA r1 r0 1
SYS SYS_APP_LAUNCH
HLT

.edit_selected
MOV r12 r10
LEA r13 r0 0xff
AND r12 r12 r13
LEA r13 r0 TYPE_TXT
CMP r12 r13
BR.EQ .edit_read
LEA r13 r0 TYPE_ASM
CMP r12 r13
BR.NE .redraw

.edit_read
CALL .check_edit_length
CMP r2 r0
BR.NE .error
CALL .open_selected_handle
CMP r2 r0
BR.NE .error
MOV r1 r15
LDI r2 EDIT_BUF
MOV r3 r11
SYS SYS_FILE_READ
CMP r2 r0
BR.NE .error
CALL .prepare_editor_context
CMP r2 r0
BR.NE .error
LEA r1 r0 1
SYS SYS_APP_LAUNCH
HLT

.run_binary
; A user binary must fit the executable cache.
LDI r13 0x3ffc
CMP r11 r13
BR.GT .too_large
CALL .open_selected_handle
CMP r2 r0
BR.NE .error
MOV r1 r15
LDI r2 RUN_BUF
MOV r3 r11
SYS SYS_FILE_READ
CMP r2 r0
BR.NE .error

LDI r1 RUN_BUF
MOV r2 r11
SYS SYS_RUN_BUFFER
HLT

.check_edit_length
MOV r2 r0
LDI r13 65535
CMP r11 r13
BR.LE .check_edit_done
LEA r2 r0 9
.check_edit_done
RET
.too_large
LEA r2 r0 9
.error
LDI r13 EDIT_CTX
STW r13 r2 32
LEA r9 r0 4
JMP .redraw
.error_event
LEA r13 r0 EVT_A
CMP r12 r13
BR.EQ .delete_no
LEA r13 r0 EVT_B
CMP r12 r13
BR.EQ .delete_no
SYS SYS_APP_EVENT
JMP .redraw
.open_selected_handle
MOV r2 r0
CMP r15 r0
BR.GT .handle_done
LDI r1 FILE_NAME_PTR
SYS SYS_FILE_OPEN
CMP r2 r0
BR.NE .handle_done
MOV r15 r1
.handle_done
RET
.close_selected
CMP r15 r0
BR.LE .close_done
MOV r1 r15
SYS SYS_FILE_CLOSE
MOV r15 r0
.close_done
RET

.prepare_editor_context
; Context survives File Explorer app-slot flush because it is main RAM.
LDI r12 EDIT_CTX
STW r12 r15 0

MOV r13 r10
LEA r14 r0 0xff
AND r13 r13 r14
STW r12 r13 4

STW r12 r11 8
LDI r13 EDIT_MAGIC
STW r12 r13 12

MOV r13 r10
LEA r14 r0 8
SHR r13 r13 r14
LEA r14 r0 0xff
AND r13 r13 r14
STW r12 r13 16
STW r12 r0 20
STW r12 r0 24
STW r12 r0 28
STW r12 r0 32
STW r12 r0 36
MOV r1 r15
LEA r2 r0 1
SYS SYS_FILE_TRANSFER
RET

.draw_selected_name
MOV r6 r10
LEA r13 r0 8
SHR r6 r6 r13
LEA r13 r0 0xff
AND r6 r6 r13
CMP r6 r0
BR.EQ .draw_selected_done

LDI r12 GPU_BASE
LEA r4 r0 0x13
STW r12 r4 4
LEA r4 r0 1
STW r12 r4 40

MOV r5 r0
.draw_selected_copy
CMP r5 r6
BR.GE .draw_selected_submit
LDI r13 FILE_NAME_PTR
ADD r13 r13 r5
LDB r4 r13 0
LDI r14 GPU_TEXT_CONTENT
ADD r14 r14 r5
STB r14 r4 0
INC r5
JMP .draw_selected_copy

.draw_selected_submit
LEA r4 r0 0xa0
STW r12 r4 8
STW r12 r6 12
LEA r4 r0 4
STW r12 r4 16
LEA r4 r0 48
STW r12 r4 20
LEA r4 r0 0x0c
STW r12 r4 4
LEA r4 r0 1
STW r12 r4 40
LEA r4 r0 0x14
STW r12 r4 4
LEA r4 r0 1
STW r12 r4 40
SYS SYS_GPU_FENCE

.draw_selected_done
RET

.draw_rename_name
CMP r7 r0
BR.EQ .draw_rename_done

LDI r12 GPU_BASE
LEA r4 r0 0x13
STW r12 r4 4
LEA r4 r0 1
STW r12 r4 40

MOV r5 r0
.draw_rename_copy
CMP r5 r7
BR.GE .draw_rename_submit
LDI r13 RENAME_PTR
ADD r13 r13 r5
LDB r4 r13 0
LDI r14 GPU_TEXT_CONTENT
ADD r14 r14 r5
STB r14 r4 0
INC r5
JMP .draw_rename_copy

.draw_rename_submit
LEA r4 r0 0xa0
STW r12 r4 8
STW r12 r7 12
LEA r4 r0 4
STW r12 r4 16
LEA r4 r0 48
STW r12 r4 20
LEA r4 r0 0x0c
STW r12 r4 4
LEA r4 r0 1
STW r12 r4 40
LEA r4 r0 0x14
STW r12 r4 4
LEA r4 r0 1
STW r12 r4 40
SYS SYS_GPU_FENCE

.draw_rename_done
RET

.draw_text_page
LDI r12 GPU_BASE
LEA r4 r0 0x13
STW r12 r4 4
LEA r4 r0 1
STW r12 r4 40
MOV r5 r7
LEA r6 r0 60
.draw_text_line
MOV r3 r0
.draw_text_copy
CMP r5 r11
BR.GE .draw_text_submit
LEA r14 r0 48
CMP r3 r14
BR.GE .draw_text_submit
LDI r13 EDIT_BUF
ADD r13 r13 r5
LDB r4 r13 0
INC r5
LEA r14 r0 10
CMP r4 r14
BR.EQ .draw_text_submit
LEA r14 r0 13
CMP r4 r14
BR.EQ .draw_text_copy
LEA r14 r0 9
CMP r4 r14
BR.NE .draw_text_char
LEA r4 r0 32
.draw_text_char
LDI r14 GPU_TEXT_CONTENT
ADD r14 r14 r3
STB r14 r4 0
INC r3
JMP .draw_text_copy
.draw_text_submit
CMP r3 r0
BR.EQ .draw_text_next
LDI r12 GPU_BASE
LEA r4 r0 0xa0
STW r12 r4 8
STW r12 r3 12
LEA r4 r0 4
STW r12 r4 16
STW r12 r6 20
LEA r4 r0 0x0c
STW r12 r4 4
LEA r4 r0 1
STW r12 r4 40
SYS SYS_GPU_FENCE
.draw_text_next
CMP r5 r11
BR.GE .draw_text_done
LEA r14 r0 10
ADD r6 r6 r14
LEA r14 r0 140
CMP r6 r14
BR.LT .draw_text_line
.draw_text_done
LDI r12 GPU_BASE
LEA r4 r0 0x14
STW r12 r4 4
LEA r4 r0 1
STW r12 r4 40
SYS SYS_GPU_FENCE
RET

.idle
SYS SYS_YIELD
JMP .loop

.read_entry
MOV r1 r8
LDI r2 FILE_NAME_PTR
SYS SYS_FILE_LIST
MOV r10 r1
MOV r11 r2
MOV r12 r10
LEA r13 r0 8
SHR r12 r12 r13
LEA r13 r0 255
AND r12 r12 r13
LDI r13 FILE_NAME_PTR
ADD r13 r13 r12
STB r13 r0 0
RET

.find_entry
; Scan in direction r4 (+1/-1); preserve r6 for selection boundary recovery.
CMP r8 r0
BR.LT .find_none
LEA r13 r0 64
CMP r8 r13
BR.GE .find_none
CALL .read_entry
CMP r10 r0
BR.EQ .find_next
CALL .matches_directory
CMP r1 r0
BR.NE .find_done
.find_next
ADD r8 r8 r4
JMP .find_entry
.find_none
MOV r10 r0
MOV r11 r0
.find_done
RET

.matches_directory
; Current prefix includes its trailing slash; the remaining name must have no
; slash. Root therefore shows only direct children, not nested descendants.
MOV r1 r0
LDI r13 EDIT_CTX
LDW r5 r13 40
CMP r12 r5
BR.LE .match_done
MOV r3 r0
.match_prefix
CMP r3 r5
BR.GE .match_child
LDI r13 DIR_PATH
ADD r13 r13 r3
LDB r2 r13 0
LDI r13 FILE_NAME_PTR
ADD r13 r13 r3
LDB r14 r13 0
CMP r2 r14
BR.NE .match_done
INC r3
JMP .match_prefix
.match_child
CMP r3 r12
BR.GE .match_yes
LDI r13 FILE_NAME_PTR
ADD r13 r13 r3
LDB r2 r13 0
LEA r14 r0 47
CMP r2 r14
BR.EQ .match_done
INC r3
JMP .match_child
.match_yes
LEA r1 r0 1
.match_done
RET

.start_name
LDI r13 EDIT_CTX
LDW r7 r13 40
MOV r1 r0
.start_name_copy
CMP r1 r7
BR.GE .start_name_done
LDI r13 DIR_PATH
ADD r13 r13 r1
LDB r2 r13 0
LDI r13 RENAME_PTR
ADD r13 r13 r1
STB r13 r2 0
INC r1
JMP .start_name_copy
.start_name_done
LDI r13 RENAME_PTR
ADD r13 r13 r7
STB r13 r0 0
RET

.draw_directory
LDI r12 GPU_BASE
LEA r1 r0 0x13
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
LDI r12 GPU_TEXT_CONTENT
LDI r1 0x20524944
STW r12 r1 0
LEA r1 r0 47
STB r12 r1 4
LDI r13 EDIT_CTX
LDW r6 r13 40
MOV r5 r0
.draw_directory_copy
CMP r5 r6
BR.GE .draw_directory_submit
LDI r13 DIR_PATH
ADD r13 r13 r5
LDB r1 r13 0
ADD r14 r12 r5
STB r14 r1 5
INC r5
JMP .draw_directory_copy
.draw_directory_submit
LEA r1 r0 5
ADD r6 r6 r1
LDI r12 GPU_BASE
LEA r1 r0 0xa0
STW r12 r1 8
STW r12 r6 12
LEA r1 r0 4
STW r12 r1 16
LEA r1 r0 84
STW r12 r1 20
LEA r1 r0 0x0c
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
LEA r1 r0 0x14
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
SYS SYS_GPU_FENCE
CMP r10 r0
BR.NE .directory_hints
LDI r12 GPU_TEXT_CONTENT
LDI r1 0x54504d45
STW r12 r1 0
LDI r1 0x4f462059
STW r12 r1 4
LDI r1 0x5245444c
STW r12 r1 8
LEA r4 r0 12
LEA r5 r0 48
CALL .draw_static
.directory_hints
LDI r12 GPU_TEXT_CONTENT
LDI r1 0x442f5055
STW r12 r1 0
LDI r1 0x204e574f
STW r12 r1 4
LDI r1 0x454c4553
STW r12 r1 8
LDI r1 0x20205443
STW r12 r1 12
LDI r1 0x41502042
STW r12 r1 16
LDI r1 0x544e4552
STW r12 r1 20
LDI r1 0x4958452f
STW r12 r1 24
LDI r1 0x20202054
STW r12 r1 28
LEA r4 r0 29
LEA r5 r0 96
CALL .draw_static
LDI r12 GPU_TEXT_CONTENT
LDI r1 0x4259454b
STW r12 r1 0
LDI r1 0x4452414f
STW r12 r1 4
LDI r1 0x46204e20
STW r12 r1 8
LDI r1 0x45444c4f
STW r12 r1 12
LDI r1 0x52202052
STW r12 r1 16
LDI r1 0x4e455220
STW r12 r1 20
LDI r1 0x20454d41
STW r12 r1 24
LEA r4 r0 27
LEA r5 r0 108
CALL .draw_static
RET

.draw_static
; Text is already staged; r4 length, r5 y. Every reuse is fenced.
LDI r12 GPU_BASE
LEA r1 r0 0x13
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
LEA r1 r0 0xa0
STW r12 r1 8
STW r12 r4 12
LEA r1 r0 4
STW r12 r1 16
STW r12 r5 20
LEA r1 r0 0x0c
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
LEA r1 r0 0x14
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
SYS SYS_GPU_FENCE
RET

.draw_metadata
PUSH r1
PUSH r2
PUSH r3
PUSH r4
PUSH r5
PUSH r6
PUSH r12
PUSH r13
PUSH r14
CMP r10 r0
BR.EQ .metadata_done
LDI r12 GPU_BASE
LEA r1 r0 0x13
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
MOV r1 r10
LEA r2 r0 255
AND r1 r1 r2
LDI r3 0x41544144
LEA r2 r0 1
CMP r1 r2
BR.NE .meta_type_1
LDI r3 0x20545854
.meta_type_1
LEA r2 r0 2
CMP r1 r2
BR.NE .meta_type_2
LDI r3 0x204d5341
.meta_type_2
LEA r2 r0 3
CMP r1 r2
BR.NE .meta_type_3
LDI r3 0x204e4942
.meta_type_3
LEA r2 r0 4
CMP r1 r2
BR.NE .meta_type_4
LDI r3 0x20535953
.meta_type_4
LEA r2 r0 5
CMP r1 r2
BR.NE .meta_type_5
LDI r3 0x20474f4c
.meta_type_5
LEA r2 r0 6
CMP r1 r2
BR.NE .meta_type_6
LDI r3 0x20474643
.meta_type_6
LEA r2 r0 7
CMP r1 r2
BR.NE .meta_type_7
LDI r3 0x20524944
.meta_type_7
LDI r12 GPU_TEXT_CONTENT
STW r12 r3 0
LDI r3 0x455a4953
STW r12 r3 4
LEA r3 r0 32
STB r12 r3 8
LEA r4 r0 28
LEA r5 r0 9
.meta_size_loop
SHR r1 r11 r4
LEA r2 r0 15
AND r1 r1 r2
LEA r2 r0 10
CMP r1 r2
BR.LT .meta_digit
LEA r2 r0 55
JMP .meta_put
.meta_digit
LEA r2 r0 48
.meta_put
ADD r1 r1 r2
ADD r13 r12 r5
STB r13 r1 0
INC r5
LEA r2 r0 4
SUB r4 r4 r2
CMP r4 r0
BR.GE .meta_size_loop
LDI r3 0x58454820
STW r12 r3 20
LEA r3 r0 32
STB r12 r3 17
STB r12 r3 18
STB r12 r3 19
LDI r12 GPU_BASE
LEA r1 r0 0xa0
STW r12 r1 8
LEA r1 r0 24
STW r12 r1 12
LEA r1 r0 4
STW r12 r1 16
LEA r1 r0 60
STW r12 r1 20
LEA r1 r0 0x0c
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
LEA r1 r0 0x14
STW r12 r1 4
LEA r1 r0 1
STW r12 r1 40
SYS SYS_GPU_FENCE
.metadata_done
POP r14
POP r13
POP r12
POP r6
POP r5
POP r4
POP r3
POP r2
POP r1
RET
