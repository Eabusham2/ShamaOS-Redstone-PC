`include "shama_boot_params.svh"

module tb_services;
    logic clk=0,rst=1;
    logic sys_valid=0,sys_ready,sys_jump_valid;
    logic [11:0] sys_id=0;
    logic [191:0] sys_args=0;
    logic [63:0] sys_ret;
    logic [31:0] sys_jump_pc;

    logic event_valid=0,event_ack;
    logic [7:0] event_code=0,key_code=0;
    logic [9:0] controller_latched=0;

    logic dma_valid,dma_we,dma_flash,dma_cache,dma_ready;
    logic [31:0] dma_addr,dma_wdata,dma_rdata;
    logic [3:0] dma_wstrb;

    logic ext_valid,ext_ready=0,ext_jump_valid=0,ext_load_app=0;
    logic [11:0] ext_id;
    logic [191:0] ext_args;
    logic [63:0] ext_ret=0;
    logic [31:0] ext_jump_pc=0;
    logic [3:0] ext_app_id=0;

    logic os_loaded;
    logic [3:0] foreground_app;
    logic cpu_hold,cpu_force_jump;
    logic [31:0] cpu_force_pc;
    logic [31:0] ram_used_bytes,cache_used_bytes;

    logic [7:0] cache [0:255];
    logic [7:0] ram [0:65535];
    logic [7:0] flash [0:65535];

    integer i;
    integer watchdog;
    logic last_jump_valid;
    logic [31:0] last_jump_pc;
    logic [31:0] aligned;

    always #5 clk=~clk;

    shama_services #(.APP_SLOT_BYTES(256)) dut(
        .clk,.rst,
        .sys_valid,.sys_id,.sys_args,.sys_ready,.sys_ret,.sys_jump_valid,.sys_jump_pc,
        .event_valid,.event_code,.key_code,.controller_latched,.event_ack,
        .flash_used_bytes(32'd0),.time_counter(64'd0),
        .ram_used_bytes,.cache_used_bytes,
        .dma_valid,.dma_we,.dma_flash,.dma_cache,.dma_addr,.dma_wdata,.dma_wstrb,
        .dma_ready,.dma_rdata,
        .ext_valid,.ext_id,.ext_args,.ext_ready,.ext_ret,.ext_jump_valid,.ext_jump_pc,
        .ext_load_app,.ext_app_id,
        .os_loaded,.foreground_app,
        .cpu_hold,.cpu_force_jump,.cpu_force_pc
    );

    always_comb begin
        dma_ready=dma_valid;
        aligned={dma_addr[31:2],2'b00};
        if(dma_cache)
            dma_rdata={cache[aligned+3],cache[aligned+2],cache[aligned+1],cache[aligned]};
        else if(dma_flash)
            dma_rdata={flash[aligned+3],flash[aligned+2],flash[aligned+1],flash[aligned]};
        else
            dma_rdata={ram[aligned+3],ram[aligned+2],ram[aligned+1],ram[aligned]};
    end

    always_ff @(posedge clk) begin
        if(dma_valid && dma_we) begin
            if(dma_cache) begin
                if(dma_wstrb[0]) cache[aligned+0]<=dma_wdata[7:0];
                if(dma_wstrb[1]) cache[aligned+1]<=dma_wdata[15:8];
                if(dma_wstrb[2]) cache[aligned+2]<=dma_wdata[23:16];
                if(dma_wstrb[3]) cache[aligned+3]<=dma_wdata[31:24];
            end else if(!dma_flash) begin
                if(dma_wstrb[0]) ram[aligned+0]<=dma_wdata[7:0];
                if(dma_wstrb[1]) ram[aligned+1]<=dma_wdata[15:8];
                if(dma_wstrb[2]) ram[aligned+2]<=dma_wdata[23:16];
                if(dma_wstrb[3]) ram[aligned+3]<=dma_wdata[31:24];
            end
        end
    end

    task automatic call_sys(input [11:0] id,input [31:0] a1,input [31:0] a2);
        begin
            $display("CALL START id=%h a1=%h a2=%h t=%0t",id,a1,a2,$time);
            @(negedge clk);
            sys_id<=id;sys_args<=0;
            sys_args[31:0]<=a1;sys_args[63:32]<=a2;sys_valid<=1;
            watchdog=0;
            while(!sys_ready && watchdog<100000) begin
                @(posedge clk);watchdog=watchdog+1;
            end
            if(!sys_ready) begin
                $display("service timeout id=%h",id);$fatal(1);
            end
            last_jump_valid=sys_jump_valid;
            last_jump_pc=sys_jump_pc;
            $display("CALL DONE id=%h ret=%h jump=%b pc=%h t=%0t",id,sys_ret,sys_jump_valid,sys_jump_pc,$time);
            @(negedge clk);sys_valid<=0;
            @(posedge clk);
        end
    endtask

    initial begin
        last_jump_valid=0;last_jump_pc=0;

        // Seed only words this test reads. Avoid clearing hundreds of thousands
        // of simulated bytes at time zero; the service itself clears the app
        // RAM/cache slot under test.
        flash[`SHAMA_KERNEL_FLASH+0]=8'h04;
        flash[`SHAMA_KERNEL_FLASH+1]=8'h00;
        flash[`SHAMA_KERNEL_FLASH+2]=8'h00;
        flash[`SHAMA_KERNEL_FLASH+3]=8'h00;
        flash[`SHAMA_KERNEL_FLASH+`SHAMA_SLOT_DATA_OFFSET+0]=8'h11;
        flash[`SHAMA_KERNEL_FLASH+`SHAMA_SLOT_DATA_OFFSET+1]=8'h00;
        flash[`SHAMA_KERNEL_FLASH+`SHAMA_SLOT_DATA_OFFSET+2]=8'h00;
        flash[`SHAMA_KERNEL_FLASH+`SHAMA_SLOT_DATA_OFFSET+3]=8'h00;

        flash[`SHAMA_DESKTOP_FLASH+0]=8'h04;
        flash[`SHAMA_DESKTOP_FLASH+1]=8'h00;
        flash[`SHAMA_DESKTOP_FLASH+2]=8'h00;
        flash[`SHAMA_DESKTOP_FLASH+3]=8'h00;
        flash[`SHAMA_DESKTOP_FLASH+`SHAMA_SLOT_DATA_OFFSET+0]=8'h22;
        flash[`SHAMA_DESKTOP_FLASH+`SHAMA_SLOT_DATA_OFFSET+1]=8'h00;
        flash[`SHAMA_DESKTOP_FLASH+`SHAMA_SLOT_DATA_OFFSET+2]=8'h00;
        flash[`SHAMA_DESKTOP_FLASH+`SHAMA_SLOT_DATA_OFFSET+3]=8'h00;

        flash[`SHAMA_EDITOR_FLASH+0]=8'h04;
        flash[`SHAMA_EDITOR_FLASH+1]=8'h00;
        flash[`SHAMA_EDITOR_FLASH+2]=8'h00;
        flash[`SHAMA_EDITOR_FLASH+3]=8'h00;
        flash[`SHAMA_EDITOR_FLASH+`SHAMA_SLOT_DATA_OFFSET+0]=8'h33;
        flash[`SHAMA_EDITOR_FLASH+`SHAMA_SLOT_DATA_OFFSET+1]=8'h00;
        flash[`SHAMA_EDITOR_FLASH+`SHAMA_SLOT_DATA_OFFSET+2]=8'h00;
        flash[`SHAMA_EDITOR_FLASH+`SHAMA_SLOT_DATA_OFFSET+3]=8'h00;

        // Staged user executable.
        ram[16'hf000]=8'h44;
        ram[16'hf001]=8'h55;
        ram[16'hf002]=8'h66;
        ram[16'hf003]=8'h77;

        repeat(3) @(posedge clk);rst<=0;

        call_sys(12'h009,0,0); // SYS_BOOT_LOAD_OS
        if(!os_loaded || foreground_app!=0) begin
            $display("boot flags fail");$fatal(1);
        end
        if(!last_jump_valid || last_jump_pc!=`SHAMA_PC_APP) begin
            $display("boot jump fail");$fatal(1);
        end
        if(ram[`SHAMA_KERNEL_RAM_BASE]!==8'h11) begin
            $display("kernel copy fail");$fatal(1);
        end
        if(ram[`SHAMA_APP_RAM_BASE]!==8'h22 || cache[0]!==8'h22) begin
            $display("desktop RAM/cache copy fail");$fatal(1);
        end
        if(cache_used_bytes!==4 || ram_used_bytes!==8) begin
            $display("desktop usage fail ram=%0d cache=%0d",ram_used_bytes,cache_used_bytes);$fatal(1);
        end

        // Foreground heap allocator: 5000 bytes -> two 4 KiB pages.
        call_sys(12'h010,5000,0);
        if(sys_ret[31:0]!==32'h00010000 || ram_used_bytes!==32'd8200) begin
            $display("alloc fail ptr=%h ram=%0d",sys_ret[31:0],ram_used_bytes);$fatal(1);
        end
        call_sys(12'h011,32'h00010000,0);
        if(sys_ret!=0 || ram_used_bytes!==32'd8) begin
            $display("free fail ram=%0d",ram_used_bytes);$fatal(1);
        end

        // Leave one allocation outstanding; app switch must release it.
        call_sys(12'h010,4096,0);
        if(ram_used_bytes!==32'd4104) begin
            $display("pre-switch alloc accounting fail");$fatal(1);
        end

        // Put garbage in the rest of the app slot; switching must clear it.
        ram[`SHAMA_APP_RAM_BASE+100]=8'hfe;
        cache[100]=8'hfe;
        call_sys(12'h006,1,0); // SYS_APP_LAUNCH editor
        if(foreground_app!=1 || ram[`SHAMA_APP_RAM_BASE]!==8'h33 || cache[0]!==8'h33) begin
            $display("editor RAM/cache load fail");$fatal(1);
        end
        if(ram[`SHAMA_APP_RAM_BASE+100]!==0 || cache[100]!==0) begin
            $display("app RAM/cache slot was not flushed");$fatal(1);
        end
        if(cache_used_bytes!==4 || ram_used_bytes!==(32'd24576+8)) begin
            $display("editor usage fail ram=%0d cache=%0d",ram_used_bytes,cache_used_bytes);$fatal(1);
        end

        // Stage a user program outside the app slot and run it.
        ram[`SHAMA_APP_RAM_BASE+200]=8'hfc;
        cache[200]=8'hfc;
        call_sys(12'h061,32'h0000f000,4); // SYS_RUN_BUFFER
        if(foreground_app!=4'hf) begin
            $display("user app marker fail");$fatal(1);
        end
        if(ram[`SHAMA_APP_RAM_BASE+0]!==8'h44 ||
           ram[`SHAMA_APP_RAM_BASE+1]!==8'h55 ||
           ram[`SHAMA_APP_RAM_BASE+2]!==8'h66 ||
           ram[`SHAMA_APP_RAM_BASE+3]!==8'h77 ||
           cache[0]!==8'h44 || cache[1]!==8'h55 ||
           cache[2]!==8'h66 || cache[3]!==8'h77) begin
            $display("staged program RAM/cache copy fail");$fatal(1);
        end
        if(ram[`SHAMA_APP_RAM_BASE+200]!==0 || cache[200]!==0) begin
            $display("user app RAM/cache flush fail");$fatal(1);
        end
        if(cache_used_bytes!==4 || ram_used_bytes!==8) begin
            $display("user app usage fail");$fatal(1);
        end
        if(!last_jump_valid || last_jump_pc!=`SHAMA_PC_APP) begin
            $display("user jump fail");$fatal(1);
        end

        // Hardware-universal Editor button must work with no syscall at all.
        // Seed Editor again and simulate a stuck/halted app that never polls.
        flash[`SHAMA_EDITOR_FLASH+0]=8'h04;
        flash[`SHAMA_EDITOR_FLASH+1]=8'h00;
        flash[`SHAMA_EDITOR_FLASH+2]=8'h00;
        flash[`SHAMA_EDITOR_FLASH+3]=8'h00;
        flash[`SHAMA_EDITOR_FLASH+`SHAMA_SLOT_DATA_OFFSET+0]=8'h33;
        flash[`SHAMA_EDITOR_FLASH+`SHAMA_SLOT_DATA_OFFSET+1]=8'h00;
        flash[`SHAMA_EDITOR_FLASH+`SHAMA_SLOT_DATA_OFFSET+2]=8'h00;
        flash[`SHAMA_EDITOR_FLASH+`SHAMA_SLOT_DATA_OFFSET+3]=8'h00;
        ram[`SHAMA_APP_RAM_BASE+100]=8'hee;
        cache[100]=8'hee;

        $display("ASYNC EDITOR START t=%0t",$time);
        @(negedge clk);
        event_code<=8'h18;
        event_valid<=1;

        watchdog=0;
        while(!event_ack && watchdog<1000) begin @(posedge clk);watchdog=watchdog+1;end
        if(!event_ack || !cpu_hold) begin
            $display("universal Editor did not seize CPU");$fatal(1);
        end
        @(negedge clk);event_valid<=0;

        watchdog=0;
        while(!cpu_force_jump && watchdog<100000) begin @(posedge clk);watchdog=watchdog+1;end
        $display("ASYNC EDITOR FORCE-JUMP t=%0t",$time);
        if(!cpu_force_jump || cpu_force_pc!=`SHAMA_PC_APP) begin
            $display("universal Editor force jump fail");$fatal(1);
        end
        if(foreground_app!=1 || cache[0]!==8'h33 ||
           ram[`SHAMA_APP_RAM_BASE+100]!==0 || cache[100]!==0) begin
            $display("universal Editor reload/flush fail");$fatal(1);
        end

        $display("SHAMA SERVICES APP LIFECYCLE PASS");
        $finish;
    end
endmodule
