module tb_asm_accel;
    logic clk=0,rst=1;
    logic req_valid=0,req_ready;
    logic [191:0] req_args=0;
    logic [63:0] req_ret;
    logic dma_valid,dma_we,dma_flash,dma_ready;
    logic [31:0] dma_addr,dma_wdata,dma_rdata;
    logic [3:0] dma_wstrb;

    logic [7:0] mem [0:8191];
    integer i;
    integer watchdog;

    always #5 clk=~clk;

    shama_asm_accel dut(
        .clk,.rst,.req_valid,.req_args,.req_ready,.req_ret,
        .dma_valid,.dma_we,.dma_flash,.dma_addr,.dma_wdata,.dma_wstrb,
        .dma_ready,.dma_rdata
    );

    always_comb begin
        dma_ready=dma_valid;
        dma_rdata={
            mem[{dma_addr[31:2],2'b00}+3],
            mem[{dma_addr[31:2],2'b00}+2],
            mem[{dma_addr[31:2],2'b00}+1],
            mem[{dma_addr[31:2],2'b00}+0]
        };
    end

    always_ff @(posedge clk) begin
        if(dma_valid && dma_we && !dma_flash) begin
            if(dma_wstrb[0]) mem[{dma_addr[31:2],2'b00}+0]<=dma_wdata[7:0];
            if(dma_wstrb[1]) mem[{dma_addr[31:2],2'b00}+1]<=dma_wdata[15:8];
            if(dma_wstrb[2]) mem[{dma_addr[31:2],2'b00}+2]<=dma_wdata[23:16];
            if(dma_wstrb[3]) mem[{dma_addr[31:2],2'b00}+3]<=dma_wdata[31:24];
        end
    end

    task automatic putstr(input integer base,input string s);
        integer k;
        begin
            for(k=0;k<s.len();k=k+1) mem[base+k]=s[k];
        end
    endtask

    function automatic [31:0] word_at(input integer base);
        word_at={mem[base+3],mem[base+2],mem[base+1],mem[base]};
    endfunction

    initial begin
        for(i=0;i<8192;i=i+1) mem[i]=0;

        putstr(16'h0100,
            "LDI r1 3\n.loop\nDEC r1\nBR.NE .loop\nHLT\n"
        );

        repeat(3) @(posedge clk);rst<=0;
        @(negedge clk);
        req_args[31:0]<=32'h00000100;
        req_args[63:32]<=38;
        req_args[95:64]<=32'h00001000;
        req_args[127:96]<=256;
        req_valid<=1;

        watchdog=0;
        while(!req_ready && watchdog<20000) begin @(posedge clk);watchdog=watchdog+1;end
        if(!req_ready) begin $display("ASM timeout");$fatal(1);end
        if(req_ret[63:32]!=0 || req_ret[31:0]!=24) begin
            $display("ASM ret fail %h",req_ret);$fatal(1);
        end

        if(word_at(16'h1000)!==32'h03100000) begin $display("w0 %h",word_at(16'h1000));$fatal(1);end
        if(word_at(16'h1004)!==32'h00000003) begin $display("w1");$fatal(1);end
        if(word_at(16'h1008)!==32'h19100000) begin $display("w2 %h",word_at(16'h1008));$fatal(1);end
        if(word_at(16'h100c)!==32'h41100000) begin $display("w3 %h",word_at(16'h100c));$fatal(1);end
        if(word_at(16'h1010)!==32'h00000002) begin $display("w4 %h",word_at(16'h1010));$fatal(1);end
        if(word_at(16'h1014)!==32'h01000000) begin $display("w5 %h",word_at(16'h1014));$fatal(1);end

        @(negedge clk);req_valid<=0;
        $display("ASM ACCEL PASS");
        $finish;
    end
endmodule
