module sha256_fixed64 (
    input  logic         clk,
    input  logic         rst,
    input  logic         start,
    input  logic [511:0] message,
    output logic         busy,
    output logic         done,
    output logic [255:0] digest
);
    localparam logic [255:0] IV = {
        32'h6a09e667,32'hbb67ae85,32'h3c6ef372,32'ha54ff53a,
        32'h510e527f,32'h9b05688c,32'h1f83d9ab,32'h5be0cd19
    };

    logic core_start, core_busy, core_done;
    logic [255:0] core_state_in, core_state_out;
    logic [511:0] core_block;
    logic [255:0] first_state;
    logic [2:0] phase;

    sha256_compress u_core(
        .clk(clk), .rst(rst), .start(core_start),
        .state_in(core_state_in), .block_in(core_block),
        .busy(core_busy), .done(core_done), .state_out(core_state_out)
    );

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0; done <= 0; digest <= 0; core_start <= 0;
            core_state_in <= IV; core_block <= 0; first_state <= 0; phase <= 0;
        end else begin
            done <= 0;
            core_start <= 0;
            case (phase)
                0: if (start) begin
                    busy <= 1;
                    core_state_in <= IV;
                    core_block <= message;
                    core_start <= 1;
                    phase <= 1;
                end
                1: if (core_done) begin
                    first_state <= core_state_out;
                    core_state_in <= core_state_out;
                    core_block <= {8'h80, 440'd0, 64'd512};
                    core_start <= 1;
                    phase <= 2;
                end
                2: if (core_done) begin
                    digest <= core_state_out;
                    busy <= 0;
                    done <= 1;
                    phase <= 0;
                end
                default: phase <= 0;
            endcase
        end
    end
endmodule
