module tb_input;
    logic clk=0,rst=1;
    logic [7:0] kb_rows=0,kb_cols=0;
    logic [9:0] controller=0,controller_latched;
    logic event_valid,event_ack=0,input_overflow,keyboard_shift;
    logic [7:0] event_code,key_code,queued_count;
    integer i,r,c,shift_mode;
    logic [127:0] seen_characters;
    always #5 clk=~clk;
    shama_input #(.FIFO_DEPTH(4)) dut(.*);

    task automatic keypress(input [7:0] rows,cols);
        begin
            @(negedge clk);kb_rows=rows;kb_cols=cols;
            @(negedge clk);kb_rows=0;kb_cols=0;
        end
    endtask
    task automatic expect_event(input [7:0] code,key);
        begin
            @(negedge clk);
            if(!event_valid || event_code!==code || key_code!==key) begin
                $display("expected event %h/%h got %h/%h valid=%b count=%d",code,key,event_code,key_code,event_valid,queued_count);
                $fatal(1);
            end
            event_ack=1;
            @(negedge clk);event_ack=0;
        end
    endtask
    initial begin
        repeat(3) @(negedge clk);rst=0;
        // Busy CPU: two short presses survive until acknowledged.
        keypress(8'h01,8'h01); // A
        keypress(8'h01,8'h02); // B
        if(queued_count!=2) $fatal(1,"short presses were lost");
        // Universal controls can exit a nonpolling program even with typing at
        // the head of the FIFO, and do not discard those older typed events.
        @(negedge clk);controller[7]=1;
        @(negedge clk);controller=0;
        expect_event(8'h17,0);
        expect_event(8'h01,"A");
        expect_event(8'h01,"B");
        if(event_valid) $fatal(1,"queue did not drain");

        // One divided-clock acknowledgement remains high across raw clocks.
        // It consumes one record, even when a further key arrives while high.
        keypress(1,1);keypress(1,2);keypress(1,4);
        @(negedge clk);event_ack=1;
        repeat(7) @(negedge clk);
        if(queued_count!=2 || key_code!="B") $fatal(1,"held slow acknowledgement dropped queued keys");
        keypress(1,8);
        repeat(3) @(negedge clk);
        if(queued_count!=3 || key_code!="B") $fatal(1,"held acknowledgement consumed a new key");
        event_ack=0;
        expect_event(1,"B");expect_event(1,"C");expect_event(1,"D");

        // Simultaneous controller and keyboard edges are all retained.
        @(negedge clk);controller=10'b0000110000;kb_rows=1;kb_cols=4;
        @(negedge clk);controller=0;kb_rows=0;kb_cols=0;
        expect_event(8'h14,0);
        expect_event(8'h15,0);
        expect_event(8'h01,"C");

        // Holding a key does not repeat without a new press.
        @(negedge clk);kb_rows=1;kb_cols=8;
        repeat(5) @(negedge clk);
        if(queued_count!=1) $fatal(1,"held key repeated");
        kb_rows=0;kb_cols=0;
        expect_event(8'h01,"D");

        // Overflow preserves the four oldest entries and signals loss.
        for(i=0;i<5;i=i+1) keypress(1,1<<i);
        if(queued_count!=4 || !input_overflow) $fatal(1,"overflow not reported");
        expect_event(1,"A");expect_event(1,"B");expect_event(1,"C");expect_event(1,"D");
        @(negedge clk);rst=1;
        @(negedge clk);rst=0;
        if(input_overflow || event_valid) $fatal(1,"reset did not clear queue");

        // Acknowledge and new input on the same clock consumes one and adds one.
        keypress(1,1);
        @(negedge clk);event_ack=1;kb_rows=1;kb_cols=2;
        @(negedge clk);event_ack=0;kb_rows=0;kb_cols=0;
        expect_event(1,"B");
        // Even a saturated typing queue cannot disable universal Exit.
        for(i=0;i<4;i=i+1) keypress(1,1<<i);
        @(negedge clk);controller[7]=1;
        @(negedge clk);controller=0;
        expect_event(8'h17,0);
        expect_event(1,"A");expect_event(1,"B");expect_event(1,"C");
        if(!input_overflow) $fatal(1,"urgent eviction must report overflow");
        // Ambiguous matrix chords do not manufacture an unintended key.
        keypress(8'h03,8'h03);
        if(event_valid) $fatal(1,"ambiguous matrix chord invented a key");

        // Shift is a toggle key, so the single-key physical matrix can reach
        // lowercase and all punctuation without ambiguous simultaneous chords.
        @(negedge clk);kb_rows=8'h80;kb_cols=8'h80;
        repeat(5) @(negedge clk);
        if(!keyboard_shift || event_valid) $fatal(1,"Shift repeated or queued a text event");
        kb_rows=0;kb_cols=0;
        keypress(1,1);expect_event(1,"a");
        keypress(8'h20,1);expect_event(1,8'h22);
        keypress(8'h20,8'h10);expect_event(1,8'h5c);
        keypress(8'h80,8'h80);
        if(keyboard_shift) $fatal(1,"Shift did not toggle off");
        keypress(1,1);expect_event(1,"A");

        // Exercise the actual row/column decoder and FIFO for both banks.
        // Every printable ASCII byte must be reachable by a physical key.
        seen_characters=0;
        for(shift_mode=0;shift_mode<2;shift_mode=shift_mode+1) begin
            if(shift_mode==1) keypress(8'h80,8'h80);
            for(r=0;r<8;r=r+1) for(c=0;c<8;c=c+1) begin
                if(!(r==7 && c==7)) begin
                    keypress(1<<r,1<<c);
                    if(!event_valid || event_code!=1) $fatal(1,"physical key produced no text event");
                    if(key_code<128) seen_characters[key_code]=1;
                    expect_event(1,key_code);
                end
            end
        end
        for(i=32;i<=126;i=i+1)
            if(!seen_characters[i]) $fatal(1,"ASCII byte %0d unreachable from physical keyboard",i);
        @(negedge clk);rst=1;
        @(negedge clk);rst=0;
        if(keyboard_shift || event_valid || input_overflow) $fatal(1,"reset did not clear modifier/FIFO state");
        $display("INPUT FIFO UNIVERSAL PRIORITY PRINTABLE ASCII SHIFT PASS");$finish;
    end
endmodule
