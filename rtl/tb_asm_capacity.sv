`include "shama_boot_params.svh"

// Exact source/output endpoints of the real assembler's reduced RAM contract.
module tb_asm_capacity;
    logic clk=0,rst=1,req_valid=0;
    always #5 clk=~clk;
    logic [191:0] req_args=0;
    wire req_ready,dma_valid,dma_we,dma_flash;
    wire [63:0] req_ret;
    wire [31:0] dma_addr,dma_wdata;
    wire [3:0] dma_wstrb;
    wire dma_ready=dma_valid;
    wire [31:0] dma_rdata=32'h0a544c48; // HLT followed by newline.
    integer writes=0,reads=0;
    shama_asm_accel dut(.*);
    always @(posedge clk) if(!rst && dma_valid) begin
        if(dma_flash || dma_addr>=`SHAMA_RAM_BYTES)
            $fatal(1,"assembler escaped physical RAM at%h",dma_addr);
        if(dma_we) begin
            writes<=writes+1;
            if(dma_wdata!==32'h01000000 || dma_wstrb!=15 || dma_addr!=req_args[95:64])
                $fatal(1,"endpoint assembly output mismatch");
        end else reads<=reads+1;
    end
    task automatic assemble_at(input [31:0] source_ptr,source_len,output_ptr,output_size,
                               input logic should_fail);
        integer before_reads,before_writes,watchdog;
        begin
            before_reads=reads;before_writes=writes;
            @(negedge clk);req_args={64'd0,output_size,output_ptr,source_len,source_ptr};req_valid=1;
            watchdog=0;
            while(!req_ready && watchdog<500) begin @(negedge clk);watchdog=watchdog+1;end
            if(!req_ready) $fatal(1,"bounded assembler request stalled");
            if(should_fail) begin
                if(req_ret!==64'h0000010100000000 || reads!=before_reads || writes!=before_writes)
                    $fatal(1,"invalid range was not rejected before DMA result=%h",req_ret);
            end else if(req_ret!==64'd4 || writes!=before_writes+1 || reads<=before_reads)
                $fatal(1,"valid endpoint assembly failed result=%h",req_ret);
            @(negedge clk);req_valid=0;
            repeat(2) @(negedge clk);
        end
    endtask
    initial begin
        repeat(3) @(negedge clk);rst=0;
        assemble_at(`SHAMA_RAM_BYTES-4,4,`SHAMA_RUN_STAGING,4,0);
        assemble_at(`SHAMA_EDIT_BUF,4,`SHAMA_RAM_BYTES-4,4,0);
        assemble_at(`SHAMA_RAM_BYTES,4,`SHAMA_RUN_STAGING,4,1);
        assemble_at(`SHAMA_RAM_BYTES-1,2,`SHAMA_RUN_STAGING,4,1);
        assemble_at(`SHAMA_EDIT_BUF,4,`SHAMA_RAM_BYTES,4,1);
        assemble_at(`SHAMA_EDIT_BUF,4,`SHAMA_RAM_BYTES-4,8,1);
        assemble_at(`SHAMA_EDIT_BUF,4,32'he0000,4,1);
        $display("ASSEMBLER PHYSICAL RAM ENDPOINTS PASS");
        $finish;
    end
endmodule
