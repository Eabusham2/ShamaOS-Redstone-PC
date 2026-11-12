// Drive the real streaming assembler from source bytes and compare its emitted
// machine words against the host assembler, including exact diagnostics.
module tb_asm_cases;
    reg clk=0,rst=1,req_valid=0;
    wire req_ready,dma_valid,dma_we,dma_flash;
    reg [191:0] req_args=0;
    wire [63:0] req_ret;
    wire [31:0] dma_addr,dma_wdata;
    wire [3:0] dma_wstrb;
    wire dma_ready=dma_valid;
    reg [7:0] source[0:65539];
    reg [31:0] expected[0:4095];
    reg [31:0] output_words[0:4095];
    wire [31:0] dma_rdata={source[{dma_addr[15:2],2'b00}+3],source[{dma_addr[15:2],2'b00}+2],source[{dma_addr[15:2],2'b00}+1],source[{dma_addr[15:2],2'b00}]};
    integer src_len,expected_len,error_expected,error_line_expected,cycles,i,j,unused;
    reg [2047:0] source_path,expected_path;
    always #5 clk=~clk;
    shama_asm_accel dut(.*);
    always @(posedge clk) if(dma_valid && dma_we) begin
        if(dma_addr<32'h20000 || dma_addr>=32'h24000 || dma_wstrb!=15)
            $fatal(1,"assembler output outside destination: %h",dma_addr);
        output_words[(dma_addr-32'h20000)>>2]<=dma_wdata;
    end
    initial begin
        unused=$value$plusargs("SRC=%s",source_path);
        unused=$value$plusargs("EXPECT=%s",expected_path);
        unused=$value$plusargs("LEN=%d",src_len);
        unused=$value$plusargs("OUT=%d",expected_len);
        unused=$value$plusargs("ERROR=%d",error_expected);
        unused=$value$plusargs("LINE=%d",error_line_expected);
        for(i=0;i<65540;i=i+1) source[i]=0;
        for(i=0;i<4096;i=i+1) output_words[i]=32'hdeadbeef;
        $readmemh(source_path,source,0,src_len-1);
        if(expected_len>0) $readmemh(expected_path,expected,0,expected_len/4-1);
        repeat(3) @(posedge clk);
        @(negedge clk);rst=0;
        req_args[31:0]=0;req_args[63:32]=src_len;
        req_args[95:64]=32'h20000;req_args[127:96]=16384;
        req_valid=1;cycles=0;
        while(!req_ready && cycles<3000000) begin @(negedge clk);cycles=cycles+1;end
        if(!req_ready) $fatal(1,"assembler timeout");
        if(error_expected!=0) begin
            if(req_ret[31:0]!=0 || req_ret[39:32]!=error_expected || req_ret[63:40]!=error_line_expected)
                $fatal(1,"diagnostic expected line=%0d code=%0d actual=%h",error_line_expected,error_expected,req_ret);
        end else begin
            if(req_ret!={32'd0,expected_len[31:0]}) $fatal(1,"assembler result %h expected %0d",req_ret,expected_len);
            for(j=0;j<expected_len/4;j=j+1)
                if(output_words[j]!==expected[j]) $fatal(1,"machine-code mismatch word=%0d actual=%h expected=%h",j,output_words[j],expected[j]);
        end
        $display("ASSEMBLER CASE PASS %0d bytes %0d cycles",src_len,cycles);
        $finish;
    end
endmodule
