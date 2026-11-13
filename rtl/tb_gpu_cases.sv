module tb_gpu_cases #(
    parameter integer WIDTH=37,
    parameter integer HEIGHT=23,
    parameter integer QUEUE_DEPTH=4,
    parameter bit USE_UPRIGHT_BRIDGE=0
);
    localparam integer PIXELS=WIDTH*HEIGHT;
    localparam integer FRAME_WORDS=(PIXELS+31)/32;
    localparam integer EXPECT_STRIDE=6+PIXELS;
    reg clk=0,rst=1;
    reg mmio_valid=0,mmio_we=0;
    reg [11:0] mmio_addr=0;
    reg [31:0] mmio_wdata=0;
    reg [3:0] mmio_wstrb=15;
    wire mmio_ready;
    wire [31:0] mmio_rdata;
    wire vram_valid,vram_we;
    wire [14:0] vram_addr;
    wire [31:0] vram_wdata;
    wire [3:0] vram_wstrb;
    wire disp_valid,disp_bit,busy;
    wire capacity_fault;
    reg clear_capacity_fault=0;
    wire [15:0] disp_index;
    integer cycle=0;
    wire vram_ready=vram_valid && (cycle%5!=1) && (cycle%5!=2);
    wire disp_ready;
    wire bridge_ready;
    wire [WIDTH-1:0] bridge_data;
    wire [$clog2(HEIGHT)-1:0] bridge_row;
    wire bridge_commit;
    wire row_ready=(cycle%19>=6);
    integer bridge_col;
    integer capture_rows=0;
    generate if(USE_UPRIGHT_BRIDGE) begin: upright
        shama_display_bridge #(.WIDTH(WIDTH),.HEIGHT(HEIGHT)) bridge(
            .clk,.rst,.pixel_valid(disp_valid),.pixel_index(disp_index[$clog2(PIXELS)-1:0]),
            .pixel_bit(disp_bit),.pixel_ready(bridge_ready),.row_data(bridge_data),
            .row_index(bridge_row),.row_commit(bridge_commit),.row_ready
        );
        assign disp_ready=bridge_ready;
    end else begin
        assign disp_ready=(cycle%7!=2) && (cycle%7!=3);
        assign bridge_ready=1'b1;
        assign bridge_commit=1'b0;
        assign bridge_data='0;
        assign bridge_row='0;
    end endgenerate
    reg [7:0] mem[0:16383];
    reg screen[0:PIXELS-1];
    wire [31:0] vram_rdata={mem[{vram_addr[14:2],2'b00}+3],mem[{vram_addr[14:2],2'b00}+2],
                            mem[{vram_addr[14:2],2'b00}+1],mem[{vram_addr[14:2],2'b00}]};
    reg [31:0] commands[0:65535];
    reg [31:0] expected[0:262143];
    reg [2047:0] command_path,expected_path;
    integer command_count,expected_count,need_queue_stall=0,need_direct_stall=0;
    integer n,k,p,check_index=0,offset,front_addr,back_addr,watchdog;
    integer queue_stalls=0,direct_stalls=0,display_updates=0;
    integer vram_transactions=0,marked_transactions=0,transaction_limit;
    reg vram_stalled=0,display_stalled=0;
    reg [14:0] held_vram_addr;
    reg held_vram_we;
    reg [31:0] held_vram_data;
    reg [3:0] held_vram_strobes;
    reg [15:0] held_disp_index;
    reg held_disp_bit;

    always #5 clk=~clk;
    shama_gpu #(.WIDTH(WIDTH),.HEIGHT(HEIGHT),.QUEUE_DEPTH(QUEUE_DEPTH)) dut(.*);

    always @(posedge clk) begin
        cycle<=cycle+1;
        if(!rst) begin
            if(vram_stalled && (!vram_valid || vram_addr!==held_vram_addr || vram_we!==held_vram_we ||
                               (vram_we && (vram_wdata!==held_vram_data || vram_wstrb!==held_vram_strobes))))
                $fatal(1,"VRAM request changed while stalled old=%h/%b/%h/%h new=%h/%b/%h/%h valid=%b state=%d q=%d mmio=%h/%b",
                    held_vram_addr,held_vram_we,held_vram_data,held_vram_strobes,vram_addr,vram_we,vram_wdata,vram_wstrb,
                    vram_valid,dut.state,dut.q_count,mmio_addr,mmio_valid);
            if(display_stalled && (!disp_valid || disp_index!==held_disp_index || disp_bit!==held_disp_bit))
                $fatal(1,"display request changed while stalled");
            if(vram_valid) begin
                if(vram_addr>=FRAME_WORDS*8+1280 || vram_addr>=16384)
                    $fatal(1,"GPU escaped its VRAM resource span at %h",vram_addr);
                if(vram_ready) vram_transactions<=vram_transactions+1;
                if(vram_ready && vram_we)
                    for(k=0;k<4;k=k+1) if(vram_wstrb[k]) mem[vram_addr+k]<=vram_wdata[k*8 +: 8];
            end
            if(disp_valid && disp_ready) begin
                if(disp_index>=PIXELS) $fatal(1,"out-of-bounds display index %d",disp_index);
                if(!USE_UPRIGHT_BRIDGE) screen[disp_index]<=disp_bit;
                display_updates<=display_updates+1;
            end
            if(USE_UPRIGHT_BRIDGE && bridge_commit) begin
                for(bridge_col=0;bridge_col<WIDTH;bridge_col=bridge_col+1)
                    screen[bridge_row*WIDTH+bridge_col]<=bridge_data[bridge_col] ^ ((HEIGHT-1-bridge_row)%2==0);
                if(capture_rows) $display("BRIDGE_ROW %0d %h",bridge_row,bridge_data);
            end
            if(mmio_valid && !mmio_ready) begin
                if(mmio_addr==12'h028) queue_stalls<=queue_stalls+1;
                if(mmio_addr>=12'h400) direct_stalls<=direct_stalls+1;
            end
        end
        vram_stalled<=!rst && vram_valid && !vram_ready;
        held_vram_addr<=vram_addr;held_vram_we<=vram_we;
        held_vram_data<=vram_wdata;held_vram_strobes<=vram_wstrb;
        display_stalled<=!rst && disp_valid && !disp_ready;
        held_disp_index<=disp_index;held_disp_bit<=disp_bit;
    end

    task write_mmio(input [11:0] addr,input [31:0] data,input [3:0] strobes);
        integer guard;
        begin
            @(negedge clk);mmio_addr=addr;mmio_wdata=data;mmio_wstrb=strobes;mmio_we=1;mmio_valid=1;
            guard=0;
            @(posedge clk);
            while(!mmio_ready && guard<1000000) begin @(posedge clk);guard=guard+1;end
            if(!mmio_ready) $fatal(1,"MMIO write deadlocked at %h, state=%d q=%d",addr,dut.state,dut.q_count);
            @(negedge clk);mmio_valid=0;mmio_we=0;
        end
    endtask

    task read_mmio(input [11:0] addr,input [31:0] value);
        integer guard;
        begin
            @(negedge clk);mmio_addr=addr;mmio_we=0;mmio_valid=1;guard=0;
            @(posedge clk);
            while(!mmio_ready && guard<1000000) begin @(posedge clk);guard=guard+1;end
            if(!mmio_ready || mmio_rdata!==value)
                $fatal(1,"MMIO read %h expected %h got %h ready=%b",addr,value,mmio_rdata,mmio_ready);
            @(negedge clk);mmio_valid=0;
        end
    endtask

    task checkpoint;
        begin
            watchdog=0;
            while(busy && watchdog<2000000) begin @(negedge clk);watchdog=watchdog+1;end
            if(busy) $fatal(1,"GPU command failed to terminate at checkpoint %d state=%d",check_index,dut.state);
            while(!bridge_ready || bridge_commit) @(negedge clk);
            @(negedge clk);
            offset=check_index*EXPECT_STRIDE;
            if(dut.front_sel!==expected[offset][0] || dut.readback!==expected[offset+1] ||
               dut.fence_count!==expected[offset+2] || dut.queue_error!==expected[offset+3][0] ||
               dut.presented_rows!==expected[offset+4] || display_updates!==expected[offset+5]) begin
                $display("checkpoint=%d front=%d/%d read=%h/%h fence=%d/%d error=%d/%d rows=%d/%d updates=%d/%d",check_index,
                    dut.front_sel,expected[offset],dut.readback,expected[offset+1],dut.fence_count,expected[offset+2],
                    dut.queue_error,expected[offset+3],dut.presented_rows,expected[offset+4],display_updates,expected[offset+5]);
                $fatal(1,"GPU state differs from reference");
            end
            front_addr=dut.front_sel?FRAME_WORDS*4:0;back_addr=dut.front_sel?0:FRAME_WORDS*4;
            for(p=0;p<PIXELS;p=p+1) begin
                if(mem[front_addr+p/8][p%8]!==expected[offset+6+p][0] ||
                   mem[back_addr+p/8][p%8]!==expected[offset+6+p][1] || screen[p]!==expected[offset+6+p][2]) begin
                    $display("checkpoint=%d pixel=(%d,%d) actual(front/back/display)=%b%b%b expected=%b",check_index,p%WIDTH,p/WIDTH,
                        mem[front_addr+p/8][p%8],mem[back_addr+p/8][p%8],screen[p],expected[offset+6+p][2:0]);
                    $fatal(1,"GPU pixels differ from reference");
                end
            end
            check_index=check_index+1;
        end
    endtask

    initial begin
        if(!$value$plusargs("COMMANDS=%s",command_path) || !$value$plusargs("EXPECTED=%s",expected_path) ||
           !$value$plusargs("COUNT=%d",command_count) || !$value$plusargs("EXPECT_COUNT=%d",expected_count))
            $fatal(1,"missing GPU fixture arguments");
        if($value$plusargs("QUEUE_STALL=%d",need_queue_stall)) begin end
        if($value$plusargs("DIRECT_STALL=%d",need_direct_stall)) begin end
        if($value$plusargs("CAPTURE_ROWS=%d",capture_rows)) begin end
        $readmemh(command_path,commands,0,command_count*10-1);
        $readmemh(expected_path,expected,0,expected_count*EXPECT_STRIDE-1);
        for(n=0;n<16384;n=n+1) mem[n]=0;
        for(n=0;n<PIXELS;n=n+1) screen[n]=0;
        repeat(3) @(negedge clk);rst=0;
        for(n=0;n<command_count;n=n+1) begin
            case(commands[n*10])
                0:begin
                    for(p=0;p<8;p=p+1) write_mmio(8+4*p,commands[n*10+2+p],15);
                    write_mmio(4,commands[n*10+1],15);write_mmio(40,1,15);
                end
                1:write_mmio(commands[n*10+1],commands[n*10+2],commands[n*10+3]);
                2:checkpoint();
                3:read_mmio(commands[n*10+1],commands[n*10+2]);
                4:begin
                    if(capacity_fault!==commands[n*10+1][0]) $fatal(1,"capacity-fault status mismatch");
                    if(commands[n*10+2][0]) begin
                        @(negedge clk);clear_capacity_fault=1;
                        @(negedge clk);clear_capacity_fault=0;
                        if(capacity_fault) $fatal(1,"capacity fault did not acknowledge");
                    end
                end
                5:begin
                    watchdog=0;
                    while(busy && watchdog<2000000) begin @(negedge clk);watchdog=watchdog+1;end
                    if(busy) $fatal(1,"GPU failed to drain before transaction marker");
                    @(negedge clk);marked_transactions=vram_transactions;
                end
                6:begin
                    watchdog=0;
                    while(busy && watchdog<2000000) begin @(negedge clk);watchdog=watchdog+1;end
                    if(busy) $fatal(1,"GPU failed to drain for transaction count");
                    @(negedge clk);transaction_limit=commands[n*10+1];
                    if(vram_transactions-marked_transactions>transaction_limit)
                        $fatal(1,"VRAM traffic %d exceeds bound %d",vram_transactions-marked_transactions,transaction_limit);
                    $display("GPU VRAM TRANSACTIONS actual=%0d maximum=%0d",vram_transactions-marked_transactions,transaction_limit);
                end
                7:begin
                    if(busy) $fatal(1,"fixture attempted to seed an active GPU");
                    @(negedge clk);
                    back_addr=(dut.front_sel?0:FRAME_WORDS*4)+commands[n*10+1]*4;
                    for(p=0;p<4;p=p+1) mem[back_addr+p]=commands[n*10+2]>>(p*8);
                end
                default:$fatal(1,"unknown GPU fixture operation");
            endcase
        end
        if(check_index!=expected_count) $fatal(1,"missing checkpoints");
        if(need_queue_stall && queue_stalls==0) $fatal(1,"queue backpressure was not exercised");
        if(need_direct_stall && direct_stalls==0) $fatal(1,"direct VRAM wait was not exercised");
        $display("GPU DIFFERENTIAL PASS checkpoints=%d queue_stalls=%d direct_stalls=%d",check_index,queue_stalls,direct_stalls);
        $finish;
    end
endmodule
