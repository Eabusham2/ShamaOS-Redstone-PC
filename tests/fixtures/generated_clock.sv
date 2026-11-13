module generated_clock (
    input wire raw_clk, data,
    output reg divided,
    output reg [1:0] q
);
    always @(posedge raw_clk) divided <= ~divided;
    always @(posedge divided) q <= {q[0], data};
endmodule
