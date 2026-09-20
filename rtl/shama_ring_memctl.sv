module shama_ring_memctl #(
    parameter integer BANK_BITS = 8,
    parameter integer ROW_BITS = 10
)(
    input  logic        clk,
    input  logic        rst,

    input  logic        req_valid,
    input  logic        req_we,
    input  logic [31:0] req_addr,
    input  logic [31:0] req_wdata,
    input  logic [3:0]  req_wstrb,
    output logic        req_ready,
    output logic [31:0] req_rdata,

    output logic        bank_step,
    output logic        row_step,
    output logic        write_commit,
    output logic [31:0] write_data,
    input  logic [31:0] selected_word,

    output logic [BANK_BITS-1:0] current_bank,
    output logic [ROW_BITS-1:0]  current_row
);
    typedef enum logic [2:0] {
        IDLE, SEEK_BANK, SEEK_ROW, READ_ACCESS, MERGE_WRITE, COMMIT_WRITE, RESP
    } state_t;
    state_t state;

    logic latched_we;
    logic [31:0] latched_wdata;
    logic [3:0] latched_wstrb;
    logic [BANK_BITS-1:0] target_bank;
    logic [ROW_BITS-1:0] target_row;
    logic [31:0] merged;

    localparam integer BANK_LSB = ROW_BITS + 2;

    function automatic logic [31:0] merge_bytes(
        input logic [31:0] oldv,
        input logic [31:0] newv,
        input logic [3:0] strb
    );
        integer i;
        begin
            merge_bytes = oldv;
            for(i=0;i<4;i=i+1)
                if(strb[i])
                    merge_bytes[i*8 +: 8] = newv[i*8 +: 8];
        end
    endfunction

    always_comb begin
        req_ready = (state == RESP);
        req_rdata = selected_word;
        bank_step = (state == SEEK_BANK) && (current_bank != target_bank);
        row_step = (state == SEEK_ROW) && (current_row != target_row);
        write_commit = (state == COMMIT_WRITE);
        write_data = merged;
    end

    always_ff @(posedge clk) begin
        if(rst) begin
            state <= IDLE;
            current_bank <= '0;
            current_row <= '0;
            latched_we <= 0;
            latched_wdata <= 0;
            latched_wstrb <= 0;
            target_bank <= 0;
            target_row <= 0;
            merged <= 0;
        end else begin
            case(state)
                IDLE: if(req_valid) begin
                    latched_we <= req_we;
                    latched_wdata <= req_wdata;
                    latched_wstrb <= req_wstrb;
                    target_bank <= req_addr[BANK_LSB + BANK_BITS - 1 : BANK_LSB];
                    target_row <= req_addr[ROW_BITS + 1 : 2];
                    state <= SEEK_BANK;
                end

                SEEK_BANK: begin
                    if(current_bank == target_bank)
                        state <= SEEK_ROW;
                    else
                        current_bank <= current_bank + 1'b1;
                end

                SEEK_ROW: begin
                    if(current_row == target_row) begin
                        if(!latched_we)
                            state <= READ_ACCESS;
                        else
                            state <= MERGE_WRITE;
                    end else
                        current_row <= current_row + 1'b1;
                end

                READ_ACCESS: state <= RESP;

                MERGE_WRITE: begin
                    merged <= merge_bytes(selected_word,latched_wdata,latched_wstrb);
                    state <= COMMIT_WRITE;
                end

                COMMIT_WRITE: state <= RESP;

                RESP: if(!req_valid) state <= IDLE;

                default: state <= IDLE;
            endcase
        end
    end
endmodule
