module shama_display_bridge #(
    parameter integer WIDTH = 320,
    parameter integer HEIGHT = 180,
    parameter bit UPRIGHT_PANEL = 1'b1
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
    // The upright column torch tower alternates polarity with physical Y.
    // Encode one serial bit before buffering, rather than adding an XOR to
    // every parallel column output. Pixel address remains the authority when
    // the GPU skips clean rows; logical row zero is the top of the panel.
    wire row_inverted = UPRIGHT_PANEL && (((HEIGHT-1)-(pixel_index/WIDTH)) % 2 == 0);
    wire encoded_pixel = pixel_bit ^ row_inverted;

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
                // The GPU may skip clean rows. Use the actual pixel address
                // instead of assuming every presentation starts at row zero.
                row_buffer[pixel_index % WIDTH] <= encoded_pixel;
                active_row <= pixel_index / WIDTH;
                if (pixel_index % WIDTH == WIDTH-1) begin
                    row_data <= {
                        encoded_pixel,
                        row_buffer[WIDTH-2:0]
                    };
                    row_index <= pixel_index / WIDTH;
                    waiting_commit <= 1'b1;
                end else begin
                    column <= column + 1'b1;
                end
            end
        end
    end
endmodule
