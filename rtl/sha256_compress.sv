module sha256_compress (
    input  logic         clk,
    input  logic         rst,
    input  logic         start,
    input  logic [255:0] state_in,
    input  logic [511:0] block_in,
    output logic         busy,
    output logic         done,
    output logic [255:0] state_out
);

    logic [31:0] w [0:15];
    logic [31:0] a,b,c,d,e,f,g,h;
    logic [31:0] h0,h1,h2,h3,h4,h5,h6,h7;
    logic [6:0] round;

    logic [31:0] wt, new_w;
    logic [31:0] t1, t2, next_a, next_e;

    function automatic logic [31:0] rotr(input logic [31:0] x, input integer n);
        rotr = (x >> n) | (x << (32-n));
    endfunction

    function automatic logic [31:0] ch(input logic [31:0] x,y,z);
        ch = (x & y) ^ ((~x) & z);
    endfunction

    function automatic logic [31:0] maj(input logic [31:0] x,y,z);
        maj = (x & y) ^ (x & z) ^ (y & z);
    endfunction

    function automatic logic [31:0] bsig0(input logic [31:0] x);
        bsig0 = rotr(x,2) ^ rotr(x,13) ^ rotr(x,22);
    endfunction

    function automatic logic [31:0] bsig1(input logic [31:0] x);
        bsig1 = rotr(x,6) ^ rotr(x,11) ^ rotr(x,25);
    endfunction

    function automatic logic [31:0] ssig0(input logic [31:0] x);
        ssig0 = rotr(x,7) ^ rotr(x,18) ^ (x >> 3);
    endfunction

    function automatic logic [31:0] ssig1(input logic [31:0] x);
        ssig1 = rotr(x,17) ^ rotr(x,19) ^ (x >> 10);
    endfunction

    function automatic logic [31:0] kval(input logic [5:0] i);
        case (i)
             0:kval=32'h428a2f98;  1:kval=32'h71374491;  2:kval=32'hb5c0fbcf;  3:kval=32'he9b5dba5;
             4:kval=32'h3956c25b;  5:kval=32'h59f111f1;  6:kval=32'h923f82a4;  7:kval=32'hab1c5ed5;
             8:kval=32'hd807aa98;  9:kval=32'h12835b01; 10:kval=32'h243185be; 11:kval=32'h550c7dc3;
            12:kval=32'h72be5d74; 13:kval=32'h80deb1fe; 14:kval=32'h9bdc06a7; 15:kval=32'hc19bf174;
            16:kval=32'he49b69c1; 17:kval=32'hefbe4786; 18:kval=32'h0fc19dc6; 19:kval=32'h240ca1cc;
            20:kval=32'h2de92c6f; 21:kval=32'h4a7484aa; 22:kval=32'h5cb0a9dc; 23:kval=32'h76f988da;
            24:kval=32'h983e5152; 25:kval=32'ha831c66d; 26:kval=32'hb00327c8; 27:kval=32'hbf597fc7;
            28:kval=32'hc6e00bf3; 29:kval=32'hd5a79147; 30:kval=32'h06ca6351; 31:kval=32'h14292967;
            32:kval=32'h27b70a85; 33:kval=32'h2e1b2138; 34:kval=32'h4d2c6dfc; 35:kval=32'h53380d13;
            36:kval=32'h650a7354; 37:kval=32'h766a0abb; 38:kval=32'h81c2c92e; 39:kval=32'h92722c85;
            40:kval=32'ha2bfe8a1; 41:kval=32'ha81a664b; 42:kval=32'hc24b8b70; 43:kval=32'hc76c51a3;
            44:kval=32'hd192e819; 45:kval=32'hd6990624; 46:kval=32'hf40e3585; 47:kval=32'h106aa070;
            48:kval=32'h19a4c116; 49:kval=32'h1e376c08; 50:kval=32'h2748774c; 51:kval=32'h34b0bcb5;
            52:kval=32'h391c0cb3; 53:kval=32'h4ed8aa4a; 54:kval=32'h5b9cca4f; 55:kval=32'h682e6ff3;
            56:kval=32'h748f82ee; 57:kval=32'h78a5636f; 58:kval=32'h84c87814; 59:kval=32'h8cc70208;
            60:kval=32'h90befffa; 61:kval=32'ha4506ceb; 62:kval=32'hbef9a3f7; default:kval=32'hc67178f2;
        endcase
    endfunction

    always_comb begin
        if (round < 16) begin
            wt = w[round[3:0]];
            new_w = wt;
        end else begin
            new_w = ssig1(w[(round-2) & 15])
                  + w[(round-7) & 15]
                  + ssig0(w[(round-15) & 15])
                  + w[(round-16) & 15];
            wt = new_w;
        end
        t1 = h + bsig1(e) + ch(e,f,g) + kval(round[5:0]) + wt;
        t2 = bsig0(a) + maj(a,b,c);
        next_a = t1 + t2;
        next_e = d + t1;
    end

    integer i;
    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            done <= 1'b0;
            round <= 7'd0;
            state_out <= '0;
            a <= 0; b <= 0; c <= 0; d <= 0;
            e <= 0; f <= 0; g <= 0; h <= 0;
            h0 <= 0; h1 <= 0; h2 <= 0; h3 <= 0;
            h4 <= 0; h5 <= 0; h6 <= 0; h7 <= 0;
            for (i=0; i<16; i=i+1) w[i] <= 0;
        end else begin
            done <= 1'b0;

            if (start && !busy) begin
                h0 <= state_in[255:224];
                h1 <= state_in[223:192];
                h2 <= state_in[191:160];
                h3 <= state_in[159:128];
                h4 <= state_in[127:96];
                h5 <= state_in[95:64];
                h6 <= state_in[63:32];
                h7 <= state_in[31:0];

                a <= state_in[255:224];
                b <= state_in[223:192];
                c <= state_in[191:160];
                d <= state_in[159:128];
                e <= state_in[127:96];
                f <= state_in[95:64];
                g <= state_in[63:32];
                h <= state_in[31:0];

                for (i=0; i<16; i=i+1)
                    w[i] <= block_in[511 - i*32 -: 32];

                round <= 0;
                busy <= 1'b1;
            end else if (busy) begin
                if (round >= 16)
                    w[round[3:0]] <= new_w;

                h <= g;
                g <= f;
                f <= e;
                e <= next_e;
                d <= c;
                c <= b;
                b <= a;
                a <= next_a;

                if (round == 63) begin
                    state_out <= {
                        h0 + next_a,
                        h1 + a,
                        h2 + b,
                        h3 + c,
                        h4 + next_e,
                        h5 + e,
                        h6 + f,
                        h7 + g
                    };
                    busy <= 1'b0;
                    done <= 1'b1;
                end else begin
                    round <= round + 1'b1;
                end
            end
        end
    end
endmodule
