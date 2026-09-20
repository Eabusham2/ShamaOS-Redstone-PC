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

    output logic [319:0] display_row_data,
    output logic [7:0]   display_row_index,
    output logic         display_row_commit,
    input  logic         display_row_ready,

    output logic         ext_sys_valid,
    output logic [11:0]  ext_sys_id,
    output logic [191:0] ext_sys_args,
    input  logic         ext_sys_ready,
    input  logic [63:0]  ext_sys_ret,
    input  logic         ext_sys_jump_valid,
    input  logic [31:0]  ext_sys_jump_pc,

    input  logic [31:0]  ram_used_bytes,
    input  logic [31:0]  cache_used_bytes,
    input  logic [31:0]  flash_used_bytes,

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

    shama_cpu u_cpu(
        .clk,.rst,
        .mem_valid(cpu_mem_valid),.mem_we(cpu_mem_we),.mem_addr(cpu_mem_addr),
        .mem_wdata(cpu_mem_wdata),.mem_wstrb(cpu_mem_wstrb),
        .mem_ready(cpu_mem_ready),.mem_rdata(cpu_mem_rdata),
        .sys_valid,.sys_id,.sys_args,.sys_ready,.sys_ret,
        .sys_jump_valid,.sys_jump_pc,
        .cache_flush,.time_counter,.halted
    );

    // ---------------- Input ----------------
    logic event_valid,event_ack;
    logic [7:0] event_code,key_code;
    logic [9:0] controller_latched;

    shama_input u_input(
        .clk,.rst,.kb_rows,.kb_cols,.controller,
        .event_valid,.event_code,.key_code,.controller_latched,.event_ack
    );

    // ---------------- GPU ----------------
    logic gpu_mmio_valid,gpu_mmio_we,gpu_mmio_ready;
    logic [11:0] gpu_mmio_addr;
    logic [31:0] gpu_mmio_wdata,gpu_mmio_rdata;
    logic gpu_disp_valid,gpu_disp_bit,gpu_disp_ready;
    logic [15:0] gpu_disp_index;

    shama_gpu #(.WIDTH(320),.HEIGHT(180),.QUEUE_DEPTH(8)) u_gpu(
        .clk,.rst,
        .mmio_valid(gpu_mmio_valid),.mmio_we(gpu_mmio_we),
        .mmio_addr(gpu_mmio_addr),.mmio_wdata(gpu_mmio_wdata),
        .mmio_ready(gpu_mmio_ready),.mmio_rdata(gpu_mmio_rdata),
        .disp_valid(gpu_disp_valid),.disp_index(gpu_disp_index),
        .disp_bit(gpu_disp_bit),.disp_ready(gpu_disp_ready)
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

    // ---------------- ShamaOS services / boot DMA ----------------
    logic dma_valid,dma_we,dma_flash,dma_ready;
    logic [31:0] dma_addr,dma_wdata,dma_rdata;
    logic [3:0] dma_wstrb;

    shama_services u_services(
        .clk,.rst,
        .sys_valid,.sys_id,.sys_args,.sys_ready,.sys_ret,.sys_jump_valid,.sys_jump_pc,
        .event_valid,.event_code,.key_code,.controller_latched,.event_ack,
        .ram_used_bytes,.cache_used_bytes,.flash_used_bytes,.time_counter,
        .dma_valid,.dma_we,.dma_flash,.dma_addr,.dma_wdata,.dma_wstrb,.dma_ready,.dma_rdata,
        .ext_valid(ext_sys_valid),.ext_id(ext_sys_id),.ext_args(ext_sys_args),
        .ext_ready(ext_sys_ready),.ext_ret(ext_sys_ret),
        .ext_jump_valid(ext_sys_jump_valid),.ext_jump_pc(ext_sys_jump_pc)
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

    // DMA gets priority on RAM/flash while boot or kernel services are moving
    // persistent data. CPU automatically stalls because its request is not
    // forwarded until DMA releases the bus.
    always_comb begin
        cache_req_valid=0;cache_req_we=0;cache_req_addr=0;cache_req_wdata=0;cache_req_wstrb=0;
        ram_req_valid=0;ram_req_we=0;ram_req_addr=0;ram_req_wdata=0;ram_req_wstrb=0;
        flash_req_valid=0;flash_req_we=0;flash_req_addr=0;flash_req_wdata=0;flash_req_wstrb=0;

        gpu_mmio_valid=0;gpu_mmio_we=0;gpu_mmio_addr=0;gpu_mmio_wdata=0;

        cpu_mem_ready=0;
        cpu_mem_rdata=0;
        dma_ready=0;
        dma_rdata=0;

        if(dma_valid) begin
            if(dma_flash) begin
                flash_req_valid=dma_valid;
                flash_req_we=dma_we;
                flash_req_addr=dma_addr;
                flash_req_wdata=dma_wdata;
                flash_req_wstrb=dma_wstrb;
                dma_ready=flash_req_ready;
                dma_rdata=flash_req_rdata;
            end else begin
                ram_req_valid=dma_valid;
                ram_req_we=dma_we;
                ram_req_addr=dma_addr;
                ram_req_wdata=dma_wdata;
                ram_req_wstrb=dma_wstrb;
                dma_ready=ram_req_ready;
                dma_rdata=ram_req_rdata;
            end
        end else if(cpu_mem_valid) begin
            if(cpu_mem_addr <= CACHE_END) begin
                cache_req_valid=1;
                cache_req_we=cpu_mem_we;
                cache_req_addr=cpu_mem_addr;
                cache_req_wdata=cpu_mem_wdata;
                cache_req_wstrb=cpu_mem_wstrb;
                cpu_mem_ready=cache_req_ready;
                cpu_mem_rdata=cache_req_rdata;
            end else if(cpu_mem_addr <= RAM_END) begin
                ram_req_valid=1;
                ram_req_we=cpu_mem_we;
                ram_req_addr=cpu_mem_addr;
                ram_req_wdata=cpu_mem_wdata;
                ram_req_wstrb=cpu_mem_wstrb;
                cpu_mem_ready=ram_req_ready;
                cpu_mem_rdata=ram_req_rdata;
            end else if(cpu_mem_addr >= FLASH_BASE && cpu_mem_addr <= FLASH_END) begin
                flash_req_valid=1;
                flash_req_we=cpu_mem_we;
                flash_req_addr=cpu_mem_addr-FLASH_BASE;
                flash_req_wdata=cpu_mem_wdata;
                flash_req_wstrb=cpu_mem_wstrb;
                cpu_mem_ready=flash_req_ready;
                cpu_mem_rdata=flash_req_rdata;
            end else if(cpu_mem_addr >= GPU_BASE && cpu_mem_addr <= GPU_END) begin
                gpu_mmio_valid=1;
                gpu_mmio_we=cpu_mem_we;
                gpu_mmio_addr=cpu_mem_addr-GPU_BASE;
                gpu_mmio_wdata=cpu_mem_wdata;
                cpu_mem_ready=gpu_mmio_ready;
                cpu_mem_rdata=gpu_mmio_rdata;
            end else if(cpu_mem_addr >= INPUT_BASE && cpu_mem_addr <= INPUT_END) begin
                cpu_mem_ready=1;
                case(cpu_mem_addr[5:2])
                    0: cpu_mem_rdata={22'd0,controller_latched};
                    1: cpu_mem_rdata={24'd0,key_code};
                    2: cpu_mem_rdata={23'd0,event_valid,event_code};
                    default: cpu_mem_rdata=0;
                endcase
            end else begin
                // Unmapped access completes with zero instead of deadlocking.
                cpu_mem_ready=1;
                cpu_mem_rdata=0;
            end
        end
    end
endmodule
