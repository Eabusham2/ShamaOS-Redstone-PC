`include "shama_boot_params.svh"

// Memory-device models only. All CPU, OS, filesystem, assembler, GPU,
// input and SoC arbitration RTL are real in this integration test.
// Physical ring adapters and generated storage cells have separate tests.
module shama_test_memory #(parameter integer WORDS=4096, FLASH=0)(
 input logic clk,rst,req_valid,req_we,
 input logic [31:0] req_addr,req_wdata,
 input logic [3:0] req_wstrb,
 output logic req_ready,
 output logic [31:0] req_rdata
);
 logic [31:0] memory[0:WORDS-1];
 logic [2:0] state;
 logic [31:0] addr,data;
 logic [3:0] strb;
 logic we;
 string flash_file;
 integer unused,flash_handle;
 initial begin
   state=0;
   for(integer k=0;k<WORDS;k=k+1) memory[k]=0;
   if(FLASH) begin
     unused=$value$plusargs("flash=%s",flash_file);
     if(!unused) $fatal(1,"missing flash image argument");
     flash_handle=$fopen(flash_file,"r");
     if(!flash_handle) $fatal(1,"cannot open flash image %s",flash_file);
     $fclose(flash_handle);
     $readmemh(flash_file,memory);
   end
 end
 assign req_ready=state==4;
 always @(posedge clk) begin
   if(rst) begin state<=0;req_rdata<=0;end
   else case(state)
     0:if(req_valid) begin addr<=req_addr;data<=req_wdata;strb<=req_wstrb;we<=req_we;state<=1;end
     1:state<=2;
     2:begin
       // The physical ring adapter selects the word from address bits [..:2].
       // Byte DMA requests retain low bits for their already-shifted strobes.
       if(addr>=WORDS*4) $fatal(1,"invalid memory request %h",addr);
       req_rdata<=memory[addr>>2];
       if(we) for(integer k=0;k<4;k=k+1) if(strb[k]) memory[addr>>2][k*8+:8]<=data[k*8+:8];
       state<=3;
     end
     3:state<=4;
     4:state<=0;
   endcase
 end
endmodule
module shama_cache_adapter(
 input logic clk,rst,req_valid,req_we,
 input logic [31:0] req_addr,req_wdata,
 input logic [3:0] req_wstrb,
 output logic req_ready,
 output logic [31:0] req_rdata,
 output logic [`SHAMA_CACHE_BANKS-1:0] bank_select,
 output logic [1023:0] row_select,
 output logic read_enable,write_commit,
 output logic [31:0] write_data,
 input logic [31:0] selected_word
);
 assign bank_select=0;assign row_select=0;assign read_enable=0;assign write_commit=0;assign write_data=0;
 shama_test_memory #(.WORDS(`SHAMA_CACHE_BYTES/4),.FLASH(0)) model(.clk,.rst,.req_valid,.req_we,.req_addr,.req_wdata,.req_wstrb,.req_ready,.req_rdata);
endmodule
module shama_ram_adapter(
 input logic clk,rst,req_valid,req_we,
 input logic [31:0] req_addr,req_wdata,
 input logic [3:0] req_wstrb,
 output logic req_ready,
 output logic [31:0] req_rdata,
 output logic [`SHAMA_RAM_BANKS-1:0] bank_select,
 output logic [1023:0] row_select,
 output logic read_enable,write_commit,
 output logic [31:0] write_data,
 input logic [31:0] selected_word
);
 assign bank_select=0;assign row_select=0;assign read_enable=0;assign write_commit=0;assign write_data=0;
 shama_test_memory #(.WORDS(`SHAMA_RAM_BYTES/4),.FLASH(0)) model(.clk,.rst,.req_valid,.req_we,.req_addr,.req_wdata,.req_wstrb,.req_ready,.req_rdata);
endmodule
module shama_flash_adapter(
 input logic clk,rst,req_valid,req_we,
 input logic [31:0] req_addr,req_wdata,
 input logic [3:0] req_wstrb,
 output logic req_ready,
 output logic [31:0] req_rdata,
 output logic [`SHAMA_FLASH_BANKS-1:0] bank_select,
 output logic [1023:0] row_select,
 output logic read_enable,write_commit,
 output logic [31:0] write_data,
 input logic [31:0] selected_word
);
 assign bank_select=0;assign row_select=0;assign read_enable=0;assign write_commit=0;assign write_data=0;
 shama_test_memory #(.WORDS(`SHAMA_FLASH_BYTES/4),.FLASH(1)) model(.clk,.rst,.req_valid,.req_we,.req_addr,.req_wdata,.req_wstrb,.req_ready,.req_rdata);
endmodule
module shama_vram_adapter(
 input logic clk,rst,req_valid,req_we,
 input logic [31:0] req_addr,req_wdata,
 input logic [3:0] req_wstrb,
 output logic req_ready,
 output logic [31:0] req_rdata,
 output logic [`SHAMA_VRAM_BANKS-1:0] bank_select,
 output logic [1023:0] row_select,
 output logic read_enable,write_commit,
 output logic [31:0] write_data,
 input logic [31:0] selected_word
);
 assign bank_select=0;assign row_select=0;assign read_enable=0;assign write_commit=0;assign write_data=0;
 shama_test_memory #(.WORDS(`SHAMA_VRAM_BYTES/4),.FLASH(0)) model(.clk,.rst,.req_valid,.req_we,.req_addr,.req_wdata,.req_wstrb,.req_ready,.req_rdata);
endmodule
