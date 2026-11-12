`include "shama_boot_params.svh"

module tb_soc;
    logic clk=0,power_switch=0,reset_button=1,pause_switch=0,step_button=0;
    always #5 clk=~clk;
    logic [7:0] kb_rows=0,kb_cols=0;
    logic [9:0] controller=0;
    logic halted,clock_hold,shutdown_ready,input_overflow;
    logic [31:0] debug_pc,debug_instruction,debug_lifecycle;
    logic [4:0] debug_flags;
    logic [5:0] debug_sha_round;
    logic [7:0] debug_fault;
    logic [3:0] debug_app;
    shama_soc #(.CORE_DIV_BIT(0)) dut(
        .clk,.power_switch,.reset_button,.pause_switch,.step_button,
        .clock_hold,.shutdown_ready,.input_overflow,.debug_pc,.debug_instruction,
        .debug_flags,.debug_sha_round,.debug_fault,.debug_app,.debug_lifecycle,
        .kb_rows,.kb_cols,.controller,.halted,
        .cache_selected_word(32'd0),.ram_selected_word(32'd0),
        .flash_selected_word(32'd0),.vram_selected_word(32'd0),.display_row_ready(1'b1)
    );
    integer cycles=0,boot_count=0;
    always @(posedge clk) begin
        cycles<=cycles+1;
        if($test$plusargs("trace") && cycles<200)
            $display("TRACE t=%0t c=%0d rst=%b pc=%h cpu=%0d svc=%0d fs=%0d bus=%0d/%0d valid=%b%b%b ready=%b%b%b sys=%b/%h",
                $time,cycles,dut.rst,debug_pc,dut.u_cpu.state,dut.u_services.state,dut.u_fs.state,
                dut.bus_owner,dut.bus_choice,dut.cpu_mem_valid,dut.svc_dma_valid,dut.fs_dma_valid,
                dut.cpu_mem_ready,dut.svc_dma_ready,dut.fs_dma_ready,dut.sys_valid,dut.sys_id);
        if($test$plusargs("progress") && cycles%500000==0)
            $display("SOC progress cycles=%0d app=%0d pc=%h cpu=%0d svc=%0d fs=%0d gpu=%0d",cycles,debug_app,debug_pc,
                dut.u_cpu.state,dut.u_services.state,dut.u_fs.state,dut.u_gpu.state);
        if(cycles>30000000) $fatal(1,"SoC watchdog app%0d pc%h cpu%0d svc%0d fs%0d gpu%0d owner%0d",debug_app,debug_pc,dut.u_cpu.state,dut.u_services.state,dut.u_fs.state,dut.u_gpu.state,dut.bus_owner);
        if(!reset_button && debug_fault!=0 && !$test$plusargs("recovery_flow"))
            $fatal(1,"CPU fault%0d pc%h",debug_fault,debug_pc);
    end
    task automatic settle;
        begin
            // Reach the actual app's event polling boundary after GUI/device
            // commands finish. Input queue depth and command completion matter.
            wait(dut.sys_valid && dut.sys_id==12'h003 && dut.u_services.state==0);
            repeat(12) @(negedge clk);
        end
    endtask
    task automatic control(input integer index);
        begin
            @(negedge clk);controller[index]=1;
            repeat(6) @(negedge clk);controller[index]=0;
            wait(dut.input_queued_count==0);
            settle();
        end
    endtask
    task automatic key(input integer row,input integer col);
        begin
            @(negedge clk);kb_rows=1<<row;kb_cols=1<<col;
            repeat(6) @(negedge clk);kb_rows=0;kb_cols=0;
            wait(dut.input_queued_count==0);
            settle();
        end
    endtask
    task automatic queued_key(input integer row,input integer col);
        begin
            // A physical typing burst stays within the real16-event FIFO.
            @(negedge clk);kb_rows=1<<row;kb_cols=1<<col;
            repeat(6) @(negedge clk);kb_rows=0;kb_cols=0;
            repeat(6) @(negedge clk);
        end
    endtask
    task automatic raw_control(input integer index);
        begin
            @(negedge clk);controller[index]=1;
            repeat(6) @(negedge clk);controller[index]=0;
            repeat(6) @(negedge clk);
        end
    endtask
    task automatic create_assemble_run;
        integer source_entry,binary_entry,base,source_data,binary_data;
        begin
            control(8);wait(debug_app==1);settle();
            control(1);control(4); // New Program, then filename.
            key(3,1);key(4,5); // Z + Enter => Z.ASM
            // LDI R1 42\nHLT\n, entered entirely through keyboard wires.
            queued_key(1,3);queued_key(0,3);queued_key(1,0);queued_key(4,4);
            queued_key(2,1);queued_key(3,3);queued_key(4,4);queued_key(3,6);
            queued_key(3,4);queued_key(4,5);queued_key(0,7);queued_key(1,3);
            queued_key(2,3);queued_key(4,5);
            wait(dut.input_queued_count==0);settle();
            if(dut.u_ram.model.memory[32'h2c208>>2]!=14 || dut.u_ram.model.memory[32'h18000>>2]!==32'h2049444c)
                $fatal(1,"physical source input was incomplete");
            control(5); // B opens Editor actions.
            repeat(5) control(1); // Assemble and Run.
            raw_control(4); // The next app intentionally never polls events.
            wait(debug_app==15 && halted);
            repeat(12) @(negedge clk);
            if(dut.u_cpu.regs[1]!==32'd42) $fatal(1,"assembled user code did not execute its LDI");
            source_entry=-1;binary_entry=-1;
            for(integer index=0;index<64;index=index+1) begin
                base=(`SHAMA_FS_TABLE_FLASH_OFFSET+index*64)>>2;
                if(dut.u_flash.model.memory[base][7:0]!=0) begin
                    if(dut.u_flash.model.memory[base+5]===32'h412e5a05 &&
                       dut.u_flash.model.memory[base+6][15:0]===16'h4d53) source_entry=base;
                    if(dut.u_flash.model.memory[base+5]===32'h422e5a05 &&
                       dut.u_flash.model.memory[base+6][15:0]===16'h4e49) binary_entry=base;
                end
            end
            if(source_entry<0 || binary_entry<0) $fatal(1,"assembly did not persist separate Z.ASM and Z.BIN");
            if(dut.u_flash.model.memory[source_entry][15:8]!=2 || dut.u_flash.model.memory[source_entry+1]!=14 ||
               dut.u_flash.model.memory[binary_entry][15:8]!=3 || dut.u_flash.model.memory[binary_entry+1]!=12)
                $fatal(1,"source/executable metadata mismatch");
            source_data=dut.u_flash.model.memory[source_entry+2]*64;
            binary_data=dut.u_flash.model.memory[binary_entry+2]*64;
            if(dut.u_flash.model.memory[source_data]!==32'h2049444c ||
               dut.u_flash.model.memory[source_data+1]!==32'h34203152 ||
               dut.u_flash.model.memory[source_data+2]!==32'h4c480a32 ||
               dut.u_flash.model.memory[source_data+3][15:0]!==16'h0a54)
                $fatal(1,"saved assembly source differs from typed bytes");
            if(dut.u_flash.model.memory[binary_data]!==32'h03100000 ||
               dut.u_flash.model.memory[binary_data+1]!==32'd42 ||
               dut.u_flash.model.memory[binary_data+2]!==32'h01000000)
                $fatal(1,"saved executable differs from executed machine code");
            raw_control(6);wait(debug_app==0);settle(); // Home escapes halted app15.
            if(dut.u_ram.model.memory[32'h28000>>2]!=0 || debug_lifecycle!=0 || input_overflow)
                $fatal(1,"user program close did not clear staging/retain input");
            $display("SOC PROGRAM ASSEMBLE RUN HOME PASS cycles=%0d",cycles);
        end
    endtask
    task automatic dismiss_notice(input integer expected_code);
        begin
            wait(dut.notice_ready);
            if(dut.notice_code!=expected_code || !dut.svc_cpu_hold)
                $fatal(1,"modal resource notice missing code=%0d expected=%0d",dut.notice_code,expected_code);
            raw_control(4);
            wait(dut.notice_code==0 && !dut.notice_busy);
        end
    endtask
    task automatic recover_resource_fault;
        integer expected_notice,unused;
        logic [31:0] old_flash_word;
        begin
            unused=$value$plusargs("expected_notice=%d",expected_notice);
            if(!unused) $fatal(1,"missing expected resource notice");
            control(9);wait(debug_app==2);settle();
            key(3,1); // Z selects the uniquely named ZFAULT.BIN.
            if(dut.u_ram.model.memory[32'h2c000>>2]!==32'h5541465a)
                $fatal(1,"fault fixture was not selected in Explorer");
            old_flash_word=dut.u_flash.model.memory[`SHAMA_FLASH_BYTES/4-1];
            raw_control(4); // Run the actual BIN; it intentionally does not poll.
            wait(dut.notice_ready);
            if(dut.notice_code!=expected_notice)
                $fatal(1,"wrong recovery reason code=%0d expected=%0d",dut.notice_code,expected_notice);
            if($test$plusargs("protected_flash")) begin
                if(debug_app!=15 || dut.u_flash.model.memory[`SHAMA_FLASH_BYTES/4-1]!==old_flash_word)
                    $fatal(1,"protected CPU flash store changed storage or discarded its app");
            end else if(debug_app!=0 || debug_fault!=0 || dut.gpu_capacity_fault)
                $fatal(1,"recovery did not reset app/fault state app=%0d cpu=%0d gpu=%b",debug_app,debug_fault,dut.gpu_capacity_fault);
            dismiss_notice(expected_notice);
            if($test$plusargs("protected_flash")) begin
                wait(halted);
                raw_control(6);wait(debug_app==0);
            end
            settle();
            if(dut.u_ram.model.memory[32'h18000>>2]!=0 ||
               dut.u_ram.model.memory[32'h28000>>2]!=0 || input_overflow)
                $fatal(1,"resource recovery did not clear staging or preserve input");
            $display("SOC RESOURCE RECOVERY PASS cause=%0d cycles=%0d",expected_notice,cycles);
        end
    endtask
    initial begin
        repeat(4) @(negedge clk);
        power_switch=1;reset_button=0;
        if($test$plusargs("expect_boot_failure")) begin : failed_boot
            integer expected_error,unused;
            logic [31:0] failure_pc;
            unused=$value$plusargs("boot_error=%d",expected_error);
            if(!unused) $fatal(1,"missing expected boot error");
            wait(halted);
            failure_pc=debug_pc;
            repeat(24) @(negedge clk);
            if(dut.os_loaded || debug_pc!=failure_pc || !halted)
                $fatal(1,"boot failure did not remain halted before Desktop");
            if(dut.u_cpu.regs[1]!==32'(expected_error) || dut.u_cpu.regs[2]!==32'hffffffff)
                $fatal(1,"boot diagnostics lost r1=%h r2=%h",dut.u_cpu.regs[1],dut.u_cpu.regs[2]);
            if(debug_fault!=0) $fatal(1,"expected deliberate boot halt, not CPU decode fault");
            $display("SOC BOOT FAILURE PASS error=%0d cycles=%0d",expected_error,cycles);
            $finish;
        end
        wait(dut.os_loaded && debug_app==0);
        // A pre-existing full volume displays its protection notice before the
        // first app gets to poll input. Acknowledge that real boot-time popup.
        if($test$plusargs("protected_flash")) dismiss_notice(1);
        settle();
        $display("SOC BOOT desktop PASS cycles=%0d",cycles);
        if($test$plusargs("recovery_flow")) begin
            recover_resource_fault();
            $finish;
        end
        if($test$plusargs("program_flow")) begin
            create_assemble_run();
            $finish;
        end
        // Pause must hold CPU state; a single step permits one full divider
        // pulse. Continue normal operation before interacting with the apps.
        pause_switch=1;repeat(8) @(negedge clk);
        begin
            logic [31:0] old_counter;
            old_counter=dut.u_cpu.cycle_counter;
            repeat(16) @(negedge clk);
            if(dut.u_cpu.cycle_counter!=old_counter) $fatal(1,"pause failed");
            step_button=1;repeat(6) @(negedge clk);step_button=0;
            repeat(12) @(negedge clk);
            if(dut.u_cpu.cycle_counter!=old_counter+1) $fatal(1,"manual step was not one clock");
        end
        pause_switch=0;
        control(8);wait(debug_app==1);settle();
        $display("SOC Editor universal launch PASS");
        control(4); // New Text, ask for name.
        key(2,7); // X
        key(4,5); // Enter => X.TXT
        wait(dut.u_cpu.regs[10]!=0);settle();
        $display("SOC named document created PASS");
        key(0,0);key(0,1);key(0,2); // ABC
        if(dut.u_ram.model.memory[32'h18000>>2][23:0]!==24'h434241) $fatal(1,"typed source corrupted");
        if(dut.u_ram.model.memory[32'h2c214>>2]!=1) $fatal(1,"dirty flag missing");
        // Universal close saves the dirty named document before clearing it.
        control(6);wait(debug_app==0);settle();
        if(dut.u_ram.model.memory[32'h18000>>2]!=0) $fatal(1,"source not cleared on close");
        if(debug_lifecycle!=0) $fatal(1,"clean close failed %h",debug_lifecycle);
        $display("SOC dirty save/cleanup PASS");
        // Turn power off while paused; shutdown must override pause and finish.
        pause_switch=1;power_switch=0;
        wait(shutdown_ready);wait(!clock_hold);
        repeat(12) @(negedge clk);
        if(dut.u_cache.model.memory[0]!=0) $fatal(1,"cache was not cleared");
        pause_switch=0;power_switch=1;
        wait(dut.os_loaded && debug_app==0);settle();
        $display("SOC reboot from erased cache PASS");
        // Open Explorer, locate X.TXT through the real directory search, transfer
        // ownership to Editor, and confirm the saved data survived shutdown.
        control(9);wait(debug_app==2);settle();
        key(2,7); // X selects the first matching filename initial.
        if(dut.u_ram.model.memory[32'h2c000>>2]!=32'h58542e58) $fatal(1,"saved X.TXT missing from Explorer");
        control(2);wait(debug_app==1);settle(); // Left edits TXT; A opens its viewer.
        if(dut.u_ram.model.memory[32'h18000>>2][23:0]!==24'h434241) $fatal(1,"saved text did not survive reboot");
        if(input_overflow) $fatal(1,"unexpected lost input");
        $display("SOC persistence/Explorer handoff PASS");
        $display("SOC INTEGRATION PASS cycles=%0d",cycles);
        $finish;
    end
endmodule
