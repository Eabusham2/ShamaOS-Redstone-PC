`include "shama_boot_params.svh"
module tb_asm_differential;
    reg clk=0,rst=1,req_valid=0;
    reg [191:0] req_args=0;
    wire req_ready_r,req_ready_c,dma_valid_r,dma_valid_c,dma_we_r,dma_we_c,dma_flash_r,dma_flash_c;
    wire [63:0] req_ret_r,req_ret_c;
    wire [31:0] dma_addr_r,dma_addr_c,dma_wdata_r,dma_wdata_c;
    wire [3:0] dma_wstrb_r,dma_wstrb_c;
    reg [7:0] source[0:65539];
    reg [31:0] expected[0:4095];
    reg [31:0] output_r[0:4095],output_c[0:4095];
    integer src_len,expected_len,expect_valid,cycles,i,j,unused,reads,writes,mode;
    reg [2047:0] source_path,expected_path;
    wire allow_dma=(mode==0 || (cycles%7!=1 && cycles%11!=2));
    wire dma_ready_r=dma_valid_r && allow_dma;
    wire dma_ready_c=dma_valid_c && allow_dma;
    wire [31:0] offset_r=dma_addr_r-`SHAMA_EDIT_BUF;
    wire [31:0] offset_c=dma_addr_c-`SHAMA_EDIT_BUF;
    wire [31:0] dma_rdata_r={source[{offset_r[15:2],2'b00}+3],source[{offset_r[15:2],2'b00}+2],source[{offset_r[15:2],2'b00}+1],source[{offset_r[15:2],2'b00}]};
    wire [31:0] dma_rdata_c={source[{offset_c[15:2],2'b00}+3],source[{offset_c[15:2],2'b00}+2],source[{offset_c[15:2],2'b00}+1],source[{offset_c[15:2],2'b00}]};
    always #5 clk=~clk;
    shama_asm_accel_reference reference(
      .clk(clk),.rst(rst),.req_valid(req_valid),.req_args(req_args),.req_ready(req_ready_r),.req_ret(req_ret_r),
      .dma_valid(dma_valid_r),.dma_we(dma_we_r),.dma_flash(dma_flash_r),.dma_addr(dma_addr_r),
      .dma_wdata(dma_wdata_r),.dma_wstrb(dma_wstrb_r),.dma_ready(dma_ready_r),.dma_rdata(dma_rdata_r));
    shama_asm_accel_candidate candidate(
      .clk(clk),.rst(rst),.req_valid(req_valid),.req_args(req_args),.req_ready(req_ready_c),.req_ret(req_ret_c),
      .dma_valid(dma_valid_c),.dma_we(dma_we_c),.dma_flash(dma_flash_c),.dma_addr(dma_addr_c),
      .dma_wdata(dma_wdata_c),.dma_wstrb(dma_wstrb_c),.dma_ready(dma_ready_c),.dma_rdata(dma_rdata_c));
    always @(posedge clk) if(!rst) begin
        if(req_ready_r!==req_ready_c || reference.state!==candidate.state)
            $fatal(1,"cycle/state divergence cycle=%0d reference=%0d candidate=%0d",cycles,reference.state,candidate.state);
        if(dma_valid_r!==dma_valid_c) $fatal(1,"DMA valid divergence cycle=%0d",cycles);
        if(dma_valid_r) begin
            if({dma_we_r,dma_flash_r,dma_addr_r,dma_wstrb_r}!=={dma_we_c,dma_flash_c,dma_addr_c,dma_wstrb_c})
                $fatal(1,"active DMA control divergence cycle=%0d",cycles);
            if(dma_we_r && dma_wdata_r!==dma_wdata_c)
                $fatal(1,"active DMA write-data divergence cycle=%0d",cycles);
            if(dma_ready_r) begin
                if(dma_we_r) begin
                    if(dma_addr_r<`SHAMA_RUN_STAGING || dma_addr_r>=`SHAMA_RUN_STAGING+16380 || dma_wstrb_r!=15)
                        $fatal(1,"write outside physical destination");
                    output_r[(dma_addr_r-`SHAMA_RUN_STAGING)>>2]<=dma_wdata_r;
                    output_c[(dma_addr_c-`SHAMA_RUN_STAGING)>>2]<=dma_wdata_c;
                    writes=writes+1;
                    $display("WRITE %08h %08h",dma_addr_r,dma_wdata_r);
                end else reads=reads+1;
            end
        end
        if(req_ready_r && req_ret_r!==req_ret_c) $fatal(1,"return/diagnostic divergence");
    end
    initial begin
        unused=$value$plusargs("SRC=%s",source_path);
        unused=$value$plusargs("EXPECT=%s",expected_path);
        unused=$value$plusargs("LEN=%d",src_len);
        unused=$value$plusargs("OUT=%d",expected_len);
        unused=$value$plusargs("VALID=%d",expect_valid);
        unused=$value$plusargs("MODE=%d",mode);
        cycles=0;reads=0;writes=0;
        for(i=0;i<65540;i=i+1) source[i]=0;
        for(i=0;i<4096;i=i+1) begin output_r[i]=32'hdeadbeef;output_c[i]=32'hdeadbeef;end
        $readmemh(source_path,source,0,src_len-1);
        if(expected_len>0) $readmemh(expected_path,expected,0,expected_len/4-1);
        repeat(3) @(posedge clk);
        @(negedge clk);rst=0;
        req_args[31:0]=`SHAMA_EDIT_BUF;req_args[63:32]=src_len;
        req_args[95:64]=`SHAMA_RUN_STAGING;req_args[127:96]=16380;
        req_valid=1;
        while(!req_ready_r && cycles<3000000) begin @(negedge clk);cycles=cycles+1;end
        if(!req_ready_r || !req_ready_c) $fatal(1,"assembler timeout or completion-cycle divergence");
        if(req_ret_r!==req_ret_c) $fatal(1,"return divergence");
        if(expect_valid) begin
            if(req_ret_r!={32'd0,expected_len[31:0]}) $fatal(1,"valid-case result mismatch %h",req_ret_r);
            if(writes!=expected_len/4) $fatal(1,"unexpected write count");
            for(j=0;j<expected_len/4;j=j+1)
                if(output_r[j]!==expected[j] || output_c[j]!==expected[j]) $fatal(1,"host binary mismatch word=%0d",j);
        end else if(req_ret_r[31:0]!=0 || req_ret_r[39:32]==0)
            $fatal(1,"invalid source unexpectedly accepted %h",req_ret_r);
        if(output_r[4095]!==32'hdeadbeef || output_c[4095]!==32'hdeadbeef) $fatal(1,"physical guard word changed");
        $display("DIFFERENTIAL PASS cycles=%0d reads=%0d writes=%0d return=%016h",cycles,reads,writes,req_ret_r);
        $finish;
    end
endmodule
