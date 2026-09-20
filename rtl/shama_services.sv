`include "shama_boot_params.svh"

module shama_services(
    input  logic         clk,
    input  logic         rst,

    input  logic         sys_valid,
    input  logic [11:0]  sys_id,
    input  logic [191:0] sys_args,
    output logic         sys_ready,
    output logic [63:0]  sys_ret,
    output logic         sys_jump_valid,
    output logic [31:0]  sys_jump_pc,

    input  logic         event_valid,
    input  logic [7:0]   event_code,
    input  logic [7:0]   key_code,
    input  logic [9:0]   controller_latched,
    output logic         event_ack,

    input  logic [31:0]  ram_used_bytes,
    input  logic [31:0]  cache_used_bytes,
    input  logic [31:0]  flash_used_bytes,
    input  logic [63:0]  time_counter,

    output logic         dma_valid,
    output logic         dma_we,
    output logic         dma_flash,
    output logic [31:0]  dma_addr,
    output logic [31:0]  dma_wdata,
    output logic [3:0]   dma_wstrb,
    input  logic         dma_ready,
    input  logic [31:0]  dma_rdata,

    output logic         ext_valid,
    output logic [11:0]  ext_id,
    output logic [191:0] ext_args,
    input  logic         ext_ready,
    input  logic [63:0]  ext_ret,
    input  logic         ext_jump_valid,
    input  logic [31:0]  ext_jump_pc
);
    localparam [11:0]
        SYS_EXIT                = 12'h001,
        SYS_YIELD               = 12'h002,
        SYS_GET_EVENT           = 12'h003,
        SYS_GET_TIME            = 12'h004,
        SYS_GET_COUNTER         = 12'h005,
        SYS_APP_LAUNCH          = 12'h006,
        SYS_APP_EXIT_FOREGROUND = 12'h007,
        SYS_BOOT_MOUNT          = 12'h008,
        SYS_BOOT_LOAD_OS        = 12'h009,
        SYS_APP_EVENT           = 12'h00a,

        SYS_RAM_USAGE           = 12'h012,
        SYS_CACHE_USAGE         = 12'h013,
        SYS_FLASH_USAGE         = 12'h02a,
        SYS_GET_KEY             = 12'h030,
        SYS_GET_CONTROLLER      = 12'h031;

    typedef enum logic [2:0] {
        ST_IDLE,
        ST_BOOT_READ,
        ST_BOOT_WRITE,
        ST_EXT,
        ST_RESP
    } state_t;

    state_t state;
    logic [11:0] latched_id;
    logic [191:0] latched_args;
    logic [31:0] copy_offset;
    logic [31:0] copy_word;
    logic [63:0] response;
    logic response_jump;
    logic [31:0] response_pc;

    function automatic logic [31:0] app_pc(input logic [31:0] app_id);
        begin
            case(app_id)
                32'd0: app_pc = `SHAMA_PC_DESKTOP;
                32'd1: app_pc = `SHAMA_PC_EDITOR;
                32'd2: app_pc = `SHAMA_PC_FILES;
                32'd3: app_pc = `SHAMA_PC_MINER;
                32'd4: app_pc = `SHAMA_PC_MONITOR;
                32'd5: app_pc = `SHAMA_PC_TERMINAL;
                32'd6: app_pc = `SHAMA_PC_CALCULATOR;
                32'd7: app_pc = `SHAMA_PC_PAINT;
                32'd8: app_pc = `SHAMA_PC_SETTINGS;
                default: app_pc = `SHAMA_PC_DESKTOP;
            endcase
        end
    endfunction

    function automatic logic is_local(input logic [11:0] id);
        begin
            case(id)
                SYS_EXIT,
                SYS_YIELD,
                SYS_GET_EVENT,
                SYS_GET_TIME,
                SYS_GET_COUNTER,
                SYS_APP_LAUNCH,
                SYS_APP_EXIT_FOREGROUND,
                SYS_BOOT_MOUNT,
                SYS_BOOT_LOAD_OS,
                SYS_RAM_USAGE,
                SYS_CACHE_USAGE,
                SYS_FLASH_USAGE,
                SYS_GET_KEY,
                SYS_GET_CONTROLLER: is_local = 1'b1;
                default: is_local = 1'b0;
            endcase
        end
    endfunction

    always_comb begin
        sys_ready = (state == ST_RESP);
        sys_ret = response;
        sys_jump_valid = (state == ST_RESP) && response_jump;
        sys_jump_pc = response_pc;

        dma_valid = 1'b0;
        dma_we = 1'b0;
        dma_flash = 1'b0;
        dma_addr = 32'd0;
        dma_wdata = copy_word;
        dma_wstrb = 4'b1111;

        ext_valid = (state == ST_EXT);
        ext_id = latched_id;
        ext_args = latched_args;

        if(state == ST_BOOT_READ) begin
            dma_valid = 1'b1;
            dma_we = 1'b0;
            dma_flash = 1'b1;
            dma_addr = `SHAMA_BUNDLE_FLASH_OFFSET + copy_offset;
        end else if(state == ST_BOOT_WRITE) begin
            dma_valid = 1'b1;
            dma_we = 1'b1;
            dma_flash = 1'b0;
            dma_addr = `SHAMA_BUNDLE_RAM_BASE + copy_offset;
            dma_wdata = copy_word;
        end
    end

    always_ff @(posedge clk) begin
        if(rst) begin
            state <= ST_IDLE;
            latched_id <= 0;
            latched_args <= 0;
            copy_offset <= 0;
            copy_word <= 0;
            response <= 0;
            response_jump <= 0;
            response_pc <= 0;
            event_ack <= 0;
        end else begin
            event_ack <= 0;
            if(state == ST_RESP && !sys_valid) begin
                state <= ST_IDLE;
                response_jump <= 0;
            end

            case(state)
                ST_IDLE: begin
                    if(sys_valid) begin
                        latched_id <= sys_id;
                        latched_args <= sys_args;
                        response <= 0;
                        response_jump <= 0;
                        response_pc <= 0;

                        if(!is_local(sys_id)) begin
                            state <= ST_EXT;
                        end else begin
                            case(sys_id)
                                SYS_EXIT,
                                SYS_APP_EXIT_FOREGROUND: begin
                                    response_jump <= 1'b1;
                                    response_pc <= `SHAMA_PC_DESKTOP;
                                    state <= ST_RESP;
                                end

                                SYS_YIELD,
                                SYS_BOOT_MOUNT: begin
                                    state <= ST_RESP;
                                end

                                SYS_BOOT_LOAD_OS: begin
                                    copy_offset <= 0;
                                    state <= ST_BOOT_READ;
                                end

                                SYS_APP_LAUNCH: begin
                                    response_jump <= 1'b1;
                                    response_pc <= app_pc(sys_args[31:0]);
                                    state <= ST_RESP;
                                end

                                SYS_GET_EVENT: begin
                                    if(event_valid) begin
                                        response <= {
                                            28'd0,
                                            controller_latched,
                                            key_code,
                                            event_code,
                                            10'd0
                                        };
                                        event_ack <= 1'b1;
                                    end else begin
                                        response <= 0;
                                    end
                                    state <= ST_RESP;
                                end

                                SYS_GET_KEY: begin
                                    response <= {56'd0,key_code};
                                    if(event_valid && event_code==8'h01)
                                        event_ack <= 1'b1;
                                    state <= ST_RESP;
                                end

                                SYS_GET_CONTROLLER: begin
                                    response <= {54'd0,controller_latched};
                                    state <= ST_RESP;
                                end

                                SYS_GET_TIME,
                                SYS_GET_COUNTER: begin
                                    response <= time_counter;
                                    state <= ST_RESP;
                                end

                                SYS_RAM_USAGE: begin
                                    response <= {32'd1048576,ram_used_bytes};
                                    state <= ST_RESP;
                                end

                                SYS_CACHE_USAGE: begin
                                    response <= {32'd16384,cache_used_bytes};
                                    state <= ST_RESP;
                                end

                                SYS_FLASH_USAGE: begin
                                    response <= {32'd4194304,flash_used_bytes};
                                    state <= ST_RESP;
                                end

                                default: state <= ST_RESP;
                            endcase
                        end
                    end
                end

                ST_BOOT_READ: begin
                    if(dma_ready) begin
                        copy_word <= dma_rdata;
                        state <= ST_BOOT_WRITE;
                    end
                end

                ST_BOOT_WRITE: begin
                    if(dma_ready) begin
                        if(copy_offset + 4 >= `SHAMA_BUNDLE_BYTES) begin
                            response <= `SHAMA_BUNDLE_BYTES;
                            state <= ST_RESP;
                        end else begin
                            copy_offset <= copy_offset + 4;
                            state <= ST_BOOT_READ;
                        end
                    end
                end

                ST_EXT: begin
                    if(ext_ready) begin
                        response <= ext_ret;
                        response_jump <= ext_jump_valid;
                        response_pc <= ext_jump_pc;
                        state <= ST_RESP;
                    end
                end

                ST_RESP: begin end
                default: state <= ST_IDLE;
            endcase
        end
    end
endmodule
