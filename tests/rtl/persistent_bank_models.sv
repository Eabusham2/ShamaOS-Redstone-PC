`timescale 1ns/1ps
`include "shama_boot_params.svh"

// Passive storage cells for integration with the actual production adapters.
// This model has no request/ready bus, byte merge, address latch or reset input:
// those operations must be performed by the real controller and SoC logic.
// It receives only the exported physical bank/row/read/write pins. The return
// is deliberately delayed after each falling core edge, so the controller must
// respect its address/read settling phases. This is an RTL timing model, not
// evidence of complete-world physical redstone timing.
module shama_persistent_bank_model #(
    parameter integer BANKS=1,
    parameter integer ROW_BITS=10,
    parameter integer FLASH=0,
    parameter integer RETURN_DELAY_NS=3
)(
    input logic clk,
    input logic [BANKS-1:0] bank_select,
    input logic [ROW_BITS-1:0] row_address,
    input logic read_enable,write_commit,
    input logic [31:0] write_data,
    output logic [31:0] selected_word
);
    localparam integer WORDS=BANKS*(1<<ROW_BITS);
    logic [31:0] memory[0:WORDS-1];
    integer selected_bank=0,selected_count=0;
    integer read_samples=0,committed_writes=0;
    string flash_file;
    integer unused,flash_handle;

    initial begin
        selected_word=0;
        for(integer index=0;index<WORDS;index=index+1) memory[index]=0;
        if(FLASH) begin
            unused=$value$plusargs("flash=%s",flash_file);
            if(!unused) $fatal(1,"missing flash image argument");
            flash_handle=$fopen(flash_file,"r");
            if(!flash_handle) $fatal(1,"cannot open flash image %s",flash_file);
            $fclose(flash_handle);
            $readmemh(flash_file,memory);
        end
    end

    // Bank decoding only runs when the exported one-hot bus changes. This
    // avoids a 125-bank software scan on every flash read in a long SoC test.
    always @(bank_select) begin
        selected_bank=0;selected_count=0;
        for(integer bank=0;bank<BANKS;bank=bank+1)
            if(bank_select[bank]===1'b1) begin selected_bank=bank;selected_count=selected_count+1;end
    end

    always @(negedge clk) begin
        if(read_enable) begin
            if(selected_count!=1 || (^row_address===1'bx))
                $fatal(1,"physical read must select exactly one real bank and a known row");
            selected_word<=#RETURN_DELAY_NS memory[(selected_bank<<ROW_BITS)|row_address];
            read_samples<=read_samples+1;
        end else selected_word<=#RETURN_DELAY_NS 0;
    end

    always @(posedge clk) begin
        if(write_commit) begin
            if(!read_enable || selected_count!=1 || (^row_address===1'bx))
                $fatal(1,"physical write must hold one selected bank/row with read return enabled");
            memory[(selected_bank<<ROW_BITS)|row_address]<=write_data;
            committed_writes<=committed_writes+1;
        end
    end
endmodule
