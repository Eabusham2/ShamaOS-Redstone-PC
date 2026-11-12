`include "shama_boot_params.svh"

// Actual capacity wrappers, controller, and one-hot rings. The storage fixture
// models isolated selected-bank read returns, with words interleaved by physical
// layer. Physical cell propagation/return-wire timing is tested separately.
module tb_memory_adapter #(
    parameter integer KIND=0,
    parameter integer LAYERS=8
);
    localparam integer BANKS=(KIND==0)?`SHAMA_CACHE_BANKS:(KIND==1)?`SHAMA_RAM_BANKS:(KIND==2)?`SHAMA_FLASH_BANKS:`SHAMA_VRAM_BANKS;
    localparam integer ROWS=1024;
    localparam integer GROUPS=(BANKS+LAYERS-1)/LAYERS;
    localparam integer PHYSICAL_WORDS=GROUPS*LAYERS*ROWS;
    logic clk=0,rst=1,req_valid=0,req_we=0;
    always #5 clk=~clk;
    logic [31:0] req_addr=0,req_wdata=0;
    logic [3:0] req_wstrb=0;
    wire req_ready,read_enable,write_commit;
    wire [31:0] req_rdata,write_data;
    wire [BANKS-1:0] bank_select;
    wire [ROWS-1:0] row_select;
    wire [31:0] selected_word;
    generate
        if(KIND==0) begin : cache
            shama_cache_adapter dut(.*);
        end else if(KIND==1) begin : ram
            shama_ram_adapter dut(.*);
        end else if(KIND==2) begin : flash
            shama_flash_adapter dut(.*);
        end else begin : vram
            shama_vram_adapter dut(.*);
        end
    endgenerate

    logic [31:0] storage[0:PHYSICAL_WORDS-1];
    logic [31:0] expected_first[0:BANKS-1],expected_last[0:BANKS-1];
    integer selected_bank,selected_row,physical_word;
    integer cycles=0,commits=0,requests=0;

    function automatic integer placement(input integer bank,row);
        // bank = group*LAYERS + layer; the layer is the innermost physical
        // dimension. This intentionally differs from logical bank-major order.
        placement=((bank/LAYERS)*ROWS+row)*LAYERS+(bank%LAYERS);
    endfunction
    function automatic [31:0] merged(input [31:0] old_word,new_word,input [3:0] strobes);
        logic [31:0] mask;
        begin
            mask={{8{strobes[3]}},{8{strobes[2]}},{8{strobes[1]}},{8{strobes[0]}}};
            merged=(old_word & ~mask) | (new_word & mask);
        end
    endfunction
    always_comb begin
        selected_bank=0;selected_row=0;
        for(integer b=0;b<BANKS;b=b+1) if(bank_select[b]) selected_bank=b;
        for(integer r=0;r<ROWS;r=r+1) if(row_select[r]) selected_row=r;
        physical_word=placement(selected_bank,selected_row);
    end
    // With asserted one-hot selection, the OR of all isolated per-bank read
    // outputs equals this one selected word; disabled banks contribute zero.
    assign selected_word=read_enable?storage[physical_word]:32'd0;
    always @(posedge clk) begin
        cycles<=cycles+1;
        if(cycles>3000000) $fatal(1,"adapter watchdog");
        if(!rst) begin
            if(bank_select==0 || (bank_select & (bank_select-1'b1))!=0 ||
               row_select==0 || (row_select & (row_select-1'b1))!=0)
                $fatal(1,"physical bank/row selection is not one-hot");
            if(write_commit) begin
                if(!read_enable) $fatal(1,"byte read/modify/write disabled the old-word read path");
                storage[physical_word]<=write_data;
                commits<=commits+1;
            end
        end
    end
    task automatic access(input logic write_request,input [31:0] address,data,
                          input [3:0] strobes,input [31:0] expected,input logic drop_valid);
        integer watchdog;
        begin
            @(negedge clk);req_valid=1;req_we=write_request;req_addr=address;
            req_wdata=data;req_wstrb=strobes;
            watchdog=0;
            @(negedge clk);
            while(!req_ready && watchdog<2*(BANKS+ROWS+16)) begin
                @(negedge clk);watchdog=watchdog+1;
            end
            if(!req_ready) $fatal(1,"memory request did not complete address=%h",address);
            if(selected_bank!=(address>>12) || selected_row!=((address>>2)&1023))
                $fatal(1,"address mapping failed addr=%h bank=%0d row=%0d",address,selected_bank,selected_row);
            if(req_rdata!==expected)
                $fatal(1,"bank/layer/byte merge mismatch addr=%h expected=%h actual=%h",address,expected,req_rdata);
            requests=requests+1;
            if(drop_valid) begin
                @(negedge clk);req_valid=0;
                @(negedge clk);
                if(read_enable || selected_word!==0) $fatal(1,"disabled read returns another bank's data");
            end
        end
    endtask
    task automatic boundary(input integer before_bank);
        integer after_bank;
        begin
            after_bank=(before_bank+1)%BANKS;
            // Last byte and halfword before a 4 KiB boundary. Low address bits
            // select the same word; CPU/DMA already supplies shifted strobes.
            expected_last[before_bank]=(expected_last[before_bank]&32'h00ffffff)|32'ha5000000;
            access(1,before_bank*4096+4095,32'ha5000000,4'b1000,expected_last[before_bank],0);
            expected_first[after_bank]=(expected_first[after_bank]&32'hffffff00)|32'h0000005a;
            access(1,after_bank*4096,32'h0000005a,4'b0001,expected_first[after_bank],0);
            access(0,before_bank*4096+4092,0,0,expected_last[before_bank],0);
            access(0,after_bank*4096,0,0,expected_first[after_bank],0);
            expected_last[before_bank]=(expected_last[before_bank]&32'h0000ffff)|32'hbeef0000;
            access(1,before_bank*4096+4094,32'hbeef0000,4'b1100,expected_last[before_bank],0);
            expected_first[after_bank]=(expected_first[after_bank]&32'hffff0000)|32'h00001234;
            access(1,after_bank*4096,32'h00001234,4'b0011,expected_first[after_bank],1);
        end
    endtask
    task automatic reject_address(input [31:0] address,input logic write_request);
        integer watchdog,previous_bank,previous_row,previous_commits;
        begin
            previous_bank=selected_bank;previous_row=selected_row;previous_commits=commits;
            @(negedge clk);req_valid=1;req_we=write_request;req_addr=address;
            req_wdata=32'hbad0cafe;req_wstrb=15;
            watchdog=0;
            @(negedge clk);
            while(!req_ready && watchdog<8) begin @(negedge clk);watchdog=watchdog+1;end
            if(!req_ready || req_rdata!==0 || read_enable || write_commit)
                $fatal(1,"out-of-capacity access did not return bounded zero addr=%h",address);
            if(selected_bank!=previous_bank || selected_row!=previous_row || commits!=previous_commits)
                $fatal(1,"out-of-capacity request stepped or wrote a real bank");
            @(negedge clk);req_valid=0;
            repeat(2) @(negedge clk);
        end
    endtask
    initial begin
        for(integer b=0;b<BANKS;b=b+1) begin
            expected_first[b]=32'h11223344 ^ (b*32'h01010101);
            expected_last[b]=32'h89abcdef ^ b;
            storage[placement(b,0)]=expected_first[b];
            storage[placement(b,ROWS-1)]=expected_last[b];
        end
        repeat(3) @(negedge clk);rst=0;
        if($bits(bank_select)*ROWS*4!=BANKS*4096) $fatal(1,"wrapper capacity mismatch");
        // Exercise every logical bank, including the highest flash/RAM address
        // bits, through each actual fixed-capacity wrapper and its one-hot ring.
        for(integer b=0;b<BANKS;b=b+1) begin
            access(0,b*4096,0,0,expected_first[b],0);
            expected_first[b]=merged(expected_first[b],32'hdeadc0de ^ b,4'(b));
            access(1,b*4096,32'hdeadc0de ^ b,4'(b),expected_first[b],0);
        end
        for(integer b=0;b<BANKS;b=b+1)
            access(0,b*4096,0,0,expected_first[b],0);
        // Adjacent layers, a layer-group boundary, and final capacity wrap.
        boundary(0);
        boundary((LAYERS-1)%BANKS);
        boundary(BANKS-1);
        access(0,BANKS*4096-1,0,0,expected_last[BANKS-1],1);
        // Logic reset selects bank/row zero but must not erase physical storage.
        @(negedge clk);rst=1;
        repeat(3) @(negedge clk);rst=0;
        access(0,0,0,0,expected_first[0],1);
        access(0,BANKS*4096-4,0,0,expected_last[BANKS-1],1);
        reject_address(BANKS*4096,0);
        reject_address(BANKS*4096,1);
        reject_address(32'hffffffff,0);
        reject_address(32'hfffffffc,1);
        access(0,0,0,0,expected_first[0],1);
        access(0,BANKS*4096-4,0,0,expected_last[BANKS-1],1);
        if(commits!=BANKS+12) $fatal(1,"held-valid requests duplicated/lost writes: %0d",commits);
        $display("MEMORY ADAPTER LAYERED BOUNDARIES PASS kind=%0d layers=%0d bytes=%0d requests=%0d cycles=%0d",
            KIND,LAYERS,BANKS*4096,requests,cycles);
        $finish;
    end
endmodule
