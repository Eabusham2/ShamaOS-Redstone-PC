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

    input  logic [31:0]  flash_used_bytes,
    input  logic [63:0]  time_counter,
    output logic [31:0]  ram_used_bytes,
    output logic [31:0]  cache_used_bytes,

    output logic         dma_valid,
    output logic         dma_we,
    output logic         dma_flash,
    output logic         dma_cache,
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
    input  logic [31:0]  ext_jump_pc,
    input  logic         ext_load_app,
    input  logic [3:0]   ext_app_id,

    output logic         os_loaded,
    output logic [3:0]   foreground_app
);
    localparam [11:0]
        SYS_EXIT                = 12'h001,
        SYS_YIELD               = 12'h002,
        SYS_GET_EVENT           = 12'h003,
        SYS_GET_TIME            = 12'h004,
        SYS_GET_COUNTER         = 12'h005,
        SYS_APP_LAUNCH          = 12'h006,
        SYS_APP_EXIT_FOREGROUND = 12'h007,
        SYS_BOOT_LOAD_OS        = 12'h009,

        SYS_RAM_USAGE           = 12'h012,
        SYS_CACHE_USAGE         = 12'h013,
        SYS_FLASH_USAGE         = 12'h02a,
        SYS_GET_KEY             = 12'h030,
        SYS_GET_CONTROLLER      = 12'h031,
        SYS_RUN_BUFFER          = 12'h061;

    typedef enum logic [3:0] {
        ST_IDLE,
        ST_SLOT_LENGTH,
        ST_COPY_READ,
        ST_COPY_RAM_WRITE,
        ST_COPY_CACHE_WRITE,
        ST_APP_CLEAR_RAM,
        ST_APP_CLEAR_CACHE,
        ST_EXT,
        ST_RESP
    } state_t;

    state_t state;

    logic [11:0] latched_id;
    logic [191:0] latched_args;

    logic [31:0] copy_offset;
    logic [31:0] copy_word;
    logic [31:0] copy_source_base;
    logic [31:0] copy_ram_base;
    logic [31:0] copy_bytes;
    logic        copy_source_flash;
    logic        copy_to_cache;
    logic        copy_is_kernel;
    logic        boot_after_kernel;

    logic [3:0] target_app;
    logic       run_staged;
    logic [31:0] staged_source_ptr;
    logic [31:0] staged_bytes;

    logic [31:0] kernel_loaded_bytes;
    logic [31:0] app_loaded_bytes;

    logic [63:0] response;
    logic response_jump;
    logic [31:0] response_pc;

    function automatic [31:0] app_flash(input logic [3:0] app_id);
        begin
            case(app_id)
                4'd0: app_flash = `SHAMA_DESKTOP_FLASH;
                4'd1: app_flash = `SHAMA_EDITOR_FLASH;
                4'd2: app_flash = `SHAMA_FILES_FLASH;
                4'd3: app_flash = `SHAMA_MINER_FLASH;
                4'd4: app_flash = `SHAMA_MONITOR_FLASH;
                4'd5: app_flash = `SHAMA_TERMINAL_FLASH;
                4'd6: app_flash = `SHAMA_CALCULATOR_FLASH;
                4'd7: app_flash = `SHAMA_PAINT_FLASH;
                4'd8: app_flash = `SHAMA_SETTINGS_FLASH;
                default: app_flash = `SHAMA_DESKTOP_FLASH;
            endcase
        end
    endfunction

    function automatic logic valid_app(input logic [31:0] app_id);
        valid_app = app_id <= 8;
    endfunction

    function automatic [31:0] workspace_bytes(input logic [3:0] app_id);
        begin
            // These regions are real fixed RAM workspaces used by firmware.
            case(app_id)
                4'd1: workspace_bytes = 32'd24576; // Editor buffers/context/run staging
                4'd2: workspace_bytes = 32'd24576; // File Explorer text/run staging
                4'd3: workspace_bytes = 32'd4096;  // Miner header/hash/target/history
                4'd5: workspace_bytes = 32'd1024;  // Terminal line buffer
                default: workspace_bytes = 32'd0;
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
                SYS_BOOT_LOAD_OS,
                SYS_RAM_USAGE,
                SYS_CACHE_USAGE,
                SYS_FLASH_USAGE,
                SYS_GET_KEY,
                SYS_GET_CONTROLLER,
                SYS_RUN_BUFFER: is_local = 1'b1;
                default: is_local = 1'b0;
            endcase
        end
    endfunction

    task automatic begin_app_replace(input logic [3:0] app_id);
        begin
            target_app <= app_id;
            run_staged <= 1'b0;
            app_loaded_bytes <= 0;
            copy_offset <= 0;
            state <= ST_APP_CLEAR_RAM;
        end
    endtask

    always_comb begin
        sys_ready = (state == ST_RESP);
        sys_ret = response;
        sys_jump_valid = (state == ST_RESP) && response_jump;
        sys_jump_pc = response_pc;

        ext_valid = (state == ST_EXT);
        ext_id = latched_id;
        ext_args = latched_args;

        ram_used_bytes =
            kernel_loaded_bytes + app_loaded_bytes +
            workspace_bytes(foreground_app);
        cache_used_bytes = app_loaded_bytes;

        dma_valid = 1'b0;
        dma_we = 1'b0;
        dma_flash = 1'b0;
        dma_cache = 1'b0;
        dma_addr = 32'd0;
        dma_wdata = copy_word;
        dma_wstrb = 4'b1111;

        case(state)
            ST_SLOT_LENGTH: begin
                dma_valid = 1'b1;
                dma_flash = 1'b1;
                dma_addr = copy_source_base;
            end

            ST_COPY_READ: begin
                dma_valid = 1'b1;
                dma_flash = copy_source_flash;
                dma_cache = 1'b0;
                dma_addr = copy_source_base + copy_offset;
            end

            ST_COPY_RAM_WRITE: begin
                dma_valid = 1'b1;
                dma_we = 1'b1;
                dma_addr = copy_ram_base + copy_offset;
                dma_wdata = copy_word;
            end

            ST_COPY_CACHE_WRITE: begin
                dma_valid = 1'b1;
                dma_we = 1'b1;
                dma_cache = 1'b1;
                dma_addr = copy_offset;
                dma_wdata = copy_word;
            end

            ST_APP_CLEAR_RAM: begin
                dma_valid = 1'b1;
                dma_we = 1'b1;
                dma_addr = `SHAMA_APP_RAM_BASE + copy_offset;
                dma_wdata = 32'd0;
            end

            ST_APP_CLEAR_CACHE: begin
                dma_valid = 1'b1;
                dma_we = 1'b1;
                dma_cache = 1'b1;
                dma_addr = copy_offset;
                dma_wdata = 32'd0;
            end

            default: begin end
        endcase
    end

    always_ff @(posedge clk) begin
        if(rst) begin
            state <= ST_IDLE;
            latched_id <= 0;
            latched_args <= 0;

            copy_offset <= 0;
            copy_word <= 0;
            copy_source_base <= 0;
            copy_ram_base <= 0;
            copy_bytes <= 0;
            copy_source_flash <= 1'b1;
            copy_to_cache <= 1'b0;
            copy_is_kernel <= 1'b0;
            boot_after_kernel <= 1'b0;

            target_app <= 0;
            run_staged <= 1'b0;
            staged_source_ptr <= 0;
            staged_bytes <= 0;

            kernel_loaded_bytes <= 0;
            app_loaded_bytes <= 0;

            response <= 0;
            response_jump <= 0;
            response_pc <= 0;

            event_ack <= 0;
            os_loaded <= 0;
            foreground_app <= 0;
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
                                    begin_app_replace(4'd0);
                                end

                                SYS_YIELD: state <= ST_RESP;

                                SYS_BOOT_LOAD_OS: begin
                                    copy_source_base <= `SHAMA_KERNEL_FLASH;
                                    copy_ram_base <= `SHAMA_KERNEL_RAM_BASE;
                                    copy_offset <= 0;
                                    copy_source_flash <= 1'b1;
                                    copy_to_cache <= 1'b0;
                                    copy_is_kernel <= 1'b1;
                                    boot_after_kernel <= 1'b1;
                                    state <= ST_SLOT_LENGTH;
                                end

                                SYS_APP_LAUNCH: begin
                                    if(valid_app(sys_args[31:0]))
                                        begin_app_replace(sys_args[3:0]);
                                    else begin
                                        response <= 64'hffffffffffffffff;
                                        state <= ST_RESP;
                                    end
                                end

                                SYS_RUN_BUFFER: begin
                                    if(sys_args[63:32] == 0 ||
                                       sys_args[63:32] > `SHAMA_SLOT_BYTES - 4 ||
                                       sys_args[31:0] < `SHAMA_SLOT_BYTES) begin
                                        response <= 64'hfffffffffffffffa;
                                        state <= ST_RESP;
                                    end else begin
                                        target_app <= 4'hf;
                                        run_staged <= 1'b1;
                                        staged_source_ptr <= sys_args[31:0];
                                        staged_bytes <= sys_args[63:32];
                                        app_loaded_bytes <= 0;
                                        copy_offset <= 0;
                                        state <= ST_APP_CLEAR_RAM;
                                    end
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
                                    end else response <= 0;
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

                ST_SLOT_LENGTH: begin
                    if(dma_ready) begin
                        if(dma_rdata == 0 || dma_rdata > `SHAMA_SLOT_BYTES - 4) begin
                            response <= 64'hfffffffffffffff9;
                            state <= ST_RESP;
                        end else begin
                            copy_bytes <= dma_rdata;
                            copy_source_base <= copy_source_base + `SHAMA_SLOT_DATA_OFFSET;
                            copy_offset <= 0;
                            state <= ST_COPY_READ;
                        end
                    end
                end

                ST_APP_CLEAR_RAM: begin
                    if(dma_ready) begin
                        if(copy_offset + 4 >= `SHAMA_SLOT_BYTES) begin
                            copy_offset <= 0;
                            state <= ST_APP_CLEAR_CACHE;
                        end else copy_offset <= copy_offset + 4;
                    end
                end

                ST_APP_CLEAR_CACHE: begin
                    if(dma_ready) begin
                        if(copy_offset + 4 >= `SHAMA_SLOT_BYTES) begin
                            copy_offset <= 0;
                            if(run_staged) begin
                                copy_source_base <= staged_source_ptr;
                                copy_source_flash <= 1'b0;
                                copy_ram_base <= `SHAMA_APP_RAM_BASE;
                                copy_bytes <= staged_bytes;
                                copy_to_cache <= 1'b1;
                                copy_is_kernel <= 1'b0;
                                boot_after_kernel <= 1'b0;
                                state <= ST_COPY_READ;
                            end else begin
                                copy_source_base <= app_flash(target_app);
                                copy_source_flash <= 1'b1;
                                copy_ram_base <= `SHAMA_APP_RAM_BASE;
                                copy_to_cache <= 1'b1;
                                copy_is_kernel <= 1'b0;
                                boot_after_kernel <= 1'b0;
                                state <= ST_SLOT_LENGTH;
                            end
                        end else copy_offset <= copy_offset + 4;
                    end
                end

                ST_COPY_READ: begin
                    if(dma_ready) begin
                        copy_word <= dma_rdata;
                        state <= ST_COPY_RAM_WRITE;
                    end
                end

                ST_COPY_RAM_WRITE: begin
                    if(dma_ready) begin
                        if(copy_to_cache)
                            state <= ST_COPY_CACHE_WRITE;
                        else if(copy_offset + 4 >= copy_bytes) begin
                            kernel_loaded_bytes <= copy_bytes;
                            if(copy_is_kernel && boot_after_kernel) begin
                                target_app <= 0;
                                run_staged <= 0;
                                copy_offset <= 0;
                                copy_is_kernel <= 0;
                                boot_after_kernel <= 0;
                                state <= ST_APP_CLEAR_RAM;
                            end else state <= ST_RESP;
                        end else begin
                            copy_offset <= copy_offset + 4;
                            state <= ST_COPY_READ;
                        end
                    end
                end

                ST_COPY_CACHE_WRITE: begin
                    if(dma_ready) begin
                        if(copy_offset + 4 >= copy_bytes) begin
                            app_loaded_bytes <= copy_bytes;
                            foreground_app <= target_app;
                            response <= copy_bytes;
                            response_jump <= 1'b1;
                            response_pc <= `SHAMA_PC_APP;
                            os_loaded <= 1'b1;
                            run_staged <= 1'b0;
                            state <= ST_RESP;
                        end else begin
                            copy_offset <= copy_offset + 4;
                            state <= ST_COPY_READ;
                        end
                    end
                end

                ST_EXT: begin
                    if(ext_ready) begin
                        if(ext_load_app) begin
                            if(valid_app(ext_app_id))
                                begin_app_replace(ext_app_id);
                            else begin
                                response <= 64'hffffffffffffffff;
                                state <= ST_RESP;
                            end
                        end else begin
                            response <= ext_ret;
                            response_jump <= ext_jump_valid;
                            response_pc <= ext_jump_pc;
                            state <= ST_RESP;
                        end
                    end
                end

                ST_RESP: begin end
                default: state <= ST_IDLE;
            endcase
        end
    end
endmodule
