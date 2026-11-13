`timescale 1ns/1ps
`include "shama_boot_params.svh"
module tb_history_journal;
    logic clk=0,rst=1,req_valid=0,req_ready;
    logic [11:0] req_id=0;
    logic [191:0] req_args=0;
    logic [3:0] req_owner=3;
    logic [63:0] req_ret;
    logic cleanup_valid=0,cleanup_ready;
    logic [3:0] cleanup_owner=0;
    logic dma_valid,dma_we,dma_flash,dma_ready=0;
    logic [31:0] dma_addr,dma_wdata,dma_rdata=0;
    logic [3:0] dma_wstrb;
    logic [31:0] flash_used_bytes;
    logic flash_write_protected,mounted;
    logic [31:0] ram [0:`SHAMA_RAM_BYTES/4-1];
    logic [31:0] flash [0:`SHAMA_FLASH_BYTES/4-1];
    string image_path,dump_path,trace_path,mode="save";
    integer trace_fd=0,cycles=0,flash_writes=0,transaction_writes=0,dma_reads=0;
    integer slot=63,length=128,cut=-1,i,j,watchdog,writes_before,used_before;
    logic capturing=0;
    logic [31:0] history_start;
    localparam integer PAYLOAD=32'h103;
    shama_fs_accel dut(.*);
    always #5 clk=~clk;
    always @(posedge clk) begin
        cycles<=cycles+1;
        if(!rst && dma_valid && dma_ready && !dma_we) dma_reads=dma_reads+1;
    end
    // Vary response latency and preserve byte strobes, including unaligned RAM.
    always @(negedge clk) begin
        dma_ready<=dma_valid && cycles%3!=0;
        if(dma_valid) begin
            if(dma_flash) begin
                if(dma_addr>=`SHAMA_FLASH_BYTES) $fatal(1,"flash bounds %h",dma_addr);
                dma_rdata<=flash[dma_addr>>2];
            end else begin
                if(dma_addr>=`SHAMA_RAM_BYTES) $fatal(1,"RAM bounds %h",dma_addr);
                dma_rdata<=ram[dma_addr>>2];
            end
        end
    end
    always @(posedge clk) if(!rst && dma_valid && dma_ready && dma_we) begin
        if(dma_flash) begin
            flash_writes=flash_writes+1;
            if(capturing) begin
                transaction_writes=transaction_writes+1;
                if(trace_fd) $fdisplay(trace_fd,"%08x %08x %01x",dma_addr,dma_wdata,dma_wstrb);
            end
        end
        for(integer lane=0;lane<4;lane=lane+1) if(dma_wstrb[lane]) begin
            if(dma_flash) flash[dma_addr>>2][lane*8+:8]<=dma_wdata[lane*8+:8];
            else ram[dma_addr>>2][lane*8+:8]<=dma_wdata[lane*8+:8];
        end
    end
    task automatic call(input [11:0] id,input [31:0] x,y,z);
        begin
            @(negedge clk);req_id=id;req_args={96'd0,z,y,x};req_valid=1;
            watchdog=0;
            while(!req_ready && watchdog<2000000) begin @(posedge clk);#1;watchdog=watchdog+1;end
            if(!req_ready) $fatal(1,"request timeout id=%h state=%d",id,dut.state);
            @(negedge clk);req_valid=0;
            @(posedge clk);#1;
        end
    endtask
    task automatic check_ok;
        if(req_ret[63:32]==32'hffffffff) $fatal(1,"request error %h id=%h",req_ret,req_id);
    endtask
    task automatic reset_mount;
        begin
            @(negedge clk);rst=1;req_valid=0;capturing=0;
            repeat(3) @(negedge clk);rst=0;
            call(12'h008,0,0,0);
        end
    endtask
    task automatic fill_record(input integer selected_slot,sequence_number);
        begin
            for(integer k=0;k<256;k=k+1)
                ram[(PAYLOAD+k)>>2][((PAYLOAD+k)&3)*8+:8]=(selected_slot*17+k*29+3+sequence_number)&255;
        end
    endtask
    task automatic assert_record(input integer selected_slot,count,sequence_number);
        integer expected,actual;
        begin
            for(integer k=0;k<128;k=k+1) begin
                expected=k<count?((selected_slot*17+k*29+3+sequence_number)&255):0;
                actual=(flash[(history_start*256+selected_slot*128+k)>>2]>>(((selected_slot*128+k)&3)*8))&255;
                if(actual!=expected) $fatal(1,"record mismatch slot=%0d byte=%0d got=%h want=%h",selected_slot,k,actual,expected);
            end
        end
    endtask
    task automatic record_save(input integer selected_slot,count,sequence_number);
        integer reads_before,cycles_before;
        begin
            fill_record(selected_slot,sequence_number);
            used_before=flash_used_bytes;writes_before=flash_writes;
            reads_before=dma_reads;cycles_before=cycles;
            call(12'h050,PAYLOAD,count,selected_slot);check_ok();
            if(req_ret!=count || flash_used_bytes!=used_before) $fatal(1,"record save changed allocation/result");
            if(flash_writes-writes_before!=264) $fatal(1,"record write amplification %0d",flash_writes-writes_before);
            if(flash[(`SHAMA_FS_TABLE_FLASH_OFFSET+`SHAMA_MINER_HISTORY_ENTRY*64+8)>>2]!=history_start)
                $fatal(1,"history extent moved");
            assert_record(selected_slot,count,sequence_number);
            $display("HISTORY SAVE METRICS slot=%0d bytes=%0d cycles=%0d reads=%0d writes=%0d",
                selected_slot,count,cycles-cycles_before,dma_reads-reads_before,flash_writes-writes_before);
        end
    endtask
    initial begin
        if(!$value$plusargs("image=%s",image_path) || !$value$plusargs("dump=%s",dump_path))
            $fatal(1,"missing image/dump paths");
        i=$value$plusargs("mode=%s",mode);
        i=$value$plusargs("slot=%d",slot);
        i=$value$plusargs("length=%d",length);
        i=$value$plusargs("cut=%d",cut);
        if($value$plusargs("trace=%s",trace_path)) trace_fd=$fopen(trace_path,"w");
        for(i=0;i<`SHAMA_RAM_BYTES/4;i=i+1) ram[i]=0;
        $readmemh(image_path,flash);
        history_start=flash[(`SHAMA_FS_TABLE_FLASH_OFFSET+`SHAMA_MINER_HISTORY_ENTRY*64+8)>>2];
        if(mode=="recovery-cut") begin
            repeat(3) @(negedge clk);rst=0;
            @(negedge clk);req_id=12'h008;req_args=0;req_valid=1;
            watchdog=0;
            while(flash_writes<cut && !req_ready && watchdog<100000) begin
                @(posedge clk);#1;watchdog=watchdog+1;
            end
            if(flash_writes!=cut) $fatal(1,"missed recovery cut");
        end
        reset_mount();
        if(mode=="bad-mount") begin
            if(req_ret!=64'hfffffffffffffffb || mounted || flash_writes!=0)
                $fatal(1,"malformed journal was not rejected before flash writes");
        end else begin
            check_ok();
            if(mode=="bulk") begin
                for(j=0;j<256;j=j+1) begin
                    record_save(j,128,0);
                    if(j%64==63) begin
                        fill_record(j,1);
                        call(12'h051,PAYLOAD,256,0);check_ok();
                    end
                end
                // Short and zero-length payloads retain full zero-padded slots.
                record_save(0,0,1);record_save(64,17,2);record_save(128,127,3);
                record_save(255,128,4);record_save(0,128,5);
                reset_mount();check_ok();
                for(j=0;j<256;j=j+1) begin
                    if(j==0) assert_record(j,128,5);
                    else if(j==64) assert_record(j,17,2);
                    else if(j==128) assert_record(j,127,3);
                    else if(j==255) assert_record(j,128,4);
                    else assert_record(j,128,0);
                end
            end else if(mode=="mount-only" || mode=="recovery-cut") begin
                // Parent checks the independently recovered full image.
            end else if(mode=="interleave") begin
                for(j=0;j<12;j=j+1) begin
                    record_save((j*63)&255,128,j);
                    fill_record(j,1);
                    call(12'h051,PAYLOAD,256,0);check_ok();
                end
                reset_mount();check_ok();
            end else if(mode=="protected") begin
                if(!flash_write_protected) $fatal(1,"protected fixture is below threshold");
                writes_before=flash_writes;fill_record(slot,0);
                call(12'h050,PAYLOAD,128,slot);
                if(req_ret!=64'hfffffffffffffff8 || flash_writes!=writes_before)
                    $fatal(1,"protected new history write mutated flash");
            end else if(mode=="bad-request-history" || mode=="bad-request-state") begin
                writes_before=flash_writes;fill_record(slot,0);
                if(mode=="bad-request-history") begin
                    call(12'h050,PAYLOAD,128,slot);
                    if(req_ret!=64'hfffffffffffffffb) $fatal(1,"wrong history identity accepted for save");
                    call(12'h052,slot,32'h500,128);
                    if(req_ret!=64'hfffffffffffffffb) $fatal(1,"wrong history identity accepted for load");
                end else begin
                    call(12'h051,PAYLOAD,256,0);
                    if(req_ret!=64'hfffffffffffffffb) $fatal(1,"wrong state identity accepted for save");
                    call(12'h053,32'h500,256,0);
                    if(req_ret!=64'hfffffffffffffffb) $fatal(1,"wrong state identity accepted for load");
                end
                if(flash_writes!=writes_before) $fatal(1,"wrong-file Miner request mutated flash");
            end else if(cut>=0) begin
                fill_record(slot,0);
                capturing=1;transaction_writes=0;
                @(negedge clk);req_id=12'h050;req_args={96'd0,32'(slot),32'(length),32'(PAYLOAD)};req_valid=1;
                watchdog=0;
                while(transaction_writes<cut && !req_ready && watchdog<100000) begin
                    @(posedge clk);#1;watchdog=watchdog+1;
                end
                if(transaction_writes!=cut) $fatal(1,"missed transaction cut %0d got%0d",cut,transaction_writes);
                reset_mount();check_ok();
                if(cut>=134) assert_record(slot,length,0);
                else for(j=0;j<128;j=j+1)
                    if(flash[(history_start*256+slot*128+j)>>2]!=0) $fatal(1,"precommit record changed");
            end else begin
                capturing=1;transaction_writes=0;
                record_save(slot,length,0);
                capturing=0;
            end
        end
        if(trace_fd) $fclose(trace_fd);
        $writememh(dump_path,flash);
        $display("HISTORY JOURNAL PASS mode=%s slot=%0d cut=%0d used=%0d cycles=%0d",mode,slot,cut,flash_used_bytes,cycles);
        $finish;
    end
endmodule
