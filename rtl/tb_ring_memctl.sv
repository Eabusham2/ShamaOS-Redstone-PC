module tb_ring_memctl;
    logic clk=0,rst=1;
    logic req_valid=0,req_we=0;
    logic [31:0] req_addr=0,req_wdata=0;
    logic [3:0] req_wstrb=0;
    logic req_ready;
    logic [31:0] req_rdata;
    logic bank_step,row_step,write_commit;
    logic [31:0] write_data;
    logic [31:0] selected_word=32'h11223344;
    logic [1:0] current_bank,current_row;

    always #5 clk=~clk;

    shama_ring_memctl #(.BANK_BITS(2),.ROW_BITS(2)) dut(
        .clk,.rst,.req_valid,.req_we,.req_addr,.req_wdata,.req_wstrb,
        .req_ready,.req_rdata,.bank_step,.row_step,.write_commit,.write_data,
        .selected_word,.current_bank,.current_row
    );

    integer bank_pulses,row_pulses;
    always @(posedge clk) begin
        if(bank_step) bank_pulses<=bank_pulses+1;
        if(row_step) row_pulses<=row_pulses+1;
    end

    task automatic start_req(
        input logic we,input logic [1:0] bank,input logic [1:0] row,
        input logic [31:0] data,input logic [3:0] strb
    );
        begin
            @(negedge clk);
            req_we<=we;
            req_addr<=((bank << 4) | (row << 2));
            req_wdata<=data;
            req_wstrb<=strb;
            req_valid<=1;
        end
    endtask

    task automatic finish_req;
        begin
            wait(req_ready);
            @(negedge clk);req_valid<=0;
            @(posedge clk);
        end
    endtask

    initial begin
        bank_pulses=0;row_pulses=0;
        repeat(3) @(posedge clk);rst<=0;

        start_req(0,2'd1,2'd2,0,0);
        finish_req();
        if(current_bank!=1 || current_row!=2 || req_rdata!==32'h11223344) begin
            $display("read seek fail");$fatal(1);
        end
        if(bank_pulses!=1 || row_pulses!=2) begin
            $display("step counts bank=%0d row=%0d",bank_pulses,row_pulses);$fatal(1);
        end

        selected_word=32'h11223344;
        start_req(1,2'd1,2'd2,32'hAABBCCDD,4'b0101);
        wait(write_commit);
        if(write_data!==32'h11BB33DD) begin
            $display("merge fail %h",write_data);$fatal(1);
        end
        finish_req();

        $display("RING MEMCTL PASS");
        $finish;
    end
endmodule
