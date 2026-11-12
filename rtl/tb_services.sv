`include "shama_boot_params.svh"
module tb_services;
    localparam integer TEST_SLOT=256;
    localparam [31:0] EDIT_CTX=32'h2c200,EDIT_BUF=32'h18000,STAGE=32'h28000,RECORD=32'h2d200;
    logic clk=0,rst=1,sys_valid=0,sys_ready,sys_jump_valid;
    logic [11:0] sys_id=0;
    logic [191:0] sys_args=0;
    logic [63:0] sys_ret;
    logic [31:0] sys_jump_pc;
    logic event_valid=0,event_ack;
    logic [25:0] event_ack_data;
    logic [7:0] event_code=0,key_code=0;
    logic [9:0] controller_latched=0;
    logic input_overflow=0;
    logic keyboard_shift=0;
    logic [7:0] cpu_fault_code=0,notice_code;
    logic gpu_capacity_fault=0,clear_gpu_capacity_fault;
    logic flash_write_protected=0,raw_flash_write_blocked=0;
    logic notice_busy=0,notice_ready=1;
    logic [7:0] input_queued_count=0;
    logic shutdown_request=0,shutdown_ready;
    logic [31:0] lifecycle_error;
    logic fs_cleanup_valid,fs_cleanup_ready=1;
    logic [3:0] fs_cleanup_owner;
    logic [31:0] flash_used_bytes=12345;
    logic [63:0] time_counter=0;
    logic [31:0] ram_used_bytes,cache_used_bytes;
    logic dma_valid,dma_we,dma_flash,dma_cache,dma_ready=0;
    logic [31:0] dma_addr,dma_wdata,dma_rdata=0;
    logic [3:0] dma_wstrb;
    logic ext_valid,ext_ready=0,ext_jump_valid=0,ext_load_app=0;
    logic [11:0] ext_id;
    logic [191:0] ext_args;
    logic [63:0] ext_ret=0;
    logic [31:0] ext_jump_pc=0;
    logic [3:0] ext_app_id=0;
    logic os_loaded,cpu_hold,cpu_force_jump;
    logic [3:0] foreground_app;
    logic [31:0] cpu_force_pc;
    logic [31:0] ram [0:`SHAMA_RAM_BYTES/4-1];
    logic [31:0] cache_mem [0:TEST_SLOT/4-1];
    logic [31:0] saved_editor [0:3];
    logic [31:0] saved_miner [0:63];
    logic fail_save=0;
    integer i,watchdog,ext_delay=0,save_count=0,cleanup_count=0;
    integer force_count=0;
    logic [11:0] seen_ext_id;
    logic [191:0] seen_ext_args;
    logic last_jump;
    always #5 clk=~clk;
    shama_services #(.APP_SLOT_BYTES(TEST_SLOT)) dut(.*);

    function automatic [31:0] flash_word(input [31:0] addr);
        case(addr)
            `SHAMA_KERNEL_FLASH,`SHAMA_DESKTOP_FLASH,`SHAMA_EDITOR_FLASH,
            `SHAMA_FILES_FLASH,`SHAMA_MINER_FLASH,`SHAMA_MONITOR_FLASH,
            `SHAMA_TERMINAL_FLASH,`SHAMA_CALCULATOR_FLASH,`SHAMA_PAINT_FLASH,
            `SHAMA_SETTINGS_FLASH:flash_word=4;
            `SHAMA_KERNEL_FLASH+4:flash_word=32'h11111111;
            `SHAMA_DESKTOP_FLASH+4:flash_word=32'h22222222;
            `SHAMA_EDITOR_FLASH+4:flash_word=32'h33333333;
            `SHAMA_FILES_FLASH+4:flash_word=32'h44444444;
            `SHAMA_MINER_FLASH+4:flash_word=32'h55555555;
            default:flash_word=32'h66666666;
        endcase
    endfunction
    always @(negedge clk) begin
        dma_ready<=dma_valid;
        if(dma_valid) begin
            if(dma_cache) begin
                if(dma_addr>=TEST_SLOT) $fatal(1,"cache address overflow %h",dma_addr);
                dma_rdata<=cache_mem[dma_addr>>2];
            end else if(dma_flash) dma_rdata<=flash_word(dma_addr);
            else begin
                if(dma_addr>=`SHAMA_RAM_BYTES) $fatal(1,"RAM address overflow %h",dma_addr);
                dma_rdata<=ram[dma_addr>>2];
            end
        end
    end
    always @(posedge clk) begin
        time_counter<=time_counter+1;
        if(!rst && dma_valid && dma_we && dma_ready) begin
            if(dma_flash) $fatal(1,"services must persist through filesystem");
            for(integer k=0;k<4;k=k+1) if(dma_wstrb[k]) begin
                if(dma_cache) cache_mem[dma_addr>>2][k*8 +:8]<=dma_wdata[k*8 +:8];
                else ram[dma_addr>>2][k*8 +:8]<=dma_wdata[k*8 +:8];
            end
        end
        if(cpu_force_jump) force_count<=force_count+1;
        if(fs_cleanup_valid && fs_cleanup_ready) begin
            if(ext_valid || ext_ready) $fatal(1,"handles closed before storage drained");
            cleanup_count<=cleanup_count+1;
        end
        // Model the external FS completion contract, including delayed/error IO.
        if(rst) begin ext_ready<=0;ext_delay<=0;end
        else if(ext_valid && ext_delay==0 && !ext_ready) begin
            seen_ext_id<=ext_id;seen_ext_args<=ext_args;ext_delay<=5;
        end else if(ext_delay>1) ext_delay<=ext_delay-1;
        else if(ext_delay==1) begin
            ext_delay<=0;ext_ready<=1;
            if(fail_save) ext_ret<=64'hfffffffffffffffd;
            else begin
                ext_ret<=seen_ext_args[95:64];
                if(seen_ext_id==12'h024) begin
                    if(seen_ext_args[31:0]!=7 || seen_ext_args[63:32]!=EDIT_BUF || seen_ext_args[95:64]>16)
                        $fatal(1,"wrong Editor close save args %h",seen_ext_args);
                    for(integer k=0;k<4;k=k+1) saved_editor[k]<=ram[(EDIT_BUF>>2)+k];
                    save_count<=save_count+1;
                end else if(seen_ext_id==12'h051) begin
                    if(seen_ext_args[31:0]!=RECORD || seen_ext_args[63:32]!=256)
                        $fatal(1,"wrong miner close save args");
                    for(integer k=0;k<64;k=k+1) saved_miner[k]<=ram[(RECORD>>2)+k];
                    save_count<=save_count+1;ext_ret<=256;
                end
            end
        end else if(ext_ready && !ext_valid) ext_ready<=0;
    end
    task automatic call2(input [11:0] id,input [31:0] a1,a2);
        begin
            @(negedge clk);sys_id=id;sys_args={128'd0,a2,a1};sys_valid=1;
            watchdog=0;
            while(!sys_ready && watchdog<300000) begin @(posedge clk);watchdog=watchdog+1;end
            if(!sys_ready) $fatal(1,"services timeout id=%h state=%d",id,dut.state);
            last_jump=sys_jump_valid;
            @(negedge clk);sys_valid=0;
            @(posedge clk);#1;
        end
    endtask
    task automatic editor_context(input [31:0] handle,bytes,dirty,seq);
        begin
            ram[(EDIT_CTX+0)>>2]=handle;ram[(EDIT_CTX+8)>>2]=bytes;
            ram[(EDIT_CTX+20)>>2]=dirty;ram[(EDIT_CTX+36)>>2]=seq;
            ram[EDIT_BUF>>2]=32'h6c6c6568;ram[(EDIT_BUF+4)>>2]=32'h0000006f;
        end
    endtask
    task automatic acknowledge_notice(input [7:0] expected);
        begin
            @(negedge clk);
            if(notice_code!==expected || !cpu_hold) $fatal(1,"missing modal recovery notice %h expected%h",notice_code,expected);
            @(negedge clk);event_valid=1;event_code=8'h14;
            wait(event_ack);
            @(negedge clk);event_valid=0;event_code=0;
            repeat(3) @(negedge clk);
            if(notice_code!=0 || cpu_hold) $fatal(1,"acknowledgement failed to release app");
        end
    endtask
    task automatic recovering_call(input [11:0] id,input [31:0] a1,a2,input [7:0] expected);
        begin
            @(negedge clk);sys_id=id;sys_args={128'd0,a2,a1};sys_valid=1;
            watchdog=0;
            while(!cpu_force_jump && watchdog<300000) begin @(negedge clk);watchdog=watchdog+1;end
            if(!cpu_force_jump || foreground_app!=0) $fatal(1,"resource fault did not restart Desktop");
            sys_valid=0;
            acknowledge_notice(expected);
        end
    endtask
    task automatic universal(input [7:0] code);
        begin
            @(negedge clk);event_code=code;event_valid=1;
            watchdog=0;
            while(!event_ack && watchdog<300000) begin @(posedge clk);watchdog=watchdog+1;end
            if(!event_ack) $fatal(1,"universal event not acknowledged");
            if(event_ack_data!=={controller_latched,key_code,event_code})
                $fatal(1,"universal acknowledgement lost its event snapshot");
            @(negedge clk);event_valid=0;
        end
    endtask
    task automatic await_force;
        begin
            watchdog=0;
            while(!cpu_force_jump && watchdog<300000) begin @(negedge clk);watchdog=watchdog+1;end
            if(!cpu_force_jump) $fatal(1,"universal did not resume at replacement app");
            @(negedge clk);
        end
    endtask
    initial begin
        for(i=0;i<`SHAMA_RAM_BYTES/4;i=i+1) ram[i]=0;
        for(i=0;i<TEST_SLOT/4;i=i+1) cache_mem[i]=0;
        repeat(3) @(negedge clk);rst=0;
        event_valid=1;event_code=1;key_code="A";controller_latched=10'h2a5;
        call2(12'h003,0,0);
        if(sys_ret!=={28'd0,10'h2a5,8'h41,8'h01,10'd0} || event_ack_data!=={10'h2a5,8'h41,8'h01})
            $fatal(1,"event response/acknowledgement snapshots disagree");
        key_code="B";call2(12'h030,0,0);
        if(sys_ret!==64'h42 || event_ack_data!=={10'h2a5,8'h42,8'h01})
            $fatal(1,"key acknowledgement lost its presented event");
        event_valid=0;event_code=0;key_code=0;controller_latched=0;
        call2(12'h009,0,0);
        if(!os_loaded || foreground_app!=0 || cache_mem[0]!=32'h22222222 || ram[`SHAMA_KERNEL_RAM_BASE>>2]!=32'h11111111)
            $fatal(1,"boot did not load real RAM/cache");
        if(!last_jump) $fatal(1,"boot did not redirect CPU");
        call2(12'h012,0,0);
        if(sys_ret!=={`SHAMA_RAM_BYTES,32'd8}) $fatal(1,"RAM total does not match physical capacity");
        call2(12'h02a,0,0);
        if(sys_ret!=={`SHAMA_FLASH_BYTES,flash_used_bytes}) $fatal(1,"flash total does not match physical capacity");
        // The reduced map exposes exactly12 heap pages; reject the next byte
        // without allocating or clearing the Editor source immediately above.
        ram[EDIT_BUF>>2]=32'hcafef00d;
        recovering_call(12'h010,12*4096+1,0,2);
        if(ram_used_bytes!=8 || ram[EDIT_BUF>>2]!=32'hcafef00d)
            $fatal(1,"heap exceeded its map or changed Editor source");
        call2(12'h010,12*4096,0);
        if(sys_ret!=32'h0c000 || ram_used_bytes!=12*4096+8 || ram[EDIT_BUF>>2]!=32'hcafef00d)
            $fatal(1,"last heap page allocation crossed source boundary");
        ram[32'h17ffc>>2]=32'h12345678;
        call2(12'h011,32'h0c000,0);
        if(ram[32'h17ffc>>2]!=0 || ram_used_bytes!=8 || ram[EDIT_BUF>>2]!=32'hcafef00d)
            $fatal(1,"free did not clear last heap page or damaged source");
        ram[EDIT_BUF>>2]=0;
        call2(12'h010,12*4096,0);
        ram[32'h17ffc>>2]=32'hdeadbeef;
        recovering_call(12'h010,1,0,2);
        if(ram[32'h17ffc>>2]!=0 || ram_used_bytes!=8) $fatal(1,"exhausted heap was not cleared on recovery");
        ram[32'h0c000>>2]=32'haaaaaaaa;ram[32'h0dffc>>2]=32'hbbbbbbbb;
        call2(12'h010,5000,0);
        if(ram[32'h0c000>>2]!=0 || ram[32'h0dffc>>2]!=0) $fatal(1,"new allocation leaks stale data");
        if(sys_ret!=32'h0c000 || ram_used_bytes!=8200) $fatal(1,"allocation/accounting");
        ram[32'h0c000>>2]=32'hdeadbeef;ram[32'h0dffc>>2]=32'habcdef01;
        call2(12'h011,32'h0c000,0);
        if(ram[32'h0c000>>2]!=0 || ram[32'h0dffc>>2]!=0 || ram_used_bytes!=8) $fatal(1,"free must zero and release allocation");
        call2(12'h010,4096,0);ram[32'h0c000>>2]=32'hdeadbeef;
        call2(12'h006,1,0);
        if(foreground_app!=1 || ram[32'h0c000>>2]!=0 || cache_mem[0]!=32'h33333333) $fatal(1,"launch cleanup");

        // Dirty named file saves before resources/memory are reclaimed.
        editor_context(7,5,1,2);
        call2(12'h001,0,0);
        if(foreground_app!=0 || save_count!=1 || saved_editor[0]!=32'h6c6c6568 || ram[EDIT_BUF>>2]!=0 || ram[(EDIT_CTX+20)>>2]!=0)
            $fatal(1,"dirty Editor exit/save/clear");
        if(ram[`SHAMA_KERNEL_RAM_BASE>>2]!=32'h11111111) $fatal(1,"exit erased OS");

        // Unnamed dirty text and failed saves keep the live editor intact.
        call2(12'h006,1,0);editor_context(0,5,1,2);
        call2(12'h001,0,0);
        if(foreground_app!=1 || lifecycle_error==0 || ram[EDIT_BUF>>2]!=32'h6c6c6568) $fatal(1,"unnamed dirty close silently discarded");
        editor_context(7,5,1,2);fail_save=1;
        call2(12'h001,0,0);
        if(foreground_app!=1 || lifecycle_error!=32'hfffffffd || ram[EDIT_BUF>>2]!=32'h6c6c6568) $fatal(1,"failed save destroyed editor");
        // Power-off uses the same failure contract: do not authorize gating or
        // erase the unsaved buffer when persistent storage rejects the write.
        @(negedge clk);shutdown_request=1;
        wait(ext_valid);wait(!ext_valid);
        repeat(5) @(negedge clk);
        if(shutdown_ready || cpu_hold || foreground_app!=1 || lifecycle_error!=32'hfffffffd || ram[EDIT_BUF>>2]!=32'h6c6c6568)
            $fatal(1,"failed shutdown save gated power or discarded live content");
        shutdown_request=0;
        repeat(3) @(negedge clk);
        fail_save=0;
        call2(12'h006,0,1); // explicit force-discard flag
        if(foreground_app!=0) $fatal(1,"explicit recovery did not exit");

        // Actual cache flush copies code/data to backing RAM without erasing
        // instructions that must execute after the syscall returns.
        cache_mem[0]=32'h77777777;
        call2(12'h014,0,0);
        if(ram[`SHAMA_APP_RAM_BASE>>2]!=32'h77777777 || cache_mem[0]!=32'h77777777 || sys_ret!=4) $fatal(1,"cache flush semantics");

        // Nonpolling app universal launch, then snapshot protection while editing.
        universal(8'h18);await_force();
        if(foreground_app!=1) $fatal(1,"universal Editor launch");
        editor_context(7,5,1,3); // write in progress, odd sequence
        universal(8'h16);
        repeat(25) begin @(negedge clk);if(cpu_hold) $fatal(1,"odd snapshot froze its writer");end
        ram[(EDIT_CTX+36)>>2]=4;
        await_force();
        if(foreground_app!=0 || save_count!=2) $fatal(1,"snapshot close failed");

        // Staged programs survive cleanup and load into executable fast memory.
        recovering_call(12'h061,32'he0000,4,2);
        recovering_call(12'h061,STAGE,TEST_SLOT+4,3);
        ram[STAGE>>2]=32'h99999999;
        call2(12'h061,STAGE,4);
        if(foreground_app!=15 || cache_mem[0]!=32'h99999999 || ram[STAGE>>2]!=0 || ram_used_bytes!=8)
            $fatal(1,"staged run/copy-before-clear/accounting");

        // Files borrows its text buffer for binaries. Editor can assemble in
        // its unused source tail; both must copy before clearing the64KiB span.
        call2(12'h006,2,0);
        if(ram_used_bytes!=66124) $fatal(1,"Files still reserves duplicate run workspace");
        ram[EDIT_BUF>>2]=32'haaaa1234;ram[(EDIT_BUF+65532)>>2]=32'hdeadbeef;
        call2(12'h061,EDIT_BUF,4);
        if(cache_mem[0]!=32'haaaa1234 || ram[EDIT_BUF>>2]!=0 || ram[(EDIT_BUF+65532)>>2]!=0 || ram_used_bytes!=8)
            $fatal(1,"Files borrowed source was erased before copy or retained after run");
        call2(12'h006,1,0);editor_context(7,0,0,2);
        ram[(EDIT_BUF+4096)>>2]=32'hbbbb5678;ram[(EDIT_CTX+64)>>2]=EDIT_BUF+4096;
        call2(12'h061,EDIT_BUF+4096,4);
        if(cache_mem[0]!=32'hbbbb5678 || ram[(EDIT_BUF+4096)>>2]!=0 || ram[(EDIT_CTX+64)>>2]!=0)
            $fatal(1,"Editor tail staging/context did not survive copy then clear");

        // A faulted writer cannot finish an odd snapshot. Recovery must not
        // deadlock or persist a half-edit; the notice names the unsaved data.
        call2(12'h006,1,0);editor_context(7,5,1,3);
        begin
            integer old_saves;
            old_saves=save_count;
            @(negedge clk);cpu_fault_code=8;
            wait(cpu_force_jump);
            cpu_fault_code=0;
            if(save_count!=old_saves || ram[EDIT_BUF>>2]!=0 || foreground_app!=0)
                $fatal(1,"faulted odd snapshot was saved or not reclaimed");
            acknowledge_notice(8'h82);
        end
        // An even snapshot is saved normally; a storage refusal still keeps
        // the previous committed file and completes a visible recovery.
        call2(12'h006,1,0);editor_context(7,5,1,4);fail_save=1;
        @(negedge clk);cpu_fault_code=8;
        wait(cpu_force_jump);cpu_fault_code=0;fail_save=0;
        acknowledge_notice(8'h82);
        call2(12'h006,7,0);
        @(negedge clk);gpu_capacity_fault=1;
        wait(clear_gpu_capacity_fault);
        @(negedge clk);gpu_capacity_fault=0;
        wait(cpu_force_jump);
        acknowledge_notice(4);

        // Storage protection is a modal warning, not a forced app discard.
        // Keep acknowledging a held raw-store rejection from reopening it.
        @(negedge clk);flash_write_protected=1;
        wait(notice_code==1);acknowledge_notice(1);
        repeat(5) @(negedge clk);
        if(notice_code!=0) $fatal(1,"stable protection reopened an acknowledged notice");
        raw_flash_write_blocked=1;
        wait(notice_code==1);acknowledge_notice(1);
        repeat(5) @(negedge clk);
        if(notice_code!=0) $fatal(1,"held rejected CPU store reopened its notice");
        raw_flash_write_blocked=0;flash_write_protected=0;

        input_overflow=1;input_queued_count=12;
        call2(12'h032,0,0);
        if(sys_ret!=268) $fatal(1,"input queue status");
        keyboard_shift=1;
        call2(12'h032,0,0);
        if(sys_ret!=780) $fatal(1,"keyboard shift status");

        // Fixed utility workspaces include the filename at +0x100, not only
        // the config record. Cleanup clears the reserved span and its accounting.
        call2(12'h006,8,0);
        if(ram_used_bytes!=280) $fatal(1,"Settings workspace accounting");
        ram[32'h2f000>>2]=32'h12345678;ram[32'h2f100>>2]=32'h74746573;ram[32'h2f10c>>2]=32'hffffffff;
        call2(12'h006,7,0);
        if(ram[32'h2f000>>2]!=0 || ram[32'h2f100>>2]!=0 || ram[32'h2f10c>>2]!=0 || ram_used_bytes!=280)
            $fatal(1,"Settings filename workspace leaked");
        ram[32'h2f200>>2]=32'h12345678;ram[32'h2f300>>2]=32'h74746573;ram[32'h2f30c>>2]=32'hffffffff;
        call2(12'h006,0,0);
        if(ram[32'h2f200>>2]!=0 || ram[32'h2f300>>2]!=0 || ram[32'h2f30c>>2]!=0 || ram_used_bytes!=8)
            $fatal(1,"Paint filename workspace leaked");

        // Shutdown saves the complete miner state and waits for storage/cleanup
        // before granting clock gating. The resident OS remains in RAM.
        call2(12'h006,3,0);
        ram[RECORD>>2]=32'h31524d53;ram[(RECORD+24)>>2]=1;ram[(RECORD+48)>>2]=32'hfeedcafe;ram[(RECORD+252)>>2]=2;
        @(negedge clk);shutdown_request=1;
        watchdog=0;
        while(!shutdown_ready && watchdog<300000) begin @(negedge clk);watchdog=watchdog+1;end
        if(!shutdown_ready || os_loaded || saved_miner[6]!=0 || saved_miner[12]!=32'hfeedcafe || ram[RECORD>>2]!=0)
            $fatal(1,"shutdown did not persist and clear state");
        if(!cpu_hold || cache_used_bytes!=0 || ram[`SHAMA_KERNEL_RAM_BASE>>2]!=32'h11111111) $fatal(1,"shutdown gate/resource state");
        $display("SERVICES SAVE CLOSE OWNERSHIP CLEAR FLUSH SNAPSHOT SHUTDOWN PASS");$finish;
    end
endmodule
