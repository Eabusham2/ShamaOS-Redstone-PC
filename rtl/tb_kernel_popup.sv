// Real320x180 GPU plus kernel popup: retain and restore every underlying pixel.
module tb_kernel_popup;
    localparam integer WIDTH=320,HEIGHT=180,PIXELS=WIDTH*HEIGHT,FRAME_WORDS=PIXELS/32;
    logic clk=0,rst=1;
    always #5 clk=~clk;
    logic [7:0] notice_code=0;
    wire notice_busy,notice_ready,gpu_capacity_fault;
    wire req_ready,req_jump_valid,req_load_app;
    wire [63:0] req_ret;
    wire [31:0] req_jump_pc;
    wire [3:0] req_app_id;
    wire gpu_valid,gpu_we,gpu_ready,gpu_busy;
    wire [11:0] gpu_addr;
    wire [31:0] gpu_wdata,gpu_rdata;
    wire [3:0] gpu_wstrb;
    wire fs_valid,asm_valid;
    wire [11:0] fs_id;
    wire [191:0] fs_args,asm_args;
    shama_kernel_accel kernel(
        .clk,.rst,.req_valid(1'b0),.req_id(12'd0),.req_args(192'd0),
        .req_ready,.req_ret,.req_jump_valid,.req_jump_pc,.req_load_app,.req_app_id,
        .ram_used_bytes(32'd0),.cache_used_bytes(32'd0),.flash_used_bytes(32'd486400),
        .cpu_halted(1'b0),.sha_busy(1'b0),.gpu_busy,.gpu_capacity_fault,
        .notice_code,.notice_busy,.notice_ready,
        .gpu_valid,.gpu_we,.gpu_addr,.gpu_wdata,.gpu_wstrb,.gpu_ready,.gpu_rdata,
        .fs_valid,.fs_id,.fs_args,.fs_ready(1'b0),.fs_ret(64'd0),
        .asm_valid,.asm_args,.asm_ready(1'b0),.asm_ret(64'd0)
    );
    wire vram_valid,vram_we,disp_valid,disp_bit;
    wire [14:0] vram_addr;
    wire [31:0] vram_wdata;
    wire [3:0] vram_wstrb;
    wire [15:0] disp_index;
    integer cycles=0,updates=0;
    logic [31:0] memory[0:4095];
    logic screen[0:PIXELS-1];
    wire vram_ready=vram_valid && cycles%3==0;
    wire disp_ready=cycles%5!=0;
    shama_gpu gpu(
        .clk,.rst,.mmio_valid(gpu_valid),.mmio_we(gpu_we),.mmio_addr(gpu_addr),
        .mmio_wdata(gpu_wdata),.mmio_wstrb(gpu_wstrb),.mmio_ready(gpu_ready),.mmio_rdata(gpu_rdata),
        .vram_valid,.vram_we,.vram_addr,.vram_wdata,.vram_wstrb,.vram_ready,
        .vram_rdata(memory[vram_addr>>2]),.disp_valid,.disp_index,.disp_bit,.disp_ready,
        .busy(gpu_busy),.capacity_fault(gpu_capacity_fault),.clear_capacity_fault(1'b0)
    );
    always @(posedge clk) begin
        cycles<=cycles+1;
        if(cycles>2000000) $fatal(1,"popup watchdog kernel=%0d GPU=%0d",kernel.state,gpu.state);
        if(vram_valid && vram_addr>=16384) $fatal(1,"popup exceeded physical VRAM");
        if(gpu_capacity_fault || req_ready) $fatal(1,"autonomous popup faulted or impersonated a syscall");
        if(vram_valid && vram_ready && vram_we)
            for(integer lane=0;lane<4;lane=lane+1)
                if(vram_wstrb[lane]) memory[vram_addr>>2][lane*8+:8]<=vram_wdata[lane*8+:8];
        if(disp_valid && disp_ready) begin screen[disp_index]<=disp_bit;updates<=updates+1;end
    end
    initial begin
        for(integer i=0;i<4096;i=i+1) memory[i]=i<FRAME_WORDS?32'hffffffff:0;
        for(integer i=0;i<PIXELS;i=i+1) screen[i]=1;
        repeat(3) @(negedge clk);rst=0;notice_code=1;
        wait(notice_ready);
        @(negedge clk);
        if(!notice_busy || !gpu.front_sel || screen[100*WIDTH+25]!==0 || screen[0]!==1)
            $fatal(1,"popup did not visibly overlay the application");
        for(integer i=0;i<FRAME_WORDS;i=i+1)
            if(memory[i]!==32'hffffffff) $fatal(1,"popup modified saved underlying pixel word%0d",i);
        notice_code=0;
        wait(!notice_busy);
        @(negedge clk);
        if(gpu.front_sel || gpu_busy) $fatal(1,"popup did not restore and fence original front");
        for(integer p=0;p<PIXELS;p=p+1)
            if(screen[p]!==1) $fatal(1,"popup left a changed visible pixel%0d",p);
        $display("KERNEL MODAL POPUP PIXEL RESTORE PASS updates=%0d cycles=%0d",updates,cycles);
        $finish;
    end
endmodule
