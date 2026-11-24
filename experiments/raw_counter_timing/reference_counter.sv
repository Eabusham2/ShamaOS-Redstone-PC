module reference_counter #(parameter integer WIDTH = 64) (
    input wire clk,
    input wire rst,
    output reg [WIDTH-1:0] value = '0
);
    always @(posedge clk) begin
        if (rst) value <= '0;
        else value <= value + 1'b1;
    end
endmodule
