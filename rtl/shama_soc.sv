`include "shama_boot_params.svh"

module shama_soc #(
    parameter integer CORE_DIV_BIT = 17
)(
    input  logic         clk,
    input  logic         power_switch,
    input  logic         reset_button,
    input  logic         pause_switch,
    input  logic         step_button,
    output logic         clock_hold,
    output logic         shutdown_ready,
    output logic         input_overflow,
    output logic [31:0]  debug_pc,debug_instruction,
    output logic [4:0]   debug_flags,
    output logic [5:0]   debug_sha_round,
    output logic [255:0] debug_sha_state,
    output logic [7:0]   debug_fault,
    output logic [3:0]   debug_app,
    output logic [31:0]  debug_lifecycle,
    output logic [511:0] debug_registers,
    output logic [5:0]   debug_state,
    output logic         debug_stall,
    output logic [31:0]  debug_ram_used,debug_cache_used,debug_flash_used,

    input  logic [7:0]   kb_rows,
    input  logic [7:0]   kb_cols,
    input  logic [9:0]   controller,

    input  logic [31:0]  cache_selected_word,
    output logic [`SHAMA_CACHE_BANKS-1:0] cache_bank_select,
    output logic [1023:0] cache_row_select,
    output logic         cache_read_enable,
    output logic         cache_write_commit,
    output logic [31:0]  cache_write_data,

    input  logic [31:0]  ram_selected_word,
    output logic [`SHAMA_RAM_BANKS-1:0] ram_bank_select,
    output logic [1023:0] ram_row_select,
    output logic         ram_read_enable,
    output logic         ram_write_commit,
    output logic [31:0]  ram_write_data,

    input  logic [31:0]  flash_selected_word,
    output logic [`SHAMA_FLASH_BANKS-1:0] flash_bank_select,
    output logic [1023:0] flash_row_select,
    output logic         flash_read_enable,
    output logic         flash_write_commit,
    output logic [31:0]  flash_write_data,

    input  logic [31:0]  vram_selected_word,
    output logic [`SHAMA_VRAM_BANKS-1:0] vram_bank_select,
    output logic [1023:0] vram_row_select,
    output logic         vram_read_enable,
    output logic         vram_write_commit,
    output logic [31:0]  vram_write_data,

    output logic [319:0] display_row_data,
    output logic [7:0]   display_row_index,
    output logic         display_row_commit,
    output logic [179:0] display_row_select,
    input  logic         display_row_ready,

    output logic         halted
);
    localparam [31:0]
        CACHE_END  = `SHAMA_CACHE_BYTES-1,
        RAM_END    = `SHAMA_RAM_BYTES-1,
        FLASH_BASE = 32'h01000000,
        FLASH_END  = 32'h01000000+`SHAMA_FLASH_BYTES-1,
        GPU_BASE   = 32'h02000000,
        GPU_END    = 32'h02000fff,
        INPUT_BASE = 32'h02001000,
        INPUT_END  = 32'h02001fff;

    logic rst;
    logic powered,power_previous;
    // Turning the lever off requests a clean close. The clock remains held on
    // until filesystem saves, ownership cleanup and volatile clearing finish.
    always_ff @(posedge clk) begin
        if(reset_button) begin powered<=power_switch;power_previous<=power_switch;end
        else begin
            // Edge history and edge detection use the same sampling clock.
            // Sampling history on the opposite edge can swallow a lever rise.
            power_previous<=power_switch;
            if(shutdown_ready) powered<=0;
            else if(power_switch && !power_previous) powered<=1;
        end
    end
    assign rst = reset_button | ~powered;
    assign clock_hold = powered;

    // Raw physical clock stays fast enough to latch short keyboard/controller
    // events. Core logic runs extremely slowly so the longest literal
    // RAM/flash redstone routes settle before the next architectural edge.
    // During reset, core_clk follows the raw clock so every state element
    // receives multiple reset edges before the physical clock stops.
    logic [CORE_DIV_BIT:0] core_div_counter;
    logic core_clk;
    logic core_gate,step_pending,step_previous;

    always_ff @(posedge clk) begin
        if(rst) begin core_div_counter<='0;step_pending<=0;step_previous<=0;end
        else begin
            core_div_counter <= core_div_counter + 1'b1;
            step_previous<=step_button;
            if(step_button && !step_previous) step_pending<=1;
            else if(core_div_counter==((1<<CORE_DIV_BIT)-1) && core_gate) step_pending<=0;
        end
    end

    // Gate changes only during a low divider phase. A step therefore emits a
    // complete normal-length core pulse; it never shortens the settling time.
    always_ff @(negedge clk) begin
        if(rst) core_gate<=1;
        else if(!core_div_counter[CORE_DIV_BIT])
            core_gate<=!pause_switch || step_pending || !power_switch;
    end
    assign core_clk = rst ? clk : (core_div_counter[CORE_DIV_BIT] & core_gate);

    logic [63:0] time_counter;
    always_ff @(posedge clk) begin
        if(rst) time_counter <= 0;
        else time_counter <= time_counter + 1'b1;
    end

    // ---------------- CPU ----------------
    logic cpu_mem_valid,cpu_mem_we,cpu_mem_ready;
    logic [31:0] cpu_mem_addr,cpu_mem_wdata,cpu_mem_rdata;
    logic [3:0] cpu_mem_wstrb;
    logic sys_valid,sys_ready,sys_jump_valid;
    logic [11:0] sys_id;
    logic [191:0] sys_args;
    logic [63:0] sys_ret;
    logic [31:0] sys_jump_pc;
    logic cache_flush;
    logic sha_busy;
    logic cpu_hold,svc_cpu_hold,cpu_force_jump;
    logic [2:0] bus_owner,bus_choice;
    logic bus_complete,bus_active;
    // Drain an already accepted CPU transaction before holding it. Neither a
    // DMA requester nor a snapshot may steal the memory adapter's response.
    assign cpu_hold=svc_cpu_hold && bus_owner!=3'd4;
    logic [31:0] cpu_force_pc;

    shama_cpu u_cpu(
        .clk(core_clk),.rst,
        .mem_valid(cpu_mem_valid),.mem_we(cpu_mem_we),.mem_addr(cpu_mem_addr),
        .mem_wdata(cpu_mem_wdata),.mem_wstrb(cpu_mem_wstrb),
        .mem_ready(cpu_mem_ready),.mem_rdata(cpu_mem_rdata),
        .sys_valid,.sys_id,.sys_args,.sys_ready,.sys_ret,
        .sys_jump_valid,.sys_jump_pc,
        .cache_flush,.time_counter,
        .external_hold(cpu_hold),.external_jump_valid(cpu_force_jump),
        .external_jump_pc(cpu_force_pc),
        .halted,.sha_busy,.fault_code(debug_fault),
        .debug_pc,.debug_instruction,.debug_flags,.debug_sha_round,.debug_sha_state
        ,.debug_registers,.debug_state,.debug_stall
    );

    // ---------------- Physical input ----------------
    logic event_valid,event_ack;
    logic [25:0] event_ack_data;
    logic [7:0] event_code,key_code;
    logic [9:0] controller_latched;
    logic [7:0] input_queued_count;
    logic keyboard_shift;

    shama_input u_input(
        .clk,.rst,.kb_rows,.kb_cols,.controller,
        .event_valid,.event_code,.key_code,.controller_latched,.event_ack,.event_ack_data,
        .input_overflow,.queued_count(input_queued_count),.keyboard_shift
    );

    // ---------------- GPU / 320x180 display ----------------
    logic gpu_mmio_valid,gpu_mmio_we,gpu_mmio_ready;
    logic [11:0] gpu_mmio_addr;
    logic [31:0] gpu_mmio_wdata,gpu_mmio_rdata;
    logic [3:0] gpu_mmio_wstrb;
    logic gpu_disp_valid,gpu_disp_bit,gpu_disp_ready;
    logic [15:0] gpu_disp_index;
    logic gpu_busy;
    logic gpu_capacity_fault,clear_gpu_capacity_fault;
    logic [7:0] notice_code;
    logic notice_busy,notice_ready,flash_write_protected,raw_flash_write_blocked;
    logic gpu_vram_valid,gpu_vram_we,gpu_vram_ready;
    logic [14:0] gpu_vram_addr;
    logic [31:0] gpu_vram_wdata,gpu_vram_rdata;
    logic [3:0] gpu_vram_wstrb;

    shama_gpu u_gpu(
        .clk(core_clk),.rst,
        .mmio_valid(gpu_mmio_valid),.mmio_we(gpu_mmio_we),
        .mmio_addr(gpu_mmio_addr),.mmio_wdata(gpu_mmio_wdata),
        .mmio_wstrb(gpu_mmio_wstrb),.mmio_ready(gpu_mmio_ready),.mmio_rdata(gpu_mmio_rdata),
        .vram_valid(gpu_vram_valid),.vram_we(gpu_vram_we),.vram_addr(gpu_vram_addr),
        .vram_wdata(gpu_vram_wdata),.vram_wstrb(gpu_vram_wstrb),
        .vram_ready(gpu_vram_ready),.vram_rdata(gpu_vram_rdata),
        .disp_valid(gpu_disp_valid),.disp_index(gpu_disp_index),
        .disp_bit(gpu_disp_bit),.disp_ready(gpu_disp_ready),.busy(gpu_busy),
        .capacity_fault(gpu_capacity_fault),.clear_capacity_fault(clear_gpu_capacity_fault)
    );

    shama_display_bridge u_display_bridge(
        .clk(core_clk),.rst,
        .pixel_valid(gpu_disp_valid),
        .pixel_index(gpu_disp_index),
        .pixel_bit(gpu_disp_bit),
        .pixel_ready(gpu_disp_ready),
        .row_data(display_row_data),
        .row_index(display_row_index),
        .row_commit(display_row_commit),
        .row_ready(display_row_ready)
    );


    always_comb begin
        display_row_select = '0;
        if(display_row_commit && display_row_index < 8'd180)
            display_row_select[display_row_index] = 1'b1;
    end

    // ---------------- Dedicated physical 16 KiB VRAM ----------------
    shama_vram_adapter u_vram(
        .clk(core_clk),.rst,
        .req_valid(gpu_vram_valid),.req_we(gpu_vram_we),
        .req_addr({17'd0,gpu_vram_addr}),.req_wdata(gpu_vram_wdata),
        .req_wstrb(gpu_vram_wstrb),
        .req_ready(gpu_vram_ready),.req_rdata(gpu_vram_rdata),
        .bank_select(vram_bank_select),.row_select(vram_row_select),
        .read_enable(vram_read_enable),.write_commit(vram_write_commit),
        .write_data(vram_write_data),.selected_word(vram_selected_word)
    );

    // ---------------- ShamaOS services ----------------
    logic svc_dma_valid,svc_dma_we,svc_dma_flash,svc_dma_cache,svc_dma_ready;
    logic [31:0] svc_dma_addr,svc_dma_wdata,svc_dma_rdata;
    logic [3:0] svc_dma_wstrb;
    logic ext_valid,ext_ready,ext_jump_valid,ext_load_app;
    logic [3:0] ext_app_id;
    logic [11:0] ext_id;
    logic [191:0] ext_args;
    logic [63:0] ext_ret;
    logic [31:0] ext_jump_pc;
    logic os_loaded;

    logic [31:0] ram_used_bytes,cache_used_bytes,flash_used_bytes;
    logic [3:0] foreground_app;
    logic fs_cleanup_valid,fs_cleanup_ready;
    logic [3:0] fs_cleanup_owner;
    assign debug_app=foreground_app;
    assign debug_ram_used=ram_used_bytes;
    assign debug_cache_used=cache_used_bytes;
    assign debug_flash_used=flash_used_bytes;

    shama_services u_services(
        .clk(core_clk),.rst,
        .sys_valid,.sys_id,.sys_args,.sys_ready,.sys_ret,.sys_jump_valid,.sys_jump_pc,
        .event_valid,.event_code,.key_code,.controller_latched,.event_ack,.event_ack_data,
        .input_overflow,.input_queued_count,.keyboard_shift,.shutdown_request(!power_switch),
        .cpu_fault_code(debug_fault),.gpu_capacity_fault,.clear_gpu_capacity_fault,
        .flash_write_protected,.raw_flash_write_blocked,.notice_code,.notice_busy,.notice_ready,
        .shutdown_ready,.lifecycle_error(debug_lifecycle),
        .fs_cleanup_valid,.fs_cleanup_owner,.fs_cleanup_ready,
        .flash_used_bytes,.time_counter,.ram_used_bytes,.cache_used_bytes,
        .dma_valid(svc_dma_valid),.dma_we(svc_dma_we),.dma_flash(svc_dma_flash),
        .dma_cache(svc_dma_cache),.dma_addr(svc_dma_addr),.dma_wdata(svc_dma_wdata),.dma_wstrb(svc_dma_wstrb),
        .dma_ready(svc_dma_ready),.dma_rdata(svc_dma_rdata),
        .ext_valid(ext_valid),.ext_id(ext_id),.ext_args(ext_args),
        .ext_ready(ext_ready),.ext_ret(ext_ret),
        .ext_jump_valid(ext_jump_valid),.ext_jump_pc(ext_jump_pc),
        .ext_load_app,.ext_app_id,
        .os_loaded,.foreground_app,
        .cpu_hold(svc_cpu_hold),.cpu_force_jump,.cpu_force_pc
    );

    // ---------------- Kernel GUI + syscall dispatcher ----------------
    logic k_gpu_valid,k_gpu_we,k_gpu_ready;
    logic [11:0] k_gpu_addr;
    logic [31:0] k_gpu_wdata,k_gpu_rdata;
    logic [3:0] k_gpu_wstrb;

    logic fs_call_valid,fs_call_ready;
    logic [11:0] fs_call_id;
    logic [191:0] fs_call_args;
    logic [63:0] fs_call_ret;

    logic asm_call_valid,asm_call_ready;
    logic [191:0] asm_call_args;
    logic [63:0] asm_call_ret;

    shama_kernel_accel u_kernel(
        .clk(core_clk),.rst,
        .req_valid(ext_valid),.req_id(ext_id),.req_args(ext_args),
        .req_ready(ext_ready),.req_ret(ext_ret),
        .req_jump_valid(ext_jump_valid),.req_jump_pc(ext_jump_pc),
        .req_load_app(ext_load_app),.req_app_id(ext_app_id),
        .ram_used_bytes,.cache_used_bytes,.flash_used_bytes,
        .cpu_halted(halted),.sha_busy,.gpu_busy,
        .gpu_capacity_fault,.notice_code,.notice_busy,.notice_ready,
        .gpu_valid(k_gpu_valid),.gpu_we(k_gpu_we),.gpu_addr(k_gpu_addr),
        .gpu_wdata(k_gpu_wdata),.gpu_wstrb(k_gpu_wstrb),.gpu_ready(k_gpu_ready),.gpu_rdata(k_gpu_rdata),
        .fs_valid(fs_call_valid),.fs_id(fs_call_id),.fs_args(fs_call_args),
        .fs_ready(fs_call_ready),.fs_ret(fs_call_ret),
        .asm_valid(asm_call_valid),.asm_args(asm_call_args),
        .asm_ready(asm_call_ready),.asm_ret(asm_call_ret)
    );

    // ---------------- Persistent ShamaFS service ----------------
    logic fs_dma_valid,fs_dma_we,fs_dma_flash,fs_dma_ready;
    logic [31:0] fs_dma_addr,fs_dma_wdata,fs_dma_rdata;
    logic [3:0] fs_dma_wstrb;
    logic fs_mounted;

    shama_fs_accel u_fs(
        .clk(core_clk),.rst,
        .req_valid(fs_call_valid),.req_id(fs_call_id),.req_args(fs_call_args),
        .req_owner(foreground_app),.cleanup_valid(fs_cleanup_valid),
        .cleanup_owner(fs_cleanup_owner),.cleanup_ready(fs_cleanup_ready),
        .req_ready(fs_call_ready),.req_ret(fs_call_ret),
        .dma_valid(fs_dma_valid),.dma_we(fs_dma_we),.dma_flash(fs_dma_flash),
        .dma_addr(fs_dma_addr),.dma_wdata(fs_dma_wdata),.dma_wstrb(fs_dma_wstrb),
        .dma_ready(fs_dma_ready),.dma_rdata(fs_dma_rdata),
        .flash_used_bytes,.mounted(fs_mounted),.flash_write_protected
    );

    // The assembler accelerator is connected here. Until its request is
    // active it consumes no resources; its full implementation is synthesized
    // as a separate block and shares the DMA arbiter.
    logic asm_dma_valid,asm_dma_we,asm_dma_flash,asm_dma_ready;
    logic [31:0] asm_dma_addr,asm_dma_wdata,asm_dma_rdata;
    logic [3:0] asm_dma_wstrb;

    shama_asm_accel u_asm(
        .clk(core_clk),.rst,
        .req_valid(asm_call_valid),.req_args(asm_call_args),
        .req_ready(asm_call_ready),.req_ret(asm_call_ret),
        .dma_valid(asm_dma_valid),.dma_we(asm_dma_we),.dma_flash(asm_dma_flash),
        .dma_addr(asm_dma_addr),.dma_wdata(asm_dma_wdata),.dma_wstrb(asm_dma_wstrb),
        .dma_ready(asm_dma_ready),.dma_rdata(asm_dma_rdata)
    );

    // ---------------- Physical memory adapters ----------------
    logic cache_req_valid,cache_req_we,cache_req_ready;
    logic [31:0] cache_req_addr,cache_req_wdata,cache_req_rdata;
    logic [3:0] cache_req_wstrb;

    shama_cache_adapter u_cache(
        .clk(core_clk),.rst,
        .req_valid(cache_req_valid),.req_we(cache_req_we),
        .req_addr(cache_req_addr),.req_wdata(cache_req_wdata),.req_wstrb(cache_req_wstrb),
        .req_ready(cache_req_ready),.req_rdata(cache_req_rdata),
        .bank_select(cache_bank_select),.row_select(cache_row_select),
        .read_enable(cache_read_enable),.write_commit(cache_write_commit),
        .write_data(cache_write_data),.selected_word(cache_selected_word)
    );

    logic ram_req_valid,ram_req_we,ram_req_ready;
    logic [31:0] ram_req_addr,ram_req_wdata,ram_req_rdata;
    logic [3:0] ram_req_wstrb;

    shama_ram_adapter u_ram(
        .clk(core_clk),.rst,
        .req_valid(ram_req_valid),.req_we(ram_req_we),
        .req_addr(ram_req_addr),.req_wdata(ram_req_wdata),.req_wstrb(ram_req_wstrb),
        .req_ready(ram_req_ready),.req_rdata(ram_req_rdata),
        .bank_select(ram_bank_select),.row_select(ram_row_select),
        .read_enable(ram_read_enable),.write_commit(ram_write_commit),
        .write_data(ram_write_data),.selected_word(ram_selected_word)
    );

    logic flash_req_valid,flash_req_we,flash_req_ready;
    logic [31:0] flash_req_addr,flash_req_wdata,flash_req_rdata;
    logic [3:0] flash_req_wstrb;

    shama_flash_adapter u_flash(
        .clk(core_clk),.rst,
        .req_valid(flash_req_valid),.req_we(flash_req_we),
        .req_addr(flash_req_addr),.req_wdata(flash_req_wdata),.req_wstrb(flash_req_wstrb),
        .req_ready(flash_req_ready),.req_rdata(flash_req_rdata),
        .bank_select(flash_bank_select),.row_select(flash_row_select),
        .read_enable(flash_read_enable),.write_commit(flash_write_commit),
        .write_data(flash_write_data),.selected_word(flash_selected_word)
    );

    // ---------------- CPU/GPU and DMA arbitration ----------------
    logic cpu_gpu_request;
    assign cpu_gpu_request =
        cpu_mem_valid && !cpu_hold && cpu_mem_addr>=GPU_BASE && cpu_mem_addr<=GPU_END;

    always_comb begin
        bus_choice=bus_owner;
        if(bus_owner==0) begin
            if(svc_dma_valid) bus_choice=1;
            else if(fs_dma_valid) bus_choice=2;
            else if(asm_dma_valid) bus_choice=3;
            else if(cpu_mem_valid && !cpu_hold) bus_choice=4;
        end
    end
    always_comb begin
        case(bus_choice)
            1:begin bus_active=svc_dma_valid;bus_complete=svc_dma_ready;end
            2:begin bus_active=fs_dma_valid;bus_complete=fs_dma_ready;end
            3:begin bus_active=asm_dma_valid;bus_complete=asm_dma_ready;end
            4:begin bus_active=cpu_mem_valid;bus_complete=cpu_mem_ready;end
            default:begin bus_active=0;bus_complete=0;end
        endcase
    end
    always_ff @(posedge core_clk) begin
        if(rst) bus_owner<=0;
        else if(bus_active) begin
            if(bus_complete) bus_owner<=0;
            else bus_owner<=bus_choice;
        end
    end

    // Immutable bootstrap ROM consists of actual synthesized gates. The fast
    // memory is deliberately reusable/erasable, so it cannot also be the only
    // copy of the reset program. These words match firmware/boot.asm.
    function automatic [31:0] boot_word(input logic [31:0] addr);
        case(addr)
            0:boot_word=32'h60000008;
            4:boot_word=32'h30200000;
            8:boot_word=32'h41100000;
            12:boot_word=32'h00000011;
            16:boot_word=32'h60000009;
            20:boot_word=32'h30200000;
            24:boot_word=32'h41100000;
            28:boot_word=32'h00000011;
            32:boot_word=32'h03100000;
            36:boot_word=32'h00000000;
            40:boot_word=32'h60000006;
            44:boot_word=32'h30200000;
            48:boot_word=32'h41100000;
            52:boot_word=32'h00000011;
            56:boot_word=32'h60000002;
            60:boot_word=32'h40000000;
            64:boot_word=32'h0000000e;
            68:boot_word=32'h01000000;
            default:boot_word=32'h01000000;
        endcase
    endfunction

    wire gpu_kernel_owner=k_gpu_valid && !(bus_owner==4 && cpu_gpu_request);
    wire cpu_bus_selected=(bus_choice==4 && cpu_mem_valid && !cpu_hold);
    wire cpu_boot_read=!os_loaded && !cpu_mem_we && cpu_mem_addr<=CACHE_END;
    wire cpu_cache_address=cpu_mem_addr<=CACHE_END;
    wire cpu_ram_address=cpu_mem_addr<=RAM_END;
    wire cpu_flash_address=cpu_mem_addr>=FLASH_BASE && cpu_mem_addr<=FLASH_END;
    wire cpu_gpu_address=cpu_mem_addr>=GPU_BASE && cpu_mem_addr<=GPU_END;
    wire cpu_input_address=cpu_mem_addr>=INPUT_BASE && cpu_mem_addr<=INPUT_END;
    // Protection gates admission, never an already accepted memory word.
    // Filesystem DMA remains available for recovery, deletion and admitted COW.
    assign raw_flash_write_blocked=cpu_bus_selected && cpu_flash_address && cpu_mem_we &&
                                   flash_write_protected && bus_owner!=3'd4;
    logic [31:0] input_read_data;

    // Requests depend only on master requests/registered ownership. In
    // particular, returned memory data and ready must not re-enter this mux:
    // DMA clients decode returned bytes combinationally, and coupling the two
    // directions produces zero-time simulation feedback and physical hazards.
    always_comb begin
        cache_req_valid=0;cache_req_we=0;cache_req_addr=0;cache_req_wdata=0;cache_req_wstrb=0;
        ram_req_valid=0;ram_req_we=0;ram_req_addr=0;ram_req_wdata=0;ram_req_wstrb=0;
        flash_req_valid=0;flash_req_we=0;flash_req_addr=0;flash_req_wdata=0;flash_req_wstrb=0;
        if(bus_choice==1 && svc_dma_valid) begin
            if(svc_dma_cache) begin
                cache_req_valid=1;cache_req_we=svc_dma_we;cache_req_addr=svc_dma_addr;
                cache_req_wdata=svc_dma_wdata;cache_req_wstrb=svc_dma_wstrb;
            end else if(svc_dma_flash) begin
                flash_req_valid=1;flash_req_we=svc_dma_we;flash_req_addr=svc_dma_addr;
                flash_req_wdata=svc_dma_wdata;flash_req_wstrb=svc_dma_wstrb;
            end else begin
                ram_req_valid=1;ram_req_we=svc_dma_we;ram_req_addr=svc_dma_addr;
                ram_req_wdata=svc_dma_wdata;ram_req_wstrb=svc_dma_wstrb;
            end
        end else if(bus_choice==2 && fs_dma_valid) begin
            if(fs_dma_flash) begin
                flash_req_valid=1;flash_req_we=fs_dma_we;flash_req_addr=fs_dma_addr;
                flash_req_wdata=fs_dma_wdata;flash_req_wstrb=fs_dma_wstrb;
            end else begin
                ram_req_valid=1;ram_req_we=fs_dma_we;ram_req_addr=fs_dma_addr;
                ram_req_wdata=fs_dma_wdata;ram_req_wstrb=fs_dma_wstrb;
            end
        end else if(bus_choice==3 && asm_dma_valid) begin
            if(asm_dma_flash) begin
                flash_req_valid=1;flash_req_we=asm_dma_we;flash_req_addr=asm_dma_addr;
                flash_req_wdata=asm_dma_wdata;flash_req_wstrb=asm_dma_wstrb;
            end else begin
                ram_req_valid=1;ram_req_we=asm_dma_we;ram_req_addr=asm_dma_addr;
                ram_req_wdata=asm_dma_wdata;ram_req_wstrb=asm_dma_wstrb;
            end
        end else if(cpu_bus_selected && !cpu_boot_read) begin
            if(cpu_cache_address) begin
                cache_req_valid=1;cache_req_we=cpu_mem_we;cache_req_addr=cpu_mem_addr;
                cache_req_wdata=cpu_mem_wdata;cache_req_wstrb=cpu_mem_wstrb;
            end else if(cpu_ram_address) begin
                ram_req_valid=1;ram_req_we=cpu_mem_we;ram_req_addr=cpu_mem_addr;
                ram_req_wdata=cpu_mem_wdata;ram_req_wstrb=cpu_mem_wstrb;
            end else if(cpu_flash_address) begin
                flash_req_valid=!raw_flash_write_blocked;flash_req_we=cpu_mem_we;
                flash_req_addr=cpu_mem_addr-FLASH_BASE;
                flash_req_wdata=cpu_mem_wdata;flash_req_wstrb=cpu_mem_wstrb;
            end
        end
    end

    always_comb begin
        gpu_mmio_valid=0;gpu_mmio_we=0;gpu_mmio_addr=0;gpu_mmio_wdata=0;gpu_mmio_wstrb=0;
        if(gpu_kernel_owner) begin
            gpu_mmio_valid=k_gpu_valid;gpu_mmio_we=k_gpu_we;
            gpu_mmio_addr=k_gpu_addr;gpu_mmio_wdata=k_gpu_wdata;gpu_mmio_wstrb=k_gpu_wstrb;
        end else if(cpu_bus_selected && cpu_gpu_address) begin
            gpu_mmio_valid=1;gpu_mmio_we=cpu_mem_we;
            gpu_mmio_addr=cpu_mem_addr-GPU_BASE;gpu_mmio_wdata=cpu_mem_wdata;
            gpu_mmio_wstrb=cpu_mem_wstrb;
        end
    end

    always_comb begin
        case(cpu_mem_addr[5:2])
            0: input_read_data={22'd0,controller_latched};
            1: input_read_data={24'd0,key_code};
            2: input_read_data={23'd0,event_valid,event_code};
            3: input_read_data={22'd0,keyboard_shift,input_overflow,input_queued_count};
            4: input_read_data=debug_lifecycle;
            default: input_read_data=0;
        endcase
    end

    // Continuous response muxes avoid transient default-zero/data transitions
    // being observed as new upstream requests. An adapter's completion is
    // delivered exclusively to the master that owns its active transaction.
    assign k_gpu_ready=gpu_kernel_owner && gpu_mmio_ready;
    assign k_gpu_rdata=gpu_mmio_rdata;
    assign svc_dma_ready=bus_choice==1 && svc_dma_valid &&
                         (svc_dma_cache?cache_req_ready:svc_dma_flash?flash_req_ready:ram_req_ready);
    assign svc_dma_rdata=bus_choice==1 ?
                         (svc_dma_cache?cache_req_rdata:svc_dma_flash?flash_req_rdata:ram_req_rdata):0;
    assign fs_dma_ready=bus_choice==2 && fs_dma_valid && (fs_dma_flash?flash_req_ready:ram_req_ready);
    assign fs_dma_rdata=bus_choice==2 ? (fs_dma_flash?flash_req_rdata:ram_req_rdata):0;
    assign asm_dma_ready=bus_choice==3 && asm_dma_valid && (asm_dma_flash?flash_req_ready:ram_req_ready);
    assign asm_dma_rdata=bus_choice==3 ? (asm_dma_flash?flash_req_rdata:ram_req_rdata):0;
    assign cpu_mem_ready=cpu_bus_selected && (cpu_boot_read?1'b1:
                         cpu_cache_address?cache_req_ready:cpu_ram_address?ram_req_ready:
                         cpu_flash_address?(raw_flash_write_blocked || flash_req_ready):cpu_gpu_address?(!gpu_kernel_owner && gpu_mmio_ready):1'b1);
    assign cpu_mem_rdata=!cpu_bus_selected?0:cpu_boot_read?boot_word(cpu_mem_addr):
                         cpu_cache_address?cache_req_rdata:cpu_ram_address?ram_req_rdata:
                         cpu_flash_address?(raw_flash_write_blocked?32'd0:flash_req_rdata):cpu_gpu_address?gpu_mmio_rdata:
                         cpu_input_address?input_read_data:0;

endmodule
