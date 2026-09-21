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
    localparam [255:0] HELP_CALCULATOR = "TYPE NUMBERS + - * / ENTER      ";
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
        ST_EVENT_RESP,
        ST_FS,
        ST_ASM,

        ST_UI_CLEAR_CMD,
        ST_UI_CLEAR_SUBMIT,

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
    logic [63:0] response;
    logic response_jump;
    logic [31:0] response_pc;
    logic response_load_app;
    logic [3:0] response_app_id;

    logic [3:0] current_view;
    logic [7:0] text_index;

    function automatic [255:0] title_for(input logic [3:0] view);
        begin
            case(view)
                VIEW_DESKTOP: begin
                    case(latched_args[3:0])
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
                    case(latched_args[35:32])
                        4'd1:title_for=FILES_DELETE;
                        4'd2:title_for=FILES_TEXT;
                        default:title_for=TITLE_FILES;
                    endcase
                end
                VIEW_EDITOR: begin
                    case(latched_args[3:0])
                        4'd0:title_for=EDIT_MENU;
                        4'd1:title_for=EDIT_NEW_TXT;
                        4'd2:title_for=EDIT_NEW_ASM;
                        4'd3:title_for=EDIT_EDIT;
                        4'd4:title_for=EDIT_ACTIONS;
                        4'd5:title_for=EDIT_DELETE;
                        4'd6:title_for=EDIT_RENAME;
                        4'd7:title_for=EDIT_SAVE_AS;
                        default:title_for=TITLE_EDITOR;
                    endcase
                end
                VIEW_MINER:title_for=TITLE_MINER;
                VIEW_MONITOR:title_for=TITLE_MONITOR;
                VIEW_TERMINAL:title_for=TITLE_TERMINAL;
                VIEW_PAINT:title_for=TITLE_PAINT;
                VIEW_SETTINGS:title_for=TITLE_SETTINGS;
                VIEW_CALCULATOR:title_for=TITLE_CALCULATOR;
                default:title_for=TITLE_GENERIC;
            endcase
        end
    endfunction

    function automatic [255:0] help_for(input logic [3:0] view);
        begin
            case(view)
                VIEW_DESKTOP:help_for=HELP_DESKTOP;
                VIEW_FILES:help_for=HELP_FILES;
                VIEW_EDITOR:help_for=HELP_EDITOR;
                VIEW_MINER:help_for=HELP_MINER;
                VIEW_MONITOR:help_for=HELP_MONITOR;
                VIEW_TERMINAL:help_for=HELP_TERMINAL;
                VIEW_PAINT:help_for=HELP_PAINT;
                VIEW_SETTINGS:help_for=HELP_SETTINGS;
                VIEW_CALCULATOR:help_for=HELP_CALCULATOR;
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
                26:monitor_char=" ";27:monitor_char="C";28:monitor_char=cpu_halted?"H":"R";
                29:monitor_char=sha_busy?"B":"I";30:monitor_char="G";31:monitor_char=gpu_busy?"B":"I";
                default:begin
                    if(index>=4 && index<12) begin
                        digit=11-index;
                        monitor_char=hex_char(latched_args[digit*4 +: 4]);
                    end else if(index>=18 && index<26) begin
                        digit=25-index;
                        monitor_char=hex_char(latched_args[32+digit*4 +: 4]);
                    end else monitor_char=" ";
                end
            endcase
        end
    endfunction

    function automatic [7:0] status_char(input integer index);
        integer digit;
        begin
            case(index)
                0:status_char="R";1:status_char="A";2:status_char="M";3:status_char=" ";
                12:status_char=" ";13:status_char="C";14:status_char="A";15:status_char="C";
                16:status_char="H";17:status_char="E";18:status_char=" ";27:status_char=" ";
                28:status_char="F";29:status_char="L";30:status_char="A";31:status_char="S";
                32:status_char="H";33:status_char=" ";
                default: begin
                    if(index>=4 && index<12) begin
                        digit=11-index;
                        status_char=hex_char(ram_used_bytes[digit*4 +: 4]);
                    end else if(index>=19 && index<27) begin
                        digit=26-index;
                        status_char=hex_char(cache_used_bytes[digit*4 +: 4]);
                    end else if(index>=34 && index<42) begin
                        digit=41-index;
                        status_char=hex_char(flash_used_bytes[digit*4 +: 4]);
                    end else status_char=" ";
                end
            endcase
        end
    endfunction

    function automatic logic is_file_sys(input logic [11:0] id);
        is_file_sys =
            (id>=SYS_FILE_CREATE && id<=SYS_FLASH_USAGE) ||
            (id>=SYS_MINER_LOG_RESULT && id<=SYS_MINER_LOAD_HISTORY) ||
            (id==SYS_BOOT_MOUNT);
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
            ST_UI_CLEAR_CMD: begin gpu_valid=1;gpu_addr=12'h004;gpu_wdata=32'h01;end
            ST_UI_CLEAR_SUBMIT: begin gpu_valid=1;gpu_addr=12'h028;gpu_wdata=1;end

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
                gpu_valid=1;gpu_addr=(12'h440+text_index)&12'hffc;
                gpu_wstrb=4'b0001 << text_index[1:0];
                gpu_wdata={24'd0,status_char(text_index)}
                          << (text_index[1:0]*8);
            end
            ST_UI_STATUS_A0: begin gpu_valid=1;gpu_addr=12'h008;gpu_wdata=64;end
            ST_UI_STATUS_A1: begin gpu_valid=1;gpu_addr=12'h00c;gpu_wdata=42;end
            ST_UI_STATUS_A2: begin gpu_valid=1;gpu_addr=12'h010;gpu_wdata=4;end
            ST_UI_STATUS_A3: begin gpu_valid=1;gpu_addr=12'h014;gpu_wdata=16;end
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
            latched_id<=0;latched_args<=0;
            response<=0;response_jump<=0;response_pc<=0;
            response_load_app<=0;response_app_id<=0;
            current_view<=VIEW_DESKTOP;
            text_index<=0;
        end else begin
            if(state==ST_RESP && !req_valid) begin
                state<=ST_IDLE;
                response_jump<=0;
                response_load_app<=0;
            end

            case(state)
                ST_IDLE: if(req_valid) begin
                    latched_id<=req_id;
                    latched_args<=req_args;
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
                    end else if(is_file_sys(req_id)) begin
                        state<=ST_FS;
                    end else if(req_id==SYS_ASSEMBLE) begin
                        state<=ST_ASM;
                    end else if(is_ui_sys(req_id)) begin
                        current_view<=view_for_sys(req_id,current_view);
                        text_index<=0;
                        state<=ST_UI_CLEAR_CMD;
                    end else begin
                        // Low-level GPU convenience calls are intentionally
                        // available, but normal ShamaOS apps use UI views.
                        response<=0;
                        state<=ST_RESP;
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

                ST_UI_CLEAR_CMD: advance_gpu(ST_UI_CLEAR_SUBMIT);
                ST_UI_CLEAR_SUBMIT: begin if(gpu_ready)begin text_index<=0;state<=ST_UI_TITLE_TEXT;end end

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
                    if(text_index==41) begin text_index<=0;state<=ST_UI_STATUS_A0;end
                    else text_index<=text_index+1'b1;
                end
                ST_UI_STATUS_A0: advance_gpu(ST_UI_STATUS_A1);
                ST_UI_STATUS_A1: advance_gpu(ST_UI_STATUS_A2);
                ST_UI_STATUS_A2: advance_gpu(ST_UI_STATUS_A3);
                ST_UI_STATUS_A3: advance_gpu(ST_UI_STATUS_CMD);
                ST_UI_STATUS_CMD: advance_gpu(ST_UI_STATUS_SUBMIT);
                ST_UI_STATUS_SUBMIT: begin if(gpu_ready)begin text_index<=0;state<=ST_UI_HELP_TEXT;end end

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
                    if(gpu_ready) begin response<=0;state<=ST_RESP;end
                end

                ST_RESP: begin end
                default: state<=ST_IDLE;
            endcase
        end
    end
endmodule
