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
        if(!reset_button && debug_fault!=0) $fatal(1,"CPU fault%0d pc%h",debug_fault,debug_pc);
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
        wait(dut.os_loaded && debug_app==0);settle();
        $display("SOC BOOT desktop PASS cycles=%0d",cycles);
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
        if(dut.u_ram.model.memory[32'hc0000>>2][23:0]!==24'h434241) $fatal(1,"typed source corrupted");
        if(dut.u_ram.model.memory[32'hb1014>>2]!=1) $fatal(1,"dirty flag missing");
        // Universal close saves the dirty named document before clearing it.
        control(6);wait(debug_app==0);settle();
        if(dut.u_ram.model.memory[32'hc0000>>2]!=0) $fatal(1,"source not cleared on close");
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
        if(dut.u_ram.model.memory[32'hb0000>>2]!=32'h58542e58) $fatal(1,"saved X.TXT missing from Explorer");
        control(2);wait(debug_app==1);settle(); // Left edits TXT; A opens its viewer.
        if(dut.u_ram.model.memory[32'hc0000>>2][23:0]!==24'h434241) $fatal(1,"saved text did not survive reboot");
        if(input_overflow) $fatal(1,"unexpected lost input");
        $display("SOC persistence/Explorer handoff PASS");
        $display("SOC INTEGRATION PASS cycles=%0d",cycles);
        $finish;
    end
endmodule
