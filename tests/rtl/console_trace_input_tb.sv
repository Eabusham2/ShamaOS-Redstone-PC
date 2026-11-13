module console_trace_input_tb #(
    parameter integer RAW_PERIOD = 70,
    parameter integer PHASES = RAW_PERIOD
);
    logic [PHASES-1:0] clocks = 0;
    logic rst = 1;
    logic [7:0] kb_rows = 0, kb_cols = 0;
    logic [9:0] controller = 0;
    logic [PHASES-1:0] event_ack = 0;
    logic [25:0] event_ack_data [0:PHASES-1];
    wire [PHASES-1:0] event_valid, input_overflow, keyboard_shift;
    wire [7:0] event_code [0:PHASES-1];
    wire [7:0] key_code [0:PHASES-1];
    wire [7:0] queued_count [0:PHASES-1];
    wire [9:0] controller_latched [0:PHASES-1];
    logic [25:0] samples [0:77*1500-1];
    logic [7:0] expected_keys [0:127];
    string trace_path, key_path;
    integer stride, elapsed=0, bank, key, control_id, p;

    for(genvar phase=0; phase<PHASES; phase=phase+1) begin : all_phases
        shama_input dut(
            .clk(clocks[phase]), .rst, .kb_rows, .kb_cols, .controller,
            .event_valid(event_valid[phase]), .event_code(event_code[phase]),
            .key_code(key_code[phase]), .controller_latched(controller_latched[phase]),
            .event_ack(event_ack[phase]), .event_ack_data(event_ack_data[phase]),
            .input_overflow(input_overflow[phase]), .queued_count(queued_count[phase]),
            .keyboard_shift(keyboard_shift[phase])
        );
    end

    task automatic tick(input logic [25:0] sample);
        begin
            {controller,kb_cols,kb_rows}=sample;
            #1;
            // One DUT represents every integer phase of the same raw period.
            // Two reset periods establish all initial high/low intervals.
            clocks[(RAW_PERIOD-elapsed%RAW_PERIOD)%RAW_PERIOD]=1;
            clocks[(RAW_PERIOD+RAW_PERIOD/2-elapsed%RAW_PERIOD)%RAW_PERIOD]=0;
            #1;
            elapsed=elapsed+1;
        end
    endtask

    task automatic idle(input integer ticks);
        for(integer t=0;t<ticks;t=t+1) tick(0);
    endtask

    task automatic play(input integer actuator);
        for(integer t=0;t<stride;t=t+1) tick(samples[actuator*stride+t]);
    endtask

    task automatic check_count(input integer wanted);
        for(integer phase=0;phase<PHASES;phase=phase+1)
            if(queued_count[phase]!==wanted || event_valid[phase] !== (wanted!=0))
                $fatal(1,"CONSOLE_CAPTURE_COUNT phase=%0d key=%0d bank=%0d wanted=%0d got=%0d at=%0d",
                    phase,key,bank,wanted,queued_count[phase],elapsed);
    endtask

    task automatic check_head(input integer count,input logic [7:0] code,value,input logic [9:0] controls);
        begin
            check_count(count);
            for(integer phase=0;phase<PHASES;phase=phase+1)
                if(event_code[phase]!==code || key_code[phase]!==value || controller_latched[phase]!==controls)
                    $fatal(1,"CONSOLE_DECODE phase=%0d key=%0d bank=%0d wanted=%h/%h/%h got=%h/%h/%h",
                        phase,key,bank,code,value,controls,event_code[phase],key_code[phase],controller_latched[phase]);
        end
    endtask

    task automatic check_shift(input logic wanted);
        for(integer phase=0;phase<PHASES;phase=phase+1)
            if(keyboard_shift[phase]!==wanted)
                $fatal(1,"CONSOLE_SHIFT phase=%0d wanted=%b got=%b",phase,wanted,keyboard_shift[phase]);
    endtask

    task automatic check_overflow(input logic wanted);
        for(integer phase=0;phase<PHASES;phase=phase+1)
            if(input_overflow[phase]!==wanted)
                $fatal(1,"CONSOLE_OVERFLOW phase=%0d wanted=%b got=%b",phase,wanted,input_overflow[phase]);
    endtask

    task automatic snapshot;
        for(integer phase=0;phase<PHASES;phase=phase+1)
            event_ack_data[phase]={controller_latched[phase],key_code[phase],event_code[phase]};
    endtask

    task automatic consume(input integer remaining);
        begin
            snapshot(); event_ack='1;
            idle(3*RAW_PERIOD);
            check_count(remaining);
            event_ack=0; idle(RAW_PERIOD);
        end
    endtask

    task automatic reset_input;
        begin
            rst=1; event_ack=0;
            for(integer phase=0;phase<PHASES;phase=phase+1) event_ack_data[phase]=0;
            idle(2*RAW_PERIOD); rst=0; idle(RAW_PERIOD);
            check_count(0);check_shift(0);check_overflow(0);
        end
    endtask

    initial begin
        if(RAW_PERIOD%2 || PHASES!=RAW_PERIOD) $fatal(1,"invalid exhaustive raw-clock phase fixture");
        if(!$value$plusargs("TRACE=%s",trace_path) || !$value$plusargs("KEYS=%s",key_path) ||
           !$value$plusargs("STRIDE=%d",stride)) $fatal(1,"missing measured trace arguments");
        if(stride<1 || stride>1500) $fatal(1,"invalid trace stride");
        $readmemh(trace_path,samples,0,77*stride-1);
        $readmemh(key_path,expected_keys,0,127);
        reset_input();

        // Two full physical keyboard banks; each actual Shift pulse toggles
        // once and emits no key event. Hold each ordinary event until its full
        // measured pulse and release have completed, detecting duplicates.
        for(bank=0;bank<2;bank=bank+1) begin
            for(key=0;key<64;key=key+1) begin
                play(key);
                if(key==63) begin
                    check_count(0);check_shift(bank==0);
                end else begin
                    check_head(1,8'h01,expected_keys[bank*64+key],0);
                    consume(0);check_shift(bank!=0);
                end
                check_overflow(0);
            end
        end
        // The reachable dedicated Shift actuator duplicates matrix [7][7].
        play(76);check_shift(1);check_count(0);
        play(0);check_head(1,1,"a",0);consume(0);
        play(76);check_shift(0);check_count(0);
        play(0);check_head(1,1,"A",0);consume(0);
        for(control_id=0;control_id<10;control_id=control_id+1) begin
            play(64+control_id);
            check_head(1,8'h10+8'(control_id),0,10'(1<<control_id));consume(0);
        end
        $display("CONSOLE ALL KEYS BOTH BANKS CONTROLS PHASES PASS phases=%0d",PHASES);

        // A slow acknowledgement spans several raw edges and another real
        // key pulse. It must remove one record, preserving later arrivals.
        reset_input();play(0);play(1);play(2);check_head(3,1,"A",0);
        snapshot();event_ack='1;idle(3*RAW_PERIOD);check_head(2,1,"B",0);
        play(3);idle(3*RAW_PERIOD);check_head(3,1,"B",0);
        event_ack=0;idle(RAW_PERIOD);
        consume(2);check_head(2,1,"C",0);consume(1);check_head(1,1,"D",0);consume(0);

        // Snapshot A, then admit real Home before that acknowledgement reaches
        // the raw clock. Remove the oldest matching A, retaining new Home and
        // preserving the relative order of B and the second A.
        reset_input();play(0);play(1);play(0);check_head(3,1,"A",0);snapshot();
        play(70);check_head(4,8'h16,0,10'h040);
        event_ack='1;idle(3*RAW_PERIOD);check_head(3,8'h16,0,10'h040);
        event_ack=0;idle(RAW_PERIOD);consume(2);check_head(2,1,"B",0);
        consume(1);check_head(1,1,"A",0);consume(0);

        // Use the real default16-entry FIFO. Overflow drops the new ordinary
        // event, while Exit can still replace the newest ordinary entry.
        reset_input();
        for(key=0;key<16;key=key+1) play(key);
        check_head(16,1,"A",0);check_overflow(0);
        play(16);check_head(16,1,"A",0);check_overflow(1);
        play(71);check_head(16,8'h17,0,10'h080);consume(15);
        for(key=0;key<15;key=key+1) begin
            check_head(15-key,1,expected_keys[key],0);consume(14-key);
        end
        check_overflow(1);reset_input();
        $display("CONSOLE TRACE INPUT HELD ACK PRIORITY FIFO SHIFT PASS phases=%0d",PHASES);
        $finish;
    end
endmodule
