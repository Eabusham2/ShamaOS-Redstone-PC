`include "shama_boot_params.svh"
// Connect the production raw FIFO to the service's slower clock and queue
// complete gestures while the overlay is drawing. Only external pins are driven.
module input_modal_fifo_tb;
    reg raw=0, core=0, reset=1, notice_ready=0, sys_valid=0;
    reg [11:0] sys_id=0;
    reg [191:0] sys_args=0;
    reg [7:0] rows=0, cols=0;
    wire [7:0] key_code,event_code,count,notice;
    wire [9:0] levels;
    wire [25:0] payload;
    wire event_valid,ack,overflow,shift,sys_ready,ext_valid,os_loaded;
    wire [3:0] app;
    wire [31:0] addr;
    reg rejected_save=0;
    integer ack_count=0;
    reg [7:0] expected[0:15];
    reg prior_ack=0;

    always #2 raw=~raw;
    always #13 core=~core;
    shama_input fifo(.clk(raw),.rst(reset),.kb_rows(rows),.kb_cols(cols),.controller(10'd0),
        .event_valid,.event_code,.key_code,.controller_latched(levels),.event_ack(ack),
        .event_ack_data(payload),.input_overflow(overflow),.queued_count(count),.keyboard_shift(shift));
    wire [31:0] read_word = addr==`SHAMA_EDIT_CTX ? 32'd7 :
        (addr==`SHAMA_EDIT_CTX+20 ? 32'd1 : 32'd4);
    shama_services #(.APP_SLOT_BYTES(16)) services(.clk(core),.rst(reset),.sys_valid,.sys_id,.sys_args,
        .sys_ready,.event_valid,.event_code,.key_code,.controller_latched(levels),.event_ack(ack),
        .event_ack_data(payload),.input_overflow(overflow),.input_queued_count(count),.keyboard_shift(shift),
        .cpu_fault_code(8'd0),.gpu_capacity_fault(1'b0),.flash_write_protected(1'b0),
        .raw_flash_write_blocked(1'b0),.notice_code(notice),.notice_busy(1'b0),.notice_ready,
        .shutdown_request(1'b0),.fs_cleanup_ready(1'b1),.flash_used_bytes(32'd0),.time_counter(64'd0),
        .dma_ready(1'b1),.dma_addr(addr),.dma_rdata(read_word),.ext_valid,.ext_ready(ext_valid),
        .ext_ret(rejected_save ? -64'sd3 : -64'sd8),.ext_jump_valid(1'b0),.ext_jump_pc(32'd0),
        .ext_load_app(1'b0),.ext_app_id(4'd0),.os_loaded,.foreground_app(app));

    always @(posedge raw) begin
        if(!reset && ack && !prior_ack) begin
            if(ack_count>=10 || payload!=={10'd0,expected[ack_count],8'h01})
                $fatal(1,"unexpected ACK %d payload=%h",ack_count,payload);
            ack_count=ack_count+1;
        end
        prior_ack=ack;
    end

    task call_service(input [11:0] id,input [31:0] arg);
        @(negedge core);sys_valid=1;sys_id=id;sys_args=arg;
        wait(sys_ready);
        @(negedge core);sys_valid=0;
        @(negedge core);
    endtask
    task key(input integer row,column);
        @(negedge raw);rows=1<<row;cols=1<<column;
        @(negedge raw);rows=0;cols=0;
    endtask
    task queue_keys;
        key(0,0);key(0,0);key(0,1); // Identical A, A, then B.
    endtask
    initial begin
        expected[0]="A";expected[1]="A";expected[2]="B";expected[3]=8'h0d;
        expected[4]="A";expected[5]="A";expected[6]="B";expected[7]=8'h83;
        expected[8]=8'h82;expected[9]=8'h0d;
        repeat(3) @(negedge core);reset=0;
        call_service(12'h020,0); // Filesystem protection rejection raises warning 1.
        if(notice!==1) $fatal(1,"missing storage warning");
        queue_keys();key(4,5);
        @(negedge raw);if(count!==4) $fatal(1,"warning queue not populated");
        @(negedge core);notice_ready=1;
        repeat(15) @(negedge core);
        if(notice!==0 || count!==0 || ack_count!=4)
            $fatal(1,"warning drain stuck notice=%d count=%d acks=%d",notice,count,ack_count);

        call_service(12'h009,0);call_service(12'h006,1);
        if(!os_loaded || app!==1) $fatal(1,"Editor did not launch");
        rejected_save=1;notice_ready=0;
        fork
            call_service(12'h006,2);
            begin
                wait(notice==6);queue_keys();key(7,3);key(7,2);key(4,5);key(0,2);
                @(negedge raw);if(count!==7) $fatal(1,"close decision queue not populated");
                @(negedge core);notice_ready=1;
                wait(notice==7);wait(notice==6);wait(notice==0);
            end
        join
        repeat(5) @(negedge core);
        if(app!==1 || count!==1 || key_code!=="C" || ack_count!=10 || overflow)
            $fatal(1,"failed cancel or consumed the later key");
        $display("QUEUED MODAL FIFO PASS 10 exact ACKs, 2 repeated-key dialogs, 1 retained key");
        $finish;
    end
    initial begin #100000;$fatal(1,"stalled service/FIFO at notice %d queue %d ACKs %d",notice,count,ack_count);end
endmodule
