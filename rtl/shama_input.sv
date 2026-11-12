// A bounded FIFO preserves short presses while the CPU is busy.  Simultaneous
// controls are queued in the documented universal-control-first order. Overflow
// drops the newest events only and remains observable until reset.
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

    always_ff @(posedge clk) begin : queue_update
        integer available_count;
        integer next_write;
        integer ctl;
        integer evict;
        integer ack_index;
        logic overflow_urgent_added;
        if(rst) begin
            count<=0;input_overflow<=0;keyboard_shift<=0;
            prev_rows<=0;prev_cols<=0;prev_controller<=0;prev_event_ack<=0;
            for(integer k=0;k<FIFO_DEPTH;k=k+1) fifo[k]<=0;
        end else begin
            if(shift_edge) keyboard_shift<=!keyboard_shift;
            available_count=count;
            overflow_urgent_added=0;
            ack_index=-1;
            // The consumer snapshots the presented record with its slow-clock
            // acknowledgement. A new universal event can change selection
            // before the next raw edge; remove the consumed record, not that
            // newly selected event. Identical records match oldest first.
            for(integer k=FIFO_DEPTH-1;k>=0;k=k-1)
                if(k<count && fifo[k]==event_ack_data) ack_index=k;
            // The consumer runs on the divided core clock. A single slow
            // acknowledgement can span many raw clocks and must pop once.
            if(event_ack && !prev_event_ack && ack_index>=0) begin
                for(integer k=0;k<FIFO_DEPTH-1;k=k+1)
                    if(k>=ack_index) fifo[k]<=fifo[k+1];
                fifo[FIFO_DEPTH-1]<=0;
                available_count=available_count-1;
            end
            next_write=available_count;
            for(integer k=0;k<10;k=k+1) begin
                ctl=control_index(k);
                if(controller_edges[ctl]) begin
                    if(available_count<FIFO_DEPTH) begin
                        fifo[next_write]<={controller,8'h00,(8'h10+8'(ctl))};
                        next_write=next_write+1;
                        available_count=available_count+1;
                    end else begin
                        input_overflow<=1;
                        // A saturated typing queue must not disable Exit/Home.
                        // Evict the newest ordinary event, leaving earlier
                        // events and all already-queued universal controls.
                        if(ctl>=6 && !overflow_urgent_added) begin
                            evict=-1;
                            for(integer j=0;j<FIFO_DEPTH;j=j+1)
                                if(fifo[j][7:0]<EVT_HOME || fifo[j][7:0]>EVT_FILES) evict=j;
                            if(evict>=0) begin
                                fifo[evict]<={controller,8'h00,(8'h10+8'(ctl))};
                                overflow_urgent_added=1;
                            end
                        end
                    end
                end
            end
            if(key_edge) begin
                if(available_count<FIFO_DEPTH) begin
                    fifo[next_write]<={controller,pending_key,EVT_KEY};
                    next_write=next_write+1;
                    available_count=available_count+1;
                end else input_overflow<=1;
            end
            count<=available_count;
            prev_rows<=kb_rows;prev_cols<=kb_cols;prev_controller<=controller;
            prev_event_ack<=event_ack;
        end
    end
endmodule
