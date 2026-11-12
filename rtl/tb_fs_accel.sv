`timescale 1ns/1ps
`include "shama_boot_params.svh"
`ifdef SHAMA_REAL_FS_ADAPTERS
`define FS_RAM_MEMORY ram_store.memory
`define FS_FLASH_MEMORY flash_store.memory
`else
`define FS_RAM_MEMORY ram
`define FS_FLASH_MEMORY flash
`endif
`ifndef SHAMA_MINER_HISTORY_ENTRY
`define SHAMA_MINER_HISTORY_ENTRY 13
`endif
`ifndef SHAMA_MINER_STATE_ENTRY
`define SHAMA_MINER_STATE_ENTRY 14
`endif
module tb_fs_accel;
    logic clk=0,rst=1,req_valid=0,req_ready;
    logic [11:0] req_id=0;
    logic [191:0] req_args=0;
    logic [3:0] req_owner=0;
    logic [63:0] req_ret;
    logic cleanup_valid=0,cleanup_ready;
    logic [3:0] cleanup_owner=0;
    logic dma_valid,dma_we,dma_flash,dma_ready;
    logic [31:0] dma_addr,dma_wdata,dma_rdata;
    logic [3:0] dma_wstrb;
    logic [31:0] flash_used_bytes;
    logic flash_write_protected;
    logic mounted;
`ifndef SHAMA_REAL_FS_ADAPTERS
    logic [31:0] ram [0:`SHAMA_RAM_BYTES/4-1];
    logic [31:0] flash [0:`SHAMA_FLASH_BYTES/4-1];
`endif
    integer i,watchdog,flash_writes=0,writes_before;
    logic [31:0] x_handle,dir_handle,child_handle,z_handle;
    logic [31:0] edge_handle;
    string edge_name;
    always #5 clk=~clk;
    shama_fs_accel #(.REQUIRE_BOOT_BUNDLE(0)) dut(.*);
`ifdef SHAMA_REAL_FS_ADAPTERS
    wire ram_ready,flash_ready;
    wire [31:0] ram_rdata,flash_rdata,ram_selected_word,flash_selected_word;
    wire [`SHAMA_RAM_BANKS-1:0] ram_bank_select;
    wire [`SHAMA_FLASH_BANKS-1:0] flash_bank_select;
    wire [9:0] ram_row_address,flash_row_address;
    wire ram_read_enable,flash_read_enable,ram_write_commit,flash_write_commit;
    wire [31:0] ram_write_data,flash_write_data;
    shama_ram_adapter ram_adapter(.clk,.rst,.req_valid(dma_valid && !dma_flash),.req_we(dma_we),
        .req_addr(dma_addr),.req_wdata(dma_wdata),.req_wstrb(dma_wstrb),.req_ready(ram_ready),.req_rdata(ram_rdata),
        .bank_select(ram_bank_select),.row_address(ram_row_address),.read_enable(ram_read_enable),
        .write_commit(ram_write_commit),.write_data(ram_write_data),.selected_word(ram_selected_word));
    shama_flash_adapter flash_adapter(.clk,.rst,.req_valid(dma_valid && dma_flash),.req_we(dma_we),
        .req_addr(dma_addr),.req_wdata(dma_wdata),.req_wstrb(dma_wstrb),.req_ready(flash_ready),.req_rdata(flash_rdata),
        .bank_select(flash_bank_select),.row_address(flash_row_address),.read_enable(flash_read_enable),
        .write_commit(flash_write_commit),.write_data(flash_write_data),.selected_word(flash_selected_word));
    shama_persistent_bank_model #(.BANKS(`SHAMA_RAM_BANKS),.CAPACITY_BYTES(`SHAMA_RAM_BYTES)) ram_store(
        .clk,.bank_select(ram_bank_select),.row_address(ram_row_address),.read_enable(ram_read_enable),
        .write_commit(ram_write_commit),.write_data(ram_write_data),.selected_word(ram_selected_word));
    shama_persistent_bank_model #(.BANKS(`SHAMA_FLASH_BANKS),.CAPACITY_BYTES(`SHAMA_FLASH_BYTES)) flash_store(
        .clk,.bank_select(flash_bank_select),.row_address(flash_row_address),.read_enable(flash_read_enable),
        .write_commit(flash_write_commit),.write_data(flash_write_data),.selected_word(flash_selected_word));
    assign dma_ready=dma_flash?flash_ready:ram_ready;
    assign dma_rdata=dma_flash?flash_rdata:ram_rdata;
    always @(posedge clk) if(flash_write_commit) flash_writes<=flash_writes+1;
`else
    initial begin dma_ready=0;dma_rdata=0;end
    // Clocked read agent avoids artificial combinational dependence on every
    // byte of storage, while each DMA request still waits for an acknowledgement.
    always @(negedge clk) begin
        dma_ready<=dma_valid;
        if(dma_valid) begin
            if(dma_flash) begin
                if(dma_addr>=`SHAMA_FLASH_BYTES) $fatal(1,"test flash address out of backing %h",dma_addr);
                dma_rdata<=`FS_FLASH_MEMORY[dma_addr>>2];
            end else begin
                if(dma_addr>=`SHAMA_RAM_BYTES) $fatal(1,"RAM range exceeded %h",dma_addr);
                dma_rdata<=`FS_RAM_MEMORY[dma_addr>>2];
            end
        end
    end
    always @(posedge clk) if(!rst && dma_valid && dma_we && dma_ready) begin
        if(dma_flash) flash_writes<=flash_writes+1;
        for(integer lane=0;lane<4;lane=lane+1) if(dma_wstrb[lane]) begin
            if(dma_flash) `FS_FLASH_MEMORY[dma_addr>>2][lane*8 +:8]<=dma_wdata[lane*8 +:8];
            else `FS_RAM_MEMORY[dma_addr>>2][lane*8 +:8]<=dma_wdata[lane*8 +:8];
        end
    end
`endif
    task automatic ram_string(input integer addr,input string value);
        begin
            for(integer k=0;k<value.len();k=k+1) `FS_RAM_MEMORY[(addr+k)>>2][((addr+k)&3)*8 +:8]=value[k];
            `FS_RAM_MEMORY[(addr+value.len())>>2][((addr+value.len())&3)*8 +:8]=0;
        end
    endtask
    task automatic entry(input integer index,input string name,input [7:0] typ,input integer start,bytes);
        integer off;
        begin
            off=`SHAMA_FS_TABLE_FLASH_OFFSET+index*64;
            `FS_FLASH_MEMORY[off/4]={16'd0,typ,8'h01};`FS_FLASH_MEMORY[off/4+1]=bytes;
            `FS_FLASH_MEMORY[off/4+2]=start;`FS_FLASH_MEMORY[off/4+3]=(bytes+255)/256;`FS_FLASH_MEMORY[off/4+4]=1;
            `FS_FLASH_MEMORY[(off+20)>>2][7:0]=name.len();
            for(integer k=0;k<name.len();k=k+1) `FS_FLASH_MEMORY[(off+21+k)>>2][((off+21+k)&3)*8 +:8]=name[k];
        end
    endtask
    task automatic call4(input [11:0] id,input [31:0] a1,a2,a3,a4);
        begin
            @(negedge clk);req_id=id;req_args={64'd0,a4,a3,a2,a1};req_valid=1;
            watchdog=0;
            while(!req_ready && watchdog<1000000) begin @(posedge clk);watchdog=watchdog+1;end
            if(!req_ready) $fatal(1,"FS timeout id=%h state=%d",id,dut.state);
            @(negedge clk);req_valid=0;
            @(posedge clk);#1;
        end
    endtask
    task automatic ok;
        if(req_ret[63:32]==32'hffffffff)
            $fatal(1,"unexpected FS error %h id=%h args=%h entry=%0d state=%0d",req_ret,req_id,req_args,dut.entry_index,dut.state);
    endtask
    task automatic error_expected;
        if(req_ret[63:32]!=32'hffffffff) $fatal(1,"expected rejection got %h",req_ret);
    endtask
    task automatic reset_and_mount;
        begin
            @(negedge clk);rst=1;req_valid=0;
            repeat(2) @(negedge clk);rst=0;
            call4(12'h008,0,0,0,0);ok();
            if(!mounted) $fatal(1,"mount flag missing");
        end
    endtask
    initial begin
        // Allow passive storage initialization to finish before constructing
        // this imported image; subsequent reset never resets storage cells.
        #1;
        for(i=0;i<`SHAMA_RAM_BYTES/4;i=i+1) `FS_RAM_MEMORY[i]=0;
        for(i=0;i<`SHAMA_FLASH_BYTES/4;i=i+1) `FS_FLASH_MEMORY[i]=0;
        `FS_FLASH_MEMORY[0]=32'h4d414853;`FS_FLASH_MEMORY[1]=32'h31534641;
        `FS_FLASH_MEMORY[2]=1;`FS_FLASH_MEMORY[3]=256;`FS_FLASH_MEMORY[4]=`SHAMA_FS_BLOCK_COUNT;`FS_FLASH_MEMORY[5]=1;
        `FS_FLASH_MEMORY[6]=`SHAMA_FS_TABLE_FLASH_OFFSET/256;`FS_FLASH_MEMORY[7]=`SHAMA_FS_DATA_START_BLOCK;`FS_FLASH_MEMORY[8]=1;`FS_FLASH_MEMORY[9]=1;
        entry(`SHAMA_MINER_HISTORY_ENTRY,"miner-history.log",5,64,32768);
        entry(`SHAMA_MINER_STATE_ENTRY,"miner-state.cfg",6,200,256);
        // Deliberately wrong bitmap: mount must reconstruct it from file table.
        `FS_FLASH_MEMORY[256/4]=32'hffffffff;`FS_FLASH_MEMORY[260/4]=32'hffffffff;
        ram_string(16'h100,"x.txt");ram_string(16'h140,"user");
        ram_string(16'h180,"user/note.txt");ram_string(16'h1c0,"z.txt");
        ram_string(16'h200,"hello");ram_string(16'h240,"replacement");
        ram_string(16'h280,"missing/bad.txt");
        reset_and_mount();
        if(flash_used_bytes!=(`SHAMA_FS_DATA_START_BLOCK+128+1)*256) $fatal(1,"rebuild usage %d",flash_used_bytes);
        for(i=`SHAMA_FS_BLOCK_COUNT%32;i<32 && `SHAMA_FS_BLOCK_COUNT%32!=0;i=i+1)
            if(!`FS_FLASH_MEMORY[256/4+`SHAMA_FS_BITMAP_WORDS-1][i])
                $fatal(1,"partial final bitmap word exposed nonexistent flash blocks");

        // Canonical 43-byte paths fit exactly in the final 44 RAM bytes.
        // A leading slash needs one more input byte, but is not stored.
        edge_name="";
        for(i=0;i<43;i=i+1) edge_name={edge_name,"a"};
        ram_string(`SHAMA_RAM_BYTES-44,edge_name);
        call4(12'h020,`SHAMA_RAM_BYTES-44,1,0,0);ok();edge_handle=req_ret[31:0];
        call4(12'h028,edge_handle,0,0,0);ok();
        if(req_ret[15:8]!=43) $fatal(1,"maximum canonical path was truncated");
        ram_string(`SHAMA_RAM_BYTES-45,{"/",edge_name});
        call4(12'h021,`SHAMA_RAM_BYTES-45,0,0,0);ok();
        if(req_ret[31:0]!=edge_handle) $fatal(1,"leading slash changed path identity");
        call4(12'h027,edge_handle,0,0,0);ok();
        ram_string(`SHAMA_RAM_BYTES-44,{"/",edge_name.substr(0,41)});
        call4(12'h020,`SHAMA_RAM_BYTES-44,1,0,0);ok();edge_handle=req_ret[31:0];
        call4(12'h027,edge_handle,0,0,0);ok();
        // No terminator is accessible after slash+43 bytes. The controller
        // must reject it before issuing a DMA beyond the last physical bank.
        for(i=0;i<44;i=i+1)
            `FS_RAM_MEMORY[(`SHAMA_RAM_BYTES-44+i)>>2][((`SHAMA_RAM_BYTES-44+i)&3)*8+:8]=(i==0)?8'h2f:8'h61;
        call4(12'h020,`SHAMA_RAM_BYTES-44,1,0,0);error_expected();
        if(req_ret!=64'hfffffffffffffffe) $fatal(1,"escaped name range needs E_RANGE");

        call4(12'h020,32'h100,1,0,0);ok();x_handle=req_ret[31:0];
        call4(12'h024,x_handle,32'h200,5,0);ok();
        if(req_ret!=5) $fatal(1,"write must return byte count");
        call4(12'h023,x_handle,32'h300,5,0);ok();
        if(`FS_RAM_MEMORY[32'h300/4]!=32'h6c6c6568 || `FS_RAM_MEMORY[32'h304/4][7:0]!="o") $fatal(1,"readback");
        call4(12'h023,x_handle,32'h340,2,32'hdeadbeef);ok();
        if(`FS_RAM_MEMORY[32'h340/4][15:0]!=16'h6568) $fatal(1,"three-argument READ interpreted scratch r4");
        call4(12'h024,x_handle,32'hfffff,10,0);error_expected();
        call4(12'h023,x_handle,32'hfffff,10,99);error_expected();
        call4(12'h020,32'h100,1,0,0);error_expected();

        // Handle ownership, transfer, close and app-wide cleanup are enforced.
        req_owner=1;call4(12'h023,x_handle,32'h300,5,0);error_expected();req_owner=0;
        call4(12'h02b,x_handle,2,0,0);ok();
        call4(12'h023,x_handle,32'h300,5,0);error_expected();
        req_owner=2;call4(12'h023,x_handle,32'h300,5,0);ok();
        @(negedge clk);cleanup_owner=2;cleanup_valid=1;
        @(negedge clk);cleanup_valid=0;
        call4(12'h023,x_handle,32'h300,5,0);error_expected();
        req_owner=0;call4(12'h021,32'h100,0,0,0);ok();x_handle=req_ret[31:0];

        // Real directory entries and full paths survive persistence.
        call4(12'h020,32'h140,7,0,0);ok();dir_handle=req_ret[31:0];
        call4(12'h020,32'h180,1,0,0);ok();child_handle=req_ret[31:0];
        call4(12'h020,32'h280,1,0,0);error_expected();
        call4(12'h020,32'h1c0,1,0,0);ok();z_handle=req_ret[31:0];
        call4(12'h026,child_handle,32'h1c0,0,0);error_expected();
        call4(12'h027,dir_handle,0,0,0);error_expected();
        call4(12'h024,dir_handle,32'h200,5,0);error_expected();
        call4(12'h024,child_handle,32'h200,5,0);ok();
        call4(12'h025,child_handle,2,0,0);ok();
        call4(12'h025,child_handle,8,0,0);ok();
        `FS_RAM_MEMORY[32'h380/4]=32'hffffffff;`FS_RAM_MEMORY[32'h384/4]=32'hffffffff;
        call4(12'h023,child_handle,32'h380,8,0);ok();
        if(`FS_RAM_MEMORY[32'h380/4]!=32'h00006568 || `FS_RAM_MEMORY[32'h384/4]!=0) $fatal(1,"truncate zero extension");
        call4(12'h029,child_handle-1,32'h400,0,0);ok();
        if(`FS_RAM_MEMORY[32'h400/4]!=32'h72657375 || `FS_RAM_MEMORY[(32'h400+13)/4][((32'h400+13)&3)*8 +:8]!=0) $fatal(1,"path list termination");

        // Reset before journal commit preserves old file and reclaims new extent.
        @(negedge clk);req_id=12'h024;req_args={96'd0,32'd11,32'h240,x_handle};req_valid=1;
        watchdog=0;
        while(!(dma_valid && dma_we && dma_flash && dma_addr>=`SHAMA_FS_DATA_START_BLOCK*256) && watchdog<100000) begin @(negedge clk);watchdog=watchdog+1;end
        if(watchdog>=100000) $fatal(1,"no precommit data write");
        reset_and_mount();
        call4(12'h021,32'h100,0,0,0);ok();x_handle=req_ret[31:0];
        call4(12'h023,x_handle,32'h500,20,0);ok();
        if(req_ret!=5 || `FS_RAM_MEMORY[32'h500/4]!=32'h6c6c6568) $fatal(1,"uncommitted file changed");

        // Reset immediately after commit marker: mount replays complete new entry.
        @(negedge clk);req_id=12'h024;req_args={96'd0,32'd11,32'h240,x_handle};req_valid=1;
        watchdog=0;
        while(!(dma_valid && dma_ready && dma_we && dma_flash && dma_addr==64 && dma_wdata==32'h314e524a) && watchdog<100000) begin @(negedge clk);watchdog=watchdog+1;end
        if(watchdog>=100000) $fatal(1,"no journal commit");
        @(posedge clk);#1;
        reset_and_mount();
        call4(12'h021,32'h100,0,0,0);ok();x_handle=req_ret[31:0];
        call4(12'h023,x_handle,32'h500,20,0);ok();
        if(req_ret!=11 || `FS_RAM_MEMORY[32'h500/4]!=32'h6c706572 || `FS_FLASH_MEMORY[16]!=0) $fatal(1,"journal replay failed");

        // Miner state and 128-byte history slots are COW, not in-place writes.
        `FS_RAM_MEMORY[32'h600/4]=32'h31524d53;`FS_RAM_MEMORY[(32'h600+48)/4]=32'hdeadbeef;`FS_RAM_MEMORY[(32'h600+80)/4]=32'h12345678;
        call4(12'h051,32'h600,256,0,0);ok();
        call4(12'h053,32'h800,256,0,0);ok();
        if(`FS_RAM_MEMORY[32'h800/4]!=32'h31524d53 || `FS_RAM_MEMORY[(32'h800+48)/4]!=32'hdeadbeef) $fatal(1,"state round trip");
        call4(12'h050,32'h600,128,3,0);ok();
        call4(12'h052,3,32'ha00,128,0);ok();
        if(`FS_RAM_MEMORY[32'ha00/4]!=32'h31524d53 || `FS_RAM_MEMORY[(32'ha00+80)/4]!=32'h12345678) $fatal(1,"history full-width round trip");
        call4(12'h052,2,32'hb00,128,0);ok();
        if(`FS_RAM_MEMORY[32'hb00/4]!=0) $fatal(1,"history corrupted neighboring slot");
        call4(12'h052,256,32'ha00,128,0);error_expected();
        call4(12'h02c,0,0,0,0);ok();
        reset_and_mount();
        call4(12'h053,32'h800,256,0,0);ok();
        if(`FS_RAM_MEMORY[32'h800/4]!=32'h31524d53) $fatal(1,"state did not persist reboot");

        // Close invalidates a real handle; reboot does not reopen old handles.
        call4(12'h021,32'h100,0,0,0);ok();x_handle=req_ret[31:0];
        call4(12'h022,x_handle,0,0,0);ok();
        call4(12'h028,x_handle,0,0,0);error_expected();

        // Loader offsets are generated once: the private bundle must never be
        // relocated by COW, truncated, renamed or deleted through normal APIs.
        @(negedge clk);rst=1;req_valid=0;
        entry(60,"boot.bundle",4,`SHAMA_FS_BLOCK_COUNT-1,32);
        `FS_FLASH_MEMORY[(`SHAMA_FLASH_BYTES-256)/4]=32'hcafe1234;
        ram_string(32'hc00,"boot.bundle");
        reset_and_mount();
        call4(12'h021,32'hc00,0,0,0);ok();edge_handle=req_ret[31:0];
        writes_before=flash_writes;
        call4(12'h024,edge_handle,32'h200,5,0);error_expected();
        if(req_ret!=64'hfffffffffffffff7 || flash_writes!=writes_before) $fatal(1,"bundle WRITE was not immutable");
        call4(12'h025,edge_handle,0,0,0);error_expected();
        if(req_ret!=64'hfffffffffffffff7 || flash_writes!=writes_before) $fatal(1,"bundle zero TRUNCATE bypassed immutability");
        call4(12'h026,edge_handle,32'h1c0,0,0);error_expected();
        if(req_ret!=64'hfffffffffffffff7 || flash_writes!=writes_before) $fatal(1,"bundle RENAME was not immutable");
        call4(12'h027,edge_handle,0,0,0);error_expected();
        if(req_ret!=64'hfffffffffffffff7 || flash_writes!=writes_before) $fatal(1,"bundle DELETE was not immutable");
        call4(12'h023,edge_handle,32'hd00,4,0);ok();
        if(`FS_RAM_MEMORY[32'hd00/4]!=32'hcafe1234) $fatal(1,"immutable bundle bytes changed");

        // A full persistent table rejects CREATE without reusing the last slot.
        @(negedge clk);rst=1;req_valid=0;
        for(i=`SHAMA_FS_TABLE_FLASH_OFFSET/4;i<(`SHAMA_FS_TABLE_FLASH_OFFSET+4096)/4;i=i+1) `FS_FLASH_MEMORY[i]=0;
        for(i=0;i<64;i=i+1) entry(i,$sformatf("entry%0d.txt",i),1,0,0);
        repeat(2) @(negedge clk);rst=0;
        call4(12'h008,0,0,0,0);ok();
        call4(12'h020,32'h100,1,0,0);error_expected();
        if(req_ret!=64'hfffffffffffffffd) $fatal(1,"full table must report full");

        // One block below95%: a one-block save can reach exactly the limit.
        // Later data/metadata mutations are rejected without any flash write.
        @(negedge clk);rst=1;req_valid=0;
        for(i=`SHAMA_FS_TABLE_FLASH_OFFSET/4;i<(`SHAMA_FS_TABLE_FLASH_OFFSET+4096)/4;i=i+1) `FS_FLASH_MEMORY[i]=0;
        entry(0,"large.bin",3,`SHAMA_FS_DATA_START_BLOCK,(`SHAMA_FS_WRITE_LIMIT_BLOCKS-`SHAMA_FS_DATA_START_BLOCK-1)*256);
        entry(1,"x.txt",1,0,0);
        entry(2,"z.txt",1,0,0);
        repeat(2) @(negedge clk);rst=0;
        call4(12'h008,0,0,0,0);ok();
        if(flash_write_protected || flash_used_bytes!=(`SHAMA_FS_WRITE_LIMIT_BLOCKS-1)*256)
            $fatal(1,"write guard enabled below limit");
        call4(12'h021,32'h100,0,0,0);ok();x_handle=req_ret[31:0];
        call4(12'h024,x_handle,32'h200,5,0);ok();
        if(!flash_write_protected || flash_used_bytes!=`SHAMA_FS_WRITE_LIMIT_BLOCKS*256)
            $fatal(1,"exact95percent usage must enable protection");
        writes_before=flash_writes;
        call4(12'h024,x_handle,32'h240,11,0);error_expected();
        if(req_ret!=64'hfffffffffffffff8 || flash_writes!=writes_before)
            $fatal(1,"protected save mutated flash or returned wrong error");
        call4(12'h020,32'h140,7,0,0);error_expected();
        if(req_ret!=64'hfffffffffffffff8 || flash_writes!=writes_before)
            $fatal(1,"protected CREATE mutated flash");
        call4(12'h026,x_handle,32'h180,0,0);error_expected();
        if(req_ret!=64'hfffffffffffffff8 || flash_writes!=writes_before)
            $fatal(1,"protected RENAME mutated flash");
        call4(12'h023,x_handle,32'h500,20,0);ok();
        if(req_ret!=5 || `FS_RAM_MEMORY[32'h500/4]!=32'h6c6c6568) $fatal(1,"protected read lost last save");
        reset_and_mount();
        if(!flash_write_protected) $fatal(1,"reboot bypassed flash protection");
        call4(12'h021,32'h100,0,0,0);ok();x_handle=req_ret[31:0];
        // Zero truncate and deletion are explicit reclamation exceptions.
        call4(12'h025,x_handle,0,0,0);ok();
        if(flash_write_protected) $fatal(1,"zero truncate failed to restore writable space");
        // A two-block COW request must fail before allocation/bitmap writes.
        writes_before=flash_writes;
        call4(12'h024,x_handle,32'h200,257,0);error_expected();
        if(req_ret!=64'hfffffffffffffff8 || flash_writes!=writes_before ||
           flash_used_bytes!=(`SHAMA_FS_WRITE_LIMIT_BLOCKS-1)*256)
            $fatal(1,"oversized COW reservation changed flash before refusal");
        call4(12'h024,x_handle,32'h200,5,0);ok();
        call4(12'h027,x_handle,0,0,0);ok();
        if(flash_write_protected) $fatal(1,"DELETE failed to recover from protected mode");

        // Imported/full images still mount/read with correct physical tail
        // accounting; they become protected rather than aliasing invalid bits.
        @(negedge clk);rst=1;req_valid=0;
        for(i=`SHAMA_FS_TABLE_FLASH_OFFSET/4;i<(`SHAMA_FS_TABLE_FLASH_OFFSET+4096)/4;i=i+1) `FS_FLASH_MEMORY[i]=0;
        entry(0,"large.bin",3,`SHAMA_FS_DATA_START_BLOCK,(`SHAMA_FS_BLOCK_COUNT-`SHAMA_FS_DATA_START_BLOCK-1)*256);
        entry(1,"x.txt",1,`SHAMA_FS_BLOCK_COUNT-1,256);
        `FS_FLASH_MEMORY[(`SHAMA_FLASH_BYTES-256)/4]=32'ha53c96f0;
        `FS_FLASH_MEMORY[`SHAMA_FLASH_BYTES/4-1]=32'hd76a135b;
        repeat(2) @(negedge clk);rst=0;
        call4(12'h008,0,0,0,0);ok();
        if(flash_used_bytes!=`SHAMA_FLASH_BYTES) $fatal(1,"full usage accounting");
        call4(12'h021,32'h100,0,0,0);ok();x_handle=req_ret[31:0];
        call4(12'h023,x_handle,32'h500,256,0);ok();
        if(`FS_RAM_MEMORY[32'h500/4]!=32'ha53c96f0 || `FS_RAM_MEMORY[32'h5fc/4]!=32'hd76a135b)
            $fatal(1,"last physical flash block/word read failed");
        writes_before=flash_writes;
        call4(12'h024,x_handle,32'h200,5,0);error_expected();
        if(req_ret!=64'hfffffffffffffff8 || flash_writes!=writes_before)
            $fatal(1,"full flash must be protected before writing");
        call4(12'h028,x_handle,0,0,0);ok();
        if(req_ret[63:32]!=256) $fatal(1,"failed save changed original metadata");

        // A committed but corrupt journal fails mount; it is not silently used.
        @(negedge clk);rst=1;req_valid=0;
        `FS_FLASH_MEMORY[16]=32'h314e524a;`FS_FLASH_MEMORY[17]=0;`FS_FLASH_MEMORY[34]=0;
        repeat(2) @(negedge clk);rst=0;
        call4(12'h008,0,0,0,0);error_expected();
        if(mounted) $fatal(1,"corrupt journal mounted");
`ifdef SHAMA_REAL_FS_ADAPTERS
        if(flash_store.last_word_reads==0 || flash_store.committed_writes==0 ||
           ram_store.read_samples==0 || ram_store.committed_writes==0)
            $fatal(1,"filesystem controller proof missed a storage endpoint");
        $display("SHAMAFS REAL ADAPTERS EXACT FLASH TAIL PASS bytes=%0d final_row=%0d",
            `SHAMA_FLASH_BYTES,(`SHAMA_FLASH_BYTES/4-1)%1024);
`endif
        $display("SHAMAFS TRANSACTIONS OWNERSHIP DIRECTORIES TRUNCATE MINER PASS");$finish;
    end
endmodule
