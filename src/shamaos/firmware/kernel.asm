; Resident kernel service loop image.
; Hardware trap logic enters kernel services independently of this idle loop.
.loop
SYS SYS_YIELD
JMP .loop
