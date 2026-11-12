`include "shama_boot_params.svh"

module shama_memory_adapter #(
    parameter integer BANK_BITS = 8,
    parameter integer ROW_BITS = 10,
    parameter integer BANK_COUNT = (1 << BANK_BITS)
)(
    input  logic                         clk,
    input  logic                         rst,

    input  logic                         req_valid,
    input  logic                         req_we,
    input  logic [31:0]                  req_addr,
    input  logic [31:0]                  req_wdata,
    input  logic [3:0]                   req_wstrb,
    output logic                         req_ready,
    output logic [31:0]                  req_rdata,

    output logic [BANK_COUNT-1:0]         bank_select,
    output logic [(1<<ROW_BITS)-1:0]     row_select,
    output logic                         read_enable,
    output logic                         write_commit,
    output logic [31:0]                  write_data,
    input  logic [31:0]                  selected_word
);
    logic bank_step,row_step;
    logic [BANK_BITS-1:0] current_bank;
    logic [ROW_BITS-1:0] current_row;

    shama_ring_memctl #(
        .BANK_BITS(BANK_BITS),
        .ROW_BITS(ROW_BITS),
        .BANK_COUNT(BANK_COUNT)
    ) u_ctl (
        .clk,.rst,
        .req_valid,.req_we,.req_addr,.req_wdata,.req_wstrb,
        .req_ready,.req_rdata,
        .bank_step,.row_step,
        .write_commit,.write_data,
        .selected_word,
        .current_bank,.current_row
    );

    shama_onehot_ring #(.N(BANK_COUNT)) u_banks(
        .clk,.rst,.step(bank_step),.select(bank_select)
    );
    shama_onehot_ring #(.N(1<<ROW_BITS)) u_rows(
        .clk,.rst,.step(row_step),.select(row_select)
    );

    // Partial writes merge unselected bytes with the selected old word. The
    // physical read path must stay enabled for write transactions as well.
    // A rejected address must neither energize a bank nor alias a low bank.
    assign read_enable = req_valid && req_addr < BANK_COUNT*(1 << ROW_BITS)*4;
endmodule


module shama_cache_adapter(
    input  logic clk,rst,
    input  logic req_valid,req_we,
    input  logic [31:0] req_addr,req_wdata,
    input  logic [3:0] req_wstrb,
    output logic req_ready,
    output logic [31:0] req_rdata,
    output logic [`SHAMA_CACHE_BANKS-1:0] bank_select,
    output logic [1023:0] row_select,
    output logic read_enable,write_commit,
    output logic [31:0] write_data,
    input  logic [31:0] selected_word
);
    shama_memory_adapter #(.BANK_BITS($clog2(`SHAMA_CACHE_BANKS)),.ROW_BITS(10),.BANK_COUNT(`SHAMA_CACHE_BANKS)) u(
        .clk,.rst,.req_valid,.req_we,.req_addr,.req_wdata,.req_wstrb,
        .req_ready,.req_rdata,.bank_select,.row_select,.read_enable,
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
    output logic [1023:0] row_select,
    output logic read_enable,write_commit,
    output logic [31:0] write_data,
    input  logic [31:0] selected_word
);
    shama_memory_adapter #(.BANK_BITS($clog2(`SHAMA_RAM_BANKS)),.ROW_BITS(10),.BANK_COUNT(`SHAMA_RAM_BANKS)) u(
        .clk,.rst,.req_valid,.req_we,.req_addr,.req_wdata,.req_wstrb,
        .req_ready,.req_rdata,.bank_select,.row_select,.read_enable,
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
    output logic [1023:0] row_select,
    output logic read_enable,write_commit,
    output logic [31:0] write_data,
    input  logic [31:0] selected_word
);
    shama_memory_adapter #(.BANK_BITS($clog2(`SHAMA_FLASH_BANKS)),.ROW_BITS(10),.BANK_COUNT(`SHAMA_FLASH_BANKS)) u(
        .clk,.rst,.req_valid,.req_we,.req_addr,.req_wdata,.req_wstrb,
        .req_ready,.req_rdata,.bank_select,.row_select,.read_enable,
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
    output logic [1023:0] row_select,
    output logic read_enable,write_commit,
    output logic [31:0] write_data,
    input  logic [31:0] selected_word
);
    shama_memory_adapter #(.BANK_BITS($clog2(`SHAMA_VRAM_BANKS)),.ROW_BITS(10),.BANK_COUNT(`SHAMA_VRAM_BANKS)) u(
        .clk,.rst,.req_valid,.req_we,.req_addr,.req_wdata,.req_wstrb,
        .req_ready,.req_rdata,.bank_select,.row_select,.read_enable,
        .write_commit,.write_data,.selected_word
    );
endmodule
