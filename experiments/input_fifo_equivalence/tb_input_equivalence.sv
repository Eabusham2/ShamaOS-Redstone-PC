// Compare against the original input controller on the same pins. Checks include
// unoccupied FIFO entries because later urgent eviction uses the pre-edge array.
module tb;
    parameter integer D=16;
    logic clk=0,rst=0,event_ack=0;
    logic [25:0] event_ack_data=0;
    logic [7:0] kb_rows=0,kb_cols=0;
    logic [9:0] controller=0;
    wire [36:0] reference_outputs,candidate_outputs;
    baseline #(.FIFO_DEPTH(D)) golden (.clk,.rst,.event_ack,.event_ack_data,.kb_rows,.kb_cols,.controller,
        .event_valid(reference_outputs[0]),.event_code(reference_outputs[8:1]),.key_code(reference_outputs[16:9]),.controller_latched(reference_outputs[26:17]),
        .input_overflow(reference_outputs[27]),.queued_count(reference_outputs[35:28]),.keyboard_shift(reference_outputs[36]));
    candidate #(.FIFO_DEPTH(D)) candidate_input (.clk,.rst,.event_ack,.event_ack_data,.kb_rows,.kb_cols,.controller,
        .event_valid(candidate_outputs[0]),.event_code(candidate_outputs[8:1]),.key_code(candidate_outputs[16:9]),.controller_latched(candidate_outputs[26:17]),
        .input_overflow(candidate_outputs[27]),.queued_count(candidate_outputs[35:28]),.keyboard_shift(candidate_outputs[36]));
    integer cycles=0,matched=0,full=0,overflow=0,held_ack=0,bad_ack=0,reprioritized=0,shift_edges=0,chords=0,simultaneous=0;
    integer reset_samples=0, unique_resets=0;
    integer random_cycles=12000;
    reg [31:0] rng=32'h731197e1;
    reg [25:0] history[0:31];
    reg [25:0] recent;
    function automatic [31:0] roll();
        reg [31:0] x;
        begin
            x=rng;
            x=x^(x<<13);
            x=x^(x>>17);
            x=x^(x<<5);
            rng=x;
            roll=x;
        end
    endfunction
    task automatic compare;
        begin
            matched++;
            if(reference_outputs!==candidate_outputs || (^reference_outputs)===1'bx ||
                {golden.count,golden.prev_event_ack,golden.prev_rows,golden.prev_cols,golden.prev_controller} !==
                {candidate_input.count,candidate_input.prev_event_ack,candidate_input.prev_rows,candidate_input.prev_cols,candidate_input.prev_controller})
                $fatal(1,"observable/state divergence D=%0d cycle=%0d A=%h B=%h",D,cycles,reference_outputs,candidate_outputs);
            for(integer k=0;k<D;k++) begin
                if(golden.fifo[k]!==candidate_input.fifo[k] || (^golden.fifo[k])===1'bx)
                    $fatal(1,"FIFO divergence D=%0d cycle=%0d slot=%0d A=%h B=%h",D,cycles,k,golden.fifo[k],candidate_input.fifo[k]);
            end
            if(golden.count>D) $fatal(1,"occupancy exceeded capacity");
        end
    endtask
    task automatic tick;
        bit found;
        begin
            #1;
            if(cycles>0) begin
                compare();
                if(!rst) begin
                    if(golden.count==D) full++;
                    if(reference_outputs[27]) overflow++;
                    if(event_ack && golden.prev_event_ack) held_ack++;
                    if(event_ack && !golden.prev_event_ack) begin
                        found=0;
                        for(integer i=0;i<golden.count;i++) if(golden.fifo[i]==event_ack_data) found=1;
                        if(!found) bad_ack++;
                        if(found && event_ack_data!=reference_outputs[26:1]) reprioritized++;
                    end
                    if(golden.shift_edge) shift_edges++;
                    if($countones(kb_rows)>1 || $countones(kb_cols)>1) chords++;
                    if($countones(golden.controller_edges)>1 && golden.key_edge) simultaneous++;
                    if(reference_outputs[0]) history[cycles%32]=reference_outputs[26:1];
                end else if(golden.count!=0) unique_resets++;
            end
            clk=1;
            #1;
            compare();
            clk=0;
            #1;
            cycles++;
            if(rst) begin
                reset_samples++;
                if(golden.count!=0 || reference_outputs[36] || reference_outputs[27]) $fatal(1,"reset failed");
            end
        end
    endtask
    task automatic idle;
        begin kb_rows=0;kb_cols=0;controller=0;event_ack=0;tick();end
    endtask
    task automatic reset;
        begin rst=1;idle();rst=0;idle();end
    endtask
    task automatic key(input [7:0] row, col);
        begin kb_rows=row; kb_cols=col;tick();idle();end
    endtask
    task automatic acknowledge(input [25:0] data);
        begin event_ack_data=data;event_ack=1;tick();idle();end
    endtask
    task automatic expect_head(input [7:0] code, ascii_value);
        if(!reference_outputs[0] || reference_outputs[8:1]!=code || reference_outputs[16:9]!=ascii_value) $fatal(1,"unexpected head D=%0d cycle=%0d",D,cycles);
    endtask
    integer prior_count;
    initial begin
        if(!$value$plusargs("cycles=%d",random_cycles)) random_cycles=12000;
        if(!$value$plusargs("seed=%h",rng)) rng=32'h731197e1;
        for(integer k=0;k<32;k++) history[k]=26'h3ffffff;
        reset();
        // Shift is consumed locally. Ambiguous row/column chords enqueue nothing.
        key(8'h80,8'h80); if(!reference_outputs[36] || reference_outputs[0]) $fatal(1,"Shift did not toggle locally");
        key(3,3);if(reference_outputs[0]) $fatal(1,"matrix chord became a key");
        key(1,1);expect_head(1,"a");acknowledge(reference_outputs[26:1]);
        key(8'h80,8'h80);key(1,1);expect_head(1,"A");
        recent=reference_outputs[26:1];
        // A slow ACK of A must leave the newly prioritized Exit intact.
        if(D>=2) begin
            controller=1<<7;tick();idle();expect_head(8'h17,0);
            acknowledge(recent);expect_head(8'h17,0);
        end
        prior_count=golden.count;acknowledge(26'h3ffffef);
        if(golden.count!=prior_count) $fatal(1,"malformed ACK consumed an event");
        event_ack=1; event_ack_data=reference_outputs[26:1]; tick();
        prior_count=golden.count;repeat(4) tick();if(golden.count!=prior_count) $fatal(1,"held ACK repeated");
        reset();
        // Fill with ordinary keys, then require Exit to displace one entry.
        for(integer k=0;k<D;k++) key(1,1<<(k%8));
        if(golden.count!=D || reference_outputs[27]) $fatal(1,"failed to fill exactly");
        controller=1<<7;tick();idle();expect_head(8'h17,0);
        if(!reference_outputs[27] || golden.count!=D) $fatal(1,"full typing FIFO lost Exit");
        reset();
        // Simultaneously present every controller edge and one matrix key.
        controller=10'h3ff;kb_rows=1;kb_cols=1;tick();idle();
        if(golden.count!=(D<11 ? D:11) || reference_outputs[27]!=(D<11)) $fatal(1,"simultaneous admission count");
        if(D>=11) begin
            for(integer k=0;k<10;k++) begin
                expect_head(8'h10+(k==0?8:k==1?9:k==2?7:k==3?6:k-4),0); acknowledge(reference_outputs[26:1]);
            end
            expect_head(1,"A");acknowledge(reference_outputs[26:1]);
        end
        // The two identical A records surround B in FIFO order.
        if(D>=3) begin
            reset();key(1,1);key(1,2);key(1,1); recent=reference_outputs[26:1]; acknowledge(recent);
            expect_head(1,"B");acknowledge(reference_outputs[26:1]);expect_head(1,"A");
        end
        for(integer step=0;step<random_cycles;step++) begin
            case(roll()%8)
                0: controller=0;
                1: controller=10'h3ff;
                2: controller=controller;
                3: controller=1<<(roll()%10);
                default:controller=roll();
            endcase
            case(roll()%11)
                0:begin kb_rows=0;kb_cols=0;end
                1:begin kb_rows=8'h80;kb_cols=8'h80;end
                2:begin kb_rows=roll();kb_cols=roll();end
                3:begin kb_rows=kb_rows;kb_cols=kb_cols;end
                4:begin kb_rows=3;kb_cols=1;end
                5:begin kb_rows=1;kb_cols=3;end
                default:begin kb_rows=1<<(roll()%8);kb_cols=1<<(roll()%8);end
            endcase
            case(roll()%7)
                0:begin event_ack=1;event_ack_data=reference_outputs[26:1];end
                1:begin event_ack=1;event_ack_data=golden.fifo[roll()%D];end
                2:begin event_ack=1;event_ack_data=history[roll()%32];end
                3:begin event_ack=1;event_ack_data=roll();end
                4:begin event_ack=event_ack;event_ack_data=event_ack_data;end
                default:event_ack=0;
            endcase
            rst=(roll()%113)==0;tick();
        end
        if(!full || !overflow || !held_ack || !bad_ack || (D>1 && !reprioritized) || !shift_edges || !chords || !simultaneous || !unique_resets)
            $fatal(1,"stimulus coverage incomplete");
        $display("MATCH D=%0d cycles=%0d comparisons=%0d full=%0d overflowing=%0d held_ack=%0d bad_ack=%0d ack_other_slot=%0d shift=%0d chords=%0d simultaneous=%0d busy_resets=%0d",D,cycles,matched,full,overflow,held_ack,bad_ack,reprioritized,shift_edges,chords,simultaneous,unique_resets);
        $finish;
    end
endmodule
