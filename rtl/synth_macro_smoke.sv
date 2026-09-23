module synth_macro_child(
    input  logic clk,
    input  logic a,
    input  logic b,
    output logic q
);
    always_ff @(posedge clk)
        q <= a ^ b;
endmodule

module synth_macro_shell(
    input  logic clk,
    input  logic a,
    input  logic b,
    output logic q,
    output logic y
);
    synth_macro_child u_child(
        .clk(clk),
        .a(a),
        .b(b),
        .q(q)
    );
    assign y = ~q;
endmodule
