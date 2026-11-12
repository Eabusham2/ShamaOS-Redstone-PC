// Real kernel and GPU: queued fences must not acknowledge later drawing.
module tb_kernel_fence;
    wire gpu_capacity_fault;
    logic clear_gpu_capacity_fault=0;
    logic [7:0] notice_code=0;
    wire notice_busy,notice_ready;
    localparam integer WIDTH=64,HEIGHT=32,PIXELS=WIDTH*HEIGHT,FRAME_WORDS=PIXELS/32;
    logic clk=0,rst=1,req_valid=0,raw_mode=1;
    always #5 clk=~clk;
    logic [11:0] req_id=12'h044;
    logic [191:0] req_args=0;
    wire req_ready,req_jump_valid,req_load_app;
    wire [63:0] req_ret;
    wire [31:0] req_jump_pc;
    wire [3:0] req_app_id;
    wire gpu_valid,gpu_we,gpu_ready;
    wire [11:0] gpu_addr;
    wire [31:0] gpu_wdata,gpu_rdata;
    wire [3:0] gpu_wstrb;
    wire fs_valid,asm_valid;
    wire [11:0] fs_id;
    wire [191:0] fs_args,asm_args;
    wire gpu_busy;
    shama_kernel_accel kernel(
        .clk,.rst,.req_valid,.req_id,.req_args,.req_ready,.req_ret,
        .req_jump_valid,.req_jump_pc,.req_load_app,.req_app_id,
        .ram_used_bytes(32'd0),.cache_used_bytes(32'd0),.flash_used_bytes(32'd0),
        .cpu_halted(1'b0),.sha_busy(1'b0),.gpu_busy,
        .gpu_capacity_fault,.notice_code,.notice_busy,.notice_ready,
        .gpu_valid,.gpu_we,.gpu_addr,.gpu_wdata,.gpu_wstrb,.gpu_ready,.gpu_rdata,
        .fs_valid,.fs_id,.fs_args,.fs_ready(1'b0),.fs_ret(64'd0),
        .asm_valid,.asm_args,.asm_ready(1'b0),.asm_ret(64'd0)
    );
    logic raw_valid=0;
    logic [11:0] raw_addr=0;
    logic [31:0] raw_data=0;
    wire mmio_ready;
    wire [31:0] mmio_rdata;
    assign gpu_ready=!raw_mode && mmio_ready;
    assign gpu_rdata=mmio_rdata;
    wire vram_valid,vram_we,disp_valid,disp_bit;
    wire [14:0] vram_addr;
    wire [31:0] vram_wdata;
    wire [3:0] vram_wstrb;
    wire [15:0] disp_index;
    integer cycles=0,updates=0;
    logic [31:0] memory[0:4095];
    logic screen[0:PIXELS-1];
    wire vram_ready=vram_valid && cycles%7==0;
    wire disp_ready=cycles%3==0;
    shama_gpu #(.WIDTH(WIDTH),.HEIGHT(HEIGHT),.QUEUE_DEPTH(8)) gpu(
        .clk,.rst,.mmio_valid(raw_mode?raw_valid:gpu_valid),.mmio_we(raw_mode?1'b1:gpu_we),
        .mmio_addr(raw_mode?raw_addr:gpu_addr),.mmio_wdata(raw_mode?raw_data:gpu_wdata),
        .mmio_wstrb(raw_mode?4'hf:gpu_wstrb),.mmio_ready,.mmio_rdata,
        .vram_valid,.vram_we,.vram_addr,.vram_wdata,.vram_wstrb,.vram_ready,
        .vram_rdata(memory[vram_addr>>2]),.disp_valid,.disp_index,.disp_bit,.disp_ready,.busy(gpu_busy),
        .capacity_fault(gpu_capacity_fault),.clear_capacity_fault(clear_gpu_capacity_fault)
    );
    always @(posedge clk) begin
        cycles<=cycles+1;
        if(cycles>100000) $fatal(1,"kernel fence watchdog");
        if(vram_valid && vram_ready && vram_we)
            for(integer b=0;b<4;b=b+1)
                if(vram_wstrb[b]) memory[vram_addr>>2][b*8+:8]<=vram_wdata[b*8+:8];
        if(disp_valid && disp_ready) begin
            screen[disp_index]<=disp_bit;
            updates<=updates+1;
        end
    end
    task automatic write_mmio(input [11:0] address,input [31:0] value);
        begin
            @(negedge clk);raw_valid=1;raw_addr=address;raw_data=value;
            @(posedge clk);while(!mmio_ready) @(posedge clk);
            @(negedge clk);raw_valid=0;
        end
    endtask
    task automatic submit(input [7:0] op,input [31:0] arg0);
        begin write_mmio(8,arg0);write_mmio(4,op);write_mmio(40,1);end
    endtask
    task automatic fence_and_check(input [31:0] expected_fence,input logic expected_pixel);
        integer front_word;
        begin
            @(negedge clk);raw_mode=0;req_valid=1;
            wait(req_ready);
            @(negedge clk);
            if(gpu_busy || req_ret!=={32'd0,expected_fence})
                $fatal(1,"kernel returned before its own fence: busy=%b count=%h return=%h",gpu_busy,gpu.fence_count,req_ret);
            front_word=gpu.front_sel?FRAME_WORDS:0;
            for(integer p=0;p<PIXELS;p=p+1) begin
                if(memory[front_word+p/32][p%32]!==expected_pixel || screen[p]!==expected_pixel)
                    $fatal(1,"fence returned before rendering/presentation finished at pixel %0d",p);
            end
            req_valid=0;
            repeat(3) @(negedge clk);raw_mode=1;
        end
    endtask
    initial begin
        for(integer i=0;i<4096;i=i+1) memory[i]=0;
        for(integer i=0;i<PIXELS;i=i+1) screen[i]=0;
        repeat(3) @(negedge clk);rst=0;
        submit(8'h01,0); // Keep the GPU busy before the first raw fence.
        submit(8'h16,0);
        submit(8'h01,1); // Must complete after that older fence.
        submit(8'h14,0); // Include display backpressure in the fence contract.
        if(gpu.fence_count!=0) $fatal(1,"test failed to queue an unfinished older fence");
        fence_and_check(2,1);
        // Seed only the counter to exercise 32-bit wrap without 2^32 jobs.
        @(negedge clk);gpu.fence_count=32'hffffffff;
        submit(8'h01,0);submit(8'h14,0);
        fence_and_check(0,0);
        if(updates!=2*PIXELS) $fatal(1,"missing presentation events");
        // A bad sprite discards a later queued fence. The kernel must release
        // its fence syscall with an error instead of waiting for that number.
        submit(8'h01,0);
        write_mmio(20,1);write_mmio(24,1);
        submit(8'h09,1024);submit(8'h16,0);
        @(negedge clk);raw_mode=0;req_valid=1;
        wait(req_ready);
        if(req_ret!==64'hfffffffffffffff6 || !gpu_capacity_fault)
            $fatal(1,"discarded fence did not release kernel for VRAM recovery");
        @(negedge clk);req_valid=0;clear_gpu_capacity_fault=1;
        @(negedge clk);clear_gpu_capacity_fault=0;
        repeat(3) @(negedge clk);raw_mode=1;
        fence_and_check(1,0);
        $display("KERNEL GPU FENCE QUEUED WORK AND WRAP PASS");$finish;
    end
endmodule
