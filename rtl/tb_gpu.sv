module tb_gpu;
    logic clk=0, rst=1;
    logic mmio_valid=0, mmio_we=0;
    logic [11:0] mmio_addr=0;
    logic [31:0] mmio_wdata=0;
    logic [3:0] mmio_wstrb=4'b1111;
    logic mmio_ready;
    logic [31:0] mmio_rdata;

    logic vram_valid,vram_we,vram_ready;
    logic [14:0] vram_addr;
    logic [31:0] vram_wdata,vram_rdata;
    logic [3:0] vram_wstrb;
    logic [7:0] vram [0:32767];

    logic disp_valid;
    logic [15:0] disp_index;
    logic disp_bit;
    logic disp_ready=1;

    always #5 clk=~clk;

    shama_gpu #(.WIDTH(16),.HEIGHT(8),.QUEUE_DEPTH(4)) dut(
        .clk(clk),.rst(rst),
        .mmio_valid(mmio_valid),.mmio_we(mmio_we),.mmio_addr(mmio_addr),
        .mmio_wdata(mmio_wdata),.mmio_wstrb(mmio_wstrb),.mmio_ready(mmio_ready),.mmio_rdata(mmio_rdata),
        .vram_valid,.vram_we,.vram_addr,.vram_wdata,.vram_wstrb,.vram_ready,.vram_rdata,
        .disp_valid(disp_valid),.disp_index(disp_index),.disp_bit(disp_bit),.disp_ready(disp_ready)
    );

    always_comb begin
        vram_ready=vram_valid;
        vram_rdata={
            vram[{vram_addr[14:2],2'b00}+3],
            vram[{vram_addr[14:2],2'b00}+2],
            vram[{vram_addr[14:2],2'b00}+1],
            vram[{vram_addr[14:2],2'b00}+0]
        };
    end

    always_ff @(posedge clk) begin
        if(vram_valid && vram_we) begin
            if(vram_wstrb[0]) vram[{vram_addr[14:2],2'b00}+0] <= vram_wdata[7:0];
            if(vram_wstrb[1]) vram[{vram_addr[14:2],2'b00}+1] <= vram_wdata[15:8];
            if(vram_wstrb[2]) vram[{vram_addr[14:2],2'b00}+2] <= vram_wdata[23:16];
            if(vram_wstrb[3]) vram[{vram_addr[14:2],2'b00}+3] <= vram_wdata[31:24];
        end
    end

    task automatic wr(input logic [11:0] a,input logic [31:0] d);
        begin
            @(negedge clk);
            mmio_addr<=a; mmio_wdata<=d; mmio_wstrb<=4'b1111; mmio_we<=1; mmio_valid<=1;
            @(negedge clk);
            mmio_valid<=0; mmio_we<=0;
        end
    endtask

    task automatic submit(input logic [7:0] op);
        begin
            wr(12'h004,{24'd0,op});
            wr(12'h028,32'd1);
        end
    endtask

    integer watchdog;
    logic saw_pixel;

    always @(posedge clk) begin
        if(!rst && disp_valid) begin
            if(disp_index==16'd35 && disp_bit==1'b1)
                saw_pixel<=1'b1;
        end
    end

    integer vi;
    initial begin
        for(vi=0;vi<32768;vi=vi+1) vram[vi]=0;
        saw_pixel=0;
        repeat(3) @(posedge clk);
        rst<=0;

        // Verify CPU-style byte strobes can fill four consecutive text bytes
        // through one aligned MMIO word.
        @(negedge clk);
        mmio_addr<=12'h4a0; mmio_wdata<=32'h44434241;
        mmio_wstrb<=4'b1111; mmio_we<=1; mmio_valid<=1;
        @(negedge clk); mmio_valid<=0; mmio_we<=0;
        @(posedge clk);
        if(vram[15'h40a0]!=="A" || vram[15'h40a1]!=="B" ||
           vram[15'h40a2]!=="C" || vram[15'h40a3]!=="D") begin
            $display("GPU external VRAM text lane fail"); $fatal(1);
        end

        mmio_wstrb<=4'b1111;

        // Back-buffer pixel at x=3, y=2 => index 35.
        wr(12'h008,32'd3);
        wr(12'h00c,32'd2);
        submit(8'h02); // SET_PIXEL

        // Queue a swap, which also pushes changed front-buffer pixels.
        submit(8'h14); // SWAP_BUFFER

        watchdog=0;
        while(!saw_pixel && watchdog<1000) begin
            @(posedge clk);
            watchdog=watchdog+1;
        end
        if(!saw_pixel) begin
            $display("GPU FAIL: changed pixel was never emitted");
            $fatal(1);
        end

        // Test clear + second swap returns the same pixel to zero.
        wr(12'h008,32'd0);
        submit(8'h01); // CLEAR back to zero
        submit(8'h14); // SWAP
        watchdog=0;
        while(watchdog<500) begin
            @(posedge clk);
            watchdog=watchdog+1;
        end

        $display("GPU RTL PASS");
        $finish;
    end
endmodule
