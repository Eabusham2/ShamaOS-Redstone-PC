// A queued app resource fault must never discard an acknowledged OS notice.
// Exercise show, changed choice and cancellation while older GPU work drains.
module tb_popup_fault_interleave;
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
    logic setup_owner=1,setup_valid=0;
    logic [11:0] setup_addr=0;
    logic [31:0] setup_data=0;
    wire mmio_ready;
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
    integer cycles=0,updates=0,writes=0,before_updates;
    logic [31:0] memory[0:4095];
    logic screen[0:PIXELS-1];
    wire vram_ready=vram_valid && cycles%3==0;
    wire disp_ready=cycles%5!=0;
    shama_gpu gpu(
        .clk,.rst,.mmio_valid(setup_owner?setup_valid:gpu_valid),.mmio_we(setup_owner?1'b1:gpu_we),
        .mmio_addr(setup_owner?setup_addr:gpu_addr),.mmio_wdata(setup_owner?setup_data:gpu_wdata),
        .mmio_wstrb(setup_owner?4'hf:gpu_wstrb),.mmio_ready(mmio_ready),.mmio_rdata(gpu_rdata),
        .vram_valid,.vram_we,.vram_addr,.vram_wdata,.vram_wstrb,.vram_ready,
        .vram_rdata(memory[vram_addr>>2]),.disp_valid,.disp_index,.disp_bit,.disp_ready,
        .busy(gpu_busy),.capacity_fault(gpu_capacity_fault),.clear_capacity_fault(1'b0)
    );
    assign gpu_ready=mmio_ready && !setup_owner;
    always @(posedge clk) begin
        cycles<=cycles+1;
        if(cycles>2000000) $fatal(1,"popup watchdog kernel=%0d GPU=%0d",kernel.state,gpu.state);
        if(vram_valid && vram_addr>=16384) $fatal(1,"popup exceeded physical VRAM");
        if(req_ready) $fatal(1,"autonomous popup faulted or impersonated a syscall");
        if(notice_ready && (notice_code==0 || gpu.notice_code!=notice_code || gpu_busy ||
                            screen[100*WIDTH+25]!==0))
            $fatal(1,"notice acknowledged before its requested visible presentation completed");
        if(vram_valid && vram_ready && vram_we) begin
            writes<=writes+1;
            for(integer lane=0;lane<4;lane=lane+1)
                if(vram_wstrb[lane]) memory[vram_addr>>2][lane*8+:8]<=vram_wdata[lane*8+:8];
        end
        if(disp_valid && disp_ready) begin screen[disp_index]<=disp_bit;updates<=updates+1;end
    end
    task automatic mmio_write(input [11:0] address,input [31:0] data);
        begin
            @(negedge clk);setup_valid=1;setup_addr=address;setup_data=data;
            @(negedge clk);while(!mmio_ready) @(negedge clk);
            setup_valid=0;
        end
    endtask
    task automatic enqueue_faulting_draw;
        begin
            setup_owner=1;
            // A long valid clear leaves time for both the invalid BLIT and the
            // OS request to queue. The BLIT will discard all following work.
            mmio_write(12'h008,0);mmio_write(12'h004,1);mmio_write(12'h028,1);
            mmio_write(12'h008,1024);mmio_write(12'h00c,0);mmio_write(12'h010,0);
            mmio_write(12'h014,1);mmio_write(12'h018,1);
            mmio_write(12'h004,9);mmio_write(12'h028,1);
            if(!gpu_busy || gpu.q_count==0) $fatal(1,"fixture did not queue fault behind app draw");
            setup_owner=0;
        end
    endtask
    task automatic dismiss;
        begin
            @(negedge clk);notice_code=0;
            wait(!notice_busy);@(negedge clk);
            if(gpu.notice_code!=0 || notice_ready || gpu_busy)
                $fatal(1,"sticky resource fault prevented notice restore/fence");
            for(integer p=0;p<PIXELS;p=p+1)
                if(screen[p]!==1) $fatal(1,"notice dismissal damaged original front pixel %d",p);
        end
    endtask
    initial begin
        for(integer i=0;i<4096;i=i+1) memory[i]=32'hffffffff;
        for(integer i=0;i<PIXELS;i=i+1) screen[i]=1;
        repeat(3) @(negedge clk);rst=0;
        enqueue_faulting_draw();notice_code=1;
        wait(notice_ready);@(negedge clk);
        if(!gpu_capacity_fault || gpu.notice_code!=1)
            $fatal(1,"app fault erased a supposedly ready flash warning");
        dismiss();

        // An updated choice while draining must be the one actually drawn;
        // the earlier Keep App request may never authorize the later choice.
        enqueue_faulting_draw();notice_code=6;
        wait(notice_busy);repeat(10) @(negedge clk);
        if(!gpu_busy || notice_ready) $fatal(1,"choice fixture did not wait behind app work");
        notice_code=7;
        wait(notice_ready);@(negedge clk);
        if(gpu.notice_code!=7) $fatal(1,"stale choice was rendered after drain");
        notice_code=6;#1;wait(notice_ready);@(negedge clk);
        if(gpu.notice_code!=6) $fatal(1,"Keep App selection failed with sticky GPU fault");
        dismiss();

        // Cancellation before any show was submitted must not flash the stale
        // warning or advertise a ready invisible choice. No scanout is needed.
        enqueue_faulting_draw();before_updates=updates;notice_code=6;
        wait(notice_busy);repeat(10) @(negedge clk);
        if(!gpu_busy || notice_ready) $fatal(1,"cancel fixture did not wait behind app work");
        notice_code=0;
        wait(!notice_busy);repeat(5) @(negedge clk);
        if(notice_ready || gpu.notice_code!=0 || updates!=before_updates)
            $fatal(1,"cancelled pending notice was rendered or acknowledged");
        for(integer p=0;p<PIXELS;p=p+1)
            if(screen[p]!==1) $fatal(1,"cancelled notice changed front pixel %d",p);
        $display("KERNEL POPUP RESOURCE FAULT INTERLEAVING PASS cycles=%0d",cycles);
        $finish;
    end
endmodule
