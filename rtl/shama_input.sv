module shama_input(
    input  logic       clk,
    input  logic       rst,
    input  logic [7:0] kb_rows,
    input  logic [7:0] kb_cols,
    input  logic [9:0] controller,
    output logic       event_valid,
    output logic [7:0] event_code,
    output logic [7:0] key_code,
    output logic [9:0] controller_latched,
    input  logic       event_ack
);
    localparam [7:0]
        EVT_KEY=8'h01, EVT_UP=8'h10, EVT_DOWN=8'h11, EVT_LEFT=8'h12,
        EVT_RIGHT=8'h13, EVT_A=8'h14, EVT_B=8'h15, EVT_HOME=8'h16,
        EVT_EXIT=8'h17, EVT_EDITOR=8'h18, EVT_FILES=8'h19;

    logic [7:0] prev_rows,prev_cols;
    logic [9:0] prev_controller;
    logic [7:0] pending_key;
    logic pending_key_valid;
    logic [9:0] controller_edges;
    integer row_idx,col_idx,i;

    function automatic [7:0] keymap(input integer r,input integer c);
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
                    default:keymap=8'h00;
                endcase
            endcase
        end
    endfunction

    always_comb begin
        row_idx=-1;col_idx=-1;
        for(i=0;i<8;i=i+1) begin
            if(kb_rows[i]) row_idx=i;
            if(kb_cols[i]) col_idx=i;
        end
        pending_key_valid=(row_idx>=0 && col_idx>=0);
        pending_key=pending_key_valid ? keymap(row_idx,col_idx) : 8'h00;
        controller_edges=controller & ~prev_controller;
    end

    always_ff @(posedge clk) begin
        if(rst) begin
            event_valid<=0;event_code<=0;key_code<=0;controller_latched<=0;
            prev_rows<=0;prev_cols<=0;prev_controller<=0;
        end else begin
            if(event_valid && event_ack)
                event_valid<=0;

            controller_latched<=controller_latched|controller;

            if(!event_valid) begin
                if(controller_edges[8]) begin event_valid<=1;event_code<=EVT_EDITOR;end
                else if(controller_edges[9]) begin event_valid<=1;event_code<=EVT_FILES;end
                else if(controller_edges[7]) begin event_valid<=1;event_code<=EVT_EXIT;end
                else if(controller_edges[6]) begin event_valid<=1;event_code<=EVT_HOME;end
                else if(controller_edges[0]) begin event_valid<=1;event_code<=EVT_UP;end
                else if(controller_edges[1]) begin event_valid<=1;event_code<=EVT_DOWN;end
                else if(controller_edges[2]) begin event_valid<=1;event_code<=EVT_LEFT;end
                else if(controller_edges[3]) begin event_valid<=1;event_code<=EVT_RIGHT;end
                else if(controller_edges[4]) begin event_valid<=1;event_code<=EVT_A;end
                else if(controller_edges[5]) begin event_valid<=1;event_code<=EVT_B;end
                else if(pending_key_valid && !(|prev_rows && |prev_cols)) begin
                    event_valid<=1;event_code<=EVT_KEY;key_code<=pending_key;
                end
            end

            prev_rows<=kb_rows;prev_cols<=kb_cols;prev_controller<=controller;
            if(event_ack) controller_latched<=controller;
        end
    end
endmodule
