module tb_display_bridge;
    localparam integer W=8;
    localparam integer H=3;
    logic clk=0,rst=1;
    logic pixel_valid=0,pixel_bit=0,pixel_ready;
    logic [$clog2(W*H)-1:0] pixel_index=0;
    logic [W-1:0] row_data;
    logic [$clog2(H)-1:0] row_index;
    logic row_commit;
    logic row_ready=1;
    always #5 clk=~clk;

    shama_display_bridge #(.WIDTH(W),.HEIGHT(H)) dut(
        .clk,.rst,.pixel_valid,.pixel_index,.pixel_bit,.pixel_ready,
        .row_data,.row_index,.row_commit,.row_ready
    );

    integer i;
    integer commits=0;
    always @(posedge clk) if(row_commit) begin
        commits=commits+1;
        if(row_index==0 && row_data!==8'b10101010) begin
            $display("bridge row0 fail %b",row_data);$fatal(1);
        end
        if(row_index==1 && row_data!==8'b01010101) begin
            $display("bridge row1 fail %b",row_data);$fatal(1);
        end
    end

    task automatic sendbit(input logic b,input integer idx);
        begin
            while(!pixel_ready) @(posedge clk);
            @(negedge clk);pixel_valid<=1;pixel_bit<=b;pixel_index<=idx;
            @(negedge clk);pixel_valid<=0;
        end
    endtask

    initial begin
        repeat(3) @(posedge clk);rst<=0;
        for(i=0;i<8;i=i+1) sendbit(i[0] ? 1'b1 : 1'b0,i);
        for(i=0;i<8;i=i+1) sendbit(i[0] ? 1'b0 : 1'b1,8+i);
        repeat(10) @(posedge clk);
        if(commits!=2) begin $display("commit count %0d",commits);$fatal(1);end
        $display("DISPLAY BRIDGE PASS");
        $finish;
    end
endmodule
