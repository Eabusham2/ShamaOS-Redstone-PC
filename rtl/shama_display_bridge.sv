module shama_display_bridge #(
    parameter integer WIDTH = 192,
    parameter integer HEIGHT = 108
)(
    input  logic                  clk,
    input  logic                  rst,
    input  logic                  pixel_valid,
    input  logic [$clog2(WIDTH*HEIGHT)-1:0] pixel_index,
    input  logic                  pixel_bit,
    output logic                  pixel_ready,

    output logic [WIDTH-1:0]      row_data,
    output logic [$clog2(HEIGHT)-1:0] row_index,
    output logic                  row_commit,
    input  logic                  row_ready
);
    logic [WIDTH-1:0] row_buffer;
    logic [$clog2(WIDTH)-1:0] column;
    logic [$clog2(HEIGHT)-1:0] active_row;
    logic waiting_commit;

    assign pixel_ready = !waiting_commit;

    always_ff @(posedge clk) begin
        if (rst) begin
            row_buffer <= '0;
            row_data <= '0;
            row_index <= '0;
            row_commit <= 1'b0;
            column <= '0;
            active_row <= '0;
            waiting_commit <= 1'b0;
        end else begin
            row_commit <= 1'b0;

            if (waiting_commit) begin
                if (row_ready) begin
                    row_commit <= 1'b1;
                    waiting_commit <= 1'b0;
                    row_buffer <= '0;
                    column <= '0;
                    if (active_row == HEIGHT-1)
                        active_row <= '0;
                    else
                        active_row <= active_row + 1'b1;
                end
            end else if (pixel_valid) begin
                // pixel_index is retained as a consistency check/debug source;
                // streaming order is row-major from the GPU.
                row_buffer[column] <= pixel_bit;
                if (column == WIDTH-1) begin
                    row_data <= {
                        pixel_bit,
                        row_buffer[WIDTH-2:0]
                    };
                    row_index <= active_row;
                    waiting_commit <= 1'b1;
                end else begin
                    column <= column + 1'b1;
                end
            end
        end
    end
endmodule
