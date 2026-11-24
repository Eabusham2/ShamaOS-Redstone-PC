// Observe the shipped kernel, 320x180 GPU and resident font on accepted
// serial display transfers. The initial blank is the saved lamp state.
module tb_kernel_title_crop;
    localparam WIDTH=320, HEIGHT=180;
    logic clk=0, rst=1, req_valid=0;
    always #5 clk=~clk;
    logic [11:0] req_id=0;
    logic [191:0] req_args=0;
    wire req_ready, gpu_valid, gpu_we, gpu_ready, gpu_busy, gpu_capacity_fault;
    wire [11:0] gpu_addr;
    wire [31:0] gpu_wdata, gpu_rdata;
    wire [3:0] gpu_wstrb;
    shama_kernel_accel kernel(
        .clk, .rst, .req_valid, .req_id, .req_args, .req_ready,
        .ram_used_bytes(32'd0), .cache_used_bytes(32'd0), .flash_used_bytes(32'd0),
        .cpu_halted(1'b0), .sha_busy(1'b0), .gpu_busy, .gpu_capacity_fault, .notice_code(8'd0),
        .gpu_valid, .gpu_we, .gpu_addr, .gpu_wdata, .gpu_wstrb, .gpu_ready, .gpu_rdata,
        .fs_ready(1'b0), .fs_ret(64'd0), .asm_ready(1'b0), .asm_ret(64'd0)
    );
    wire vram_valid, vram_we, disp_valid, disp_bit;
    wire [14:0] vram_addr;
    wire [31:0] vram_wdata;
    wire [3:0] vram_wstrb;
    wire [15:0] disp_index;
    integer cycles = 0, updates = 0;
    logic [31:0] memory [0:4095];
    logic screen [0:WIDTH*HEIGHT-1];
    bit received [0:WIDTH*HEIGHT-1];
    wire disp_ready = cycles % 5 != 0;
    shama_gpu gpu(
        .clk, .rst, .mmio_valid(gpu_valid), .mmio_we(gpu_we), .mmio_addr(gpu_addr),
        .mmio_wdata(gpu_wdata), .mmio_wstrb(gpu_wstrb), .mmio_ready(gpu_ready), .mmio_rdata(gpu_rdata),
        .vram_valid, .vram_we, .vram_addr, .vram_wdata, .vram_wstrb, .vram_ready(vram_valid),
        .vram_rdata(memory[vram_addr>>2]), .disp_valid, .disp_index, .disp_bit, .disp_ready,
        .busy(gpu_busy), .capacity_fault(gpu_capacity_fault), .clear_capacity_fault(1'b0)
    );
    always @(posedge clk) begin
        cycles<=cycles+1;
        if(cycles>2000000 || gpu_capacity_fault) $fatal(1,"title render stalled or exceeded VRAM");
        if(vram_valid && vram_we) begin
            if(vram_addr>=16384) $fatal(1,"title render outside physical VRAM");
            for(integer lane=0; lane<4; lane=lane+1) begin
                if(vram_wstrb[lane]) memory[vram_addr>>2][lane*8+:8]<=vram_wdata[lane*8+:8];
            end
        end
        if(disp_valid && disp_ready) begin
            if(disp_index>=WIDTH*HEIGHT) $fatal(1,"presented pixel outside display");
            screen[disp_index]<=disp_bit;
            received[disp_index]<=1;
            updates<=updates+1;
        end
    end
    task automatic view(input [11:0] id, input [31:0] arg1, input [31:0] arg2);
        begin
            @(negedge clk); req_id=id; req_args={128'd0,arg2,arg1}; req_valid=1;
            @(negedge clk); while(!req_ready) @(negedge clk);
            req_valid=0; @(negedge clk);
        end
    endtask
    task automatic capture(input integer frame);
        begin
            for(integer y=3; y<=11; y=y+1) begin
                $write("TITLE %0d %0d ",frame,y);
                for(integer x=3; x<=196; x=x+1) begin
                    if(frame>0 && !received[y*WIDTH+x]) $fatal(1,"missing serial transfer at (%0d,%0d)",x,y);
                    $write("%b",screen[y*WIDTH+x]);
                end
                $display("");
            end
        end
    endtask
    initial begin
        for(integer i=0; i<4096; i=i+1) memory[i]=0;
        for(integer i=0; i<WIDTH*HEIGHT; i=i+1) screen[i]=0;
        repeat(3) @(negedge clk); rst=0;
        capture(0);
        view(12'h045,1,0); // Desktop firmware selects Editor.
        if(updates<WIDTH*HEIGHT) $fatal(1,"GPU omitted the initial full presentation");
        view(12'h046,1,0); // Its live status redraw must preserve the title.
        capture(1);
        view(12'h048,0,0); // Fresh Editor menu selects New Text.
        capture(2);
        view(12'h048,0,1); // Different selection with an identical prefix.
        capture(3);
        view(12'h045,1,0); // Desktop after Home.
        capture(4);
        $display("KERNEL TITLE CROP PASS updates=%0d cycles=%0d",updates,cycles);
        $finish;
    end
endmodule
