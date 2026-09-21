module tb_fs_accel;
    logic clk=0,rst=1;
    logic req_valid=0,req_ready;
    logic [11:0] req_id=0;
    logic [191:0] req_args=0;
    logic [63:0] req_ret;

    logic dma_valid,dma_we,dma_flash,dma_ready;
    logic [31:0] dma_addr,dma_wdata,dma_rdata;
    logic [3:0] dma_wstrb;
    logic [31:0] flash_used_bytes;
    logic mounted;

    logic [7:0] ram [0:4095];
    logic [7:0] flash [0:65535];

    integer i;
    integer watchdog;
    logic [31:0] aligned;

    always #5 clk=~clk;

    shama_fs_accel dut(
        .clk,.rst,
        .req_valid,.req_id,.req_args,.req_ready,.req_ret,
        .dma_valid,.dma_we,.dma_flash,.dma_addr,.dma_wdata,.dma_wstrb,
        .dma_ready,.dma_rdata,.flash_used_bytes,.mounted
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
        if(dma_valid && dma_we) begin
            if(dma_flash) begin
                if(dma_wstrb[0]) flash[aligned+0]<=dma_wdata[7:0];
                if(dma_wstrb[1]) flash[aligned+1]<=dma_wdata[15:8];
                if(dma_wstrb[2]) flash[aligned+2]<=dma_wdata[23:16];
                if(dma_wstrb[3]) flash[aligned+3]<=dma_wdata[31:24];
            end else begin
                if(dma_wstrb[0]) ram[aligned+0]<=dma_wdata[7:0];
                if(dma_wstrb[1]) ram[aligned+1]<=dma_wdata[15:8];
                if(dma_wstrb[2]) ram[aligned+2]<=dma_wdata[23:16];
                if(dma_wstrb[3]) ram[aligned+3]<=dma_wdata[31:24];
            end
        end
    end

    task automatic call0(input [11:0] id);
        begin
            @(negedge clk);req_id<=id;req_args<=0;req_valid<=1;
            watchdog=0;
            while(!req_ready && watchdog<50000) begin @(posedge clk);watchdog=watchdog+1;end
            if(!req_ready) begin $display("FS timeout id=%h",id);$fatal(1);end
            @(negedge clk);req_valid<=0;
            @(posedge clk);
        end
    endtask

    task automatic call3(
        input [11:0] id,input [31:0] a1,input [31:0] a2,input [31:0] a3
    );
        begin
            @(negedge clk);
            req_id<=id;req_args<=0;
            req_args[31:0]<=a1;req_args[63:32]<=a2;req_args[95:64]<=a3;
            req_valid<=1;
            watchdog=0;
            while(!req_ready && watchdog<100000) begin @(posedge clk);watchdog=watchdog+1;end
            if(!req_ready) begin $display("FS timeout id=%h",id);$fatal(1);end
            @(negedge clk);req_valid<=0;
            @(posedge clk);
        end
    endtask

    task automatic flash_put32(input integer addr,input logic [31:0] value);
        begin
            flash[addr+0]=value[7:0];
            flash[addr+1]=value[15:8];
            flash[addr+2]=value[23:16];
            flash[addr+3]=value[31:24];
        end
    endtask

    initial begin
        for(i=0;i<4096;i=i+1) ram[i]=0;
        for(i=0;i<65536;i=i+1) flash[i]=0;

        // 4 MiB ShamaFS reserved blocks 0..24 are marked used.
        flash[256]=8'hff;
        flash[257]=8'hff;
        flash[258]=8'hff;
        flash[259]=8'h01;

        // Fixed miner-history entry 13: 16 KiB at block 30.
        flash_put32(2304 + 13*64 + 0, 32'h00000501); // used, LOG type 5
        flash_put32(2304 + 13*64 + 4, 32'd16384);
        flash_put32(2304 + 13*64 + 8, 32'd30);
        flash_put32(2304 + 13*64 + 12, 32'd64);

        // Fixed miner-state entry 14: 256 bytes at block 100.
        flash_put32(2304 + 14*64 + 0, 32'h00000601); // used, CFG type 6
        flash_put32(2304 + 14*64 + 4, 32'd256);
        flash_put32(2304 + 14*64 + 8, 32'd100);
        flash_put32(2304 + 14*64 + 12, 32'd1);

        ram[16'h0100]="x";ram[16'h0101]=".";ram[16'h0102]="t";
        ram[16'h0103]="x";ram[16'h0104]="t";ram[16'h0105]=0;
        ram[16'h0120]="y";ram[16'h0121]=".";ram[16'h0122]="t";
        ram[16'h0123]="x";ram[16'h0124]="t";ram[16'h0125]=0;
        ram[16'h0200]="h";ram[16'h0201]="i";

        repeat(3) @(posedge clk);rst<=0;

        call0(12'h008);
        if(!mounted) begin $display("mount flag");$fatal(1);end

        // Create x.txt, type TXT=1.
        call3(12'h020,32'h100,1,0);
        if(req_ret[31:0]!=1) begin $display("create handle %h",req_ret);$fatal(1);end

        // Replace contents with "hi".
        call3(12'h024,1,32'h200,2);
        if(req_ret[63:32]==32'hffffffff) begin $display("write err");$fatal(1);end

        // Read it back into RAM.
        call3(12'h023,1,32'h300,2);
        if(ram[16'h300]!="h" || ram[16'h301]!="i") begin
            $display("readback %h %h",ram[16'h300],ram[16'h301]);$fatal(1);
        end

        // Rename to y.txt.
        call3(12'h026,1,32'h120,0);

        // List entry zero into RAM 0x400.
        call3(12'h029,0,32'h400,0);
        if(ram[16'h400]!="y" || ram[16'h401]!=".") begin
            $display("list name fail");$fatal(1);
        end

        // Delete handle 1 and verify list no longer reports it.
        call3(12'h027,1,0,0);
        call3(12'h029,0,32'h400,0);
        if(req_ret!=0) begin $display("delete/list fail %h",req_ret);$fatal(1);end

        // Dedicated miner state write is in-place.
        ram[16'h0500]=8'h53;ram[16'h0501]=8'h54;ram[16'h0502]=8'h41;ram[16'h0503]=8'h54;
        call3(12'h051,32'h500,4,0);
        if(flash[100*256+0]!==8'h53 || flash[100*256+3]!==8'h54) begin
            $display("miner state persistence fail");$fatal(1);
        end

        // History slot 3 is a fixed 64-byte record.
        ram[16'h0520]=8'h48;ram[16'h0521]=8'h49;ram[16'h0522]=8'h53;ram[16'h0523]=8'h54;
        call3(12'h050,32'h520,4,3);
        if(flash[30*256+3*64+0]!==8'h48 || flash[30*256+3*64+3]!==8'h54) begin
            $display("miner history log fail");$fatal(1);
        end

        call3(12'h052,3,32'h600,4);
        if(ram[16'h0600]!==8'h48 || ram[16'h0601]!==8'h49 ||
           ram[16'h0602]!==8'h53 || ram[16'h0603]!==8'h54) begin
            $display("miner history load fail");$fatal(1);
        end

        $display("SHAMAFS ACCEL PASS");
        $finish;
    end
endmodule
