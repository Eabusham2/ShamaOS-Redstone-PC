module synth_smoke(
    input  logic clk,
    input  logic rst,
    input  logic a,
    input  logic b,
    output logic q,
    output logic y
);
    always_ff @(posedge clk) begin
        if (rst)
            q <= 1'b0;
        else
            q <= a ^ b;
    end
    assign y = ~(q & a);
endmodule
