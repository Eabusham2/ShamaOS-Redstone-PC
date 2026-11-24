// The two production FIFOs see the same inputs. Only the exact ACK epoch differs.
module ack_credit_equivalence_witness;
    reg clk = 0;
    reg rst = 1;
    reg [7:0] kb_rows = 0;
    reg [7:0] kb_cols = 0;
    reg [1:0] ack = 0;
    wire [7:0] count [0:1];
    wire [1:0] overflow;
    wire [25:0] tail [0:1];

    for (genvar i = 0; i < 2; i = i + 1) begin : fifo_variant
        shama_input dut(
            .clk, .rst, .kb_rows, .kb_cols, .controller(10'b0),
            .event_ack(ack[i]), .event_ack_data(26'h4101),
            .queued_count(count[i]), .input_overflow(overflow[i])
        );
`ifdef SHAMA_INPUT_MAPPED
        assign tail[i] = dut.\fifo[15] ;
`else
        assign tail[i] = dut.fifo[15];
`endif
    end

    task tick;
        begin
            #2; clk = 1; #2; clk = 0; #2;
        end
    endtask

    initial begin
        tick;
        rst = 0;
        repeat (16) begin
            kb_rows = 1; kb_cols = 1; tick;
            kb_rows = 0; kb_cols = 0; tick;
        end
        if (count[0] !== 16 || count[1] !== 16 || overflow !== 0)
            $fatal(1, "initial A fill");

        // B arrives concurrently with the first FIFO's ACK. The second FIFO
        // receives the same ACK one edge later, immediately after B releases.
        kb_rows = 1; kb_cols = 2; ack = 1; tick;
        kb_rows = 0; kb_cols = 0; ack = 2; tick;
        if (count[0] !== 16 || count[1] !== 15 || overflow !== 2 ||
            tail[0] !== 26'h4201 || tail[1] !== 26'h0000)
            $fatal(1, "counts=%d/%d overflow=%b tails=%h/%h",
                   count[0], count[1], overflow, tail[0], tail[1]);
        $display("ACK CREDIT WITNESS PASS");
        $finish;
    end
endmodule
