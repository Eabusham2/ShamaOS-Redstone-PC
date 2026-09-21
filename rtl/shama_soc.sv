`include "shama_boot_params.svh"

module shama_soc(
    input  logic         clk,
    input  logic         power_switch,
    input  logic         reset_button,

    input  logic [7:0]   kb_rows,
    input  logic [7:0]   kb_cols,
    input  logic [9:0]   controller,

    input  logic [31:0]  cache_selected_word,
    output logic [3:0]   cache_bank_select,
    output logic [1023:0] cache_row_select,
    output logic         cache_read_enable,
    output logic         cache_write_commit,
    output logic [31:0]  cache_write_data,

    input  logic [31:0]  ram_selected_word,
    output logic [255:0] ram_bank_select,
    output logic [1023:0] ram_row_select,
    output logic         ram_read_enable,
    output logic         ram_write_commit,
    output logic [31:0]  ram_write_data,

    input  logic [31:0]  flash_selected_word,
    output logic [1023:0] flash_bank_select,
    output logic [1023:0] flash_row_select,
    output logic         flash_read_enable,
    output logic         flash_write_commit,
    output logic [31:0]  flash_write_data,

    input  logic [31:0]  vram_selected_word,
    output logic [7:0]   vram_bank_select,
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
        CACHE_END  = 32'h00003fff,
        RAM_END    = 32'h000fffff,
        FLASH_BASE = 32'h01000000,
        FLASH_END  = 32'h013fffff,
        GPU_BASE   = 32'h02000000,
        GPU_END    = 32'h02000fff,
        INPUT_BASE = 32'h02001000,
        INPUT_END  = 32'h02001fff;

    logic rst;
    assign rst = reset_button | ~power_switch;

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

    shama_cpu u_cpu(
        .clk,.rst,
        .mem_valid(cpu_mem_valid),.mem_we(cpu_mem_we),.mem_addr(cpu_mem_addr),
        .mem_wdata(cpu_mem_wdata),.mem_wstrb(cpu_mem_wstrb),
        .mem_ready(cpu_mem_ready),.mem_rdata(cpu_mem_rdata),
        .sys_valid,.sys_id,.sys_args,.sys_ready,.sys_ret,
        .sys_jump_valid,.sys_jump_pc,
        .cache_flush,.time_counter,.halted,.sha_busy
    );

    // ---------------- Physical input ----------------
    logic event_valid,event_ack;
    logic [7:0] event_code,key_code;
    logic [9:0] controller_latched;

    shama_input u_input(
        .clk,.rst,.kb_rows,.kb_cols,.controller,
        .event_valid,.event_code,.key_code,.controller_latched,.event_ack
    );

    // ---------------- GPU / 320x180 display ----------------
    logic gpu_mmio_valid,gpu_mmio_we,gpu_mmio_ready;
    logic [11:0] gpu_mmio_addr;
    logic [31:0] gpu_mmio_wdata,gpu_mmio_rdata;
    logic [3:0] gpu_mmio_wstrb;
    logic gpu_disp_valid,gpu_disp_bit,gpu_disp_ready;
    logic [15:0] gpu_disp_index;
    logic gpu_busy;
    logic gpu_vram_valid,gpu_vram_we,gpu_vram_ready;
    logic [14:0] gpu_vram_addr;
    logic [31:0] gpu_vram_wdata,gpu_vram_rdata;
    logic [3:0] gpu_vram_wstrb;

    shama_gpu #(.WIDTH(320),.HEIGHT(180),.QUEUE_DEPTH(8)) u_gpu(
        .clk,.rst,
        .mmio_valid(gpu_mmio_valid),.mmio_we(gpu_mmio_we),
        .mmio_addr(gpu_mmio_addr),.mmio_wdata(gpu_mmio_wdata),
        .mmio_wstrb(gpu_mmio_wstrb),.mmio_ready(gpu_mmio_ready),.mmio_rdata(gpu_mmio_rdata),
        .vram_valid(gpu_vram_valid),.vram_we(gpu_vram_we),.vram_addr(gpu_vram_addr),
        .vram_wdata(gpu_vram_wdata),.vram_wstrb(gpu_vram_wstrb),
        .vram_ready(gpu_vram_ready),.vram_rdata(gpu_vram_rdata),
        .disp_valid(gpu_disp_valid),.disp_index(gpu_disp_index),
        .disp_bit(gpu_disp_bit),.disp_ready(gpu_disp_ready),.busy(gpu_busy)
    );

    shama_display_bridge #(.WIDTH(320),.HEIGHT(180)) u_display_bridge(
        .clk,.rst,
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

    // ---------------- Dedicated physical 32 KiB VRAM ----------------
    shama_vram_adapter u_vram(
        .clk,.rst,
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

    shama_services u_services(
        .clk,.rst,
        .sys_valid,.sys_id,.sys_args,.sys_ready,.sys_ret,.sys_jump_valid,.sys_jump_pc,
        .event_valid,.event_code,.key_code,.controller_latched,.event_ack,
        .flash_used_bytes,.time_counter,.ram_used_bytes,.cache_used_bytes,
        .dma_valid(svc_dma_valid),.dma_we(svc_dma_we),.dma_flash(svc_dma_flash),
        .dma_cache(svc_dma_cache),.dma_addr(svc_dma_addr),.dma_wdata(svc_dma_wdata),.dma_wstrb(svc_dma_wstrb),
        .dma_ready(svc_dma_ready),.dma_rdata(svc_dma_rdata),
        .ext_valid(ext_valid),.ext_id(ext_id),.ext_args(ext_args),
        .ext_ready(ext_ready),.ext_ret(ext_ret),
        .ext_jump_valid(ext_jump_valid),.ext_jump_pc(ext_jump_pc),
        .ext_load_app,.ext_app_id,
        .os_loaded,.foreground_app
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
        .clk,.rst,
        .req_valid(ext_valid),.req_id(ext_id),.req_args(ext_args),
        .req_ready(ext_ready),.req_ret(ext_ret),
        .req_jump_valid(ext_jump_valid),.req_jump_pc(ext_jump_pc),
        .req_load_app(ext_load_app),.req_app_id(ext_app_id),
        .ram_used_bytes,.cache_used_bytes,.flash_used_bytes,
        .cpu_halted(halted),.sha_busy,.gpu_busy,
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
        .clk,.rst,
        .req_valid(fs_call_valid),.req_id(fs_call_id),.req_args(fs_call_args),
        .req_ready(fs_call_ready),.req_ret(fs_call_ret),
        .dma_valid(fs_dma_valid),.dma_we(fs_dma_we),.dma_flash(fs_dma_flash),
        .dma_addr(fs_dma_addr),.dma_wdata(fs_dma_wdata),.dma_wstrb(fs_dma_wstrb),
        .dma_ready(fs_dma_ready),.dma_rdata(fs_dma_rdata),
        .flash_used_bytes,.mounted(fs_mounted)
    );

    // The assembler accelerator is connected here. Until its request is
    // active it consumes no resources; its full implementation is synthesized
    // as a separate block and shares the DMA arbiter.
    logic asm_dma_valid,asm_dma_we,asm_dma_flash,asm_dma_ready;
    logic [31:0] asm_dma_addr,asm_dma_wdata,asm_dma_rdata;
    logic [3:0] asm_dma_wstrb;

    shama_asm_accel u_asm(
        .clk,.rst,
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
        .clk,.rst,
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
        .clk,.rst,
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
        .clk,.rst,
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
        cpu_mem_valid && cpu_mem_addr>=GPU_BASE && cpu_mem_addr<=GPU_END;

    always_comb begin
        cache_req_valid=0;cache_req_we=0;cache_req_addr=0;cache_req_wdata=0;cache_req_wstrb=0;
        ram_req_valid=0;ram_req_we=0;ram_req_addr=0;ram_req_wdata=0;ram_req_wstrb=0;
        flash_req_valid=0;flash_req_we=0;flash_req_addr=0;flash_req_wdata=0;flash_req_wstrb=0;

        gpu_mmio_valid=0;gpu_mmio_we=0;gpu_mmio_addr=0;gpu_mmio_wdata=0;gpu_mmio_wstrb=4'b0000;
        k_gpu_ready=0;k_gpu_rdata=gpu_mmio_rdata;

        cpu_mem_ready=0;
        cpu_mem_rdata=0;

        svc_dma_ready=0;svc_dma_rdata=0;
        fs_dma_ready=0;fs_dma_rdata=0;
        asm_dma_ready=0;asm_dma_rdata=0;

        // Kernel rendering owns GPU while CPU is stalled in the syscall.
        if(k_gpu_valid) begin
            gpu_mmio_valid=k_gpu_valid;gpu_mmio_we=k_gpu_we;
            gpu_mmio_addr=k_gpu_addr;gpu_mmio_wdata=k_gpu_wdata;gpu_mmio_wstrb=k_gpu_wstrb;
            k_gpu_ready=gpu_mmio_ready;k_gpu_rdata=gpu_mmio_rdata;
        end

        // Memory/DMA priority: boot > filesystem > assembler > CPU.
        if(svc_dma_valid) begin
            if(svc_dma_cache) begin
                cache_req_valid=1;cache_req_we=svc_dma_we;cache_req_addr=svc_dma_addr;
                cache_req_wdata=svc_dma_wdata;cache_req_wstrb=svc_dma_wstrb;
                svc_dma_ready=cache_req_ready;svc_dma_rdata=cache_req_rdata;
            end else if(svc_dma_flash) begin
                flash_req_valid=1;flash_req_we=svc_dma_we;flash_req_addr=svc_dma_addr;
                flash_req_wdata=svc_dma_wdata;flash_req_wstrb=svc_dma_wstrb;
                svc_dma_ready=flash_req_ready;svc_dma_rdata=flash_req_rdata;
            end else begin
                ram_req_valid=1;ram_req_we=svc_dma_we;ram_req_addr=svc_dma_addr;
                ram_req_wdata=svc_dma_wdata;ram_req_wstrb=svc_dma_wstrb;
                svc_dma_ready=ram_req_ready;svc_dma_rdata=ram_req_rdata;
            end
        end else if(fs_dma_valid) begin
            if(fs_dma_flash) begin
                flash_req_valid=1;flash_req_we=fs_dma_we;flash_req_addr=fs_dma_addr;
                flash_req_wdata=fs_dma_wdata;flash_req_wstrb=fs_dma_wstrb;
                fs_dma_ready=flash_req_ready;fs_dma_rdata=flash_req_rdata;
            end else begin
                ram_req_valid=1;ram_req_we=fs_dma_we;ram_req_addr=fs_dma_addr;
                ram_req_wdata=fs_dma_wdata;ram_req_wstrb=fs_dma_wstrb;
                fs_dma_ready=ram_req_ready;fs_dma_rdata=ram_req_rdata;
            end
        end else if(asm_dma_valid) begin
            if(asm_dma_flash) begin
                flash_req_valid=1;flash_req_we=asm_dma_we;flash_req_addr=asm_dma_addr;
                flash_req_wdata=asm_dma_wdata;flash_req_wstrb=asm_dma_wstrb;
                asm_dma_ready=flash_req_ready;asm_dma_rdata=flash_req_rdata;
            end else begin
                ram_req_valid=1;ram_req_we=asm_dma_we;ram_req_addr=asm_dma_addr;
                ram_req_wdata=asm_dma_wdata;ram_req_wstrb=asm_dma_wstrb;
                asm_dma_ready=ram_req_ready;asm_dma_rdata=ram_req_rdata;
            end
        end else if(cpu_mem_valid) begin
            if(cpu_mem_addr <= CACHE_END) begin
                cache_req_valid=1;cache_req_we=cpu_mem_we;cache_req_addr=cpu_mem_addr;
                cache_req_wdata=cpu_mem_wdata;cache_req_wstrb=cpu_mem_wstrb;
                cpu_mem_ready=cache_req_ready;cpu_mem_rdata=cache_req_rdata;
            end else if(cpu_mem_addr <= RAM_END) begin
                ram_req_valid=1;ram_req_we=cpu_mem_we;ram_req_addr=cpu_mem_addr;
                ram_req_wdata=cpu_mem_wdata;ram_req_wstrb=cpu_mem_wstrb;
                cpu_mem_ready=ram_req_ready;cpu_mem_rdata=ram_req_rdata;
            end else if(cpu_mem_addr >= FLASH_BASE && cpu_mem_addr <= FLASH_END) begin
                flash_req_valid=1;flash_req_we=cpu_mem_we;
                flash_req_addr=cpu_mem_addr-FLASH_BASE;
                flash_req_wdata=cpu_mem_wdata;flash_req_wstrb=cpu_mem_wstrb;
                cpu_mem_ready=flash_req_ready;cpu_mem_rdata=flash_req_rdata;
            end else if(cpu_mem_addr >= GPU_BASE && cpu_mem_addr <= GPU_END) begin
                if(!k_gpu_valid) begin
                    gpu_mmio_valid=1;gpu_mmio_we=cpu_mem_we;
                    gpu_mmio_addr=cpu_mem_addr-GPU_BASE;gpu_mmio_wdata=cpu_mem_wdata;
                    gpu_mmio_wstrb=cpu_mem_wstrb;
                    cpu_mem_ready=gpu_mmio_ready;cpu_mem_rdata=gpu_mmio_rdata;
                end
            end else if(cpu_mem_addr >= INPUT_BASE && cpu_mem_addr <= INPUT_END) begin
                cpu_mem_ready=1;
                case(cpu_mem_addr[5:2])
                    0: cpu_mem_rdata={22'd0,controller_latched};
                    1: cpu_mem_rdata={24'd0,key_code};
                    2: cpu_mem_rdata={23'd0,event_valid,event_code};
                    default: cpu_mem_rdata=0;
                endcase
            end else begin
                cpu_mem_ready=1;
                cpu_mem_rdata=0;
            end
        end
    end
endmodule
