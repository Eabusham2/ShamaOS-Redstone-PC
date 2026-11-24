// A bounded FIFO preserves short presses while the CPU is busy.  Simultaneous
// controls are queued in universal-control-first order. Overflow is sticky. One
// overflowing universal control per edge may displace the newest ordinary record.
module shama_input #(
    parameter integer FIFO_DEPTH = 16
)(
    input  logic       clk,
    input  logic       rst,
    input  logic [7:0] kb_rows,
    input  logic [7:0] kb_cols,
    input  logic [9:0] controller,
    output logic       event_valid,
    output logic [7:0] event_code,
    output logic [7:0] key_code,
    output logic [9:0] controller_latched,
    input  logic       event_ack,
    input  logic [25:0] event_ack_data,
    output logic       input_overflow,
    output logic [7:0] queued_count,
    output logic       keyboard_shift
);
    localparam [7:0]
        EVT_KEY=8'h01, EVT_UP=8'h10, EVT_DOWN=8'h11, EVT_LEFT=8'h12,
        EVT_RIGHT=8'h13, EVT_A=8'h14, EVT_B=8'h15, EVT_HOME=8'h16,
        EVT_EXIT=8'h17, EVT_EDITOR=8'h18, EVT_FILES=8'h19;
    localparam integer PTR_BITS = (FIFO_DEPTH <= 2) ? 1 : $clog2(FIFO_DEPTH);
    logic [25:0] fifo [0:FIFO_DEPTH-1];
    logic [PTR_BITS-1:0] selected_index;
    logic [PTR_BITS:0] count;
    logic [7:0] prev_rows,prev_cols;
    logic [9:0] prev_controller;
    logic prev_event_ack;
    logic [7:0] pending_key;
    logic pending_key_valid,key_edge,shift_edge;
    logic [9:0] controller_edges;
    integer row_idx,col_idx,i;

    function automatic [7:0] keymap(input integer r,input integer c,input logic shifted);
        begin
            case(r)
                0: keymap = "A"+c;
                1: keymap = "I"+c;
                2: keymap = "Q"+c;
                3: case(c)
                    0:keymap="Y";1:keymap="Z";2:keymap="0";3:keymap="1";
                    4:keymap="2";5:keymap="3";6:keymap="4";default:keymap="5";
                endcase
                4: case(c)
                    0:keymap="6";1:keymap="7";2:keymap="8";3:keymap="9";
                    4:keymap=8'h20;5:keymap=8'h0d;6:keymap=8'h08;default:keymap=8'h09;
                endcase
                5: case(c)
                    0:keymap=".";1:keymap=",";2:keymap="-";3:keymap="_";
                    4:keymap="/";5:keymap="+";6:keymap="=";default:keymap=":";
                endcase
                6: case(c)
                    0:keymap="[";1:keymap="]";2:keymap="(";3:keymap=")";
                    4:keymap="!";5:keymap="?";6:keymap="*";default:keymap=";";
                endcase
                default: case(c)
                    0:keymap=8'h80; // up
                    1:keymap=8'h81; // down
                    2:keymap=8'h82; // left
                    3:keymap=8'h83; // right
                    4:keymap=8'h1b; // escape
                    5:keymap=8'h7f; // delete
                    6:keymap=" ";
                    default:keymap=8'h00; // Shift toggle is consumed locally.
                endcase
            endcase
            if(shifted) begin
                if(keymap>="A" && keymap<="Z") keymap=keymap+8'd32;
                // The alternate punctuation bank supplies every remaining
                // printable ASCII character without multi-key matrix chords.
                if(r==5) case(c)
                    0:keymap=8'h22;1:keymap=8'h27;2:keymap="<";3:keymap=">";
                    4:keymap=8'h5c;5:keymap="|";6:keymap="@";default:keymap=8'h60;
                endcase
                if(r==6) case(c)
                    0:keymap="{";1:keymap="}";2:keymap="#";3:keymap="$";
                    4:keymap="%";5:keymap="&";6:keymap="^";default:keymap="~";
                endcase
            end
        end
    endfunction


    function automatic integer control_index(input integer priority_index);
        case(priority_index)
            0:control_index=8;
            1:control_index=9;
            2:control_index=7;
            3:control_index=6;
            default:control_index=priority_index-4;
        endcase
    endfunction

    function automatic [3:0] event_population(input [10:0] events);
        logic [1:0] p0,p1,p2,p3,p4;
        logic [2:0] q0,q1;
        begin
            p0={1'b0,events[0]}+{1'b0,events[1]};
            p1={1'b0,events[2]}+{1'b0,events[3]};
            p2={1'b0,events[4]}+{1'b0,events[5]};
            p3={1'b0,events[6]}+{1'b0,events[7]};
            p4={1'b0,events[8]}+{1'b0,events[9]};
            q0={1'b0,p0}+{1'b0,p1};
            q1={1'b0,p2}+{1'b0,p3};
            event_population={1'b0,q0}+{1'b0,q1}+{2'b0,p4}+{3'b0,events[10]};
        end
    endfunction

    always_comb begin
        row_idx=-1;col_idx=-1;
        for(integer k=0;k<8;k=k+1) begin
            if(kb_rows[k]) row_idx=k;
            if(kb_cols[k]) col_idx=k;
        end
        // The keyboard matrix is single-key: reject ambiguous chords instead of
        // inventing the row/column intersection of two keys.
        pending_key_valid=(row_idx>=0 && col_idx>=0 &&
            (kb_rows & (kb_rows-1'b1))==0 && (kb_cols & (kb_cols-1'b1))==0);
        pending_key=pending_key_valid ? keymap(row_idx,col_idx,keyboard_shift) : 8'h00;
        shift_edge=pending_key_valid && row_idx==7 && col_idx==7 &&
            (kb_rows!=prev_rows || kb_cols!=prev_cols);
        key_edge=pending_key_valid && pending_key!=0 &&
            (kb_rows!=prev_rows || kb_cols!=prev_cols);
        controller_edges=controller & ~prev_controller;
        selected_index=0;
        // Universal buttons must remain usable behind unconsumed typing in a
        // halted/nonpolling program. Select the oldest queued universal event;
        // all other events retain their relative FIFO order.
        for(integer k=FIFO_DEPTH-1;k>=0;k=k-1)
            if(k<count && fifo[k][7:0]>=EVT_HOME && fifo[k][7:0]<=EVT_FILES)
                selected_index=k;
        event_valid=count!=0;
        event_code=event_valid ? fifo[selected_index][7:0] : 8'h00;
        key_code=event_valid ? fifo[selected_index][15:8] : 8'h00;
        controller_latched=event_valid ? fifo[selected_index][25:16] : controller;
        queued_count=count;
    end

    // Work in the old FIFO's index space. An ACK removes exactly the first
    // occupied record with the captured payload. If more universal events
    // arrive than will fit, one may displace the newest surviving ordinary
    // record. Arriving universal records never enter the eviction scan.
    wire ack_rise=event_ack && !prev_event_ack;
    wire [FIFO_DEPTH-1:0] ack_matches,ordinary_survivor;
    wire [FIFO_DEPTH:0] ack_prefix,evict_prefix,ordinary_suffix;
    assign ack_prefix[0]=0;
    assign evict_prefix[0]=0;
    assign ordinary_suffix[FIFO_DEPTH]=0;
    wire pop=ack_prefix[FIFO_DEPTH];
    wire [PTR_BITS:0] count_after_ack=count-pop;
    wire [PTR_BITS:0] free_after_ack=count_after_ack<FIFO_DEPTH ? FIFO_DEPTH-count_after_ack : 0;
    wire [3:0] urgent_arrivals=event_population({7'b0,controller_edges[9:6]});
    wire evict_for_urgent=(urgent_arrivals>free_after_ack) && ordinary_suffix[0];
    wire [FIFO_DEPTH-1:0] shift_stored;
    wire [25:0] shifted_record [0:FIFO_DEPTH-1];
    for(genvar slot=0;slot<FIFO_DEPTH;slot=slot+1) begin : fifo_lane
        assign ack_matches[slot]=slot<count && fifo[slot]==event_ack_data;
        assign ack_prefix[slot+1]=ack_rise && (|ack_matches[slot:0]);
        wire consumed_here=ack_rise && ack_matches[slot] && !ack_prefix[slot];
        assign ordinary_survivor[slot]=slot<count && !consumed_here &&
            (fifo[slot][7:0]<EVT_HOME || fifo[slot][7:0]>EVT_FILES);
        assign ordinary_suffix[slot]=|ordinary_survivor[FIFO_DEPTH-1:slot];
        assign evict_prefix[slot+1]=evict_for_urgent && !ordinary_suffix[slot+1];

        // Compact both removals before appending anything. A destination
        // skips two old entries if both removals occur by its next slot.
        // Otherwise a removal at or before this slot advances it by one.
        assign shift_stored[slot]=ack_prefix[slot+1] || evict_prefix[slot+1];
        if(slot+1==FIFO_DEPTH) begin
            assign shifted_record[slot]=0;
        end else begin
            wire skip_two=ack_prefix[slot+2] && evict_prefix[slot+2];
            if(slot+2==FIFO_DEPTH)
                assign shifted_record[slot]=skip_two ? 26'b0 : fifo[slot+1];
            else
                assign shifted_record[slot]=skip_two ? fifo[slot+2] : fifo[slot+1];
        end
    end

    always_ff @(posedge clk) begin : queue_update
        // Queue occupancy and its insertion slot range from zero through
        // FIFO_DEPTH. Keep that arithmetic at the actual queue width; a
        // 32-bit integer here replicates a wide carry chain for every control.
        logic [PTR_BITS:0] available_count;
        logic [PTR_BITS:0] next_write;
        logic [PTR_BITS:0] remaining_count;
        logic [10:0] arrivals;
        logic [3:0] arrival_rank,total_arrivals;
        integer ctl;
        if(rst) begin
            count<=0;input_overflow<=0;keyboard_shift<=0;
            prev_rows<=0;prev_cols<=0;prev_controller<=0;prev_event_ack<=0;
            for(integer k=0;k<FIFO_DEPTH;k=k+1) fifo[k]<=0;
        end else begin
            if(shift_edge) keyboard_shift<=!keyboard_shift;
            available_count=count_after_ack-evict_for_urgent;
            if(evict_for_urgent) input_overflow<=1;
            for(integer k=0;k<FIFO_DEPTH;k=k+1)
                if(shift_stored[k]) fifo[k]<=shifted_record[k];
            remaining_count=available_count<FIFO_DEPTH ? FIFO_DEPTH-available_count : 0;
            for(integer k=0;k<10;k=k+1) arrivals[k]=controller_edges[control_index(k)];
            arrivals[10]=key_edge;
            total_arrivals=event_population(arrivals);
            for(integer k=0;k<10;k=k+1) begin
                ctl=control_index(k);
                arrival_rank=event_population(arrivals & ((11'b1<<k)-1'b1));
                if(controller_edges[ctl]) begin
                    if(arrival_rank<remaining_count) begin
                        next_write=available_count+arrival_rank;
                        // Explicitly bound every field of this 26-bit MMIO payload.
                        fifo[next_write]<={controller,8'h00,4'h1,ctl[3:0]};
                    end else input_overflow<=1;
                end
            end
            if(key_edge) begin
                arrival_rank=event_population(arrivals & 11'b01111111111);
                if(arrival_rank<remaining_count) begin
                    next_write=available_count+arrival_rank;
                    fifo[next_write]<={controller,pending_key,EVT_KEY};
                end else input_overflow<=1;
            end
            count<=available_count+(total_arrivals<remaining_count ? total_arrivals : remaining_count);
            prev_rows<=kb_rows;prev_cols<=kb_cols;prev_controller<=controller;
            prev_event_ack<=event_ack;
        end
    end
endmodule
