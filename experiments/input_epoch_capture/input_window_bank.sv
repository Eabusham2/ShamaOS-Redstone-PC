// One bank of compact raw-domain epoch storage. The core/worker reads only a
// sealed immutable bank; bank selection and cross-domain handshakes are external.
module input_window_bank(
    input wire clk, rst, active, begin_window, key_valid, key_shift,
    input wire [5:0] key_index,
    input wire [9:0] levels, edges,
    input wire [3:0] read_index,
    output wire [27:0] prefix_word,
    output wire [11:0] urgent_word,
    output reg [16:0] next_prefix, next_urgent,
    output reg lost
);
    reg [27:0] prefix [0:15];
    reg [11:0] urgent [0:15];
    wire [16:0] prefix_pointer=begin_window ? 17'b1 : next_prefix;
    wire [16:0] urgent_pointer=begin_window ? 17'b1 : next_urgent;
    wire eventful=(|edges) || key_valid;
    wire prefix_push=active && eventful && !prefix_pointer[16];
    wire after_prefix=active && eventful && prefix_pointer[16];
    wire urgent_push=after_prefix && (|edges[9:6]) && !urgent_pointer[16];
    wire [1:0] winner={edges[8] || edges[9], !edges[8] && (edges[9] || edges[7])};

    always @(posedge clk) begin
        if(rst) begin
            next_prefix<=1;next_urgent<=1;lost<=0;
        end else if(active) begin
            next_prefix<=prefix_push ? prefix_pointer<<1 : prefix_pointer;
            next_urgent<=urgent_push ? urgent_pointer<<1 : urgent_pointer;
            lost<=after_prefix || (!begin_window && lost);
        end
    end
    for(genvar i=0;i<16;i=i+1) begin
        always @(posedge clk) begin
            if(prefix_push && prefix_pointer[i])
                prefix[i]<={levels,edges,key_valid,key_index,key_shift};
            if(urgent_push && urgent_pointer[i])
                urgent[i]<={levels,winner};
        end
    end
    assign prefix_word=prefix[read_index];
    assign urgent_word=urgent[read_index];
endmodule
