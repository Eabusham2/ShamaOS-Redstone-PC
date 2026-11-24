// Representative independently enabled payload bit from four storage lanes.
// The launch register represents captured input data. Each lane must retain
// the old value when deselected and receive the previous epoch when selected.
module four_lane(input wire clk, incoming, input wire [3:0] selected,
                 output reg captured,output reg [3:0] q);
    always @(posedge clk) captured <= incoming;
    for(genvar i=0;i<4;i=i+1) begin
        always @(posedge clk) if(selected[i]) q[i] <= captured;
    end
endmodule
