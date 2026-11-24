// Coherent raw sample, edge decode, Shift and immutable slow-domain epoch payload.
// All signals are still in the raw clock domain. Storage and crossing are separate.
module input_capture(
    input wire clk, rst,
    input wire [7:0] kb_rows, kb_cols,
    input wire [9:0] controller,
    input wire event_ack, observe,
    input wire [25:0] event_ack_data,
    output reg [9:0] levels, edges,
    output reg [5:0] key_index,
    output reg key_valid, key_shift, ack_rise, observed, sample_valid,
    output reg [25:0] ack_data
);
    reg [7:0] rows_q, cols_q, rows_prev, cols_prev;
    reg [9:0] control_q, control_prev;
    reg observe_q, decoded_observe;
    reg ack_q, ack_prev, q_valid, decoded_valid, toggle_shift, shift_state;
    reg [25:0] ack_qdata, decoded_ack_data;
    reg [9:0] decoded_levels, decoded_edges;
    reg [5:0] index_q;
    reg decoded_key, decoded_ack;

    function automatic onehot(input [7:0] x);
        case (x)
            8'h01,8'h02,8'h04,8'h08,8'h10,8'h20,8'h40,8'h80: onehot=1'b1;
            default: onehot=1'b0;
        endcase
    endfunction
    function automatic [2:0] encode(input [7:0] x);
        encode={|x[7:4], |(x & 8'hcc), |(x & 8'haa)};
    endfunction

    wire changed=(rows_q!=rows_prev || cols_q!=cols_prev);
    wire shift_key=(rows_q==8'h80 && cols_q==8'h80);
    always @(posedge clk) begin
        // A reset invalidates every older in-flight sample.
        if(rst) begin
            q_valid<=0; decoded_valid<=0; sample_valid<=0; shift_state<=0;
            rows_prev<=0;cols_prev<=0;control_prev<=0;ack_prev<=0;
        end else begin
            q_valid<=1;decoded_valid<=q_valid;sample_valid<=decoded_valid;
            if(q_valid) begin
                rows_prev<=rows_q;cols_prev<=cols_q;control_prev<=control_q;ack_prev<=ack_q;
            end
            if(decoded_valid && toggle_shift) shift_state<=!shift_state;
        end

        observe_q<=observe;decoded_observe<=observe_q;observed<=decoded_observe;
        rows_q<=kb_rows;cols_q<=kb_cols;control_q<=controller;ack_q<=event_ack;ack_qdata<=event_ack_data;
        decoded_key<=onehot(rows_q) && onehot(cols_q) && changed && !shift_key;
        toggle_shift<=shift_key && changed;
        index_q<={encode(rows_q),encode(cols_q)};
        decoded_levels<=control_q;decoded_edges<=control_q & ~control_prev;
        decoded_ack<=ack_q && !ack_prev;decoded_ack_data<=ack_qdata;

        levels<=decoded_levels;edges<=decoded_edges;key_index<=index_q;
        key_valid<=decoded_key;key_shift<=shift_state;
        ack_rise<=decoded_ack;ack_data<=decoded_ack_data;
    end
endmodule
