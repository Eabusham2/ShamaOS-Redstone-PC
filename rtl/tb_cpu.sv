`include "shama_boot_params.svh"

// Behavioral CPU regression bus: real instruction fetches, byte strobes,
// deterministic wait states, and a debug hold during SHA completion.
module tb_cpu;
    logic clk=0,rst=1;
    always #5 clk=~clk;
    logic mem_valid,mem_we,mem_ready;
    logic [31:0] mem_addr,mem_wdata,mem_rdata;
    logic [3:0] mem_wstrb;
    logic sys_valid;
    logic [11:0] sys_id;
    logic [191:0] sys_args;
    logic halted,sha_busy,cache_flush;
    logic [7:0] fault_code;
    logic [31:0] debug_pc,debug_instruction;
    logic [4:0] debug_flags;
    logic [5:0] debug_sha_round;
    logic external_hold=0;
    logic [31:0] memory [0:`SHAMA_RAM_BYTES/4-1];
    integer cycles=0,stall=0,hold_sha=0,hold_count=0;
    logic did_hold=0;
    shama_cpu dut(
        .clk,.rst,.mem_valid,.mem_we,.mem_addr,.mem_wdata,.mem_wstrb,.mem_ready,.mem_rdata,
        .sys_valid,.sys_id,.sys_args,.sys_ready(1'b1),.sys_ret(64'h8765432112345678),
        .sys_jump_valid(1'b0),.sys_jump_pc(32'd0),.cache_flush,.time_counter(64'd12345678),
        .external_hold,.external_jump_valid(1'b0),.external_jump_pc(32'd0),
        .halted,.sha_busy,.fault_code,.debug_pc,.debug_instruction,.debug_flags,.debug_sha_round
    );
    assign mem_ready=mem_valid && !external_hold && (stall==0 || cycles%(stall+1)==0);
    assign mem_rdata=mem_addr<`SHAMA_RAM_BYTES ? memory[mem_addr>>2] : 32'd0;
    always @(posedge clk) begin
        cycles<=cycles+1;
        if(mem_ready && mem_we) begin
            for(integer lane=0;lane<4;lane=lane+1)
                if(mem_wstrb[lane]) memory[mem_addr>>2][lane*8+:8]<=mem_wdata[lane*8+:8];
        end
        if(!rst && hold_sha!=0 && sha_busy && !did_hold) begin
            external_hold<=1;did_hold<=1;hold_count<=500;
        end else if(hold_count>0) begin
            hold_count<=hold_count-1;
            if(hold_count==1) external_hold<=0;
        end
        if(cycles>3000000) $fatal(1,"CPU watchdog pc=%h state=%d",debug_pc,dut.state);
    end
    string program_file;
    integer dump_base=98304,dump_words=0,ignored;
    initial begin
        ignored=$value$plusargs("program=%s",program_file);
        ignored=$value$plusargs("stall=%d",stall);
        ignored=$value$plusargs("hold_sha=%d",hold_sha);
        ignored=$value$plusargs("dump_base=%d",dump_base);
        ignored=$value$plusargs("dump_words=%d",dump_words);
        for(integer i=0;i<`SHAMA_RAM_BYTES/4;i=i+1) memory[i]=0;
        $readmemh(program_file,memory);
        repeat(3) @(negedge clk);
        rst=0;
        wait(halted);
        @(negedge clk);
        for(integer i=0;i<16;i=i+1) $display("REG %0d %08h",i,i==0?32'd0:dut.regs[i]);
        $display("FLAGS %02h",debug_flags);
        $display("FAULT %02h",fault_code);
        $display("COUNTERS %0d %0d",dut.cycle_counter,dut.retired_counter);
        for(integer i=0;i<dump_words;i=i+1) $display("MEM %0d %08h",dump_base+4*i,memory[(dump_base>>2)+i]);
        $finish;
    end
endmodule
