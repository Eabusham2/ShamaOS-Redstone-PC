// Real320x180 GPU plus kernel notice overlay. Preserve both app frames,
// drawing resources and style, even with an unfinished frame and custom clip.
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
    integer cycles=0,updates=0,writes=0,saved_writes;
    logic [31:0] memory[0:4095];
    logic [31:0] saved_memory[0:4095];
    logic screen[0:PIXELS-1];
    logic [HEIGHT-1:0] saved_diff;
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
        if(gpu_capacity_fault || req_ready) $fatal(1,"autonomous popup faulted or impersonated a syscall");
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
    task automatic command(input [7:0] opcode,input [31:0] a0,a1,a2,a3);
        begin
            mmio_write(12'h008,a0);mmio_write(12'h00c,a1);
            mmio_write(12'h010,a2);mmio_write(12'h014,a3);
            mmio_write(12'h004,opcode);mmio_write(12'h028,1);
            wait(!gpu_busy);@(negedge clk);
        end
    endtask
    task automatic check_app_state;
        begin
            if(gpu.clip_x0!=1 || gpu.clip_y0!=1 || gpu.clip_x1!=2 || gpu.clip_y1!=2 ||
               gpu.draw_color || gpu.font_id!=0 || gpu.cursor_x!=10 || gpu.cursor_y!=10 ||
               !gpu.cursor_enabled || !gpu.cursor_single || gpu.buffer_diff_rows!==saved_diff)
                $fatal(1,"notice changed app drawing/cursor/dirty state");
            if(writes!=saved_writes) $fatal(1,"notice wrote app-owned VRAM");
            for(integer word_index=0;word_index<4096;word_index=word_index+1)
                if(memory[word_index]!==saved_memory[word_index])
                    $fatal(1,"notice corrupted app VRAM word%0d",word_index);
        end
    endtask
    initial begin
        for(integer i=0;i<4096;i=i+1) memory[i]=i<FRAME_WORDS?32'hffffffff:(32'h5a69c3f0 ^ (i*32'h01010101));
        for(integer i=0;i<PIXELS;i=i+1) screen[i]=1;
        repeat(3) @(negedge clk);rst=0;
        command(8'h0e,1,1,2,2); // A custom clip excludes the whole notice.
        command(8'h11,0,0,0,0); // And the app is drawing black.
        command(8'h12,10,10,3,0); // An active single-pixel overlay cursor.
        command(8'h15,0,0,0,0);
        if(screen[10*WIDTH+10]!==0) $fatal(1,"fixture cursor was not presented");
        for(integer i=0;i<4096;i=i+1) saved_memory[i]=memory[i];
        saved_diff=gpu.buffer_diff_rows;saved_writes=writes;
        setup_owner=0;notice_code=1;
        wait(notice_ready);
        @(negedge clk);
        if(!notice_busy || gpu.front_sel || screen[100*WIDTH+25]!==0 || screen[0]!==1)
            $fatal(1,"popup did not visibly overlay the application");
        check_app_state();
        notice_code=0;
        wait(!notice_busy);
        @(negedge clk);
        if(gpu.front_sel || gpu_busy) $fatal(1,"popup did not restore and fence original front");
        check_app_state();
        for(integer p=0;p<PIXELS;p=p+1)
            if(screen[p]!==1'(p!=10*WIDTH+10)) $fatal(1,"popup left a changed visible pixel%0d",p);
        // The app can still present the unfinished back frame after dismissal.
        setup_owner=1;command(8'h14,0,0,0,0);
        for(integer p=0;p<PIXELS;p=p+1)
            if(screen[p] !== (saved_memory[FRAME_WORDS+(p>>5)][p&31] ^ (p==10*WIDTH+10)))
                $fatal(1,"notice destroyed the unpresented app frame at pixel%0d",p);
        $display("KERNEL MODAL POPUP PIXEL RESTORE PASS updates=%0d cycles=%0d",updates,cycles);
        $finish;
    end
endmodule
