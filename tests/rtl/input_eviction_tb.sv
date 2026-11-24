// Replay wire stimuli. Each expected record in the compacted FIFO, including
// the unused tail, comes from the independent chronological queue model.
module input_eviction_tb;
    parameter integer DEPTH=16;
    logic clk=0, rst, event_ack;
    logic [7:0] kb_rows, kb_cols;
    logic [9:0] controller;
    logic [25:0] event_ack_data;
    wire event_valid, input_overflow, keyboard_shift;
    wire [7:0] event_code,key_code,queued_count;
    wire [9:0] controller_latched;
`ifdef SHAMA_INPUT_MAPPED
    shama_input dut(.*);
`else
    shama_input #(.FIFO_DEPTH(DEPTH)) dut(.*);
`endif
    localparam WIDTH=37+26*DEPTH;
    wire [WIDTH-1:0] observed;
    assign observed[36:0]={keyboard_shift,queued_count,input_overflow,controller_latched,key_code,event_code,event_valid};
`ifdef SHAMA_INPUT_MAPPED
    // The production backend emits a separate escaped signal for each word.
    `include "fifo_slot_probes.svh"
`else
    for(genvar i=0;i<DEPTH;i=i+1) assign observed[37+26*i+:26]=dut.fifo[i];
`endif
    reg [53:0] pins;
    reg [WIDTH-1:0] expected;
    string filename;
    integer stream,records=0,columns;
    initial begin
        if(!$value$plusargs("VECTORS=%s",filename)) $fatal(1,"missing expected vectors");
        stream=$fopen(filename,"r");
        if(!stream) $fatal(1,"cannot read expected vectors");
        while(!$feof(stream)) begin
            columns=$fscanf(stream,"%h %h\n",pins,expected);
            if(columns!=2) $fatal(1,"incomplete vector %0d",records);
            {rst,kb_rows,kb_cols,controller,event_ack,event_ack_data}=pins;
            #1;clk=1;#1;
            if(observed!==expected) $fatal(1,"FIFO transaction mismatch depth=%0d edge=%0d pins=%h got=%h expected=%h",DEPTH,records,pins,observed,expected);
            clk=0;#1;records++;
        end
        $display("FIFO TRANSACTIONS PASS depth=%0d edges=%0d",DEPTH,records);
        $finish;
    end
endmodule
