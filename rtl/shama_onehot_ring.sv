module shama_onehot_ring #(
    parameter integer N = 256
)(
    input  logic clk,
    input  logic rst,
    input  logic step,
    output logic [N-1:0] select
);
    always_ff @(posedge clk) begin
        if (rst)
            select <= {{(N-1){1'b0}},1'b1};
        else if (step)
            select <= {select[N-2:0],select[N-1]};
    end
endmodule
