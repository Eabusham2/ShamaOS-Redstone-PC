; ShamaOS Editor
.redraw
SYS SYS_UI_EDITOR_VIEW
SYS SYS_UI_STATUSBAR
.loop
SYS SYS_GET_EVENT
CMP r1 r0
BR.EQ .loop
; The app event service performs cursor/key/menu dispatch. File mutations still
; go through FILE_* syscalls and assembly through SYS_ASSEMBLE.
SYS SYS_APP_EVENT
SYS SYS_UI_EDITOR_VIEW
SYS SYS_UI_STATUSBAR
JMP .loop
