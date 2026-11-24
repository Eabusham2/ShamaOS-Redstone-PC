module counter_miter #(parameter integer WIDTH = 64, parameter integer SLICE = 8) (
    input wire clk,
    input wire rst
);
    wire [WIDTH-1:0] reference_value, predicted_value;
    reference_counter #(.WIDTH(WIDTH)) reference_instance(clk, rst, reference_value);
    predicted_counter #(.WIDTH(WIDTH), .SLICE(SLICE)) predicted_instance(clk, rst, predicted_value);
    always @* assert(reference_value == predicted_value);
endmodule
