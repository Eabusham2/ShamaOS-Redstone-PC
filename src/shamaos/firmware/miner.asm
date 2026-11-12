; ShamaOS Bitcoin Miner: real software/native double SHA-256.
; A Start/resume, B Stop/save, UP new generation, DOWN live,
; LEFT/RIGHT saved generation. Keyboard M changes mode, C compares both,
; D cycles difficulty, T edits full target64hex, H edits header160hex.
; Enter commits exact-length hexadecimal input; Escape cancels unchanged.
; Immutable record ABI is documented in docs/MINER.md.
define EVT_UP 0x10
define EVT_DOWN 0x11
define EVT_LEFT 0x12
define EVT_RIGHT 0x13
define EVT_A 0x14
define EVT_B 0x15
define HEADER 0x0002d000
define HASHBUF 0x0002d100
define TARGET 0x0002d140
define COMPARE_HASH 0x0002d180
define RECORD 0x0002d200
define HISTORY 0x0002d400
define CONFIG_BUF 0x0002d600
define GPU_BASE 0x02000000
define GPU_TEXT 0x02000400

CALL .soft_constants
LDI r1 RECORD
LEA r2 r0 256
SYS SYS_MINER_LOAD_STATE
LDI r15 RECORD
LDW r1 r15 0
LDI r2 0x31524d53
CMP r1 r2
BR.NE .initialize
LDW r1 r15 4
LEA r2 r0 1
CMP r1 r2
BR.NE .initialize
LDW r1 r15 28
CMP r1 r2
BR.GT .initialize
; Reload the saved header/target/hash, always paused after app reopening.
LDI r1 RECORD
ADDI r1 128
LDI r2 HEADER
LEA r3 r0 80
CALL .copy_bytes
LDI r1 RECORD
ADDI r1 80
LDI r2 TARGET
LEA r3 r0 32
CALL .copy_bytes
LDI r1 RECORD
ADDI r1 48
LDI r2 HASHBUF
LEA r3 r0 32
CALL .copy_bytes
STW r15 r0 24
LDW r1 r15 244
LEA r2 r0 2
CMP r1 r2
BR.NE .restore_ready
LEA r1 r0 3
STW r15 r1 24
.restore_ready
STW r15 r0 208
STW r15 r0 220
STW r15 r0 252
JMP .redraw
.initialize
LDI r1 HEADER
LEA r2 r0 1024
.init_clear
STB r1 r0 0
INC r1
DEC r2
BR.NE .init_clear
LDI r15 RECORD
LDI r1 0x31524d53
STW r15 r1 0
LEA r1 r0 1
STW r15 r1 4
LEA r1 r0 8
STW r15 r1 40
CALL .make_target
CALL .snapshot
CALL .save_state

.redraw
CALL .draw
.loop
SYS SYS_GET_EVENT
CMP r1 r0
BR.NE .event
LDI r15 RECORD
LDW r1 r15 24
LEA r2 r0 1
CMP r1 r2
BR.NE .idle
CALL .hash_once
CALL .draw
JMP .loop
.idle
SYS SYS_YIELD
JMP .loop

.event
MOV r12 r1
LEA r13 r0 10
SHR r12 r12 r13
ANDI r12 255
LEA r13 r0 1
CMP r12 r13
BR.EQ .keyboard
LEA r13 r0 EVT_A
CMP r12 r13
BR.EQ .start
LEA r13 r0 EVT_B
CMP r12 r13
BR.EQ .stop
LEA r13 r0 EVT_UP
CMP r12 r13
BR.EQ .new_generation
LEA r13 r0 EVT_DOWN
CMP r12 r13
BR.EQ .live
LEA r13 r0 EVT_LEFT
CMP r12 r13
BR.EQ .previous
LEA r13 r0 EVT_RIGHT
CMP r12 r13
BR.EQ .next
SYS SYS_APP_EVENT
JMP .redraw

.start
LDI r15 RECORD
LDW r1 r15 208
CMP r1 r0
BR.NE .redraw
LDW r1 r15 244
LEA r2 r0 2
CMP r1 r2
BR.EQ .redraw
CALL .begin_update
LDW r1 r15 244
CMP r1 r0
BR.EQ .start_ready
LDI r1 HEADER
LDW r2 r1 76
LDI r3 0xffffffff
CMP r2 r3
BR.EQ .start_exhausted
INCNONCE r1
STW r15 r0 244
.start_ready
LEA r1 r0 1
STW r15 r1 24
STW r15 r0 220
CALL .snapshot
CALL .save_state
JMP .redraw
.start_exhausted
LEA r1 r0 3
STW r15 r1 24
LEA r1 r0 2
STW r15 r1 244
CALL .snapshot
CALL .save_state
JMP .redraw
.stop
LDI r15 RECORD
CALL .begin_update
STW r15 r0 24
CALL .snapshot
CALL .log_record
CALL .save_state
JMP .redraw
.new_generation
LDI r15 RECORD
CALL .log_record
CALL .begin_update
LDW r1 r15 8
INC r1
STW r15 r1 8
STW r15 r0 12
STW r15 r0 16
STW r15 r0 20
STW r15 r0 24
STW r15 r0 32
STW r15 r0 36
STW r15 r0 120
STW r15 r0 124
STW r15 r0 220
STW r15 r0 244
LDI r1 HEADER
STW r1 r0 76
LDI r1 HASHBUF
LEA r2 r0 8
.new_clear_hash
STW r1 r0 0
ADDI r1 4
DEC r2
BR.NE .new_clear_hash
CALL .snapshot
CALL .save_state
JMP .redraw
.live
LDI r15 RECORD
STW r15 r0 220
JMP .redraw
.previous
LDI r15 RECORD
LDW r1 r15 216
DEC r1
ANDI r1 255
JMP .history_load
.next
LDI r15 RECORD
LDW r1 r15 216
INC r1
ANDI r1 255
.history_load
STW r15 r1 216
STW r15 r0 24
LDI r2 HISTORY
LEA r3 r0 128
SYS SYS_MINER_LOAD_HISTORY
LDI r2 HISTORY
LDW r3 r2 0
LDI r4 0x31524d53
CMP r3 r4
BR.NE .history_empty
LDW r3 r2 4
LEA r4 r0 1
CMP r3 r4
BR.NE .history_empty
LEA r1 r0 1
JMP .history_selected
.history_empty
LEA r1 r0 2
.history_selected
STW r15 r1 220
CALL .snapshot
CALL .save_state
JMP .redraw

.keyboard
LEA r12 r0 18
SHR r12 r1 r12
ANDI r12 255
LDW r1 r15 208
CMP r1 r0
BR.NE .config_key
; Commands are case insensitive; hexadecimal input retains its own parser.
ANDI r12 0xdf
LEA r13 r0 'M'
CMP r12 r13
BR.EQ .toggle_mode
LEA r13 r0 'C'
CMP r12 r13
BR.EQ .benchmark
LEA r13 r0 'D'
CMP r12 r13
BR.EQ .difficulty
LEA r13 r0 'T'
CMP r12 r13
BR.EQ .edit_target
LEA r13 r0 'H'
CMP r12 r13
BR.EQ .edit_header
JMP .loop
.toggle_mode
CALL .begin_update
STW r15 r0 24
LDW r1 r15 28
XORI r1 1
STW r15 r1 28
CALL .snapshot
CALL .save_state
JMP .redraw
.difficulty
CALL .begin_update
STW r15 r0 24
LDW r1 r15 40
ADDI r1 4
LEA r2 r0 32
CMP r1 r2
BR.NC .difficulty_valid
MOV r1 r0
.difficulty_valid
STW r15 r1 40
CALL .make_target
CALL .snapshot
CALL .save_state
JMP .redraw
.edit_target
LEA r1 r0 1
LDI r2 TARGET
LEA r3 r0 32
JMP .edit_begin
.edit_header
LEA r1 r0 2
LDI r2 HEADER
LEA r3 r0 80
.edit_begin
STW r15 r0 24
STW r15 r0 220
STW r15 r1 208
STW r15 r0 212
LDI r4 CONFIG_BUF
LEA r5 r0 80
.clear_config
STB r4 r0 0
INC r4
DEC r5
BR.NE .clear_config
MOV r1 r2
LDI r2 CONFIG_BUF
CALL .copy_bytes
JMP .redraw
.config_key
LEA r13 r0 27
CMP r12 r13
BR.EQ .config_cancel
LEA r13 r0 13
CMP r12 r13
BR.EQ .config_commit
LEA r13 r0 8
CMP r12 r13
BR.EQ .config_back
LDW r3 r15 212
LDW r1 r15 208
LEA r2 r0 1
CMP r1 r2
BR.NE .config_header_size
LEA r2 r0 64
JMP .config_size_ready
.config_header_size
LEA r2 r0 160
.config_size_ready
CMP r3 r2
BR.C .loop
; Exact decimal digit or A..F/a..f; invalid keys leave buffer/cursor unchanged.
LEA r1 r0 '0'
CMP r12 r1
BR.NC .loop
LEA r1 r0 '9'
CMP r12 r1
BR.LE .config_digit
ANDI r12 0xdf
LEA r1 r0 'A'
CMP r12 r1
BR.NC .loop
LEA r1 r0 'F'
CMP r12 r1
BR.GT .loop
SUBI r12 55
JMP .config_nibble
.config_digit
SUBI r12 48
.config_nibble
MOV r4 r3
RSH r4 r4
LDI r5 CONFIG_BUF
ADD r5 r5 r4
LDB r6 r5 0
MOV r4 r3
ANDI r4 1
CMP r4 r0
BR.NE .config_low
ANDI r6 15
LEA r4 r0 4
SHL r12 r12 r4
JMP .config_store
.config_low
ANDI r6 240
.config_store
OR r6 r6 r12
STB r5 r6 0
INC r3
STW r15 r3 212
JMP .redraw
.config_back
LDW r1 r15 212
CMP r1 r0
BR.EQ .loop
DEC r1
STW r15 r1 212
JMP .redraw
.config_cancel
STW r15 r0 208
JMP .redraw
.config_commit
LDW r1 r15 208
LDW r4 r15 212
LEA r2 r0 1
CMP r1 r2
BR.NE .commit_header
LEA r2 r0 64
CMP r4 r2
BR.NE .loop
CALL .begin_update
LDI r1 CONFIG_BUF
LDI r2 TARGET
LEA r3 r0 32
CALL .copy_bytes
CALL .count_difficulty
JMP .commit_done
.commit_header
LEA r2 r0 160
CMP r4 r2
BR.NE .loop
CALL .begin_update
LDI r1 CONFIG_BUF
LDI r2 HEADER
LEA r3 r0 80
CALL .copy_bytes
.commit_done
STW r15 r0 208
STW r15 r0 244
CALL .snapshot
CALL .save_state
JMP .redraw

.hash_once
CALL .begin_update
LDI r15 RECORD
LDI r1 HEADER
LDW r2 r1 76
STW r15 r2 12
RDPMC r2 0
STW r15 r2 224
RDPMC r2 2
STW r15 r2 248
RDTIME r2
STW r15 r2 228
RDPMC r2 5
STW r15 r2 44
LDW r2 r15 28
CMP r2 r0
BR.NE .hash_software
LDI r2 HASHBUF
DSHA256 r2 r1
JMP .hash_computed
.hash_software
LDI r3 HASHBUF
CALL .soft_dsha256
.hash_computed
LDI r15 RECORD
RDPMC r1 0
RDPMC r4 2
LDW r2 r15 224
SUB r1 r1 r2
LDW r2 r15 248
SBC r4 r4 r2
LDW r2 r15 28
CMP r2 r0
BR.NE .hash_soft_cost
STW r15 r1 112
JMP .hash_account
.hash_soft_cost
STW r15 r1 116
.hash_account
LDW r2 r15 32
ADD r2 r2 r1
LDW r3 r15 36
ADC r3 r3 r4
STW r15 r2 32
STW r15 r3 36
RDTIME r1
RDPMC r4 5
LDW r2 r15 228
SUB r1 r1 r2
LDW r2 r15 44
SBC r4 r4 r2
STW r15 r1 232
STW r15 r4 248
LDW r2 r15 120
ADD r2 r2 r1
LDW r3 r15 124
ADC r3 r3 r4
STW r15 r2 120
STW r15 r3 124
LDW r2 r15 16
LEA r1 r0 1
ADD r2 r2 r1
LDW r3 r15 20
ADC r3 r3 r0
STW r15 r2 16
STW r15 r3 20
LDI r1 HASHBUF
LDI r2 TARGET
HASHCMP r1 r2
BR.VALID .hash_found
LDI r1 HEADER
LDW r2 r1 76
LDI r3 0xffffffff
CMP r2 r3
BR.EQ .hash_exhausted
INCNONCE r1
CALL .snapshot
RET
.hash_found
LEA r1 r0 2
STW r15 r1 24
LEA r1 r0 1
STW r15 r1 244
CALL .snapshot
CALL .log_record
CALL .save_state
RET
.hash_exhausted
LEA r1 r0 3
STW r15 r1 24
LEA r1 r0 2
STW r15 r1 244
CALL .snapshot
CALL .log_record
CALL .save_state
RET

.benchmark
CALL .begin_update
STW r15 r0 24
LDI r1 HEADER
LDI r3 HASHBUF
RDPMC r4 0
DSHA256 r3 r1
RDPMC r5 0
SUB r5 r5 r4
STW r15 r5 112
LDI r3 COMPARE_HASH
RDPMC r4 0
CALL .soft_dsha256
RDPMC r5 0
SUB r5 r5 r4
STW r15 r5 116
LDI r1 HASHBUF
LDI r2 COMPARE_HASH
LEA r3 r0 8
LEA r6 r0 1
.compare_loop
LDW r4 r1 0
LDW r5 r2 0
CMP r4 r5
BR.NE .compare_fail
ADDI r1 4
ADDI r2 4
DEC r3
BR.NE .compare_loop
JMP .compare_done
.compare_fail
LEA r6 r0 2
.compare_done
STW r15 r6 236
CALL .snapshot
CALL .save_state
JMP .redraw

.begin_update
PUSH r1
LDI r15 RECORD
LDW r1 r15 252
ORI r1 1
STW r15 r1 252
POP r1
RET
.snapshot
PUSH r1
PUSH r2
PUSH r3
LDI r1 HASHBUF
LDI r2 RECORD
ADDI r2 48
LEA r3 r0 32
CALL .copy_bytes
LDI r1 TARGET
LDI r2 RECORD
ADDI r2 80
LEA r3 r0 32
CALL .copy_bytes
LDI r1 HEADER
LDI r2 RECORD
ADDI r2 128
LEA r3 r0 80
CALL .copy_bytes
LDW r1 r15 252
INC r1
ANDI r1 0xfffffffe
STW r15 r1 252
POP r3
POP r2
POP r1
RET
.copy_bytes
PUSH r4
.copy_bytes_loop
CMP r3 r0
BR.EQ .copy_bytes_done
LDB r4 r1 0
STB r2 r4 0
INC r1
INC r2
DEC r3
JMP .copy_bytes_loop
.copy_bytes_done
POP r4
RET
.log_record
PUSH r1
PUSH r2
PUSH r3
LDI r1 RECORD
LEA r2 r0 128
LDW r3 r15 8
ANDI r3 255
STW r15 r3 216
SYS SYS_MINER_LOG_RESULT
POP r3
POP r2
POP r1
RET
.save_state
PUSH r1
PUSH r2
LDI r1 RECORD
LEA r2 r0 256
SYS SYS_MINER_SAVE_STATE
LEA r2 r0 256
CMP r1 r2
BR.EQ .save_ok
STW r15 r1 240
JMP .save_done
.save_ok
STW r15 r0 240
.save_done
POP r2
POP r1
RET
.make_target
LDI r1 TARGET
LDW r2 r15 40
LEA r3 r0 32
.make_target_loop
LEA r4 r0 8
CMP r2 r4
BR.NC .target_partial
STB r1 r0 0
SUBI r2 8
JMP .target_advance
.target_partial
LEA r4 r0 255
SHR r4 r4 r2
STB r1 r4 0
MOV r2 r0
.target_advance
INC r1
DEC r3
BR.NE .make_target_loop
RET
.count_difficulty
LDI r1 TARGET
MOV r2 r0
LEA r3 r0 32
.count_target_bytes
LDB r4 r1 0
CMP r4 r0
BR.NE .count_target_bits
ADDI r2 8
INC r1
DEC r3
BR.NE .count_target_bytes
JMP .count_target_done
.count_target_bits
LEA r3 r0 128
.count_bit
AND r5 r4 r3
CMP r5 r0
BR.NE .count_target_done
INC r2
RSH r3 r3
JMP .count_bit
.count_target_done
STW r15 r2 40
RET

.draw
PUSH r1
PUSH r2
PUSH r3
PUSH r4
PUSH r5
PUSH r6
PUSH r7
PUSH r8
PUSH r9
PUSH r10
PUSH r11
PUSH r12
PUSH r13
PUSH r14
LDI r15 RECORD
SYS SYS_UI_MINER_VIEW
CALL .gpu_fence
; Preserve the window and status bar before modifying the back buffer.
LEA r1 r0 0x13
CALL .gpu_cmd
LDW r1 r15 208
CMP r1 r0
BR.NE .draw_config
LDI r11 RECORD
LDW r1 r15 220
CMP r1 r0
BR.EQ .draw_record
LEA r2 r0 2
CMP r1 r2
BR.EQ .draw_empty_history
LDI r11 HISTORY
.draw_record
CALL .line_clear
LEA r1 r0 'G'
MOV r2 r0
CALL .line_char
LDW r1 r11 8
LEA r2 r0 1
CALL .line_hex32
LEA r1 r0 'N'
LEA r2 r0 10
CALL .line_char
LDW r1 r11 12
LEA r2 r0 11
CALL .line_hex32
LEA r1 r0 'A'
LEA r2 r0 20
CALL .line_char
LDW r1 r11 20
LEA r2 r0 21
CALL .line_hex32
LDW r1 r11 16
LEA r2 r0 29
CALL .line_hex32
LEA r1 r0 37
LEA r2 r0 48
CALL .line_submit
CALL .line_clear
LEA r1 r0 'H'
MOV r2 r0
CALL .line_char
MOV r1 r11
ADDI r1 48
LEA r2 r0 16
LEA r3 r0 2
CALL .line_hex_bytes
LEA r1 r0 34
LEA r2 r0 64
CALL .line_submit
CALL .line_clear
LEA r1 r0 'H'
MOV r2 r0
CALL .line_char
MOV r1 r11
ADDI r1 64
LEA r2 r0 16
LEA r3 r0 2
CALL .line_hex_bytes
LEA r1 r0 34
LEA r2 r0 80
CALL .line_submit
CALL .line_clear
LEA r1 r0 'T'
MOV r2 r0
CALL .line_char
MOV r1 r11
ADDI r1 80
LEA r2 r0 16
LEA r3 r0 2
CALL .line_hex_bytes
LEA r1 r0 34
LEA r2 r0 96
CALL .line_submit
CALL .line_clear
LEA r1 r0 'T'
MOV r2 r0
CALL .line_char
MOV r1 r11
ADDI r1 96
LEA r2 r0 16
LEA r3 r0 2
CALL .line_hex_bytes
LEA r1 r0 34
LEA r2 r0 112
CALL .line_submit
CALL .line_clear
LEA r1 r0 'S'
MOV r2 r0
CALL .line_char
LDW r1 r11 24
LEA r2 r0 1
CALL .line_hex32
LEA r1 r0 'M'
LEA r2 r0 10
CALL .line_char
LDW r1 r11 28
LEA r2 r0 11
CALL .line_hex32
LEA r1 r0 'D'
LEA r2 r0 20
CALL .line_char
LDW r1 r11 40
LEA r2 r0 21
CALL .line_hex32
LEA r1 r0 'V'
LEA r2 r0 30
CALL .line_char
MOV r1 r11
ADDI r1 48
MOV r2 r11
ADDI r2 80
HASHCMP r1 r2
MOV r1 r0
BR.NVALID .draw_valid_done
LEA r1 r0 1
.draw_valid_done
LEA r2 r0 31
CALL .line_hex32
LEA r1 r0 39
LEA r2 r0 128
CALL .line_submit
CALL .line_clear
LEA r1 r0 'N'
MOV r2 r0
CALL .line_char
LDW r1 r11 112
LEA r2 r0 1
CALL .line_hex32
LEA r1 r0 'S'
LEA r2 r0 10
CALL .line_char
LDW r1 r11 116
LEA r2 r0 11
CALL .line_hex32
LEA r1 r0 'R'
LEA r2 r0 20
CALL .line_char
; Instantaneous measured microhashes/raw-clock-period. A zero elapsed delta
; is displayed as unavailable(FFFFFFFF), never as infinite speed.
LDW r3 r15 232
LDW r4 r15 248
CMP r4 r0
BR.NE .draw_rate_zero
CMP r3 r0
BR.EQ .draw_rate_unknown
LDI r1 1000000
DIV r1 r1 r3
JMP .draw_rate
.draw_rate_zero
MOV r1 r0
JMP .draw_rate
.draw_rate_unknown
LDI r1 0xffffffff
.draw_rate
LEA r2 r0 21
CALL .line_hex32
LEA r1 r0 'C'
LEA r2 r0 30
CALL .line_char
LDW r1 r15 236
LEA r2 r0 31
CALL .line_hex32
LEA r1 r0 39
LEA r2 r0 144
CALL .line_submit
CALL .line_clear
LEA r1 r0 'E'
MOV r2 r0
CALL .line_char
LDW r1 r15 240
LEA r2 r0 1
CALL .line_hex32
LEA r1 r0 'X'
LEA r2 r0 10
CALL .line_char
LDW r1 r15 244
LEA r2 r0 11
CALL .line_hex32
LEA r1 r0 'T'
LEA r2 r0 20
CALL .line_char
LDW r1 r11 124
LEA r2 r0 21
CALL .line_hex32
LDW r1 r11 120
LEA r2 r0 29
CALL .line_hex32
LEA r1 r0 37
LEA r2 r0 160
CALL .line_submit
JMP .draw_finish
.draw_empty_history
CALL .line_clear
LEA r1 r0 'E'
MOV r2 r0
CALL .line_char
LEA r1 r0 'M'
LEA r2 r0 1
CALL .line_char
LEA r1 r0 'P'
LEA r2 r0 2
CALL .line_char
LEA r1 r0 'T'
LEA r2 r0 3
CALL .line_char
LEA r1 r0 'Y'
LEA r2 r0 4
CALL .line_char
LDW r1 r15 216
LEA r2 r0 6
CALL .line_hex32
LEA r1 r0 14
LEA r2 r0 64
CALL .line_submit
JMP .draw_finish
.draw_config
CALL .line_clear
LEA r1 r0 'E'
MOV r2 r0
CALL .line_char
LDW r1 r15 208
LEA r2 r0 1
CALL .line_hex32
LDW r1 r15 212
LEA r2 r0 10
CALL .line_hex32
LEA r1 r0 18
LEA r2 r0 48
CALL .line_submit
CALL .line_clear
LDI r1 CONFIG_BUF
ADDI r1 0
LEA r2 r0 20
MOV r3 r0
CALL .line_hex_bytes
LEA r1 r0 40
LEA r2 r0 64
CALL .line_submit
CALL .line_clear
LDI r1 CONFIG_BUF
ADDI r1 20
LEA r2 r0 20
MOV r3 r0
CALL .line_hex_bytes
LEA r1 r0 40
LEA r2 r0 80
CALL .line_submit
CALL .line_clear
LDI r1 CONFIG_BUF
ADDI r1 40
LEA r2 r0 20
MOV r3 r0
CALL .line_hex_bytes
LEA r1 r0 40
LEA r2 r0 96
CALL .line_submit
CALL .line_clear
LDI r1 CONFIG_BUF
ADDI r1 60
LEA r2 r0 20
MOV r3 r0
CALL .line_hex_bytes
LEA r1 r0 40
LEA r2 r0 112
CALL .line_submit

.draw_finish
LEA r1 r0 0x14
CALL .gpu_cmd
CALL .gpu_fence
POP r14
POP r13
POP r12
POP r11
POP r10
POP r9
POP r8
POP r7
POP r6
POP r5
POP r4
POP r3
POP r2
POP r1
RET
.gpu_cmd
PUSH r2
PUSH r3
LDI r2 GPU_BASE
STW r2 r1 4
LEA r3 r0 1
STW r2 r3 40
POP r3
POP r2
RET
.line_clear
PUSH r1
PUSH r2
PUSH r3
LDI r1 GPU_TEXT
LEA r2 r0 53
LEA r3 r0 0x20
.line_clear_loop
CMP r2 r0
BR.EQ .line_clear_done
STB r1 r3 0
INC r1
DEC r2
JMP .line_clear_loop
.line_clear_done
POP r3
POP r2
POP r1
RET

.line_char
; r1 ascii, r2 text index.
PUSH r3
LDI r3 GPU_TEXT
ADD r3 r3 r2
STB r3 r1 0
POP r3
RET

.line_hex32
; r1 value, r2 text index.
PUSH r3
PUSH r4
PUSH r12
PUSH r13
LEA r3 r0 28
LEA r4 r0 8
.hex32_loop
CMP r4 r0
BR.EQ .hex32_done
MOV r12 r1
SHR r12 r12 r3
LEA r13 r0 0x0f
AND r12 r12 r13
MOV r13 r12
LEA r12 r0 10
CMP r13 r12
BR.LT .hex32_digit
LEA r12 r0 55
ADD r13 r13 r12
JMP .hex32_store
.hex32_digit
LEA r12 r0 48
ADD r13 r13 r12
.hex32_store
LDI r12 GPU_TEXT
ADD r12 r12 r2
STB r12 r13 0
INC r2
LEA r12 r0 4
SUB r3 r3 r12
DEC r4
JMP .hex32_loop
.hex32_done
POP r13
POP r12
POP r4
POP r3
RET

.line_hex_bytes
; r1 byte ptr, r2 count, r3 text index.
PUSH r4
PUSH r12
PUSH r13
PUSH r14
.hexbytes_loop
CMP r2 r0
BR.EQ .hexbytes_done
LDB r4 r1 0

MOV r12 r4
LEA r13 r0 4
SHR r12 r12 r13
LEA r13 r0 0x0f
AND r12 r12 r13
CALL .nibble_ascii
LDI r14 GPU_TEXT
ADD r14 r14 r3
STB r14 r12 0
INC r3

MOV r12 r4
LEA r13 r0 0x0f
AND r12 r12 r13
CALL .nibble_ascii
LDI r14 GPU_TEXT
ADD r14 r14 r3
STB r14 r12 0
INC r3

INC r1
DEC r2
JMP .hexbytes_loop
.hexbytes_done
POP r14
POP r13
POP r12
POP r4
RET

.nibble_ascii
; r12 nibble -> r12 ASCII.
PUSH r13
MOV r13 r12
LEA r12 r0 10
CMP r13 r12
BR.LT .nibble_digit
LEA r12 r0 55
ADD r12 r13 r12
POP r13
RET
.nibble_digit
LEA r12 r0 48
ADD r12 r13 r12
POP r13
RET

.line_submit
; r1 length, r2 y.
PUSH r3
PUSH r4
LDI r3 GPU_BASE
MOV r4 r0
STW r3 r4 8
STW r3 r1 12
LEA r4 r0 4
STW r3 r4 16
STW r3 r2 20
LEA r4 r0 0x0c
STW r3 r4 4
LEA r4 r0 1
STW r3 r4 40
CALL .gpu_fence
POP r4
POP r3
RET

.gpu_fence
PUSH r1
PUSH r2
PUSH r3
LDI r1 GPU_BASE
LDW r2 r1 48
LEA r3 r0 0x16
STW r1 r3 4
LEA r3 r0 1
STW r1 r3 40
INC r2
.gpu_fence_wait
LDW r3 r1 48
CMP r3 r2
BR.LT .gpu_fence_wait
POP r3
POP r2
POP r1
RET


; CPU-only SHA-256. All rounds use ordinary ALU/load/store instructions.
; .soft_sha256: r1 source, r2 byte length (0..119), r3 output32.
; .soft_dsha256: r1 header80, r3 output32. Preserves all registers.
; Workspace 0x2e000..0x2e41f, separate from app/file/edit buffers.
define SOFT_BASE 0x0002e000
define SOFT_MSG 0x0002e100
define SOFT_W 0x0002e200
define SOFT_K 0x0002e300
define SOFT_TMP 0x0002e400

.soft_constants
PUSH r1
PUSH r2
LDI r1 SOFT_K
LDI r2 0x428a2f98
STW r1 r2 0
LDI r2 0x71374491
STW r1 r2 4
LDI r2 0xb5c0fbcf
STW r1 r2 8
LDI r2 0xe9b5dba5
STW r1 r2 12
LDI r2 0x3956c25b
STW r1 r2 16
LDI r2 0x59f111f1
STW r1 r2 20
LDI r2 0x923f82a4
STW r1 r2 24
LDI r2 0xab1c5ed5
STW r1 r2 28
LDI r2 0xd807aa98
STW r1 r2 32
LDI r2 0x12835b01
STW r1 r2 36
LDI r2 0x243185be
STW r1 r2 40
LDI r2 0x550c7dc3
STW r1 r2 44
LDI r2 0x72be5d74
STW r1 r2 48
LDI r2 0x80deb1fe
STW r1 r2 52
LDI r2 0x9bdc06a7
STW r1 r2 56
LDI r2 0xc19bf174
STW r1 r2 60
LDI r2 0xe49b69c1
STW r1 r2 64
LDI r2 0xefbe4786
STW r1 r2 68
LDI r2 0x0fc19dc6
STW r1 r2 72
LDI r2 0x240ca1cc
STW r1 r2 76
LDI r2 0x2de92c6f
STW r1 r2 80
LDI r2 0x4a7484aa
STW r1 r2 84
LDI r2 0x5cb0a9dc
STW r1 r2 88
LDI r2 0x76f988da
STW r1 r2 92
LDI r2 0x983e5152
STW r1 r2 96
LDI r2 0xa831c66d
STW r1 r2 100
LDI r2 0xb00327c8
STW r1 r2 104
LDI r2 0xbf597fc7
STW r1 r2 108
LDI r2 0xc6e00bf3
STW r1 r2 112
LDI r2 0xd5a79147
STW r1 r2 116
LDI r2 0x06ca6351
STW r1 r2 120
LDI r2 0x14292967
STW r1 r2 124
LDI r2 0x27b70a85
STW r1 r2 128
LDI r2 0x2e1b2138
STW r1 r2 132
LDI r2 0x4d2c6dfc
STW r1 r2 136
LDI r2 0x53380d13
STW r1 r2 140
LDI r2 0x650a7354
STW r1 r2 144
LDI r2 0x766a0abb
STW r1 r2 148
LDI r2 0x81c2c92e
STW r1 r2 152
LDI r2 0x92722c85
STW r1 r2 156
LDI r2 0xa2bfe8a1
STW r1 r2 160
LDI r2 0xa81a664b
STW r1 r2 164
LDI r2 0xc24b8b70
STW r1 r2 168
LDI r2 0xc76c51a3
STW r1 r2 172
LDI r2 0xd192e819
STW r1 r2 176
LDI r2 0xd6990624
STW r1 r2 180
LDI r2 0xf40e3585
STW r1 r2 184
LDI r2 0x106aa070
STW r1 r2 188
LDI r2 0x19a4c116
STW r1 r2 192
LDI r2 0x1e376c08
STW r1 r2 196
LDI r2 0x2748774c
STW r1 r2 200
LDI r2 0x34b0bcb5
STW r1 r2 204
LDI r2 0x391c0cb3
STW r1 r2 208
LDI r2 0x4ed8aa4a
STW r1 r2 212
LDI r2 0x5b9cca4f
STW r1 r2 216
LDI r2 0x682e6ff3
STW r1 r2 220
LDI r2 0x748f82ee
STW r1 r2 224
LDI r2 0x78a5636f
STW r1 r2 228
LDI r2 0x84c87814
STW r1 r2 232
LDI r2 0x8cc70208
STW r1 r2 236
LDI r2 0x90befffa
STW r1 r2 240
LDI r2 0xa4506ceb
STW r1 r2 244
LDI r2 0xbef9a3f7
STW r1 r2 248
LDI r2 0xc67178f2
STW r1 r2 252
POP r2
POP r1
RET

.soft_dsha256
PUSH r1
PUSH r2
PUSH r3
LEA r2 r0 80
LDI r3 SOFT_TMP
CALL .soft_sha256
POP r3
LDI r1 SOFT_TMP
LEA r2 r0 32
CALL .soft_sha256
POP r2
POP r1
RET

.soft_sha256
PUSH r1
PUSH r2
PUSH r3
PUSH r4
PUSH r5
PUSH r6
PUSH r7
PUSH r8
PUSH r9
PUSH r10
PUSH r11
PUSH r12
PUSH r13
PUSH r14
PUSH r15
LDI r15 SOFT_BASE
STW r15 r1 32
STW r15 r2 36
STW r15 r3 40
STW r15 r0 44
LEA r4 r0 56
CMP r2 r4
BR.NC .soft_one_block
LEA r4 r0 128
JMP .soft_block_count
.soft_one_block
LEA r4 r0 64
.soft_block_count
STW r15 r4 48
LDI r1 SOFT_MSG
LEA r2 r0 128
.soft_clear_msg
STB r1 r0 0
INC r1
DEC r2
BR.NE .soft_clear_msg
LDW r1 r15 32
LDW r2 r15 36
LDI r3 SOFT_MSG
.soft_copy_msg
CMP r2 r0
BR.EQ .soft_pad
LDB r4 r1 0
STB r3 r4 0
INC r1
INC r3
DEC r2
JMP .soft_copy_msg
.soft_pad
LEA r4 r0 128
STB r3 r4 0
LDI r3 SOFT_MSG
LDW r4 r15 48
ADD r3 r3 r4
LDW r1 r15 36
LEA r2 r0 3
SHL r1 r1 r2
STB r3 r1 -1
LEA r2 r0 8
SHR r1 r1 r2
STB r3 r1 -2
LDI r1 0x6a09e667
STW r15 r1 0
LDI r1 0xbb67ae85
STW r15 r1 4
LDI r1 0x3c6ef372
STW r15 r1 8
LDI r1 0xa54ff53a
STW r15 r1 12
LDI r1 0x510e527f
STW r15 r1 16
LDI r1 0x9b05688c
STW r15 r1 20
LDI r1 0x1f83d9ab
STW r15 r1 24
LDI r1 0x5be0cd19
STW r15 r1 28

.soft_next_block
LDI r15 SOFT_BASE
LDW r14 r15 44
LDI r13 SOFT_MSG
ADD r14 r14 r13
LDI r15 SOFT_W
LEA r13 r0 16
.soft_initial_words
LDB r1 r14 0
LEA r4 r0 8
SHL r1 r1 r4
LDB r2 r14 1
OR r1 r1 r2
SHL r1 r1 r4
LDB r2 r14 2
OR r1 r1 r2
SHL r1 r1 r4
LDB r2 r14 3
OR r1 r1 r2
STW r15 r1 0
ADDI r14 4
ADDI r15 4
DEC r13
BR.NE .soft_initial_words
LEA r13 r0 48
.soft_schedule
LDW r1 r15 -8
LEA r4 r0 17
ROR r2 r1 r4
LEA r4 r0 19
ROR r5 r1 r4
XOR r2 r2 r5
LEA r4 r0 10
SHR r5 r1 r4
XOR r2 r2 r5
LDW r1 r15 -60
LEA r4 r0 7
ROR r3 r1 r4
LEA r4 r0 18
ROR r5 r1 r4
XOR r3 r3 r5
LEA r4 r0 3
SHR r5 r1 r4
XOR r3 r3 r5
LDW r5 r15 -28
LDW r6 r15 -64
ADD r2 r2 r3
ADD r2 r2 r5
ADD r2 r2 r6
STW r15 r2 0
ADDI r15 4
DEC r13
BR.NE .soft_schedule
LDI r15 SOFT_BASE
LDW r1 r15 0
LDW r2 r15 4
LDW r3 r15 8
LDW r4 r15 12
LDW r5 r15 16
LDW r6 r15 20
LDW r7 r15 24
LDW r8 r15 28
LDI r15 SOFT_W
LDI r14 SOFT_K
LEA r13 r0 64
.soft_round
LEA r12 r0 6
ROR r9 r5 r12
LEA r12 r0 11
ROR r11 r5 r12
XOR r9 r9 r11
LEA r12 r0 25
ROR r11 r5 r12
XOR r9 r9 r11
AND r11 r5 r6
NOT r12 r5
AND r12 r12 r7
XOR r11 r11 r12
ADD r9 r9 r11
ADD r9 r9 r8
LDW r11 r14 0
ADD r9 r9 r11
LDW r11 r15 0
ADD r9 r9 r11
LEA r12 r0 2
ROR r10 r1 r12
LEA r12 r0 13
ROR r11 r1 r12
XOR r10 r10 r11
LEA r12 r0 22
ROR r11 r1 r12
XOR r10 r10 r11
AND r11 r1 r2
OR r12 r1 r2
AND r12 r12 r3
OR r11 r11 r12
ADD r10 r10 r11
MOV r8 r7
MOV r7 r6
MOV r6 r5
ADD r5 r4 r9
MOV r4 r3
MOV r3 r2
MOV r2 r1
ADD r1 r9 r10
ADDI r14 4
ADDI r15 4
DEC r13
BR.NE .soft_round
LDI r15 SOFT_BASE
LDW r9 r15 0
ADD r9 r9 r1
STW r15 r9 0
LDW r9 r15 4
ADD r9 r9 r2
STW r15 r9 4
LDW r9 r15 8
ADD r9 r9 r3
STW r15 r9 8
LDW r9 r15 12
ADD r9 r9 r4
STW r15 r9 12
LDW r9 r15 16
ADD r9 r9 r5
STW r15 r9 16
LDW r9 r15 20
ADD r9 r9 r6
STW r15 r9 20
LDW r9 r15 24
ADD r9 r9 r7
STW r15 r9 24
LDW r9 r15 28
ADD r9 r9 r8
STW r15 r9 28
LDW r1 r15 44
ADDI r1 64
STW r15 r1 44
LDW r2 r15 48
CMP r1 r2
BR.NC .soft_next_block
LDW r1 r15 40
LEA r2 r0 8
.soft_digest_words
LDW r3 r15 0
LEA r4 r0 24
SHR r5 r3 r4
STB r1 r5 0
LEA r4 r0 16
SHR r5 r3 r4
STB r1 r5 1
LEA r4 r0 8
SHR r5 r3 r4
STB r1 r5 2
STB r1 r3 3
ADDI r1 4
ADDI r15 4
DEC r2
BR.NE .soft_digest_words
POP r15
POP r14
POP r13
POP r12
POP r11
POP r10
POP r9
POP r8
POP r7
POP r6
POP r5
POP r4
POP r3
POP r2
POP r1
RET
