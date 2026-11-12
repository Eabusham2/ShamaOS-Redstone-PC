`include "shama_boot_params.svh"

// Latched direct addressing. Local physical decoders turn row_address into
// isolated word selects beside each storage tile; no rotating row seek is used.
module shama_memory_adapter #(
    parameter integer BANK_BITS = 8,
    parameter integer ROW_BITS = 10,
    parameter integer BANK_COUNT = (1 << BANK_BITS)
)(
    input logic clk,rst,req_valid,req_we,
    input logic [31:0] req_addr,req_wdata,
    input logic [3:0] req_wstrb,
    output logic req_ready,
    output logic [31:0] req_rdata,
    output logic [BANK_COUNT-1:0] bank_select,
    output logic [ROW_BITS-1:0] row_address,
    output logic read_enable,write_commit,
    output logic [31:0] write_data,
    input logic [31:0] selected_word
);
    // Address changes occur with reads/writes disabled. A complete read-settle
    // phase precedes sampling, and a post-commit phase holds the selected word
    // after the write pulse. Core clock routing/settling remains a physical
    // timing contract, separately exercised by literal memory runtime tests.
    typedef enum logic [2:0] {
        IDLE, ADDRESS_SETTLE, READ_SETTLE, ACCESS, COMMIT, POST_COMMIT, RESP
    } state_t;
    state_t state;
    logic [BANK_BITS-1:0] selected_bank;
    logic [ROW_BITS-1:0] selected_row;
    logic latched_we;
    logic [31:0] latched_wdata,merged,response;
    logic [3:0] latched_wstrb;

    function automatic [31:0] merge_bytes(input [31:0] old_word,new_word,input [3:0] strobes);
        begin
            merge_bytes=old_word;
            for(integer lane=0;lane<4;lane=lane+1)
                if(strobes[lane]) merge_bytes[lane*8+:8]=new_word[lane*8+:8];
        end
    endfunction

    for(genvar bank=0;bank<BANK_COUNT;bank=bank+1)
        assign bank_select[bank]=selected_bank==bank;
    assign row_address=selected_row;

    assign req_ready=!rst && state==RESP;
    assign req_rdata=response;
    assign read_enable=!rst && (state==READ_SETTLE || state==ACCESS || state==COMMIT || state==POST_COMMIT);
    assign write_commit=!rst && state==COMMIT;
    assign write_data=merged;

    always_ff @(posedge clk) begin
        if(rst) begin
            state<=IDLE;selected_bank<=0;selected_row<=0;latched_we<=0;
            latched_wdata<=0;latched_wstrb<=0;merged<=0;response<=0;
        end else case(state)
            IDLE:if(req_valid) begin
                if(req_addr>=BANK_COUNT*(1<<ROW_BITS)*4) begin response<=0;state<=RESP;end
                else begin
                    selected_bank<=req_addr[ROW_BITS+2+:BANK_BITS];
                    selected_row<=req_addr[2+:ROW_BITS];
                    latched_we<=req_we;latched_wdata<=req_wdata;latched_wstrb<=req_wstrb;
                    state<=ADDRESS_SETTLE;
                end
            end
            ADDRESS_SETTLE:state<=READ_SETTLE;
            READ_SETTLE:state<=ACCESS;
            ACCESS:begin
                if(latched_we) begin
                    merged<=merge_bytes(selected_word,latched_wdata,latched_wstrb);
                    response<=merge_bytes(selected_word,latched_wdata,latched_wstrb);
                    state<=COMMIT;
                end else begin response<=selected_word;state<=RESP;end
            end
            COMMIT:state<=POST_COMMIT;
            POST_COMMIT:state<=RESP;
            // A one-cycle completion allows a master to retain req_valid and
            // advance its address on ready without repeating the old write.
            RESP:state<=IDLE;
            default:state<=IDLE;
        endcase
    end
endmodule

module shama_cache_adapter(
    input  logic clk,rst,
    input  logic req_valid,req_we,
    input  logic [31:0] req_addr,req_wdata,
    input  logic [3:0] req_wstrb,
    output logic req_ready,
    output logic [31:0] req_rdata,
    output logic [`SHAMA_CACHE_BANKS-1:0] bank_select,
    output logic [9:0] row_address,
    output logic read_enable,write_commit,
    output logic [31:0] write_data,
    input  logic [31:0] selected_word
);
    shama_memory_adapter #(.BANK_BITS($clog2(`SHAMA_CACHE_BANKS)),.ROW_BITS(10),.BANK_COUNT(`SHAMA_CACHE_BANKS)) u(
        .clk,.rst,.req_valid,.req_we,.req_addr,.req_wdata,.req_wstrb,
        .req_ready,.req_rdata,.bank_select,.row_address,.read_enable,
        .write_commit,.write_data,.selected_word
    );
endmodule


module shama_ram_adapter(
    input  logic clk,rst,
    input  logic req_valid,req_we,
    input  logic [31:0] req_addr,req_wdata,
    input  logic [3:0] req_wstrb,
    output logic req_ready,
    output logic [31:0] req_rdata,
    output logic [`SHAMA_RAM_BANKS-1:0] bank_select,
    output logic [9:0] row_address,
    output logic read_enable,write_commit,
    output logic [31:0] write_data,
    input  logic [31:0] selected_word
);
    shama_memory_adapter #(.BANK_BITS($clog2(`SHAMA_RAM_BANKS)),.ROW_BITS(10),.BANK_COUNT(`SHAMA_RAM_BANKS)) u(
        .clk,.rst,.req_valid,.req_we,.req_addr,.req_wdata,.req_wstrb,
        .req_ready,.req_rdata,.bank_select,.row_address,.read_enable,
        .write_commit,.write_data,.selected_word
    );
endmodule


module shama_flash_adapter(
    input  logic clk,rst,
    input  logic req_valid,req_we,
    input  logic [31:0] req_addr,req_wdata,
    input  logic [3:0] req_wstrb,
    output logic req_ready,
    output logic [31:0] req_rdata,
    output logic [`SHAMA_FLASH_BANKS-1:0] bank_select,
    output logic [9:0] row_address,
    output logic read_enable,write_commit,
    output logic [31:0] write_data,
    input  logic [31:0] selected_word
);
    shama_memory_adapter #(.BANK_BITS($clog2(`SHAMA_FLASH_BANKS)),.ROW_BITS(10),.BANK_COUNT(`SHAMA_FLASH_BANKS)) u(
        .clk,.rst,.req_valid,.req_we,.req_addr,.req_wdata,.req_wstrb,
        .req_ready,.req_rdata,.bank_select,.row_address,.read_enable,
        .write_commit,.write_data,.selected_word
    );
endmodule


module shama_vram_adapter(
    input  logic clk,rst,
    input  logic req_valid,req_we,
    input  logic [31:0] req_addr,req_wdata,
    input  logic [3:0] req_wstrb,
    output logic req_ready,
    output logic [31:0] req_rdata,
    output logic [`SHAMA_VRAM_BANKS-1:0] bank_select,
    output logic [9:0] row_address,
    output logic read_enable,write_commit,
    output logic [31:0] write_data,
    input  logic [31:0] selected_word
);
    shama_memory_adapter #(.BANK_BITS($clog2(`SHAMA_VRAM_BANKS)),.ROW_BITS(10),.BANK_COUNT(`SHAMA_VRAM_BANKS)) u(
        .clk,.rst,.req_valid,.req_we,.req_addr,.req_wdata,.req_wstrb,
        .req_ready,.req_rdata,.bank_select,.row_address,.read_enable,
        .write_commit,.write_data,.selected_word
    );
endmodule
