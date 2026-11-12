`include "shama_boot_params.svh"
module tb_services;
    localparam integer TEST_SLOT=`SHAMA_SLOT_BYTES,TEST_CACHE=`SHAMA_CACHE_BYTES;
    localparam [31:0] EDIT_CTX=`SHAMA_EDIT_CTX,EDIT_BUF=`SHAMA_EDIT_BUF,STAGE=`SHAMA_RUN_STAGING,RECORD=`SHAMA_MINER_RECORD;
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
    logic [31:0] cache_mem [0:TEST_CACHE/4-1];
    logic [31:0] saved_editor [0:3];
    logic [31:0] saved_miner [0:63];
    logic fail_save=0;
    logic [63:0] save_error=64'hfffffffffffffffd;
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
                if(dma_addr>=TEST_CACHE) $fatal(1,"cache address overflow %h",dma_addr);
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
            if(fail_save) ext_ret<=save_error;
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
    task automatic modal_event(input [7:0] code,key);
        begin
            @(negedge clk);event_code=code;key_code=key;event_valid=1;
            wait(event_ack);
            if(event_ack_data!=={controller_latched,key_code,event_code}) $fatal(1,"modal event snapshot mismatch");
            @(negedge clk);event_valid=0;event_code=0;key_code=0;
            repeat(2) @(negedge clk);
        end
    endtask
    task automatic call_keep2(input [11:0] id,input [31:0] a1,a2);
        fork
            call2(id,a1,a2);
            begin wait(notice_code==6);modal_event(8'h01,8'h0d);end
        join
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
    task automatic check_foreground_heap(
        input [3:0] app,
        input integer prefix_pages,
        input [31:0] tail_base,
        input integer tail_pages,
        input [31:0] work_base,
        input integer work_bytes,
        input [31:0] extra_base,
        input integer extra_bytes
    );
        begin
            call2(12'h006,app,0);
            if(app==1) editor_context(0,0,0,2);
            if(app==3) ram[(RECORD+252)>>2]=0;
            if(work_bytes!=0) begin
                ram[work_base>>2]=32'h13579bdf;
                ram[(work_base+work_bytes-4)>>2]=32'h2468ace0;
            end
            if(extra_bytes!=0) begin
                ram[extra_base>>2]=32'haabbccdd;
                ram[(extra_base+extra_bytes-4)>>2]=32'hee112233;
            end
            call2(12'h010,prefix_pages*4096,0);
            if(sys_ret!=`SHAMA_HEAP_BASE) $fatal(1,"wrong heap prefix for app %d",app);
            ram[`SHAMA_HEAP_BASE>>2]=32'hdeadbeef;
            ram[(`SHAMA_HEAP_BASE+prefix_pages*4096-4)>>2]=32'habcdef01;
            if(tail_pages!=0) begin
                call2(12'h010,tail_pages*4096,0);
                if(sys_ret!=tail_base) $fatal(1,"wrong heap tail for app %d",app);
                ram[tail_base>>2]=32'hfedcba98;
                ram[(tail_base+tail_pages*4096-4)>>2]=32'h76543210;
            end
            if(dut.heap_used_bytes!=(prefix_pages+tail_pages)*4096)
                $fatal(1,"heap accounting changed for app %d",app);
            if(work_bytes!=0 && (ram[work_base>>2]!=32'h13579bdf || ram[(work_base+work_bytes-4)>>2]!=32'h2468ace0))
                $fatal(1,"heap clobbered foreground workspace for app %d",app);
            if(extra_bytes!=0 && (ram[extra_base>>2]!=32'haabbccdd || ram[(extra_base+extra_bytes-4)>>2]!=32'hee112233))
                $fatal(1,"heap clobbered future staging/SHA workspace for app %d",app);
            call2(12'h006,0,0);
            if(ram[`SHAMA_HEAP_BASE>>2]!=0 || ram[(`SHAMA_HEAP_BASE+prefix_pages*4096-4)>>2]!=0 || dut.heap_used_bytes!=0)
                $fatal(1,"app replacement retained heap prefix for app %d",app);
            if(tail_pages!=0 && (ram[tail_base>>2]!=0 || ram[(tail_base+tail_pages*4096-4)>>2]!=0))
                $fatal(1,"app replacement retained heap tail for app %d",app);
            if(work_bytes!=0 && (ram[work_base>>2]!=0 || ram[(work_base+work_bytes-4)>>2]!=0))
                $fatal(1,"app replacement retained workspace for app %d",app);
            if(extra_bytes!=0 && (ram[extra_base>>2]!=0 || ram[(extra_base+extra_bytes-4)>>2]!=0))
                $fatal(1,"app replacement retained staging/SHA workspace for app %d",app);
            if(ram[`SHAMA_KERNEL_RAM_BASE>>2]!=32'h11111111)
                $fatal(1,"app replacement damaged resident kernel");
        end
    endtask
    initial begin
        for(i=0;i<`SHAMA_RAM_BYTES/4;i=i+1) ram[i]=0;
        for(i=0;i<TEST_CACHE/4;i=i+1) cache_mem[i]=0;
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
        // Desktop can use every page above the protected 12 KiB prefix. The
        // last allocation ends at the physical RAM boundary; the next byte
        // recovers gracefully without touching kernel/name/context storage.
        ram[`SHAMA_EDIT_NAME>>2]=32'hcafef00d;
        recovering_call(12'h010,`SHAMA_HEAP_PAGES*4096+1,0,2);
        if(ram_used_bytes!=8 || ram[`SHAMA_EDIT_NAME>>2]!=32'hcafef00d)
            $fatal(1,"heap exceeded its map or changed resident metadata");
        call2(12'h010,`SHAMA_HEAP_PAGES*4096,0);
        if(sys_ret!=`SHAMA_HEAP_BASE || ram_used_bytes!=`SHAMA_HEAP_PAGES*4096+8 || ram[`SHAMA_EDIT_NAME>>2]!=32'hcafef00d)
            $fatal(1,"last heap page allocation crossed its boundary");
        ram[(`SHAMA_RAM_BYTES-4)>>2]=32'h12345678;
        call2(12'h011,`SHAMA_HEAP_BASE,0);
        if(ram[(`SHAMA_RAM_BYTES-4)>>2]!=0 || ram_used_bytes!=8 || ram[`SHAMA_EDIT_NAME>>2]!=32'hcafef00d)
            $fatal(1,"free did not clear last heap page or damaged metadata");
        call2(12'h010,`SHAMA_HEAP_PAGES*4096,0);
        ram[(`SHAMA_RAM_BYTES-4)>>2]=32'hdeadbeef;
        recovering_call(12'h010,1,0,2);
        if(ram[(`SHAMA_RAM_BYTES-4)>>2]!=0 || ram_used_bytes!=8) $fatal(1,"exhausted heap was not cleared on recovery");
        ram[`SHAMA_HEAP_BASE>>2]=32'haaaaaaaa;ram[(`SHAMA_HEAP_BASE+8192-4)>>2]=32'hbbbbbbbb;
        call2(12'h010,5000,0);
        if(ram[`SHAMA_HEAP_BASE>>2]!=0 || ram[(`SHAMA_HEAP_BASE+8192-4)>>2]!=0) $fatal(1,"new allocation leaks stale data");
        if(sys_ret!=`SHAMA_HEAP_BASE || ram_used_bytes!=8200) $fatal(1,"allocation/accounting");
        ram[`SHAMA_HEAP_BASE>>2]=32'hdeadbeef;ram[(`SHAMA_HEAP_BASE+8192-4)>>2]=32'habcdef01;
        call2(12'h011,`SHAMA_HEAP_BASE,0);
        if(ram[`SHAMA_HEAP_BASE>>2]!=0 || ram[(`SHAMA_HEAP_BASE+8192-4)>>2]!=0 || ram_used_bytes!=8) $fatal(1,"free must zero and release allocation");
        call2(12'h010,4096,0);ram[`SHAMA_HEAP_BASE>>2]=32'hdeadbeef;
        call2(12'h006,1,0);
        if(foreground_app!=1 || ram[`SHAMA_HEAP_BASE>>2]!=0 || cache_mem[0]!=32'h33333333) $fatal(1,"launch cleanup");

        // Dirty named file saves before resources/memory are reclaimed.
        editor_context(7,5,1,2);
        call2(12'h001,0,0);
        if(foreground_app!=0 || save_count!=1 || saved_editor[0]!=32'h6c6c6568 || ram[EDIT_BUF>>2]!=0 || ram[(EDIT_CTX+20)>>2]!=0)
            $fatal(1,"dirty Editor exit/save/clear");
        if(ram[`SHAMA_KERNEL_RAM_BASE>>2]!=32'h11111111) $fatal(1,"exit erased OS");

        // Unnamed dirty text and failed saves keep the live editor intact.
        call2(12'h006,1,0);editor_context(0,5,1,2);
        call_keep2(12'h001,0,0);
        if(foreground_app!=1 || lifecycle_error==0 || ram[EDIT_BUF>>2]!=32'h6c6c6568) $fatal(1,"unnamed dirty close silently discarded");
        editor_context(7,5,1,2);fail_save=1;
        call_keep2(12'h001,0,0);
        if(foreground_app!=1 || lifecycle_error!=32'hfffffffd || ram[EDIT_BUF>>2]!=32'h6c6c6568) $fatal(1,"failed save destroyed editor");
        // Power-off uses the same failure contract: do not authorize gating or
        // erase the unsaved buffer when persistent storage rejects the write.
        @(negedge clk);shutdown_request=1;
        wait(ext_valid);wait(!ext_valid);
        wait(notice_code==6);
        if(shutdown_ready || !cpu_hold || foreground_app!=1) $fatal(1,"failed shutdown did not wait for safe choice");
        modal_event(8'h15,0); // Back always keeps the app, even for shutdown.
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
        recovering_call(12'h061,STAGE,TEST_CACHE+4,3);
        ram[STAGE>>2]=32'h99999999;
        call2(12'h061,STAGE,4);
        if(foreground_app!=15 || cache_mem[0]!=32'h99999999 || ram[STAGE>>2]!=0 || ram_used_bytes!=8)
            $fatal(1,"staged run/copy-before-clear/accounting");

        // A full-cache user executable can live in an allocated heap span.
        // Cleanup retains the source until its first and last words are in
        // cache and low RAM backing, then wipes the whole assembly span.
        call2(12'h006,0,0);
        call2(12'h010,`SHAMA_HEAP_PAGES*4096,0);
        for(i=0;i<TEST_CACHE/4;i=i+1) ram[(STAGE>>2)+i]=32'hc0de0000+i;
        ram[(`SHAMA_RAM_BYTES-4)>>2]=32'habadcafe;
        ram[`SHAMA_EDIT_NAME>>2]=32'h6d616e65;
        ram[(EDIT_CTX+64)>>2]=32'h13572468;
        call2(12'h061,STAGE,TEST_CACHE);
        if(foreground_app!=15 || ram_used_bytes!=TEST_CACHE+4 || cache_used_bytes!=TEST_CACHE ||
           cache_mem[0]!=32'hc0de0000 || cache_mem[TEST_CACHE/4-1]!=32'hc0de0000+TEST_CACHE/4-1 ||
           ram[`SHAMA_APP_RAM_BASE>>2]!=32'hc0de0000 || ram[(`SHAMA_APP_RAM_BASE+TEST_CACHE-4)>>2]!=32'hc0de0000+TEST_CACHE/4-1 ||
           ram[STAGE>>2]!=0 || ram[(STAGE+TEST_CACHE-4)>>2]!=0 || ram[(`SHAMA_RAM_BYTES-4)>>2]!=0)
            $fatal(1,"heap cleanup lost full-cache staged source or retained it after copy");
        cache_mem[0]=32'h01234567;cache_mem[TEST_CACHE/4-1]=32'h89abcdef;
        call2(12'h014,0,0);
        if(sys_ret!=TEST_CACHE || ram[`SHAMA_APP_RAM_BASE>>2]!=32'h01234567 ||
           ram[(`SHAMA_APP_RAM_BASE+TEST_CACHE-4)>>2]!=32'h89abcdef)
            $fatal(1,"full-cache flush missed backing endpoints");
        call2(12'h006,0,0);
        if(ram[`SHAMA_APP_RAM_BASE>>2]!=32'h22222222 || ram[(`SHAMA_APP_RAM_BASE+TEST_CACHE-4)>>2]!=0 ||
           cache_mem[TEST_CACHE/4-1]!=0 || ram[`SHAMA_KERNEL_RAM_BASE>>2]!=32'h11111111 ||
           ram[`SHAMA_EDIT_NAME>>2]!=32'h6d616e65 || ram[(EDIT_CTX+64)>>2]!=32'h13572468)
            $fatal(1,"app clear did not stop at 8 KiB or erased resident metadata");
        call2(12'h010,`SHAMA_HEAP_PAGES*4096,0);
        ram[STAGE>>2]=32'hdeadbeef;ram[(`SHAMA_RAM_BYTES-4)>>2]=32'habcdef01;
        recovering_call(12'h061,STAGE,TEST_CACHE+4,3);
        if(dut.heap_used_bytes!=0 || ram[STAGE>>2]!=0 || ram[(`SHAMA_RAM_BYTES-4)>>2]!=0 ||
           ram[`SHAMA_KERNEL_RAM_BASE>>2]!=32'h11111111 || ram[`SHAMA_EDIT_NAME>>2]!=32'h6d616e65)
            $fatal(1,"oversized staged-run recovery retained heap data or erased resident state");
        ram[(`SHAMA_RAM_BYTES-4)>>2]=32'hcafe1234;
        call2(12'h061,`SHAMA_RAM_BYTES-4,4);
        if(cache_mem[0]!=32'hcafe1234 || ram[`SHAMA_APP_RAM_BASE>>2]!=32'hcafe1234 || ram[(`SHAMA_RAM_BYTES-4)>>2]!=0)
            $fatal(1,"last physical RAM word cannot stage an executable");
        call2(12'h006,0,0);

        // Foreground ownership, including partial pages, protects all present
        // and future workspaces. Each complete available heap is cleared when
        // replacing its owner, even where the next app uses the same addresses.
        check_foreground_heap(1,1,0,0,EDIT_BUF,65536,STAGE,16384);
        check_foreground_heap(2,1,STAGE,4,EDIT_BUF,65536,0,0);
        check_foreground_heap(3,1,32'h6000,18,`SHAMA_MINER_BASE,4096,`SHAMA_MINER_SOFT_BASE,1280);
        check_foreground_heap(4,21,0,0,0,0,0,0);
        check_foreground_heap(5,2,32'h6000,18,`SHAMA_TERMINAL_BUF,1024,0,0);
        check_foreground_heap(6,21,0,0,0,0,0,0);
        check_foreground_heap(7,1,32'h5000,19,`SHAMA_PAINT_BASE,272,0,0);
        check_foreground_heap(8,1,32'h5000,19,`SHAMA_SETTINGS_BASE,272,0,0);
        call2(12'h006,1,0);editor_context(0,0,0,2);
        call2(12'h010,4096,0);
        recovering_call(12'h010,1,0,2);
        if(ram[EDIT_BUF>>2]!=0 || ram_used_bytes!=8 || foreground_app!=0)
            $fatal(1,"Editor heap exhaustion failed to recover without aliasing its workspace");

        // Cancelling a failed staged launch keeps its allocation and data.
        // A later explicit free must clear the once-staged words normally.
        call2(12'h006,3,0);ram[(RECORD+252)>>2]=2;
        call2(12'h010,18*4096,0);
        if(sys_ret!=32'h6000) $fatal(1,"Miner large heap allocation misplaced");
        ram[STAGE>>2]=32'h76543210;ram[(`SHAMA_RAM_BYTES-4)>>2]=32'habcdef01;
        fail_save=1;call_keep2(12'h061,STAGE,4);
        if(foreground_app!=3 || dut.heap_used_bytes!=18*4096 || ram[STAGE>>2]!=32'h76543210 ||
           ram[(`SHAMA_RAM_BYTES-4)>>2]!=32'habcdef01 || cache_mem[0]!=32'h55555555)
            $fatal(1,"cancelled staged launch discarded its owned heap or cache");
        call2(12'h011,32'h6000,0);
        if(dut.heap_used_bytes!=0 || ram[STAGE>>2]!=0 || ram[(`SHAMA_RAM_BYTES-4)>>2]!=0)
            $fatal(1,"cancelled staging protection leaked into explicit free");
        fail_save=0;call2(12'h006,0,0);

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
        wait(notice_code==1);
        modal_event(8'h01,"X");
        if(notice_code!=1 || !cpu_hold) $fatal(1,"irrelevant modal key dismissed warning");
        modal_event(8'h01,8'h0d);
        if(notice_code!=0 || cpu_hold) $fatal(1,"Enter did not dismiss storage warning");
        repeat(5) @(negedge clk);
        if(notice_code!=0) $fatal(1,"stable protection reopened an acknowledged notice");
        raw_flash_write_blocked=1;
        wait(notice_code==1);modal_event(8'h01,8'h1b);
        if(notice_code!=0 || cpu_hold) $fatal(1,"Escape did not dismiss storage warning");
        repeat(5) @(negedge clk);
        if(notice_code!=0) $fatal(1,"held rejected CPU store reopened its notice");
        raw_flash_write_blocked=0;flash_write_protected=0;

        // At95%, Miner must have an explicit way to reach Files and reclaim
        // space. Default/Back preserve live state; Right then Confirm alone
        // permits discard. The last committed state is never overwritten.
        call2(12'h006,3,0);
        ram[RECORD>>2]=32'h31524d53;ram[(RECORD+24)>>2]=1;
        ram[(RECORD+48)>>2]=32'habcdef12;ram[(RECORD+252)>>2]=2;
        fail_save=1;save_error=64'hfffffffffffffff8;flash_write_protected=1;
        wait(notice_code==1);acknowledge_notice(1);
        begin
            integer before_cleanup,before_saves;
            before_cleanup=cleanup_count;before_saves=save_count;
            universal(8'h19);wait(notice_code==6);
            if(foreground_app!=3 || !cpu_hold || cleanup_count!=before_cleanup)
                $fatal(1,"failed Miner save did not retain workspace");
            modal_event(8'h01,"Q");
            if(notice_code!=6) $fatal(1,"ordinary typing changed safe close choice");
            modal_event(8'h13,0);
            if(notice_code!=7) $fatal(1,"Right did not select explicit discard");
            modal_event(8'h15,0); // Back cancels even after selecting Discard.
            if(foreground_app!=3 || cpu_hold || cleanup_count!=before_cleanup || ram[(RECORD+48)>>2]!=32'habcdef12)
                $fatal(1,"cancel discarded Miner state");
            universal(8'h19);wait(notice_code==6);
            modal_event(8'h13,0);modal_event(8'h01,8'h1b);
            if(foreground_app!=3 || cpu_hold || cleanup_count!=before_cleanup)
                $fatal(1,"Escape confirmed selected discard");
            universal(8'h19);wait(notice_code==6);
            modal_event(8'h13,0);modal_event(8'h12,0);modal_event(8'h01,8'h0d);
            if(foreground_app!=3 || cleanup_count!=before_cleanup) $fatal(1,"Keep App was not the safe confirm choice");
            universal(8'h19);wait(notice_code==6);
            modal_event(8'h01,8'h83);
            notice_busy=1; // Keep the overlay-restore fence outstanding.
            modal_event(8'h01,8'h0d);
            repeat(5) @(negedge clk);
            if(cleanup_count!=before_cleanup || foreground_app!=3 || ram[(RECORD+48)>>2]!=32'habcdef12)
                $fatal(1,"discard raced the modal restore fence");
            notice_busy=0;await_force();
            if(foreground_app!=2 || cache_mem[0]!=32'h44444444 || ram[RECORD>>2]!=0 ||
               save_count!=before_saves || cleanup_count!=before_cleanup+1)
                $fatal(1,"explicit discard did not reach Files without another write");
        end
        fail_save=0;flash_write_protected=0;

        // A synchronous staged Run retains its original source pointer and
        // target while the failed-close dialog is open, including source-tail
        // reuse. Explicit discard copies the code before clearing that source.
        call2(12'h006,1,0);editor_context(7,5,1,2);
        ram[(EDIT_BUF+4096)>>2]=32'hddccbb77;fail_save=1;
        fork
            call2(12'h061,EDIT_BUF+4096,4);
            begin wait(notice_code==6);modal_event(8'h13,0);modal_event(8'h14,0);end
        join
        if(foreground_app!=15 || cache_mem[0]!=32'hddccbb77 || ram[(EDIT_BUF+4096)>>2]!=0 || !last_jump)
            $fatal(1,"discard choice lost staged Run intent or source data");
        fail_save=0;save_error=64'hfffffffffffffffd;

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
        ram[`SHAMA_SETTINGS_BASE>>2]=32'h12345678;ram[(`SHAMA_SETTINGS_BASE+256)>>2]=32'h74746573;ram[(`SHAMA_SETTINGS_BASE+268)>>2]=32'hffffffff;
        call2(12'h006,7,0);
        if(ram[`SHAMA_SETTINGS_BASE>>2]!=0 || ram[(`SHAMA_SETTINGS_BASE+256)>>2]!=0 || ram[(`SHAMA_SETTINGS_BASE+268)>>2]!=0 || ram_used_bytes!=280)
            $fatal(1,"Settings filename workspace leaked");
        ram[`SHAMA_PAINT_BASE>>2]=32'h12345678;ram[(`SHAMA_PAINT_BASE+256)>>2]=32'h74746573;ram[(`SHAMA_PAINT_BASE+268)>>2]=32'hffffffff;
        call2(12'h006,0,0);
        if(ram[`SHAMA_PAINT_BASE>>2]!=0 || ram[(`SHAMA_PAINT_BASE+256)>>2]!=0 || ram[(`SHAMA_PAINT_BASE+268)>>2]!=0 || ram_used_bytes!=8)
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

        // An explicitly confirmed discard may also finish the originally
        // requested shutdown, after draining storage and clearing live data.
        @(negedge clk);shutdown_request=0;rst=1;
        repeat(3) @(negedge clk);rst=0;
        call2(12'h009,0,0);call2(12'h006,3,0);
        ram[RECORD>>2]=32'h31524d53;ram[(RECORD+48)>>2]=32'h11223344;ram[(RECORD+252)>>2]=2;
        fail_save=1;save_error=64'hfffffffffffffff8;
        @(negedge clk);shutdown_request=1;wait(notice_code==6);
        modal_event(8'h13,0);modal_event(8'h14,0);
        wait(shutdown_ready);
        if(os_loaded || cache_used_bytes!=0 || ram[RECORD>>2]!=0 || saved_miner[12]!=32'hfeedcafe)
            $fatal(1,"explicit shutdown discard lost saved state or left live resources");
        $display("SERVICES SAVE CLOSE OWNERSHIP CLEAR FLUSH SNAPSHOT SHUTDOWN PASS");$finish;
    end
endmodule
