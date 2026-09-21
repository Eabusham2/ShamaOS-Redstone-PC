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

    logic dma_valid,dma_we,dma_flash,dma_ready;
    logic [31:0] dma_addr,dma_wdata,dma_rdata;
    logic [3:0] dma_wstrb;

    logic ext_valid,ext_ready=0,ext_jump_valid=0;
    logic [11:0] ext_id;
    logic [191:0] ext_args;
    logic [63:0] ext_ret=0;
    logic [31:0] ext_jump_pc=0;

    logic os_loaded;
    logic [3:0] foreground_app;

    logic [7:0] ram [0:131071];
    logic [7:0] flash [0:262143];

    integer i;
    integer watchdog;
    logic [31:0] aligned;

    always #5 clk=~clk;

    shama_services dut(
        .clk,.rst,
        .sys_valid,.sys_id,.sys_args,.sys_ready,.sys_ret,.sys_jump_valid,.sys_jump_pc,
        .event_valid,.event_code,.key_code,.controller_latched,.event_ack,
        .ram_used_bytes(32'd0),.cache_used_bytes(32'd0),.flash_used_bytes(32'd0),
        .time_counter(64'd0),
        .dma_valid,.dma_we,.dma_flash,.dma_addr,.dma_wdata,.dma_wstrb,
        .dma_ready,.dma_rdata,
        .ext_valid,.ext_id,.ext_args,.ext_ready,.ext_ret,.ext_jump_valid,.ext_jump_pc,
        .os_loaded,.foreground_app
    );

    always_comb begin
        dma_ready=dma_valid;
        aligned={dma_addr[31:2],2'b00};
        if(dma_flash)
            dma_rdata={flash[aligned+3],flash[aligned+2],flash[aligned+1],flash[aligned]};
        else
            dma_rdata={ram[aligned+3],ram[aligned+2],ram[aligned+1],ram[aligned]};
    end

    always_ff @(posedge clk) begin
        if(dma_valid && dma_we && !dma_flash) begin
            if(dma_wstrb[0]) ram[aligned+0]<=dma_wdata[7:0];
            if(dma_wstrb[1]) ram[aligned+1]<=dma_wdata[15:8];
            if(dma_wstrb[2]) ram[aligned+2]<=dma_wdata[23:16];
            if(dma_wstrb[3]) ram[aligned+3]<=dma_wdata[31:24];
        end
    end

    task automatic call_sys(input [11:0] id,input [31:0] a1,input [31:0] a2);
        begin
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
            @(negedge clk);sys_valid<=0;
            @(posedge clk);
        end
    endtask

    initial begin
        for(i=0;i<131072;i=i+1) ram[i]=8'haa;
        for(i=0;i<262144;i=i+1) flash[i]=0;

        // Distinct first words in each flash slot.
        flash[`SHAMA_KERNEL_FLASH+0]=8'h11;
        flash[`SHAMA_DESKTOP_FLASH+0]=8'h22;
        flash[`SHAMA_EDITOR_FLASH+0]=8'h33;

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
        if(!sys_jump_valid || sys_jump_pc!=`SHAMA_PC_APP) begin
            $display("boot jump fail");$fatal(1);
        end
        if(ram[`SHAMA_KERNEL_RAM_BASE]!==8'h11) begin
            $display("kernel copy fail");$fatal(1);
        end
        if(ram[`SHAMA_APP_RAM_BASE]!==8'h22) begin
            $display("desktop copy fail");$fatal(1);
        end

        // Put garbage in the rest of the app slot; switching must clear it.
        ram[`SHAMA_APP_RAM_BASE+100]=8'hfe;
        call_sys(12'h006,1,0); // SYS_APP_LAUNCH editor
        if(foreground_app!=1 || ram[`SHAMA_APP_RAM_BASE]!==8'h33) begin
            $display("editor load fail");$fatal(1);
        end
        if(ram[`SHAMA_APP_RAM_BASE+100]!==0) begin
            $display("app slot was not flushed");$fatal(1);
        end

        // Stage a user program outside the app slot and run it.
        ram[`SHAMA_APP_RAM_BASE+200]=8'hfc;
        call_sys(12'h061,32'h0000f000,4); // SYS_RUN_BUFFER
        if(foreground_app!=4'hf) begin
            $display("user app marker fail");$fatal(1);
        end
        if(ram[`SHAMA_APP_RAM_BASE+0]!==8'h44 ||
           ram[`SHAMA_APP_RAM_BASE+1]!==8'h55 ||
           ram[`SHAMA_APP_RAM_BASE+2]!==8'h66 ||
           ram[`SHAMA_APP_RAM_BASE+3]!==8'h77) begin
            $display("staged program copy fail");$fatal(1);
        end
        if(ram[`SHAMA_APP_RAM_BASE+200]!==0) begin
            $display("user app flush fail");$fatal(1);
        end
        if(!sys_jump_valid || sys_jump_pc!=`SHAMA_PC_APP) begin
            $display("user jump fail");$fatal(1);
        end

        $display("SHAMA SERVICES APP LIFECYCLE PASS");
        $finish;
    end
endmodule
