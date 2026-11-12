`include "shama_boot_params.svh"

module shama_kernel_accel(
    input  logic         clk,
    input  logic         rst,

    input  logic         req_valid,
    input  logic [11:0]  req_id,
    input  logic [191:0] req_args,
    output logic         req_ready,
    output logic [63:0]  req_ret,
    output logic         req_jump_valid,
    output logic [31:0]  req_jump_pc,
    output logic         req_load_app,
    output logic [3:0]   req_app_id,

    input  logic [31:0]  ram_used_bytes,
    input  logic [31:0]  cache_used_bytes,
    input  logic [31:0]  flash_used_bytes,
    input  logic         cpu_halted,
    input  logic         sha_busy,
    input  logic         gpu_busy,
    input  logic         gpu_capacity_fault,
    input  logic [7:0]   notice_code,
    output logic         notice_busy,
    output logic         notice_ready,

    output logic         gpu_valid,
    output logic         gpu_we,
    output logic [11:0]  gpu_addr,
    output logic [31:0]  gpu_wdata,
    output logic [3:0]   gpu_wstrb,
    input  logic         gpu_ready,
    input  logic [31:0]  gpu_rdata,

    output logic         fs_valid,
    output logic [11:0]  fs_id,
    output logic [191:0] fs_args,
    input  logic         fs_ready,
    input  logic [63:0]  fs_ret,

    output logic         asm_valid,
    output logic [191:0] asm_args,
    input  logic         asm_ready,
    input  logic [63:0]  asm_ret
);
    localparam [11:0]
        SYS_BOOT_MOUNT      = 12'h008,
        SYS_APP_EVENT       = 12'h00a,

        SYS_FILE_CREATE     = 12'h020,
        SYS_FILE_OPEN       = 12'h021,
        SYS_FILE_CLOSE      = 12'h022,
        SYS_FILE_READ       = 12'h023,
        SYS_FILE_WRITE      = 12'h024,
        SYS_FILE_TRUNCATE   = 12'h025,
        SYS_FILE_RENAME     = 12'h026,
        SYS_FILE_DELETE     = 12'h027,
        SYS_FILE_STAT       = 12'h028,
        SYS_FILE_LIST       = 12'h029,
        SYS_FILE_TRANSFER   = 12'h02b,
        SYS_FS_SYNC         = 12'h02c,
        SYS_FLASH_USAGE     = 12'h02a,

        SYS_GPU_SUBMIT      = 12'h040,
        SYS_DRAW_TEXT       = 12'h041,
        SYS_DRAW_RECT       = 12'h042,
        SYS_MESSAGE_BOX     = 12'h043,
        SYS_GPU_FENCE       = 12'h044,
        SYS_UI_REDRAW       = 12'h045,
        SYS_UI_STATUSBAR    = 12'h046,
        SYS_UI_FILE_LIST    = 12'h047,
        SYS_UI_EDITOR_VIEW  = 12'h048,
        SYS_UI_MINER_VIEW   = 12'h049,
        SYS_UI_MONITOR_VIEW = 12'h04a,
        SYS_UI_TERMINAL_VIEW= 12'h04b,
        SYS_UI_PAINT_VIEW   = 12'h04c,
        SYS_UI_SETTINGS_VIEW= 12'h04d,
        SYS_UI_CALCULATOR_VIEW=12'h04e,

        SYS_MINER_LOG_RESULT = 12'h050,
        SYS_MINER_SAVE_STATE = 12'h051,
        SYS_MINER_LOAD_HISTORY = 12'h052,
        SYS_MINER_LOAD_STATE = 12'h053,

        SYS_ASSEMBLE        = 12'h060;

    localparam [7:0]
        EVT_KEY=8'h01,
        EVT_HOME=8'h16,
        EVT_EXIT=8'h17,
        EVT_EDITOR=8'h18,
        EVT_FILES=8'h19;

    localparam [3:0]
        VIEW_DESKTOP=0,
        VIEW_FILES=1,
        VIEW_EDITOR=2,
        VIEW_MINER=3,
        VIEW_MONITOR=4,
        VIEW_TERMINAL=5,
        VIEW_PAINT=6,
        VIEW_SETTINGS=7,
        VIEW_CALCULATOR=8,
        VIEW_GENERIC=9;

    localparam [255:0] TITLE_DESKTOP  = "SHAMAOS DESKTOP                 ";
    localparam [255:0] TITLE_FILES    = "FILE EXPLORER                   ";
    localparam [255:0] TITLE_EDITOR   = "EDITOR                          ";
    localparam [255:0] TITLE_MINER    = "BITCOIN MINER                   ";
    localparam [255:0] TITLE_MONITOR  = "SYSTEM MONITOR                  ";
    localparam [255:0] TITLE_TERMINAL = "TERMINAL                        ";
    localparam [255:0] TITLE_PAINT    = "PAINT                           ";
    localparam [255:0] TITLE_SETTINGS = "SETTINGS                        ";
    localparam [255:0] TITLE_CALCULATOR = "CALCULATOR                      ";
    localparam [255:0] TITLE_GENERIC  = "SHAMAOS                         ";

    localparam [255:0] HELP_DESKTOP  = "A OPEN  EDITOR  FILES  EXIT     ";
    localparam [255:0] HELP_FILES    = "A OPEN  B BACK  EDITOR  EXIT    ";
    localparam [255:0] HELP_EDITOR   = "KEYBOARD  SAVE  FILES  EXIT     ";
    localparam [255:0] HELP_MINER    = "A START  B STOP  RUNS  EXIT     ";
    localparam [255:0] HELP_MONITOR  = "LIVE RAM CACHE FLASH GPU CPU    ";
    localparam [255:0] HELP_TERMINAL = "TYPE COMMAND  ENTER  EXIT       ";
    localparam [255:0] HELP_PAINT    = "DPAD MOVE  A DRAW  B ERASE      ";
    localparam [255:0] HELP_SETTINGS = "A SELECT  B BACK  EXIT          ";
    localparam [255:0] HELP_CALCULATOR = "SIGNED + - * / % ENTER B CLEAR  ";
    localparam [255:0] HELP_GENERIC  = "HOME  EDITOR  FILES  EXIT       ";

    localparam [255:0] DESK_EDITOR   = "DESKTOP > EDITOR                ";
    localparam [255:0] DESK_FILES    = "DESKTOP > FILE EXPLORER         ";
    localparam [255:0] DESK_MINER    = "DESKTOP > BITCOIN MINER         ";
    localparam [255:0] DESK_MONITOR  = "DESKTOP > SYSTEM MONITOR        ";
    localparam [255:0] DESK_TERMINAL = "DESKTOP > TERMINAL              ";
    localparam [255:0] DESK_CALC     = "DESKTOP > CALCULATOR            ";
    localparam [255:0] DESK_PAINT    = "DESKTOP > PAINT                 ";
    localparam [255:0] DESK_SETTINGS = "DESKTOP > SETTINGS              ";

    localparam [255:0] FILES_DELETE  = "DELETE FILE? A YES  B NO        ";
    localparam [255:0] FILES_TEXT    = "TEXT VIEW  UP/DOWN SCROLL       ";
    localparam [255:0] FILES_RENAME  = "RENAME  TYPE NAME ENTER         ";

    localparam [255:0] EDIT_MENU     = "EDITOR > NEW TXT/PROGRAM/OPEN   ";
    localparam [255:0] EDIT_NEW_TXT  = "EDITOR > NEW TEXT NAME          ";
    localparam [255:0] EDIT_NEW_ASM  = "EDITOR > NEW PROGRAM NAME       ";
    localparam [255:0] EDIT_EDIT     = "EDITOR > EDIT  A SAVE B MENU    ";
    localparam [255:0] EDIT_ACTIONS  = "EDITOR > ACTIONS A SELECT       ";
    localparam [255:0] EDIT_DELETE   = "DELETE FILE? A YES  B NO        ";
    localparam [255:0] EDIT_RENAME   = "EDITOR > RENAME                 ";
    localparam [255:0] EDIT_SAVE_AS  = "EDITOR > SAVE AS                ";

    typedef enum logic [5:0] {
        ST_IDLE,
        ST_NOTICE_DRAIN,
        ST_EVENT_RESP,
        ST_FS,
        ST_ASM,

        ST_GFX_A0,
        ST_GFX_A1,
        ST_GFX_A2,
        ST_GFX_A3,
        ST_GFX_A4,
        ST_GFX_CMD,
        ST_GFX_SUBMIT,
        ST_GFX_FENCE_READ,
        ST_GFX_FENCE_WAIT,

        ST_UI_CURSOR_ARG, ST_UI_CURSOR_CMD, ST_UI_CURSOR_SUBMIT,
        ST_UI_CLEAR_VALUE, ST_UI_CLEAR_CMD,
        ST_UI_CLEAR_SUBMIT,

        ST_UI_ERASE_X, ST_UI_ZERO_CMD, ST_UI_ZERO_SUBMIT, ST_UI_ERASE_Y, ST_UI_ERASE_W, ST_UI_ERASE_H,
        ST_UI_ERASE_COLOR, ST_UI_ERASE_CMD, ST_UI_ERASE_SUBMIT,
        ST_UI_ONE_VALUE, ST_UI_ONE_CMD, ST_UI_ONE_SUBMIT,
        ST_UI_TITLE_TEXT,
        ST_UI_TITLE_A0,
        ST_UI_TITLE_A1,
        ST_UI_TITLE_A2,
        ST_UI_TITLE_A3,
        ST_UI_TITLE_CMD,
        ST_UI_TITLE_SUBMIT,

        ST_UI_STATUS_TEXT,
        ST_UI_STATUS_A0,
        ST_UI_STATUS_A1,
        ST_UI_STATUS_A2,
        ST_UI_STATUS_A3,
        ST_UI_STATUS_CMD,
        ST_UI_STATUS_SUBMIT,

        ST_UI_HELP_TEXT,
        ST_UI_HELP_A0,
        ST_UI_HELP_A1,
        ST_UI_HELP_A2,
        ST_UI_HELP_A3,
        ST_UI_HELP_CMD,
        ST_UI_HELP_SUBMIT,

        ST_UI_SWAP_CMD,
        ST_UI_SWAP_SUBMIT,
        ST_RESP
    } state_t;

    state_t state;
    logic [11:0] latched_id;
    logic [191:0] latched_args;
    logic [191:0] view_args;
    logic [63:0] response;
    logic response_jump;
    logic [31:0] response_pc;
    logic response_load_app;
    logic [3:0] response_app_id;

    logic [3:0] current_view;
    logic [7:0] text_index;
    logic [1:0] status_line;
    logic status_only;

    logic [7:0] gfx_opcode;
    logic [31:0] gfx_a0,gfx_a1,gfx_a2,gfx_a3,gfx_a4;
    logic [31:0] gfx_fence_target;
    logic gfx_wait_fence;
    logic popup_render,popup_visible,popup_restoring;
    logic [7:0] popup_code;

    function automatic [255:0] title_for(input logic [3:0] view);
        begin
            case(view)
                VIEW_DESKTOP: begin
                    case(view_args[3:0])
                        4'd1:title_for=DESK_EDITOR;
                        4'd2:title_for=DESK_FILES;
                        4'd3:title_for=DESK_MINER;
                        4'd4:title_for=DESK_MONITOR;
                        4'd5:title_for=DESK_TERMINAL;
                        4'd6:title_for=DESK_CALC;
                        4'd7:title_for=DESK_PAINT;
                        4'd8:title_for=DESK_SETTINGS;
                        default:title_for=TITLE_DESKTOP;
                    endcase
                end
                VIEW_FILES: begin
                    case(view_args[35:32])
                        4'd1:title_for=view_args[64] ? "DELETE > CONFIRM   B CANCEL     " : "DELETE > CANCEL   A SELECT      ";
                        4'd2:title_for=FILES_TEXT;
                        4'd3:title_for=FILES_RENAME;
                        4'd4:title_for="ERROR - A/B BACK; FILE PRESERVED";
                        4'd5:title_for="NEW FOLDER - TYPE PATH ENTER    ";
                        default:title_for=TITLE_FILES;
                    endcase
                end
                VIEW_EDITOR: begin
                    case(view_args[3:0])
                        4'd0:case(view_args[35:32])
                            0:title_for="EDITOR > NEW TEXT               ";
                            1:title_for="EDITOR > NEW PROGRAM            ";
                            default:title_for="EDITOR > OPEN FILE              ";
                        endcase
                        4'd1:title_for=EDIT_NEW_TXT;
                        4'd2:title_for=EDIT_NEW_ASM;
                        4'd3:title_for=EDIT_EDIT;
                        4'd4:case(view_args[35:32])
                            0:title_for="ACTION > SAVE                   ";
                            1:title_for="ACTION > SAVE AS                ";
                            2:title_for="ACTION > RENAME                 ";
                            3:title_for="ACTION > DELETE                 ";
                            4:title_for="ACTION > ASSEMBLE               ";
                            5:title_for="ACTION > ASSEMBLE AND RUN       ";
                            6:title_for="ACTION > NEW TEXT               ";
                            7:title_for="ACTION > NEW PROGRAM            ";
                            default:title_for="ACTION > EXIT                   ";
                        endcase
                        4'd5:title_for=view_args[32] ? "DELETE > CONFIRM   B CANCEL     " : "DELETE > CANCEL   A SELECT      ";
                        4'd6:title_for=EDIT_RENAME;
                        4'd7:title_for=EDIT_SAVE_AS;
                        4'd8:title_for="ERROR - A/B BACK; FILE PRESERVED";
                        default:title_for=TITLE_EDITOR;
                    endcase
                end
                VIEW_MINER:title_for=TITLE_MINER;
                VIEW_MONITOR:title_for=TITLE_MONITOR;
                VIEW_TERMINAL:title_for=TITLE_TERMINAL;
                VIEW_PAINT:title_for=TITLE_PAINT;
                VIEW_SETTINGS:case(view_args[3:0])
                    0:title_for="SETTINGS > CURSOR VISIBILITY    ";
                    1:title_for="SETTINGS > CURSOR SHAPE         ";
                    2:title_for="SETTINGS > SAVE                 ";
                    3:title_for="SETTINGS > SYSTEM MONITOR       ";
                    default:title_for="SETTINGS > EXIT                 ";
                endcase
                VIEW_CALCULATOR:begin
                    if(view_args[95:64]!=0) title_for="CALCULATOR > ERROR              ";
                    else case(view_args[1:0])
                        1:title_for="CALCULATOR > SECOND OPERAND     ";
                        2:title_for="CALCULATOR > RESULT             ";
                        default:title_for="CALCULATOR > FIRST OPERAND      ";
                    endcase
                end
                default:title_for=TITLE_GENERIC;
            endcase
        end
    endfunction

    function automatic [255:0] help_for(input logic [3:0] view);
        begin
            case(view)
                VIEW_DESKTOP:help_for=HELP_DESKTOP;
                VIEW_FILES:begin
                    if(view_args[35:32]==4)
                        help_for=view_args[103:96]==9 ? "FILE TOO LARGE - NOT MODIFIED   " : "FILE OPERATION FAILED           ";
                    else if(view_args[35:32]==0) help_for="A OPEN LEFT EDIT RIGHT DELETE  ";
                    else help_for=HELP_FILES;
                end
                VIEW_EDITOR:begin
                    if(view_args[3:0]==8) begin
                        case(view_args[71:64])
                            1:help_for="SOURCE OR OUTPUT RANGE INVALID  ";
                            2:help_for="INVALID TOKEN OR NUMBER         ";
                            3:help_for="DUPLICATE LABEL OR SYMBOL LIMIT ";
                            4:help_for="INVALID OR DUPLICATE DEFINE     ";
                            5:help_for="UNKNOWN INSTRUCTION             ";
                            6:help_for="BAD OPERANDS OR UNDEFINED NAME  ";
                            7:help_for="PROGRAM EXCEEDS EXECUTABLE SPACE";
                            9:help_for="TEXT CAPACITY REACHED           ";
                            10:help_for="SAVE A NAMED ASM PROGRAM FIRST  ";
                            11:help_for="BIN NAME OR TYPE CONFLICT       ";
                            default:help_for="FILE OPERATION FAILED           ";
                        endcase
                    end else help_for=HELP_EDITOR;
                end
                VIEW_MINER:help_for=HELP_MINER;
                VIEW_MONITOR:help_for=HELP_MONITOR;
                VIEW_TERMINAL:help_for=view_args[0]?"UNKNOWN COMMAND - LS EDIT MEM   ":HELP_TERMINAL;
                VIEW_PAINT:help_for=HELP_PAINT;
                VIEW_SETTINGS:begin
                    if(view_args[64]) help_for="SAVE FAILED - SELECT SAVE RETRY ";
                    else case(view_args[33:32])
                        0:help_for="CURSOR OFF CROSS  A TOGGLE      ";
                        1:help_for="CURSOR ON CROSS   A TOGGLE      ";
                        2:help_for="CURSOR OFF POINT  A TOGGLE      ";
                        default:help_for="CURSOR ON POINT   A TOGGLE      ";
                    endcase
                end
                VIEW_CALCULATOR:begin
                    case(view_args[95:64])
                        1:help_for="ZERO DIVISOR - BACKSPACE/B CLEAR";
                        2:help_for="OVERFLOW - BACKSPACE OR B CLEAR ";
                        default:help_for=HELP_CALCULATOR;
                    endcase
                end
                default:help_for=HELP_GENERIC;
            endcase
        end
    endfunction

    function automatic [7:0] packed_char(
        input [255:0] text_pack,
        input integer index
    );
        packed_char = text_pack >> ((31-index)*8);
    endfunction

    function automatic [7:0] hex_char(input logic [3:0] nib);
        hex_char = (nib < 10) ? ("0"+nib) : ("A"+(nib-10));
    endfunction

    function automatic [7:0] monitor_char(input integer index);
        integer digit;
        begin
            case(index)
                0:monitor_char="C";1:monitor_char="Y";2:monitor_char="C";3:monitor_char=" ";
                12:monitor_char=" ";13:monitor_char="I";14:monitor_char="N";15:monitor_char="S";
                16:monitor_char="T";17:monitor_char=" ";
                26:monitor_char=" ";27:monitor_char="C";28:monitor_char=cpu_halted?    "H":"R";
                29:monitor_char=sha_busy?"B":"I";30:monitor_char="G";31:monitor_char=gpu_busy?      "B":"I";
                default:begin
                    if(index>=4 && index<12) begin
                        digit=11-index;
                        monitor_char=hex_char(view_args[digit*4 +: 4]);
                    end else if(index>=18 && index<26) begin
                        digit=25-index;
                        monitor_char=hex_char(view_args[32+digit*4 +: 4]);
                    end else monitor_char=" ";
                end
            endcase
        end
    endfunction

    function automatic [7:0] status_char(input integer index);
        logic [31:0] used,total;
        logic [63:0] label;
        begin
            case(status_line)
                0:begin used=ram_used_bytes;total=`SHAMA_RAM_BYTES;label="RAM     ";end
                1:begin used=cache_used_bytes;total=`SHAMA_CACHE_BYTES;label="FAST    ";end
                default:begin used=flash_used_bytes;total=`SHAMA_FLASH_BYTES;label="FLASH   ";end
            endcase
            if(index<8) status_char=label >> ((7-index)*8);
            else if(index<16) status_char=hex_char(used[(15-index)*4 +: 4]);
            else if(index==16) status_char="/";
            else if(index<25) status_char=hex_char(total[(24-index)*4 +: 4]);
            else if(index==26) status_char="B";
            else if(index==28) status_char="H";
            else if(index==29) status_char="E";
            else if(index==30) status_char="X";
            else status_char=" ";
        end
    endfunction

    function automatic logic is_file_sys(input logic [11:0] id);
        is_file_sys =
            (id>=SYS_FILE_CREATE && id<=SYS_FS_SYNC) ||
            (id>=SYS_MINER_LOG_RESULT && id<=SYS_MINER_LOAD_STATE) ||
            (id==SYS_BOOT_MOUNT);
    endfunction

    function automatic logic is_gpu_sys(input logic [11:0] id);
        is_gpu_sys =
            id==SYS_GPU_SUBMIT || id==SYS_DRAW_TEXT ||
            id==SYS_DRAW_RECT || id==SYS_MESSAGE_BOX ||
            id==SYS_GPU_FENCE;
    endfunction

    function automatic logic is_ui_sys(input logic [11:0] id);
        is_ui_sys = (id>=SYS_UI_REDRAW && id<=SYS_UI_CALCULATOR_VIEW);
    endfunction

    function automatic [3:0] view_for_sys(input logic [11:0] id,input logic [3:0] oldview);
        begin
            case(id)
                SYS_UI_FILE_LIST:view_for_sys=VIEW_FILES;
                SYS_UI_EDITOR_VIEW:view_for_sys=VIEW_EDITOR;
                SYS_UI_MINER_VIEW:view_for_sys=VIEW_MINER;
                SYS_UI_MONITOR_VIEW:view_for_sys=VIEW_MONITOR;
                SYS_UI_TERMINAL_VIEW:view_for_sys=VIEW_TERMINAL;
                SYS_UI_PAINT_VIEW:view_for_sys=VIEW_PAINT;
                SYS_UI_SETTINGS_VIEW:view_for_sys=VIEW_SETTINGS;
                SYS_UI_CALCULATOR_VIEW:view_for_sys=VIEW_CALCULATOR;
                SYS_UI_REDRAW:view_for_sys=VIEW_DESKTOP;
                default:view_for_sys=oldview;
            endcase
        end
    endfunction

    always_comb begin
        req_ready=(state==ST_RESP);
        req_ret=response;
        req_jump_valid=(state==ST_RESP)&&response_jump;
        req_jump_pc=response_pc;
        req_load_app=(state==ST_RESP)&&response_load_app;
        req_app_id=response_app_id;
        notice_busy=popup_render || popup_visible || popup_restoring;
        notice_ready=popup_visible && !popup_render && !popup_restoring && popup_code==notice_code;

        fs_valid=(state==ST_FS);
        fs_id=latched_id;
        fs_args=latched_args;

        asm_valid=(state==ST_ASM);
        asm_args=latched_args;

        gpu_valid=1'b0;
        gpu_we=1'b1;
        gpu_addr=0;
        gpu_wdata=0;
        gpu_wstrb=4'b1111;

        case(state)
            ST_GFX_A0: begin gpu_valid=1;gpu_addr=12'h008;gpu_wdata=gfx_a0;end
            ST_GFX_A1: begin gpu_valid=1;gpu_addr=12'h00c;gpu_wdata=gfx_a1;end
            ST_GFX_A2: begin gpu_valid=1;gpu_addr=12'h010;gpu_wdata=gfx_a2;end
            ST_GFX_A3: begin gpu_valid=1;gpu_addr=12'h014;gpu_wdata=gfx_a3;end
            ST_GFX_A4: begin gpu_valid=1;gpu_addr=12'h018;gpu_wdata=gfx_a4;end
            ST_GFX_CMD: begin gpu_valid=1;gpu_addr=12'h004;gpu_wdata={24'd0,gfx_opcode};end
            ST_GFX_SUBMIT: begin gpu_valid=1;gpu_addr=12'h028;gpu_wdata=1;end
            ST_GFX_FENCE_READ,ST_GFX_FENCE_WAIT: begin
                gpu_valid=1;gpu_we=0;gpu_addr=12'h030;gpu_wdata=0;
            end

            ST_UI_CURSOR_ARG:begin gpu_valid=1;gpu_addr=12'h010;gpu_wdata=0;end
            ST_UI_CURSOR_CMD:begin gpu_valid=1;gpu_addr=12'h004;gpu_wdata=8'h12;end
            ST_UI_CURSOR_SUBMIT:begin gpu_valid=1;gpu_addr=12'h028;gpu_wdata=1;end
            ST_UI_CLEAR_VALUE: begin gpu_valid=1;gpu_addr=12'h008;gpu_wdata=0;end
            ST_UI_CLEAR_CMD: begin gpu_valid=1;gpu_addr=12'h004;gpu_wdata=status_only?32'h13:32'h01;end
            ST_UI_CLEAR_SUBMIT: begin gpu_valid=1;gpu_addr=12'h028;gpu_wdata=1;end

            ST_UI_ERASE_X: begin gpu_valid=1;gpu_addr=12'h008;gpu_wdata=0;end
            ST_UI_ZERO_CMD: begin gpu_valid=1;gpu_addr=12'h004;gpu_wdata=8'h11;end
            ST_UI_ZERO_SUBMIT: begin gpu_valid=1;gpu_addr=12'h028;gpu_wdata=1;end
            ST_UI_ONE_VALUE: begin gpu_valid=1;gpu_addr=12'h008;gpu_wdata=1;end
            ST_UI_ONE_CMD: begin gpu_valid=1;gpu_addr=12'h004;gpu_wdata=8'h11;end
            ST_UI_ONE_SUBMIT: begin gpu_valid=1;gpu_addr=12'h028;gpu_wdata=1;end
            ST_UI_ERASE_Y: begin gpu_valid=1;gpu_addr=12'h00c;gpu_wdata=140;end
            ST_UI_ERASE_W: begin gpu_valid=1;gpu_addr=12'h010;gpu_wdata=320;end
            ST_UI_ERASE_H: begin gpu_valid=1;gpu_addr=12'h014;gpu_wdata=40;end
            ST_UI_ERASE_COLOR: begin gpu_valid=1;gpu_addr=12'h018;gpu_wdata=0;end
            ST_UI_ERASE_CMD: begin gpu_valid=1;gpu_addr=12'h004;gpu_wdata=8'h08;end
            ST_UI_ERASE_SUBMIT: begin gpu_valid=1;gpu_addr=12'h028;gpu_wdata=1;end

            ST_UI_TITLE_TEXT: begin
                gpu_valid=1;gpu_addr=(12'h400+text_index)&12'hffc;
                gpu_wstrb=4'b0001 << text_index[1:0];
                gpu_wdata={24'd0,packed_char(title_for(current_view),text_index)}
                          << (text_index[1:0]*8);
            end
            ST_UI_TITLE_A0: begin gpu_valid=1;gpu_addr=12'h008;gpu_wdata=0;end
            ST_UI_TITLE_A1: begin gpu_valid=1;gpu_addr=12'h00c;gpu_wdata=32;end
            ST_UI_TITLE_A2: begin gpu_valid=1;gpu_addr=12'h010;gpu_wdata=4;end
            ST_UI_TITLE_A3: begin gpu_valid=1;gpu_addr=12'h014;gpu_wdata=4;end
            ST_UI_TITLE_CMD: begin gpu_valid=1;gpu_addr=12'h004;gpu_wdata=32'h0c;end
            ST_UI_TITLE_SUBMIT: begin gpu_valid=1;gpu_addr=12'h028;gpu_wdata=1;end

            ST_UI_STATUS_TEXT: begin
                gpu_valid=1;gpu_addr=(12'h420+{status_line,5'b0}+text_index)&12'hffc;
                gpu_wstrb=4'b0001 << text_index[1:0];
                gpu_wdata={24'd0,status_char(text_index)}
                          << (text_index[1:0]*8);
            end
            ST_UI_STATUS_A0: begin gpu_valid=1;gpu_addr=12'h008;gpu_wdata=32+{status_line,5'b0};end
            ST_UI_STATUS_A1: begin gpu_valid=1;gpu_addr=12'h00c;gpu_wdata=32;end
            ST_UI_STATUS_A2: begin gpu_valid=1;gpu_addr=12'h010;gpu_wdata=4;end
            ST_UI_STATUS_A3: begin gpu_valid=1;gpu_addr=12'h014;gpu_wdata=144+status_line*12;end
            ST_UI_STATUS_CMD: begin gpu_valid=1;gpu_addr=12'h004;gpu_wdata=32'h0c;end
            ST_UI_STATUS_SUBMIT: begin gpu_valid=1;gpu_addr=12'h028;gpu_wdata=1;end

            ST_UI_HELP_TEXT: begin
                gpu_valid=1;gpu_addr=(12'h480+text_index)&12'hffc;
                gpu_wstrb=4'b0001 << text_index[1:0];
                if(current_view==VIEW_MONITOR)
                    gpu_wdata={24'd0,monitor_char(text_index)}
                              << (text_index[1:0]*8);
                else
                    gpu_wdata={24'd0,packed_char(help_for(current_view),text_index)}
                              << (text_index[1:0]*8);
            end
            ST_UI_HELP_A0: begin gpu_valid=1;gpu_addr=12'h008;gpu_wdata=128;end
            ST_UI_HELP_A1: begin gpu_valid=1;gpu_addr=12'h00c;gpu_wdata=32;end
            ST_UI_HELP_A2: begin gpu_valid=1;gpu_addr=12'h010;gpu_wdata=4;end
            ST_UI_HELP_A3: begin gpu_valid=1;gpu_addr=12'h014;gpu_wdata=28;end
            ST_UI_HELP_CMD: begin gpu_valid=1;gpu_addr=12'h004;gpu_wdata=32'h0c;end
            ST_UI_HELP_SUBMIT: begin gpu_valid=1;gpu_addr=12'h028;gpu_wdata=1;end

            ST_UI_SWAP_CMD: begin gpu_valid=1;gpu_addr=12'h004;gpu_wdata=32'h14;end
            ST_UI_SWAP_SUBMIT: begin gpu_valid=1;gpu_addr=12'h028;gpu_wdata=1;end
            default: begin end
        endcase
    end

    task automatic advance_gpu(input state_t next_state);
        if(gpu_ready) state<=next_state;
    endtask

    always_ff @(posedge clk) begin
        if(rst) begin
            state<=ST_IDLE;
            latched_id<=0;latched_args<=0;view_args<=0;
            response<=0;response_jump<=0;response_pc<=0;
            response_load_app<=0;response_app_id<=0;
            current_view<=VIEW_DESKTOP;
            text_index<=0;status_line<=0;status_only<=0;
            gfx_opcode<=0;gfx_a0<=0;gfx_a1<=0;gfx_a2<=0;gfx_a3<=0;gfx_a4<=0;
            gfx_fence_target<=0;gfx_wait_fence<=0;
            popup_render<=0;popup_visible<=0;popup_restoring<=0;
            popup_code<=0;
        end else begin
            if(state==ST_RESP && !req_valid) begin
                state<=ST_IDLE;
                response_jump<=0;
                response_load_app<=0;
            end

            if(gpu_capacity_fault && state!=ST_IDLE && state!=ST_FS && state!=ST_ASM && state!=ST_RESP &&
               !popup_render && !popup_visible && !popup_restoring) begin
                // A rejected resource command may have discarded a queued
                // fence. Release its caller so OS recovery can acknowledge it.
                response<=64'hfffffffffffffff6;gfx_wait_fence<=0;state<=ST_RESP;
            end else case(state)
                ST_IDLE: if(popup_visible && notice_code==0) begin
                    popup_restoring<=1;gfx_opcode<=8'h18;gfx_a0<=0;
                    gfx_a1<=0;gfx_a2<=0;gfx_a3<=0;gfx_a4<=0;
                    gfx_wait_fence<=0;state<=ST_NOTICE_DRAIN;
                end else if(notice_code!=0 && (!popup_visible || popup_code!=notice_code)) begin
                    // The notice is a scanout-only overlay. Both app frames,
                    // drawing resources, clip/color/font and cursor remain
                    // untouched, including a not-yet-presented back frame.
                    popup_render<=1;popup_code<=notice_code;gfx_opcode<=8'h18;gfx_a0<={24'd0,notice_code};
                    gfx_a1<=0;gfx_a2<=0;gfx_a3<=0;gfx_a4<=0;
                    gfx_wait_fence<=0;state<=ST_NOTICE_DRAIN;
                end else if(req_valid) begin
                    latched_id<=req_id;
                    latched_args<=req_args;
                    if(is_ui_sys(req_id) && req_id!=SYS_UI_STATUSBAR)
                        view_args<=req_args;
                    response<=0;response_jump<=0;response_pc<=0;
                    response_load_app<=0;response_app_id<=0;

                    if(req_id==SYS_APP_EVENT) begin
                        case(req_args[17:10])
                            EVT_HOME,EVT_EXIT: begin
                                // Bit 63 asks shama_services to flush the
                                // foreground slot and load app ID 0 (Desktop).
                                response_load_app<=1;
                                response_app_id<=0;
                                current_view<=VIEW_DESKTOP;
                            end
                            EVT_EDITOR: begin
                                response_load_app<=1;
                                response_app_id<=1;
                                current_view<=VIEW_EDITOR;
                            end
                            EVT_FILES: begin
                                response_load_app<=1;
                                response_app_id<=2;
                                current_view<=VIEW_FILES;
                            end
                            default: begin end
                        endcase
                        state<=ST_RESP;
                    end else if(is_gpu_sys(req_id)) begin
                        gfx_wait_fence<=0;
                        gfx_a0<=0;gfx_a1<=0;gfx_a2<=0;gfx_a3<=0;gfx_a4<=0;

                        if(req_id==SYS_GPU_SUBMIT) begin
                            gfx_opcode<=req_args[7:0];
                            gfx_a0<=req_args[63:32];
                            gfx_a1<=req_args[95:64];
                            gfx_a2<=req_args[127:96];
                            gfx_a3<=req_args[159:128];
                            gfx_a4<=req_args[191:160];
                            state<=ST_GFX_A0;
                        end else if(req_id==SYS_DRAW_TEXT || req_id==SYS_MESSAGE_BOX) begin
                            gfx_opcode<=8'h0c;
                            gfx_a0<=req_args[31:0];
                            gfx_a1<=req_args[63:32];
                            gfx_a2<=req_args[95:64];
                            gfx_a3<=req_args[127:96];
                            state<=ST_GFX_A0;
                        end else if(req_id==SYS_DRAW_RECT) begin
                            gfx_opcode<=req_args[128] ? 8'h08 : 8'h07;
                            gfx_a0<=req_args[31:0];
                            gfx_a1<=req_args[63:32];
                            gfx_a2<=req_args[95:64];
                            gfx_a3<=req_args[127:96];
                            state<=ST_GFX_A0;
                        end else begin
                            state<=ST_GFX_FENCE_READ;
                        end                    end else if(is_file_sys(req_id)) begin
                        state<=ST_FS;
                    end else if(req_id==SYS_ASSEMBLE) begin
                        state<=ST_ASM;
                    end else if(is_ui_sys(req_id)) begin
                        current_view<=view_for_sys(req_id,current_view);
                        status_only<=(req_id==SYS_UI_STATUSBAR);status_line<=0;
                        text_index<=0;
                        state<=(req_id==SYS_UI_STATUSBAR)?ST_UI_CLEAR_VALUE:ST_UI_CURSOR_ARG;
                    end else begin
                        // Low-level GPU convenience calls are intentionally
                        // available, but normal ShamaOS apps use UI views.
                        response<=0;
                        state<=ST_RESP;
                    end
                end

                ST_NOTICE_DRAIN:if(!gpu_busy) begin
                    // An older app command may reject a resource and discard
                    // the entire queued suffix, including an early NOTICE.
                    // Drain that work before submitting any show/update/hide,
                    // then fence the notice itself before declaring it ready.
                    // Re-read the requested choice: it may have changed or
                    // been cancelled while the app's GPU work was draining.
                    popup_code<=notice_code;gfx_a0<={24'd0,notice_code};
                    if(notice_code==0 && !popup_visible) begin
                        popup_render<=0;popup_restoring<=0;state<=ST_IDLE;
                    end else begin
                        popup_render<=notice_code!=0;
                        popup_restoring<=notice_code==0;
                        state<=ST_GFX_A0;
                    end
                end

                ST_GFX_A0: advance_gpu(ST_GFX_A1);
                ST_GFX_A1: advance_gpu(ST_GFX_A2);
                ST_GFX_A2: advance_gpu(ST_GFX_A3);
                ST_GFX_A3: advance_gpu(ST_GFX_A4);
                ST_GFX_A4: advance_gpu(ST_GFX_CMD);
                ST_GFX_CMD: advance_gpu(ST_GFX_SUBMIT);
                ST_GFX_SUBMIT: begin
                    if(gpu_ready) begin
                        if(gfx_wait_fence)
                            state<=ST_GFX_FENCE_WAIT;
                        else if(popup_render || popup_restoring)
                            state<=ST_GFX_FENCE_READ;
                        else begin
                            response<=0;
                            state<=ST_RESP;
                        end
                    end
                end

                ST_GFX_FENCE_READ: begin
                    // A previously queued raw FENCE must not satisfy this
                    // syscall while work after it is still running. Drain the
                    // existing queue before selecting our completion number.
                    if(gpu_ready && !gpu_busy) begin
                        gfx_fence_target<=gpu_rdata+1'b1;
                        gfx_opcode<=8'h16;
                        gfx_wait_fence<=1'b1;
                        state<=ST_GFX_CMD;
                    end
                end

                ST_GFX_FENCE_WAIT: begin
                    // Exactly one fence was queued after the idle baseline;
                    // equality also handles FFFFFFFF -> 00000000 correctly.
                    if(gpu_ready && gpu_rdata==gfx_fence_target) begin
                        response<=gpu_rdata;
                        gfx_wait_fence<=0;
                        if(popup_render) begin popup_render<=0;popup_visible<=1;state<=ST_IDLE;end
                        else if(popup_restoring) begin popup_restoring<=0;popup_visible<=0;state<=ST_IDLE;end
                        else state<=ST_RESP;
                    end
                end

                ST_FS: if(fs_ready) begin
                    response<=fs_ret;
                    state<=ST_RESP;
                end

                ST_ASM: if(asm_ready) begin
                    response<=asm_ret;
                    state<=ST_RESP;
                end

                ST_UI_CURSOR_ARG:advance_gpu(ST_UI_CURSOR_CMD);
                ST_UI_CURSOR_CMD:advance_gpu(ST_UI_CURSOR_SUBMIT);
                ST_UI_CURSOR_SUBMIT:advance_gpu(ST_UI_CLEAR_VALUE);
                ST_UI_CLEAR_VALUE: advance_gpu(ST_UI_CLEAR_CMD);
                ST_UI_CLEAR_CMD: advance_gpu(ST_UI_CLEAR_SUBMIT);
                ST_UI_CLEAR_SUBMIT: begin
                    if(gpu_ready) begin
                        text_index<=0;
                        state<=status_only?ST_UI_ERASE_X:ST_UI_ONE_VALUE;
                    end
                end
                ST_UI_ERASE_X:advance_gpu(ST_UI_ZERO_CMD);
                ST_UI_ZERO_CMD:advance_gpu(ST_UI_ZERO_SUBMIT);
                ST_UI_ZERO_SUBMIT:advance_gpu(ST_UI_ERASE_Y);
                ST_UI_ERASE_Y:advance_gpu(ST_UI_ERASE_W);
                ST_UI_ERASE_W:advance_gpu(ST_UI_ERASE_H);
                ST_UI_ERASE_H:advance_gpu(ST_UI_ERASE_COLOR);
                ST_UI_ERASE_COLOR:advance_gpu(ST_UI_ERASE_CMD);
                ST_UI_ERASE_CMD:advance_gpu(ST_UI_ERASE_SUBMIT);
                ST_UI_ERASE_SUBMIT:advance_gpu(ST_UI_ONE_VALUE);
                ST_UI_ONE_VALUE:advance_gpu(ST_UI_ONE_CMD);
                ST_UI_ONE_CMD:advance_gpu(ST_UI_ONE_SUBMIT);
                ST_UI_ONE_SUBMIT:if(gpu_ready) state<=status_only?ST_UI_STATUS_TEXT:ST_UI_TITLE_TEXT;

                ST_UI_TITLE_TEXT: if(gpu_ready) begin
                    if(text_index==31) begin text_index<=0;state<=ST_UI_TITLE_A0;end
                    else text_index<=text_index+1'b1;
                end
                ST_UI_TITLE_A0: advance_gpu(ST_UI_TITLE_A1);
                ST_UI_TITLE_A1: advance_gpu(ST_UI_TITLE_A2);
                ST_UI_TITLE_A2: advance_gpu(ST_UI_TITLE_A3);
                ST_UI_TITLE_A3: advance_gpu(ST_UI_TITLE_CMD);
                ST_UI_TITLE_CMD: advance_gpu(ST_UI_TITLE_SUBMIT);
                ST_UI_TITLE_SUBMIT: begin if(gpu_ready)begin text_index<=0;state<=ST_UI_STATUS_TEXT;end end

                ST_UI_STATUS_TEXT: if(gpu_ready) begin
                    if(text_index==31) begin text_index<=0;state<=ST_UI_STATUS_A0;end
                    else text_index<=text_index+1'b1;
                end
                ST_UI_STATUS_A0: advance_gpu(ST_UI_STATUS_A1);
                ST_UI_STATUS_A1: advance_gpu(ST_UI_STATUS_A2);
                ST_UI_STATUS_A2: advance_gpu(ST_UI_STATUS_A3);
                ST_UI_STATUS_A3: advance_gpu(ST_UI_STATUS_CMD);
                ST_UI_STATUS_CMD: advance_gpu(ST_UI_STATUS_SUBMIT);
                ST_UI_STATUS_SUBMIT: if(gpu_ready) begin
                    text_index<=0;
                    if(status_line<2) begin status_line<=status_line+1'b1;state<=ST_UI_STATUS_TEXT;end
                    else state<=status_only?ST_UI_SWAP_CMD:ST_UI_HELP_TEXT;
                end

                ST_UI_HELP_TEXT: if(gpu_ready) begin
                    if(text_index==31) begin text_index<=0;state<=ST_UI_HELP_A0;end
                    else text_index<=text_index+1'b1;
                end
                ST_UI_HELP_A0: advance_gpu(ST_UI_HELP_A1);
                ST_UI_HELP_A1: advance_gpu(ST_UI_HELP_A2);
                ST_UI_HELP_A2: advance_gpu(ST_UI_HELP_A3);
                ST_UI_HELP_A3: advance_gpu(ST_UI_HELP_CMD);
                ST_UI_HELP_CMD: advance_gpu(ST_UI_HELP_SUBMIT);
                ST_UI_HELP_SUBMIT: advance_gpu(ST_UI_SWAP_CMD);

                ST_UI_SWAP_CMD: advance_gpu(ST_UI_SWAP_SUBMIT);
                ST_UI_SWAP_SUBMIT: begin
                    if(gpu_ready) begin response<=0;state<=ST_GFX_FENCE_READ;end
                end

                ST_RESP: begin end
                default: state<=ST_IDLE;
            endcase
        end
    end
endmodule
