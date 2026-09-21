; ShamaOS Settings / maintenance
; UP/DOWN select:
; 0 Refresh UI
; 1 System Monitor
; 2 Desktop
; A applies, B returns Desktop.
;
; Cache is the currently executing app image, so Settings intentionally does
; not issue CFLUSH.

define EVT_UP 0x10
define EVT_DOWN 0x11
define EVT_A 0x14
define EVT_B 0x15

LDI r8 0

.redraw
MOV r1 r8
SYS SYS_UI_SETTINGS_VIEW
SYS SYS_UI_STATUSBAR

.loop
SYS SYS_GET_EVENT
CMP r1 r0
BR.EQ .idle

MOV r12 r1
LDI r14 10
SHR r12 r12 r14
LDI r14 0xff
AND r12 r12 r14

LDI r14 EVT_UP
CMP r12 r14
BR.EQ .up
LDI r14 EVT_DOWN
CMP r12 r14
BR.EQ .down
LDI r14 EVT_A
CMP r12 r14
BR.EQ .apply
LDI r14 EVT_B
CMP r12 r14
BR.EQ .desktop

SYS SYS_APP_EVENT
JMP .redraw

.up
CMP r8 r0
BR.EQ .redraw
DEC r8
JMP .redraw

.down
LDI r14 2
CMP r8 r14
BR.GE .redraw
INC r8
JMP .redraw

.apply
CMP r8 r0
BR.EQ .redraw
LDI r14 1
CMP r8 r14
BR.EQ .monitor
JMP .desktop

.monitor
LDI r1 4
SYS SYS_APP_LAUNCH
HLT

.desktop
LDI r1 0
SYS SYS_APP_LAUNCH
HLT

.idle
SYS SYS_YIELD
JMP .loop
