module tb_memory_adapter_reset;
    logic clk=0,rst=1,req_valid=0,req_we=0;
    logic [31:0] req_addr=0,req_wdata=0;
    logic [3:0] req_wstrb=0;
    wire req_ready,read_enable,write_commit;
    wire [31:0] req_rdata,write_data;
    wire [2:0] bank_select;
    wire [2:0] row_address;
    wire [7:0] row_select=8'b1 << row_address;
    logic [31:0] memory[0:23];
    integer bank,row,commits=0,cycles=0;
    wire [31:0] selected_word=read_enable?memory[bank*8+row]:0;
    shama_memory_adapter #(.BANK_BITS(2),.BANK_COUNT(3),.ROW_BITS(3)) dut(.*);
    always #5 clk=~clk;
    always_comb begin
        bank=0;row=0;
        for(integer b=0;b<3;b=b+1) if(bank_select[b]) bank=b;
        for(integer r=0;r<8;r=r+1) if(row_select[r]) row=r;
    end
    always @(posedge clk) begin
        cycles<=cycles+1;
        if(cycles>500) $fatal(1,"direct reset watchdog");
        // Persistent cells have no logic-reset input. The controller itself
        // must suppress write enable before resetting its selected address.
        if(write_commit) begin memory[bank*8+row]<=write_data;commits<=commits+1;end
    end
    task automatic start_write;
        begin
            @(negedge clk);req_valid=1;req_we=1;req_addr=92;
            req_wdata=32'h12345678;req_wstrb=15;
        end
    endtask
    task automatic reset_logic;
        begin
            @(negedge clk);rst=1;req_valid=0;
            #1;if(write_commit || read_enable || req_ready) $fatal(1,"reset did not suppress bus enables");
            repeat(3) @(negedge clk);rst=0;
        end
    endtask
    task automatic check_unchanged;
        begin
            for(integer word_index=0;word_index<24;word_index=word_index+1)
                if(memory[word_index]!==32'hc0ff0000+word_index)
                    $fatal(1,"reset caused an unintended persistent write at%0d",word_index);
        end
    endtask
    initial begin
        for(integer i=0;i<24;i=i+1) memory[i]=32'hc0ff0000+i;
        repeat(3) @(negedge clk);rst=0;
        start_write();wait(read_enable);reset_logic();check_unchanged();
        start_write();wait(write_commit);reset_logic();check_unchanged();
        if(commits!=0) $fatal(1,"an aborted write committed");
        start_write();wait(req_ready);
        if(req_rdata!==32'h12345678 || memory[23]!==32'h12345678 || commits!=1)
            $fatal(1,"normal post-reset write failed");
        reset_logic();
        if(memory[23]!==32'h12345678 || memory[0]!==32'hc0ff0000 || commits!=1)
            $fatal(1,"reset erased persistent data or duplicated a commit");
        $display("DIRECT RESET WRITE-SUPPRESSION AND RETENTION PASS");$finish;
    end
endmodule
