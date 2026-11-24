// Isolated candidate: exact increment with registered carry lookahead.
// The carry used for this edge was predicted from the previous low slice.
module predicted_counter #(
    parameter integer WIDTH = 64,
    parameter integer SLICE = 8
) (
    input wire clk,
    input wire rst,
    output reg [WIDTH-1:0] value = '0
);
    localparam integer LANES = (WIDTH + SLICE - 1) / SLICE;
    reg [LANES-1:0] carry = '0;
    for (genvar lane = 0; lane < LANES; lane = lane + 1) begin: slices
        localparam integer BASE = lane * SLICE;
        localparam integer BITS = (WIDTH - BASE < SLICE) ? WIDTH - BASE : SLICE;
        if (lane == 0) begin: lowest
            always @(posedge clk) begin
                if (rst) value[BASE+:BITS] <= '0;
                else value[BASE+:BITS] <= value[BASE+:BITS] + 1'b1;
            end
        end else begin: upper
            always @(posedge clk) begin
                if (rst) begin
                    carry[lane] <= 1'b0;
                    value[BASE+:BITS] <= '0;
                end else begin
                    carry[lane] <= (value[BASE-1:0] == {{(BASE-1){1'b1}}, 1'b0});
                    value[BASE+:BITS] <= value[BASE+:BITS] + carry[lane];
                end
            end
        end
    end
endmodule
