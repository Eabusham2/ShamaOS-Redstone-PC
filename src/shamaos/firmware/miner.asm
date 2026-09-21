; ShamaOS Bitcoin Miner
; Real CPU-native DSHA256/HASHCMP/INCNONCE mining.
;
; Controls:
; A     Start/live
; B     Stop + save state
; UP    New generation/run (nonce+attempts reset)
; DOWN  Return from history to live
; LEFT  Previous saved run
; RIGHT Next saved run
; universal Home/Exit/Editor/Files are handled by the kernel.
;
; Persistent 64-byte run record:
; +0 generation u32
; +4 nonce u32
; +8 attempts u32
; +12 status u32 (0 stopped, 1 valid/found)
; +16 hash[32]
; +48 target prefix[16]

define EVT_UP 0x10
define EVT_DOWN 0x11
define EVT_LEFT 0x12
define EVT_RIGHT 0x13
define EVT_A 0x14
define EVT_B 0x15

define HEADER 0x000b2000
define HASHBUF 0x000b2100
define TARGET 0x000b2140
define RECORD 0x000b2200
define HISTORY 0x000b2400

define GPU_BASE 0x02000000
define GPU_TEXT 0x02000400

LDI r4 HEADER
LDI r5 HASHBUF
LDI r6 TARGET
LDI r7 0
LDI r8 0
LDI r9 0
LDI r10 0
LDI r11 0

; Clear the 80-byte header.
MOV r12 r4
LDI r13 80
.init_header
CMP r13 r0
BR.EQ .init_target
STB r12 r0 0
INC r12
DEC r13
JMP .init_header

; Easy but non-trivial educational target:
; 00FFFFFFFF... gives roughly 1/256 success probability.
.init_target
MOV r12 r6
LDI r13 32
LDI r14 0xff
.init_target_loop
CMP r13 r0
BR.EQ .target_zero
STB r12 r14 0
INC r12
DEC r13
JMP .init_target_loop
.target_zero
STB r6 r0 0

.redraw
SYS SYS_UI_MINER_VIEW
CALL .gpu_fence
CMP r11 r0
BR.EQ .draw_live
CALL .draw_history
JMP .loop

.draw_live
CALL .draw_live_screen

.loop
SYS SYS_GET_EVENT
CMP r1 r0
BR.NE .event

CMP r7 r0
BR.EQ .idle

DSHA256 r5 r4
HASHCMP r5 r6
BR.VALID .found

INCNONCE r4
INC r8

; Refresh every 16 hashes.
MOV r12 r8
LDI r13 0x0f
AND r12 r12 r13
CMP r12 r0
BR.NE .loop
CALL .draw_live_screen
JMP .loop

.found
LDI r7 0
LDI r12 1
CALL .build_record
CALL .log_record
CALL .save_state
CALL .draw_live_screen
JMP .loop

.event
; event = (r1 >> 10) & ff
MOV r12 r1
LDI r13 10
SHR r12 r12 r13
LDI r13 0xff
AND r12 r12 r13

LDI r13 EVT_A
CMP r12 r13
BR.EQ .start
LDI r13 EVT_B
CMP r12 r13
BR.EQ .stop
LDI r13 EVT_UP
CMP r12 r13
BR.EQ .new_generation
LDI r13 EVT_DOWN
CMP r12 r13
BR.EQ .live_mode
LDI r13 EVT_LEFT
CMP r12 r13
BR.EQ .history_prev
LDI r13 EVT_RIGHT
CMP r12 r13
BR.EQ .history_next

SYS SYS_APP_EVENT
JMP .redraw

.start
LDI r11 0
LDI r7 1
JMP .redraw

.stop
LDI r7 0
LDI r12 0
CALL .build_record
CALL .save_state
JMP .redraw

.new_generation
LDI r7 0
LDI r11 0
INC r9
LDI r8 0
; Reset little-endian nonce at header+76.
STW r4 r0 76
LDI r12 0
CALL .build_record
CALL .save_state
JMP .redraw

.live_mode
LDI r11 0
JMP .redraw

.history_prev
LDI r7 0
LDI r11 1
CMP r10 r0
BR.NE .hist_dec
LDI r10 255
JMP .hist_load
.hist_dec
DEC r10
JMP .hist_load

.history_next
LDI r7 0
LDI r11 1
LDI r13 255
CMP r10 r13
BR.NE .hist_inc
LDI r10 0
JMP .hist_load
.hist_inc
INC r10

.hist_load
MOV r1 r10
LDI r2 HISTORY
LDI r3 64
SYS SYS_MINER_LOAD_HISTORY
JMP .redraw

.idle
SYS SYS_YIELD
JMP .loop

; ---------------------------------------------------------------------------
; Record/persistence helpers
; ---------------------------------------------------------------------------
.build_record
; input r12 status
PUSH r1
PUSH r2
PUSH r3
PUSH r13
PUSH r14

LDI r13 RECORD
STW r13 r9 0

LDW r14 r4 76
STW r13 r14 4
STW r13 r8 8
STW r13 r12 12

; Copy 32-byte hash -> record+16.
MOV r1 r5
LDI r2 RECORD
LDI r14 16
ADD r2 r2 r14
LDI r3 8
.copy_hash_record
CMP r3 r0
BR.EQ .copy_target_record_start
LDW r14 r1 0
STW r2 r14 0
LDI r14 4
ADD r1 r1 r14
ADD r2 r2 r14
DEC r3
JMP .copy_hash_record

.copy_target_record_start
MOV r1 r6
LDI r2 RECORD
LDI r14 48
ADD r2 r2 r14
LDI r3 4
.copy_target_record
CMP r3 r0
BR.EQ .record_done
LDW r14 r1 0
STW r2 r14 0
LDI r14 4
ADD r1 r1 r14
ADD r2 r2 r14
DEC r3
JMP .copy_target_record

.record_done
POP r14
POP r13
POP r3
POP r2
POP r1
RET

.log_record
PUSH r1
PUSH r2
PUSH r3
PUSH r12
LDI r1 RECORD
LDI r2 64
MOV r3 r9
LDI r12 0xff
AND r3 r3 r12
SYS SYS_MINER_LOG_RESULT
MOV r10 r3
POP r12
POP r3
POP r2
POP r1
RET

.save_state
PUSH r1
PUSH r2
LDI r1 RECORD
LDI r2 64
SYS SYS_MINER_SAVE_STATE
POP r2
POP r1
RET

; ---------------------------------------------------------------------------
; GPU rendering helpers
; ---------------------------------------------------------------------------
.draw_live_screen
PUSH r1
PUSH r2
PUSH r3
PUSH r12
PUSH r13
PUSH r14

; Line 1: N<nonce> A<attempts> G<generation>
CALL .line_clear
LDI r1 0x4e
LDI r2 0
CALL .line_char
LDW r1 r4 76
LDI r2 1
CALL .line_hex32
LDI r1 0x20
LDI r2 9
CALL .line_char
LDI r1 0x41
LDI r2 10
CALL .line_char
MOV r1 r8
LDI r2 11
CALL .line_hex32
LDI r1 0x20
LDI r2 19
CALL .line_char
LDI r1 0x47
LDI r2 20
CALL .line_char
MOV r1 r9
LDI r2 21
CALL .line_hex32
LDI r1 29
LDI r2 48
CALL .line_submit

; HASH first 16 bytes.
CALL .line_clear
LDI r1 0x48
LDI r2 0
CALL .line_char
LDI r1 0x30
LDI r2 1
CALL .line_char
LDI r1 0x20
LDI r2 2
CALL .line_char
MOV r1 r5
LDI r2 16
LDI r3 3
CALL .line_hex_bytes
LDI r1 35
LDI r2 64
CALL .line_submit

; HASH second 16 bytes.
CALL .line_clear
LDI r1 0x48
LDI r2 0
CALL .line_char
LDI r1 0x31
LDI r2 1
CALL .line_char
LDI r1 0x20
LDI r2 2
CALL .line_char
MOV r1 r5
LDI r12 16
ADD r1 r1 r12
LDI r2 16
LDI r3 3
CALL .line_hex_bytes
LDI r1 35
LDI r2 80
CALL .line_submit

; TARGET first 16.
CALL .line_clear
LDI r1 0x54
LDI r2 0
CALL .line_char
LDI r1 0x30
LDI r2 1
CALL .line_char
LDI r1 0x20
LDI r2 2
CALL .line_char
MOV r1 r6
LDI r2 16
LDI r3 3
CALL .line_hex_bytes
LDI r1 35
LDI r2 96
CALL .line_submit

; TARGET second 16.
CALL .line_clear
LDI r1 0x54
LDI r2 0
CALL .line_char
LDI r1 0x31
LDI r2 1
CALL .line_char
LDI r1 0x20
LDI r2 2
CALL .line_char
MOV r1 r6
LDI r12 16
ADD r1 r1 r12
LDI r2 16
LDI r3 3
CALL .line_hex_bytes
LDI r1 35
LDI r2 112
CALL .line_submit

POP r14
POP r13
POP r12
POP r3
POP r2
POP r1
RET

.draw_history
PUSH r1
PUSH r2
PUSH r3
PUSH r12

; History summary line from loaded record.
CALL .line_clear
LDI r1 0x52
LDI r2 0
CALL .line_char
LDI r1 0x20
LDI r2 1
CALL .line_char
LDI r12 HISTORY
LDW r1 r12 0
LDI r2 2
CALL .line_hex32
LDI r1 0x20
LDI r2 10
CALL .line_char
LDI r1 0x4e
LDI r2 11
CALL .line_char
LDW r1 r12 4
LDI r2 12
CALL .line_hex32
LDI r1 20
LDI r2 48
CALL .line_submit

; Saved hash first half.
CALL .line_clear
LDI r12 HISTORY
LDI r1 16
ADD r1 r12 r1
LDI r2 16
LDI r3 0
CALL .line_hex_bytes
LDI r1 32
LDI r2 64
CALL .line_submit

; Saved hash second half.
CALL .line_clear
LDI r12 HISTORY
LDI r1 32
ADD r1 r12 r1
LDI r2 16
LDI r3 0
CALL .line_hex_bytes
LDI r1 32
LDI r2 80
CALL .line_submit

POP r12
POP r3
POP r2
POP r1
RET

.line_clear
PUSH r1
PUSH r2
PUSH r3
LDI r1 GPU_TEXT
LDI r2 53
LDI r3 0x20
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
LDI r3 28
LDI r4 8
.hex32_loop
CMP r4 r0
BR.EQ .hex32_done
MOV r12 r1
SHR r12 r12 r3
LDI r13 0x0f
AND r12 r12 r13
MOV r13 r12
LDI r12 10
CMP r13 r12
BR.LT .hex32_digit
LDI r12 55
ADD r13 r13 r12
JMP .hex32_store
.hex32_digit
LDI r12 48
ADD r13 r13 r12
.hex32_store
LDI r12 GPU_TEXT
ADD r12 r12 r2
STB r12 r13 0
INC r2
LDI r12 4
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
LDI r13 4
SHR r12 r12 r13
LDI r13 0x0f
AND r12 r12 r13
CALL .nibble_ascii
LDI r14 GPU_TEXT
ADD r14 r14 r3
STB r14 r12 0
INC r3

MOV r12 r4
LDI r13 0x0f
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
LDI r12 10
CMP r13 r12
BR.LT .nibble_digit
LDI r12 55
ADD r12 r13 r12
POP r13
RET
.nibble_digit
LDI r12 48
ADD r12 r13 r12
POP r13
RET

.line_submit
; r1 length, r2 y.
PUSH r3
PUSH r4
LDI r3 GPU_BASE
LDI r4 0
STW r3 r4 8
STW r3 r1 12
LDI r4 4
STW r3 r4 16
STW r3 r2 20
LDI r4 0x0c
STW r3 r4 4
LDI r4 1
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
LDI r3 0x16
STW r1 r3 4
LDI r3 1
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
