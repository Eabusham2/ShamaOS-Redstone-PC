module shama_cpu (
    input  logic         clk,
    input  logic         rst,

    output logic         mem_valid,
    output logic         mem_we,
    output logic [31:0]  mem_addr,
    output logic [31:0]  mem_wdata,
    output logic [3:0]   mem_wstrb,
    input  logic         mem_ready,
    input  logic [31:0]  mem_rdata,

    output logic         sys_valid,
    output logic [11:0]  sys_id,
    output logic [191:0] sys_args,
    input  logic         sys_ready,
    input  logic [63:0]  sys_ret,
    input  logic         sys_jump_valid,
    input  logic [31:0]  sys_jump_pc,

    output logic         cache_flush,
    input  logic [63:0]  time_counter,
    input  logic         external_hold,
    input  logic         external_jump_valid,
    input  logic [31:0]  external_jump_pc,
    output logic         halted,
    output logic         sha_busy,
    output logic [7:0]   fault_code,
    output logic [31:0]  debug_pc,
    output logic [31:0]  debug_instruction,
    output logic [4:0]   debug_flags,
    output logic [5:0]   debug_sha_round,
    output logic [255:0] debug_sha_state,
    output logic [511:0] debug_registers,
    output logic [5:0]   debug_state,
    output logic         debug_stall
);
    localparam [7:0]
        OP_NOP=8'h00, OP_HLT=8'h01, OP_MOV=8'h02, OP_LDI=8'h03, OP_LUI=8'h04,
        OP_ADD=8'h10, OP_ADC=8'h11, OP_SUB=8'h12, OP_SBC=8'h13,
        OP_MUL=8'h14, OP_DIV=8'h15, OP_MOD=8'h16, OP_NEG=8'h17,
        OP_INC=8'h18, OP_DEC=8'h19, OP_ADDI=8'h1a, OP_SUBI=8'h1b,
        OP_AND=8'h20, OP_OR=8'h21, OP_XOR=8'h22, OP_NOR=8'h23,
        OP_NAND=8'h24, OP_XNOR=8'h25, OP_NOT=8'h26,
        OP_SHL=8'h27, OP_SHR=8'h28, OP_SAR=8'h29, OP_ROL=8'h2a, OP_ROR=8'h2b,
        OP_ANDI=8'h2c, OP_ORI=8'h2d, OP_XORI=8'h2e, OP_RSH1=8'h2f,
        OP_CMP=8'h30, OP_TEST=8'h31,
        OP_JMP=8'h40, OP_BR=8'h41, OP_CALL=8'h42, OP_RET=8'h43,
        OP_PUSH=8'h44, OP_POP=8'h45,
        OP_LDB=8'h50, OP_LDH=8'h51, OP_LDW=8'h52,
        OP_STB=8'h53, OP_STH=8'h54, OP_STW=8'h55, OP_LEA=8'h56,
        OP_SYS=8'h60, OP_IRET=8'h61, OP_FENCE=8'h62, OP_CFLUSH=8'h63,
        OP_RDTIME=8'h64, OP_RDPMC=8'h65,
        OP_CH=8'h70, OP_MAJ=8'h71, OP_BSIG0=8'h72, OP_BSIG1=8'h73,
        OP_SSIG0=8'h74, OP_SSIG1=8'h75, OP_SHAROUND=8'h76,
        OP_SHA256=8'h77, OP_DSHA256=8'h78, OP_HASHCMP=8'h79, OP_INCNONCE=8'h7a;

    typedef enum logic [5:0] {
        S_FETCH, S_FETCH_EXT, S_EXEC,
        S_LOAD, S_STORE, S_SYS,
        S_SHA64_LOAD, S_SHA64_START, S_SHA64_WAIT, S_SHA64_WRITE,
        S_DSHA_LOAD, S_DSHA_START, S_DSHA_WAIT, S_DSHA_WRITE,
        S_HASH_LOAD_HASH, S_HASH_LOAD_TARGET, S_HASH_COMPARE,
        S_NONCE_READ, S_NONCE_WRITE,
        S_HALTED
    } state_t;

    state_t state;

    logic [31:0] regs [0:15];
    logic [31:0] call_stack [0:255];
    logic [31:0] data_stack [0:255];
    logic [8:0] call_sp, data_sp;

    logic [31:0] pc;
    logic [31:0] ir, ext_word;
    logic [7:0] opcode;
    logic [3:0] rd, ra, rb;
    logic signed [11:0] imm12;

    logic flag_z, flag_c, flag_n, flag_v, flag_valid;

    logic [31:0] effective_addr, store_value;
    logic [1:0] mem_size;
    logic [3:0] load_dest;

    logic [63:0] cycle_counter;
    logic [63:0] retired_counter;
    logic [31:0] cycle_snapshot_hi,retired_snapshot_hi,time_snapshot_hi;

    logic [511:0] sha64_message;
    logic [639:0] dsha_header;
    logic [255:0] sha_digest;
    logic [255:0] dsha_digest;
    logic sha64_start, sha64_busy, sha64_done;
    logic dsha_start, dsha_busy, dsha_done;
    logic sha64_complete, dsha_complete;
    logic [5:0] sha64_debug_round, dsha_debug_round;
    logic [255:0] sha64_debug_state, dsha_debug_state;
    logic [4:0] sha_word_index;
    logic [31:0] sha_src_ptr, sha_dst_ptr;

    logic [255:0] hash_buf, target_buf;
    logic [3:0] hash_word_index;

    logic [31:0] nonce_word;
    logic [31:0] ra_value, rb_value, rd_value;

    logic [5:0] round_counter;

    sha256_fixed64 u_sha64(
        .clk(clk), .rst(rst), .start(sha64_start), .message(sha64_message),
        .busy(sha64_busy), .done(sha64_done), .digest(sha_digest),
        .debug_round(sha64_debug_round), .debug_state(sha64_debug_state)
    );

    bitcoin_dsha256 u_dsha(
        .clk(clk), .rst(rst), .start(dsha_start), .header(dsha_header),
        .busy(dsha_busy), .done(dsha_done), .digest(dsha_digest),
        .debug_round(dsha_debug_round), .debug_state(dsha_debug_state)
    );

    assign sha_busy = sha64_busy | dsha_busy;
    assign debug_pc = pc;
    assign debug_instruction = ir;
    assign debug_flags = {flag_valid,flag_v,flag_n,flag_c,flag_z};
    assign debug_sha_round = dsha_busy ? dsha_debug_round :
                             sha64_busy ? sha64_debug_round : round_counter;
    assign debug_sha_state = dsha_busy ? dsha_debug_state :
                             sha64_busy ? sha64_debug_state :
                             {regs[1],regs[2],regs[3],regs[4],regs[5],regs[6],regs[7],regs[8]};
    assign debug_state = state;
    assign debug_stall = external_hold || (mem_valid && !mem_ready) || (sys_valid && !sys_ready) || sha_busy;
    genvar register_index;
    generate for(register_index=0;register_index<16;register_index=register_index+1) begin: debug_regs
        assign debug_registers[register_index*32 +:32] = register_index==0 ? 32'd0 : regs[register_index];
    end endgenerate

    // Accelerators may finish while a debug hold pauses the CPU. Retain their
    // completion pulse until the next invocation, so resuming cannot hang.
    always_ff @(posedge clk) begin
        if(rst || external_jump_valid) begin sha64_complete<=0;dsha_complete<=0;end
        else begin
            if(sha64_start) sha64_complete<=0;
            else if(sha64_done) sha64_complete<=1;
            if(dsha_start) dsha_complete<=0;
            else if(dsha_done) dsha_complete<=1;
        end
    end

    function automatic logic [31:0] rreg(input logic [3:0] idx);
        rreg = (idx == 0) ? 32'd0 : regs[idx];
    endfunction

    function automatic logic [31:0] bswap32(input logic [31:0] x);
        bswap32 = {x[7:0],x[15:8],x[23:16],x[31:24]};
    endfunction

    function automatic logic [31:0] rotr(input logic [31:0] x, input integer n);
        rotr = (x >> n) | (x << (32-n));
    endfunction

    function automatic logic [31:0] f_ch(input logic [31:0] x,y,z);
        f_ch = (x & y) ^ ((~x) & z);
    endfunction
    function automatic logic [31:0] f_maj(input logic [31:0] x,y,z);
        f_maj = (x & y) ^ (x & z) ^ (y & z);
    endfunction
    function automatic logic [31:0] f_bsig0(input logic [31:0] x);
        f_bsig0 = rotr(x,2)^rotr(x,13)^rotr(x,22);
    endfunction
    function automatic logic [31:0] f_bsig1(input logic [31:0] x);
        f_bsig1 = rotr(x,6)^rotr(x,11)^rotr(x,25);
    endfunction
    function automatic logic [31:0] f_ssig0(input logic [31:0] x);
        f_ssig0 = rotr(x,7)^rotr(x,18)^(x>>3);
    endfunction
    function automatic logic [31:0] f_ssig1(input logic [31:0] x);
        f_ssig1 = rotr(x,17)^rotr(x,19)^(x>>10);
    endfunction

    function automatic logic cond_true(input logic [3:0] cond);
        logic slt;
        begin
            slt = flag_n ^ flag_v;
            case(cond)
                4'd0: cond_true = flag_z;
                4'd1: cond_true = !flag_z;
                4'd2: cond_true = flag_c;
                4'd3: cond_true = !flag_c;
                4'd4: cond_true = slt;
                4'd5: cond_true = slt | flag_z;
                4'd6: cond_true = (!slt) & (!flag_z);
                4'd7: cond_true = !slt;
                4'd8: cond_true = flag_n;
                4'd9: cond_true = !flag_n;
                4'd10: cond_true = flag_valid;
                4'd11: cond_true = !flag_valid;
                default: cond_true = 1'b0;
            endcase
        end
    endfunction

    function automatic logic needs_ext(input logic [7:0] op);
        needs_ext = (op==OP_LDI)||(op==OP_LUI)||(op==OP_JMP)||(op==OP_BR)||(op==OP_CALL)
                 ||(op==OP_ADDI)||(op==OP_SUBI)||(op==OP_ANDI)||(op==OP_ORI)||(op==OP_XORI);
    endfunction

    function automatic logic data_address_ok(input logic [31:0] addr, input logic [2:0] size);
        data_address_ok = ((addr < 32'h00100000 && {1'b0,addr}+size <= 33'h00100000)
                         ||(addr >= 32'h01000000 && {1'b0,addr}+size <= 33'h01400000)
                         ||(addr >= 32'h02000000 && {1'b0,addr}+size <= 33'h02010000));
    endfunction

    task automatic fault(input logic [7:0] code);
        begin fault_code <= code; halted <= 1'b1; state <= S_HALTED; end
    endtask

    task automatic set_zn(input logic [31:0] v);
        begin
            flag_z <= (v == 0);
            flag_n <= v[31];
        end
    endtask

    always_comb begin
        opcode = ir[31:24];
        rd = ir[23:20];
        ra = ir[19:16];
        rb = ir[15:12];
        imm12 = ir[11:0];
        ra_value = rreg(ra);
        rb_value = rreg(rb);
        rd_value = rreg(rd);

        mem_valid = 1'b0;
        mem_we = 1'b0;
        mem_addr = 32'd0;
        mem_wdata = 32'd0;
        mem_wstrb = 4'b0000;

        sys_valid = (state == S_SYS);
        sys_id = (opcode==OP_CFLUSH)?12'h014:ir[11:0];
        sys_args = {rreg(4'd6),rreg(4'd5),rreg(4'd4),rreg(4'd3),rreg(4'd2),rreg(4'd1)};

        case (state)
            S_FETCH: begin
                mem_valid = 1;
                mem_addr = pc << 2;
            end
            S_FETCH_EXT: begin
                mem_valid = 1;
                mem_addr = pc << 2;
            end
            S_LOAD: begin
                mem_valid = 1;
                mem_addr = {effective_addr[31:2],2'b00};
            end
            S_STORE: begin
                mem_valid = 1;
                mem_we = 1;
                mem_addr = {effective_addr[31:2],2'b00};
                case(mem_size)
                    2'd0: begin
                        mem_wdata = store_value << (8*effective_addr[1:0]);
                        mem_wstrb = 4'b0001 << effective_addr[1:0];
                    end
                    2'd1: begin
                        mem_wdata = store_value << (8*effective_addr[1:0]);
                        mem_wstrb = 4'b0011 << effective_addr[1:0];
                    end
                    default: begin
                        mem_wdata = store_value;
                        mem_wstrb = 4'b1111;
                    end
                endcase
            end
            S_SHA64_LOAD, S_DSHA_LOAD: begin
                mem_valid = 1;
                mem_addr = sha_src_ptr + (sha_word_index << 2);
            end
            S_SHA64_WRITE, S_DSHA_WRITE: begin
                mem_valid = 1;
                mem_we = 1;
                mem_addr = sha_dst_ptr + (sha_word_index << 2);
                mem_wstrb = 4'b1111;
                if (state == S_SHA64_WRITE)
                    mem_wdata = bswap32(sha_digest[255 - sha_word_index*32 -: 32]);
                else
                    mem_wdata = bswap32(dsha_digest[255 - sha_word_index*32 -: 32]);
            end
            S_HASH_LOAD_HASH: begin
                mem_valid = 1;
                mem_addr = sha_src_ptr + (hash_word_index << 2);
            end
            S_HASH_LOAD_TARGET: begin
                mem_valid = 1;
                mem_addr = sha_dst_ptr + (hash_word_index << 2);
            end
            S_NONCE_READ: begin
                mem_valid = 1;
                mem_addr = sha_src_ptr + 32'd76;
            end
            S_NONCE_WRITE: begin
                mem_valid = 1;
                mem_we = 1;
                mem_addr = sha_src_ptr + 32'd76;
                mem_wdata = nonce_word + 1'b1;
                mem_wstrb = 4'b1111;
            end
            default: begin end
        endcase
    end

    integer i;
    logic [32:0] wide;
    logic [31:0] result;
    logic [31:0] t1_dbg, t2_dbg;

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= S_FETCH;
            pc <= 0;
            ir <= 0;
            ext_word <= 0;
            call_sp <= 0;
            data_sp <= 0;
            flag_z <= 0; flag_c <= 0; flag_n <= 0; flag_v <= 0; flag_valid <= 0;
            halted <= 0;
            fault_code <= 0;
            round_counter <= 0;
            cache_flush <= 0;
            cycle_counter <= 0;
            retired_counter <= 0;
            cycle_snapshot_hi<=0;retired_snapshot_hi<=0;time_snapshot_hi<=0;
            effective_addr <= 0; store_value <= 0; mem_size <= 0; load_dest <= 0;
            sha64_message <= 0; dsha_header <= 0;
            sha64_start <= 0; dsha_start <= 0;
            sha_word_index <= 0; sha_src_ptr <= 0; sha_dst_ptr <= 0;
            hash_buf <= 0; target_buf <= 0; hash_word_index <= 0; nonce_word <= 0;
            for (i=0;i<16;i=i+1) regs[i] <= 0;
            for (i=0;i<256;i=i+1) begin call_stack[i] <= 0; data_stack[i] <= 0; end
        end else if(external_jump_valid) begin
            pc <= external_jump_pc;
            state <= S_FETCH;
            halted <= 1'b0;
            fault_code <= 0;
            round_counter <= 0;
            call_sp <= 0;
            data_sp <= 0;
            flag_z <= 0;flag_c <= 0;flag_n <= 0;flag_v <= 0;flag_valid <= 0;
            cache_flush <= 0;
            sha64_start <= 0;
            dsha_start <= 0;
            for (i=0;i<16;i=i+1) regs[i] <= 0;
        end else if(external_hold) begin
            cache_flush <= 0;
            sha64_start <= 0;
            dsha_start <= 0;
        end else begin
            cycle_counter <= cycle_counter + 1'b1;
            cache_flush <= 0;
            sha64_start <= 0;
            dsha_start <= 0;
            regs[0] <= 0;

            case(state)
                S_FETCH: if (mem_ready) begin
                    ir <= mem_rdata;
                    pc <= pc + 1'b1;
                    if (needs_ext(mem_rdata[31:24]))
                        state <= S_FETCH_EXT;
                    else
                        state <= S_EXEC;
                end

                S_FETCH_EXT: if (mem_ready) begin
                    ext_word <= mem_rdata;
                    pc <= pc + 1'b1;
                    state <= S_EXEC;
                end

                S_EXEC: begin
                    retired_counter <= retired_counter + 1'b1;
                    case(opcode)
                        OP_NOP: state <= S_FETCH;
                        OP_HLT: begin halted <= 1; state <= S_HALTED; end
                        OP_MOV: begin regs[rd] <= rreg(ra); state <= S_FETCH; end
                        OP_LDI: begin regs[rd] <= ext_word; state <= S_FETCH; end
                        OP_LUI: begin regs[rd] <= ext_word << 12; state <= S_FETCH; end
                        OP_ADDI: begin
                            wide={1'b0,rd_value}+{1'b0,ext_word}; result=wide[31:0];
                            regs[rd]<=result; flag_c<=wide[32]; set_zn(result);
                            flag_v<=(~(rd_value[31]^ext_word[31]))&(rd_value[31]^result[31]); state<=S_FETCH;
                        end
                        OP_SUBI: begin
                            result=rd_value-ext_word; regs[rd]<=result; flag_c<=(rd_value>=ext_word); set_zn(result);
                            flag_v<=(rd_value[31]^ext_word[31])&(rd_value[31]^result[31]); state<=S_FETCH;
                        end
                        OP_ANDI: begin result=rd_value&ext_word; regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_ORI: begin result=rd_value|ext_word; regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_XORI: begin result=rd_value^ext_word; regs[rd]<=result; set_zn(result); state<=S_FETCH; end

                        OP_ADD, OP_ADC: begin
                            wide = {1'b0,rreg(ra)} + {1'b0,rreg(rb)}
                                 + ((opcode==OP_ADC && flag_c) ? 1 : 0);
                            result = wide[31:0];
                            regs[rd] <= result; flag_c <= wide[32]; set_zn(result);
                            flag_v <= (~(ra_value[31]^rb_value[31])) & (ra_value[31]^result[31]);
                            state <= S_FETCH;
                        end
                        OP_SUB, OP_SBC: begin
                            result = rreg(ra) - rreg(rb) - ((opcode==OP_SBC && !flag_c) ? 1 : 0);
                            regs[rd] <= result;
                            flag_c <= ({1'b0,rreg(ra)} >= ({1'b0,rreg(rb)}+((opcode==OP_SBC && !flag_c)?33'd1:33'd0)));
                            set_zn(result);
                            flag_v <= (ra_value[31]^rb_value[31]) & (ra_value[31]^result[31]);
                            state <= S_FETCH;
                        end
                        OP_MUL: begin result=rreg(ra)*rreg(rb); regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_DIV: begin result=(rreg(rb)==0)?32'hffffffff:(rreg(ra)/rreg(rb)); regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_MOD: begin result=(rreg(rb)==0)?rreg(ra):(rreg(ra)%rreg(rb)); regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_NEG: begin result=-rreg(ra); regs[rd]<=result; set_zn(result); flag_c<=(ra_value==0); flag_v<=(ra_value==32'h80000000); state<=S_FETCH; end
                        OP_INC: begin result=rreg(rd)+1; regs[rd]<=result; set_zn(result); flag_c<=(rd_value==32'hffffffff); flag_v<=(rd_value==32'h7fffffff); state<=S_FETCH; end
                        OP_DEC: begin result=rreg(rd)-1; regs[rd]<=result; set_zn(result); flag_c<=(rreg(rd)!=0); flag_v<=(rd_value==32'h80000000); state<=S_FETCH; end

                        OP_AND: begin result=rreg(ra)&rreg(rb); regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_OR: begin result=rreg(ra)|rreg(rb); regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_XOR: begin result=rreg(ra)^rreg(rb); regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_NOR: begin result=~(rreg(ra)|rreg(rb)); regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_NAND: begin result=~(rreg(ra)&rreg(rb)); regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_XNOR: begin result=~(rreg(ra)^rreg(rb)); regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_NOT: begin result=~rreg(ra); regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_SHL: begin result=rreg(ra) << rb_value[4:0]; regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_SHR: begin result=rreg(ra) >> rb_value[4:0]; regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_RSH1: begin result=rreg(ra)>>1; regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_SAR: begin result=$signed(rreg(ra)) >>> rb_value[4:0]; regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_ROL: begin result=(rreg(ra)<<rb_value[4:0]) | (rreg(ra)>>(32-rb_value[4:0])); regs[rd]<=result; set_zn(result); state<=S_FETCH; end
                        OP_ROR: begin result=(rreg(ra)>>rb_value[4:0]) | (rreg(ra)<<(32-rb_value[4:0])); regs[rd]<=result; set_zn(result); state<=S_FETCH; end

                        OP_CMP: begin
                            result = rreg(rd)-rreg(ra);
                            flag_c <= (rreg(rd)>=rreg(ra)); set_zn(result);
                            flag_v <= (rd_value[31]^ra_value[31]) & (rd_value[31]^result[31]);
                            state <= S_FETCH;
                        end
                        OP_TEST: begin result=rreg(rd)&rreg(ra); set_zn(result); state<=S_FETCH; end

                        OP_JMP: begin pc<=ext_word; state<=S_FETCH; end
                        OP_BR: begin if(rd>11) fault(8'd2); else begin if(cond_true(rd)) pc<=ext_word; state<=S_FETCH; end end
                        OP_CALL: begin
                            if(call_sp==256) fault(8'd3);
                            else begin call_stack[call_sp[7:0]]<=pc; call_sp<=call_sp+1'b1; pc<=ext_word; state<=S_FETCH; end
                        end
                        OP_RET, OP_IRET: begin
                            if(call_sp==0) fault(8'd4);
                            else begin call_sp<=call_sp-1'b1; pc<=call_stack[(call_sp-1'b1)&8'hff]; state<=S_FETCH; end
                        end
                        OP_PUSH: begin if(data_sp==256) fault(8'd5); else begin data_stack[data_sp[7:0]]<=rreg(rd); data_sp<=data_sp+1'b1; state<=S_FETCH; end end
                        OP_POP: begin if(data_sp==0) fault(8'd6); else begin data_sp<=data_sp-1'b1; regs[rd]<=data_stack[(data_sp-1'b1)&8'hff]; state<=S_FETCH; end end

                        OP_LDB,OP_LDH,OP_LDW: begin
                            effective_addr <= rreg(ra)+{{20{imm12[11]}},imm12};
                            load_dest <= rd;
                            mem_size <= (opcode==OP_LDB)?0:(opcode==OP_LDH)?1:2;
                            result=rreg(ra)+{{20{imm12[11]}},imm12};
                            if(!data_address_ok(result,(opcode==OP_LDB)?1:(opcode==OP_LDH)?2:4)) fault(8'd8);
                            else if((opcode==OP_LDH && result[0])||(opcode==OP_LDW && |result[1:0])) fault(8'd7);
                            else state <= S_LOAD;
                        end
                        OP_STB,OP_STH,OP_STW: begin
                            effective_addr <= rreg(ra)+{{20{imm12[11]}},imm12};
                            store_value <= rreg(rb);
                            mem_size <= (opcode==OP_STB)?0:(opcode==OP_STH)?1:2;
                            result=rreg(ra)+{{20{imm12[11]}},imm12};
                            if(!data_address_ok(result,(opcode==OP_STB)?1:(opcode==OP_STH)?2:4)) fault(8'd8);
                            else if((opcode==OP_STH && result[0])||(opcode==OP_STW && |result[1:0])) fault(8'd7);
                            else state <= S_STORE;
                        end
                        OP_LEA: begin regs[rd]<=rreg(ra)+{{20{imm12[11]}},imm12}; state<=S_FETCH; end

                        OP_SYS: state <= S_SYS;
                        OP_FENCE: state <= S_FETCH;
                        OP_CFLUSH: begin cache_flush<=1; state<=S_SYS; end
                        OP_RDTIME: begin regs[rd]<=time_counter[31:0]; time_snapshot_hi<=time_counter[63:32]; state<=S_FETCH; end
                        OP_RDPMC: begin
                            case(ir[11:0])
                                12'd0: begin regs[rd]<=cycle_counter[31:0];cycle_snapshot_hi<=cycle_counter[63:32];end
                                12'd1: begin regs[rd]<=retired_counter[31:0];retired_snapshot_hi<=retired_counter[63:32];end
                                12'd2: regs[rd] <= cycle_snapshot_hi;
                                12'd3: regs[rd] <= retired_snapshot_hi;
                                12'd4: begin regs[rd]<=time_counter[31:0];time_snapshot_hi<=time_counter[63:32];end
                                12'd5: regs[rd] <= time_snapshot_hi;
                                12'd32: regs[rd] <= pc;
                                12'd33: regs[rd] <= ir;
                                12'd34: regs[rd] <= {27'd0,debug_flags};
                                12'd35: regs[rd] <= {24'd0,fault_code};
                                12'd36: regs[rd] <= {26'd0,round_counter};
                                default: regs[rd] <= (ir[11:0]>=16 && ir[11:0]<32) ? rreg(ir[3:0]) : 0;
                            endcase
                            state<=S_FETCH;
                        end

                        OP_CH: begin regs[rd]<=f_ch(rreg(ra),rreg(rb),rreg(ir[3:0])); state<=S_FETCH; end
                        OP_MAJ: begin regs[rd]<=f_maj(rreg(ra),rreg(rb),rreg(ir[3:0])); state<=S_FETCH; end
                        OP_BSIG0: begin regs[rd]<=f_bsig0(rreg(ra)); state<=S_FETCH; end
                        OP_BSIG1: begin regs[rd]<=f_bsig1(rreg(ra)); state<=S_FETCH; end
                        OP_SSIG0: begin regs[rd]<=f_ssig0(rreg(ra)); state<=S_FETCH; end
                        OP_SSIG1: begin regs[rd]<=f_ssig1(rreg(ra)); state<=S_FETCH; end
                        OP_SHAROUND: begin
                            // Architected round ABI: r1..r8=A..H, r9=W, r10=K.
                            t1_dbg = rreg(8)+f_bsig1(rreg(5))+f_ch(rreg(5),rreg(6),rreg(7))+rreg(9)+rreg(10);
                            t2_dbg = f_bsig0(rreg(1))+f_maj(rreg(1),rreg(2),rreg(3));
                            regs[8]<=rreg(7); regs[7]<=rreg(6); regs[6]<=rreg(5); regs[5]<=rreg(4)+t1_dbg;
                            regs[4]<=rreg(3); regs[3]<=rreg(2); regs[2]<=rreg(1); regs[1]<=t1_dbg+t2_dbg;
                            round_counter<=round_counter+1'b1;
                            state<=S_FETCH;
                        end

                        OP_SHA256: begin
                            sha_src_ptr<=rreg(ra); sha_dst_ptr<=rreg(rd); sha_word_index<=0;
                            if(|ra_value[1:0] || |rd_value[1:0]) fault(8'd7);
                            else if(ra_value>32'h000fffc0 || rd_value>32'h000fffe0) fault(8'd8);
                            else state<=S_SHA64_LOAD;
                        end
                        OP_DSHA256: begin
                            sha_src_ptr<=rreg(ra); sha_dst_ptr<=rreg(rd); sha_word_index<=0;
                            if(|ra_value[1:0] || |rd_value[1:0]) fault(8'd7);
                            else if(ra_value>32'h000fffb0 || rd_value>32'h000fffe0) fault(8'd8);
                            else state<=S_DSHA_LOAD;
                        end
                        OP_HASHCMP: begin
                            sha_src_ptr<=rreg(rd); sha_dst_ptr<=rreg(ra); hash_word_index<=0;
                            if(|ra_value[1:0] || |rd_value[1:0]) fault(8'd7);
                            else if(ra_value>32'h000fffe0 || rd_value>32'h000fffe0) fault(8'd8);
                            else state<=S_HASH_LOAD_HASH;
                        end
                        OP_INCNONCE: begin sha_src_ptr<=rreg(rd); if(|rd_value[1:0]) fault(8'd7); else if(rd_value>32'h000fffb0) fault(8'd8); else state<=S_NONCE_READ; end
                        default: fault(8'd1);
                    endcase
                end

                S_LOAD: if(mem_ready) begin
                    case(mem_size)
                        0: regs[load_dest] <= (mem_rdata >> (8*effective_addr[1:0])) & 32'hff;
                        1: regs[load_dest] <= (mem_rdata >> (8*effective_addr[1:0])) & 32'hffff;
                        default: regs[load_dest] <= mem_rdata;
                    endcase
                    state <= S_FETCH;
                end
                S_STORE: if(mem_ready) state <= S_FETCH;

                S_SYS: if(sys_ready) begin
                    if(opcode!=OP_CFLUSH) begin
                        regs[1] <= sys_ret[31:0];
                        regs[2] <= sys_ret[63:32];
                    end
                    if(sys_jump_valid)
                        pc <= sys_jump_pc;
                    state <= S_FETCH;
                end

                S_SHA64_LOAD: if(mem_ready) begin
                    sha64_message[511-sha_word_index*32 -: 32] <= bswap32(mem_rdata);
                    if(sha_word_index==15) begin sha_word_index<=0; state<=S_SHA64_START; end
                    else sha_word_index<=sha_word_index+1'b1;
                end
                S_SHA64_START: begin sha64_start<=1; state<=S_SHA64_WAIT; end
                S_SHA64_WAIT: if(sha64_done || sha64_complete) begin sha_word_index<=0; state<=S_SHA64_WRITE; end
                S_SHA64_WRITE: if(mem_ready) begin
                    if(sha_word_index==7) begin sha_word_index<=0; state<=S_FETCH; end
                    else sha_word_index<=sha_word_index+1'b1;
                end

                S_DSHA_LOAD: if(mem_ready) begin
                    dsha_header[639-sha_word_index*32 -: 32] <= bswap32(mem_rdata);
                    if(sha_word_index==19) begin sha_word_index<=0; state<=S_DSHA_START; end
                    else sha_word_index<=sha_word_index+1'b1;
                end
                S_DSHA_START: begin dsha_start<=1; state<=S_DSHA_WAIT; end
                S_DSHA_WAIT: if(dsha_done || dsha_complete) begin sha_word_index<=0; state<=S_DSHA_WRITE; end
                S_DSHA_WRITE: if(mem_ready) begin
                    if(sha_word_index==7) begin sha_word_index<=0; state<=S_FETCH; end
                    else sha_word_index<=sha_word_index+1'b1;
                end

                S_HASH_LOAD_HASH: if(mem_ready) begin
                    hash_buf[255-hash_word_index*32 -: 32] <= bswap32(mem_rdata);
                    if(hash_word_index==7) begin hash_word_index<=0; state<=S_HASH_LOAD_TARGET; end
                    else hash_word_index<=hash_word_index+1'b1;
                end
                S_HASH_LOAD_TARGET: if(mem_ready) begin
                    target_buf[255-hash_word_index*32 -: 32] <= bswap32(mem_rdata);
                    if(hash_word_index==7) begin hash_word_index<=0; state<=S_HASH_COMPARE; end
                    else hash_word_index<=hash_word_index+1'b1;
                end
                S_HASH_COMPARE: begin flag_valid <= (hash_buf <= target_buf); state<=S_FETCH; end

                S_NONCE_READ: if(mem_ready) begin nonce_word<=mem_rdata; state<=S_NONCE_WRITE; end
                S_NONCE_WRITE: if(mem_ready) state<=S_FETCH;

                S_HALTED: begin halted<=1; end
                default: state<=S_FETCH;
            endcase
        end
    end
endmodule
