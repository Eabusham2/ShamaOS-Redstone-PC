module tb_sha;
    logic clk=0, rst=1, start64=0, startbtc=0;
    logic busy64, done64, busybtc, donebtc;
    logic [511:0] message64;
    logic [639:0] header;
    logic [255:0] digest64, digestbtc;

    always #5 clk = ~clk;

    sha256_fixed64 u64(
        .clk(clk),.rst(rst),.start(start64),.message(message64),
        .busy(busy64),.done(done64),.digest(digest64)
    );
    bitcoin_dsha256 ubtc(
        .clk(clk),.rst(rst),.start(startbtc),.header(header),
        .busy(busybtc),.done(donebtc),.digest(digestbtc)
    );

    initial begin
        // 64 zero bytes: SHA-256(00*64)
        message64 = 512'd0;
        header = 640'd0;
        repeat(3) @(posedge clk);
        rst <= 0;

        @(posedge clk); start64 <= 1;
        @(posedge clk); start64 <= 0;
        wait(done64);
        if (digest64 !== 256'hf5a5fd42d16a20302798ef6ed309979b43003d2320d9f0e8ea9831a92759fb4b) begin
            $display("SHA64 FAIL %h", digest64);
            $fatal(1);
        end

        @(posedge clk); startbtc <= 1;
        @(posedge clk); startbtc <= 0;
        wait(donebtc);
        if (digestbtc !== 256'h4be7570e8f70eb093640c8468274ba759745a7aa2b7d25ab1e0421b259845014) begin
            $display("DSHA80 FAIL %h", digestbtc);
            $fatal(1);
        end

        $display("SHA RTL PASS");
        $finish;
    end
endmodule
