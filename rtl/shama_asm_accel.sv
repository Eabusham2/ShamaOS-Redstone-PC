module shama_asm_accel(
    input  logic         clk,
    input  logic         rst,

    input  logic         req_valid,
    input  logic [191:0] req_args,
    output logic         req_ready,
    output logic [63:0]  req_ret,

    output logic         dma_valid,
    output logic         dma_we,
    output logic         dma_flash,
    output logic [31:0]  dma_addr,
    output logic [31:0]  dma_wdata,
    output logic [3:0]   dma_wstrb,
    input  logic         dma_ready,
    input  logic [31:0]  dma_rdata
);
    localparam integer MAX_SOURCE=4096;
    localparam integer MAX_TOKENS=1024;
    localparam integer MAX_LINES=512;
    localparam integer MAX_SYMBOLS=128;

    localparam [3:0]
        FMT_NONE=0,FMT_R=1,FMT_RR=2,FMT_RRR=3,FMT_RRRR=4,
        FMT_RI32=5,FMT_J32=6,FMT_BR32=7,FMT_MEM=8,FMT_SYS=9,FMT_RSYS=10;

    localparam [31:0] HASH_DEFINE=32'h3b4f3492;

    typedef enum logic [5:0] {
        ST_IDLE,ST_LOAD_SRC,
        ST_TOK_SCAN,ST_TOK_ACCUM,ST_TOK_COMMENT,
        ST_PASS1,ST_PASS2,
        ST_WRITE0,ST_WRITE1,
        ST_DONE,ST_ERROR
    } state_t;
    state_t state;

    logic [7:0] source [0:MAX_SOURCE-1];
    logic [11:0] tok_start [0:MAX_TOKENS-1];
    logic [7:0] tok_len [0:MAX_TOKENS-1];
    logic [31:0] tok_hash [0:MAX_TOKENS-1];
    logic [8:0] tok_line [0:MAX_TOKENS-1];
    logic [9:0] line_first [0:MAX_LINES-1];
    logic [5:0] line_tokens [0:MAX_LINES-1];

    logic [31:0] label_hash [0:MAX_SYMBOLS-1];
    logic [31:0] label_value [0:MAX_SYMBOLS-1];
    logic [31:0] def_hash [0:MAX_SYMBOLS-1];
    logic [31:0] def_value [0:MAX_SYMBOLS-1];

    logic [31:0] src_ptr,src_len,dst_ptr,dst_capacity;
    logic [31:0] src_index;
    logic [11:0] scan_pos,token_begin;
    logic [7:0] token_length;
    logic [31:0] hash_work;
    logic [8:0] current_line;
    logic [9:0] token_count;
    logic [9:0] total_lines;

    logic [8:0] line_iter;
    logic [31:0] pc_words;
    logic [7:0] label_count,def_count;

    logic [31:0] out_bytes;
    logic [31:0] emit0,emit1;
    logic emit_two;
    logic [31:0] error_code,error_line;

    integer i,j;
    integer first_idx,mn_idx,arg_count;
    logic [18:0] dec;
    logic [31:0] v1,v2,v3,v4;
    logic [3:0] r1,r2,r3,r4;
    logic args_valid;

    function automatic [7:0] up(input [7:0] c);
        if(c>="a" && c<="z") up=c-8'd32;
        else up=c;
    endfunction

    function automatic logic is_space(input [7:0] c);
        is_space=(c==" "||c==8'h09||c==8'h0d||c==",");
    endfunction

    function automatic logic is_digit(input [7:0] c);
        is_digit=(c>="0"&&c<="9");
    endfunction

    function automatic logic token_is_reg(input integer t);
        integer st,ln;
        begin
            st=tok_start[t];ln=tok_len[t];
            token_is_reg=(ln>=2 && ln<=3 && up(source[st])=="R" && is_digit(source[st+1]));
        end
    endfunction

    function automatic [3:0] token_reg(input integer t);
        integer st,ln,val,k;
        begin
            st=tok_start[t];ln=tok_len[t];val=0;
            for(k=1;k<3;k=k+1)
                if(k<ln && is_digit(source[st+k]))
                    val=val*10+(source[st+k]-"0");
            token_reg=val[3:0];
        end
    endfunction

    function automatic logic token_is_number(input integer t);
        integer st,ln;
        begin
            st=tok_start[t];ln=tok_len[t];
            token_is_number=(ln>0) &&
                (is_digit(source[st]) || source[st]=="-" || source[st]=="+");
        end
    endfunction

    function automatic [31:0] token_number(input integer t);
        integer st,ln,k,base,digit,pos;
        logic neg;
        logic [31:0] value;
        logic [7:0] c;
        begin
            st=tok_start[t];ln=tok_len[t];pos=0;neg=0;value=0;base=10;
            if(source[st]=="-") begin neg=1;pos=1;end
            else if(source[st]=="+") pos=1;
            if(pos+1<ln && source[st+pos]=="0" &&
               (up(source[st+pos+1])=="X" || up(source[st+pos+1])=="B")) begin
                base=(up(source[st+pos+1])=="X")?16:2;
                pos=pos+2;
            end
            for(k=0;k<32;k=k+1) begin
                if(k>=pos && k<ln) begin
                    c=up(source[st+k]);
                    if(c>="0"&&c<="9") digit=c-"0";
                    else if(c>="A"&&c<="F") digit=10+c-"A";
                    else digit=0;
                    value=value*base+digit;
                end
            end
            token_number=neg ? (~value+1'b1) : value;
        end
    endfunction

    function automatic logic label_exists(input [31:0] h);
        integer k;
        begin
            label_exists=0;
            for(k=0;k<MAX_SYMBOLS;k=k+1)
                if(k<label_count && label_hash[k]==h) label_exists=1;
        end
    endfunction

    function automatic [31:0] label_lookup(input [31:0] h);
        integer k;
        begin
            label_lookup=0;
            for(k=0;k<MAX_SYMBOLS;k=k+1)
                if(k<label_count && label_hash[k]==h) label_lookup=label_value[k];
        end
    endfunction

    function automatic logic def_exists(input [31:0] h);
        integer k;
        begin
            def_exists=0;
            for(k=0;k<MAX_SYMBOLS;k=k+1)
                if(k<def_count && def_hash[k]==h) def_exists=1;
        end
    endfunction

    function automatic [31:0] def_lookup(input [31:0] h);
        integer k;
        begin
            def_lookup=0;
            for(k=0;k<MAX_SYMBOLS;k=k+1)
                if(k<def_count && def_hash[k]==h) def_lookup=def_value[k];
        end
    endfunction

    function automatic [31:0] syscall_value(input [31:0] h);
        begin
            case(h)
                32'h1a25d85b: syscall_value=32'h00000001; // SYS_EXIT
                32'h641043a8: syscall_value=32'h00000002; // SYS_YIELD
                32'hf794ae3a: syscall_value=32'h00000003; // SYS_GET_EVENT
                32'h2a41aac7: syscall_value=32'h00000004; // SYS_GET_TIME
                32'hff50555e: syscall_value=32'h00000005; // SYS_GET_COUNTER
                32'h29e47bec: syscall_value=32'h00000006; // SYS_APP_LAUNCH
                32'hb0b016b5: syscall_value=32'h00000007; // SYS_APP_EXIT_FOREGROUND
                32'h5b39b9e5: syscall_value=32'h00000008; // SYS_BOOT_MOUNT
                32'h7feb842b: syscall_value=32'h00000009; // SYS_BOOT_LOAD_OS
                32'hb93159d1: syscall_value=32'h0000000a; // SYS_APP_EVENT
                32'h675886b0: syscall_value=32'h00000010; // SYS_ALLOC
                32'hb6c23fd1: syscall_value=32'h00000011; // SYS_FREE
                32'hdb0fe20d: syscall_value=32'h00000012; // SYS_RAM_USAGE
                32'h824631a7: syscall_value=32'h00000013; // SYS_CACHE_USAGE
                32'hfe2d959e: syscall_value=32'h00000014; // SYS_CACHE_FLUSH_APP
                32'h6655e96a: syscall_value=32'h00000020; // SYS_FILE_CREATE
                32'h3c6dcb0e: syscall_value=32'h00000021; // SYS_FILE_OPEN
                32'h665b689a: syscall_value=32'h00000022; // SYS_FILE_CLOSE
                32'h3f885ec6: syscall_value=32'h00000023; // SYS_FILE_READ
                32'hff319d29: syscall_value=32'h00000024; // SYS_FILE_WRITE
                32'h78ec08d4: syscall_value=32'h00000025; // SYS_FILE_TRUNCATE
                32'h12a4ca48: syscall_value=32'h00000026; // SYS_FILE_RENAME
                32'h93c8dc59: syscall_value=32'h00000027; // SYS_FILE_DELETE
                32'h373912a4: syscall_value=32'h00000028; // SYS_FILE_STAT
                32'h91b7091a: syscall_value=32'h00000029; // SYS_FILE_LIST
                32'h8871dcab: syscall_value=32'h0000002a; // SYS_FLASH_USAGE
                32'h747b8b55: syscall_value=32'h00000030; // SYS_GET_KEY
                32'h8b87a5ce: syscall_value=32'h00000031; // SYS_GET_CONTROLLER
                32'h35ddd32c: syscall_value=32'h00000040; // SYS_GPU_SUBMIT
                32'hb7983119: syscall_value=32'h00000041; // SYS_DRAW_TEXT
                32'ha1423ec0: syscall_value=32'h00000042; // SYS_DRAW_RECT
                32'h3315a628: syscall_value=32'h00000043; // SYS_MESSAGE_BOX
                32'ha6aa9e7d: syscall_value=32'h00000044; // SYS_GPU_FENCE
                32'h7b469ded: syscall_value=32'h00000045; // SYS_UI_REDRAW
                32'h0cbaf039: syscall_value=32'h00000046; // SYS_UI_STATUSBAR
                32'h36bb730d: syscall_value=32'h00000047; // SYS_UI_FILE_LIST
                32'h24158d1d: syscall_value=32'h00000048; // SYS_UI_EDITOR_VIEW
                32'h571c9393: syscall_value=32'h00000049; // SYS_UI_MINER_VIEW
                32'h9c291c7c: syscall_value=32'h0000004a; // SYS_UI_MONITOR_VIEW
                32'hfa43f96a: syscall_value=32'h0000004b; // SYS_UI_TERMINAL_VIEW
                32'hdf51ec26: syscall_value=32'h0000004c; // SYS_UI_PAINT_VIEW
                32'hdfb1ad49: syscall_value=32'h0000004d; // SYS_UI_SETTINGS_VIEW
                32'h7f3612cd: syscall_value=32'h00000050; // SYS_MINER_LOG_RESULT
                32'h967ebac4: syscall_value=32'h00000051; // SYS_MINER_SAVE_STATE
                32'h55e4c614: syscall_value=32'h00000052; // SYS_MINER_LOAD_HISTORY
                32'h17e68ca3: syscall_value=32'h00000060; // SYS_ASSEMBLE
                default: syscall_value=32'hffffffff;
            endcase
        end
    endfunction

    function automatic logic value_exists(input integer t);
        logic [31:0] sv;
        begin
            sv=syscall_value(tok_hash[t]);
            value_exists=token_is_number(t) || label_exists(tok_hash[t]) ||
                         def_exists(tok_hash[t]) || (sv!=32'hffffffff);
        end
    endfunction

    function automatic [31:0] resolve_value(input integer t);
        logic [31:0] sv;
        begin
            sv=syscall_value(tok_hash[t]);
            if(token_is_number(t)) resolve_value=token_number(t);
            else if(label_exists(tok_hash[t])) resolve_value=label_lookup(tok_hash[t]);
            else if(def_exists(tok_hash[t])) resolve_value=def_lookup(tok_hash[t]);
            else if(sv!=32'hffffffff) resolve_value=sv;
            else resolve_value=0;
        end
    endfunction

    function automatic [18:0] decode_info(input [31:0] h);
        begin
            case(h)
                32'ha7ee415e: decode_info={1'b1,8'h00,4'd0,2'd1,4'd0}; // NOP
                32'h73a4c96f: decode_info={1'b1,8'h01,4'd0,2'd1,4'd0}; // HLT
                32'h5653dd99: decode_info={1'b1,8'h02,4'd2,2'd1,4'd0}; // MOV
                32'hdc47cc9c: decode_info={1'b1,8'h03,4'd5,2'd2,4'd0}; // LDI
                32'h986c3269: decode_info={1'b1,8'h04,4'd5,2'd2,4'd0}; // LUI
                32'h7d7558d4: decode_info={1'b1,8'h10,4'd3,2'd1,4'd0}; // ADD
                32'h7c755741: decode_info={1'b1,8'h11,4'd3,2'd1,4'd0}; // ADC
                32'h1f285035: decode_info={1'b1,8'h12,4'd3,2'd1,4'd0}; // SUB
                32'h35f01bd9: decode_info={1'b1,8'h13,4'd3,2'd1,4'd0}; // SBC
                32'h703c01a1: decode_info={1'b1,8'h14,4'd3,2'd1,4'd0}; // MUL
                32'h6a19bf68: decode_info={1'b1,8'h15,4'd3,2'd1,4'd0}; // DIV
                32'h6453f3a3: decode_info={1'b1,8'h16,4'd3,2'd1,4'd0}; // MOD
                32'hbad4c761: decode_info={1'b1,8'h17,4'd2,2'd1,4'd0}; // NEG
                32'hebc3b367: decode_info={1'b1,8'h18,4'd1,2'd1,4'd0}; // INC
                32'h470efaf3: decode_info={1'b1,8'h19,4'd1,2'd1,4'd0}; // DEC
                32'h91666dc6: decode_info={1'b1,8'h20,4'd3,2'd1,4'd0}; // AND
                32'h7ce4aa04: decode_info={1'b1,8'h21,4'd3,2'd1,4'd0}; // OR
                32'h4f46575e: decode_info={1'b1,8'h22,4'd3,2'd1,4'd0}; // XOR
                32'ha5ee3e38: decode_info={1'b1,8'h23,4'd3,2'd1,4'd0}; // NOR
                32'h81bd12ba: decode_info={1'b1,8'h24,4'd3,2'd1,4'd0}; // NAND
                32'h4376183a: decode_info={1'b1,8'h25,4'd3,2'd1,4'd0}; // XNOR
                32'habee47aa: decode_info={1'b1,8'h26,4'd2,2'd1,4'd0}; // NOT
                32'h230802d6: decode_info={1'b1,8'h27,4'd3,2'd1,4'd0}; // SHL
                32'h1d07f964: decode_info={1'b1,8'h28,4'd3,2'd1,4'd0}; // SHR
                32'h16f6a6d1: decode_info={1'b1,8'h29,4'd3,2'd1,4'd0}; // SAR
                32'h6fe81066: decode_info={1'b1,8'h2a,4'd3,2'd1,4'd0}; // ROL
                32'h89e83954: decode_info={1'b1,8'h2b,4'd3,2'd1,4'd0}; // ROR
                32'h8d6cf347: decode_info={1'b1,8'h30,4'd2,2'd1,4'd0}; // CMP
                32'hb2d739e5: decode_info={1'b1,8'h31,4'd2,2'd1,4'd0}; // TEST
                32'h48850ae0: decode_info={1'b1,8'h40,4'd6,2'd2,4'd0}; // JMP
                32'h2edd7375: decode_info={1'b1,8'h41,4'd7,2'd2,4'd0}; // BR
                32'hb14354e9: decode_info={1'b1,8'h42,4'd6,2'd2,4'd0}; // CALL
                32'h73ce7ecc: decode_info={1'b1,8'h43,4'd0,2'd1,4'd0}; // RET
                32'h33a4171d: decode_info={1'b1,8'h44,4'd1,2'd1,4'd0}; // PUSH
                32'h95e87c30: decode_info={1'b1,8'h45,4'd1,2'd1,4'd0}; // POP
                32'he747dded: decode_info={1'b1,8'h50,4'd8,2'd1,4'd0}; // LDB
                32'hdd47ce2f: decode_info={1'b1,8'h51,4'd8,2'd1,4'd0}; // LDH
                32'hd247bcde: decode_info={1'b1,8'h52,4'd8,2'd1,4'd0}; // LDW
                32'hf525cf80: decode_info={1'b1,8'h53,4'd8,2'd1,4'd0}; // STB
                32'hff25df3e: decode_info={1'b1,8'h54,4'd8,2'd1,4'd0}; // STH
                32'h0a25f08f: decode_info={1'b1,8'h55,4'd8,2'd1,4'd0}; // STW
                32'hc04561f1: decode_info={1'b1,8'h56,4'd8,2'd1,4'd0}; // LEA
                32'h46331af6: decode_info={1'b1,8'h60,4'd9,2'd1,4'd0}; // SYS
                32'h2ef25f43: decode_info={1'b1,8'h61,4'd0,2'd1,4'd0}; // IRET
                32'h9f365476: decode_info={1'b1,8'h62,4'd0,2'd1,4'd0}; // FENCE
                32'hf79e7d64: decode_info={1'b1,8'h63,4'd0,2'd1,4'd0}; // CFLUSH
                32'h6208173a: decode_info={1'b1,8'h64,4'd1,2'd1,4'd0}; // RDTIME
                32'he373b69d: decode_info={1'b1,8'h65,4'd10,2'd1,4'd0}; // RDPMC
                32'h7eda1fce: decode_info={1'b1,8'h70,4'd4,2'd1,4'd0}; // CH
                32'h2a59a883: decode_info={1'b1,8'h71,4'd4,2'd1,4'd0}; // MAJ
                32'h3ea827f6: decode_info={1'b1,8'h72,4'd2,2'd1,4'd0}; // BSIG0
                32'h3fa82989: decode_info={1'b1,8'h73,4'd2,2'd1,4'd0}; // BSIG1
                32'ha8367d31: decode_info={1'b1,8'h74,4'd2,2'd1,4'd0}; // SSIG0
                32'ha7367b9e: decode_info={1'b1,8'h75,4'd2,2'd1,4'd0}; // SSIG1
                32'hc2d2e393: decode_info={1'b1,8'h76,4'd0,2'd1,4'd0}; // SHAROUND
                32'hb0b2f99a: decode_info={1'b1,8'h77,4'd2,2'd1,4'd0}; // SHA256
                32'hb65afe44: decode_info={1'b1,8'h78,4'd2,2'd1,4'd0}; // DSHA256
                32'h22f949c3: decode_info={1'b1,8'h79,4'd2,2'd1,4'd0}; // HASHCMP
                32'ha563fc0e: decode_info={1'b1,8'h7a,4'd1,2'd1,4'd0}; // INCNONCE
                32'h314e4d5f: decode_info={1'b1,8'h42,4'd6,2'd2,4'd0}; // CAL
                32'hbf2bc878: decode_info={1'b1,8'h52,4'd8,2'd1,4'd0}; // LOD
                32'h0525e8b0: decode_info={1'b1,8'h55,4'd8,2'd1,4'd0}; // STR
                32'h8bb69fae: decode_info={1'b1,8'h28,4'd3,2'd1,4'd0}; // RSH
                32'h02b3f7a7: decode_info={1'b1,8'h41,4'd7,2'd2,4'd0}; // BR.EQ
                32'h0ecfe108: decode_info={1'b1,8'h41,4'd7,2'd2,4'd1}; // BR.NE
                32'heb834b26: decode_info={1'b1,8'h41,4'd7,2'd2,4'd2}; // BR.C
                32'h14cfea7a: decode_info={1'b1,8'h41,4'd7,2'd2,4'd3}; // BR.NC
                32'h33ca0b19: decode_info={1'b1,8'h41,4'd7,2'd2,4'd4}; // BR.LT
                32'h42ca22b6: decode_info={1'b1,8'h41,4'd7,2'd2,4'd5}; // BR.LE
                32'h31b8bed2: decode_info={1'b1,8'h41,4'd7,2'd2,4'd6}; // BR.GT
                32'h42b8d995: decode_info={1'b1,8'h41,4'd7,2'd2,4'd7}; // BR.GE
                32'ha03faf5d: decode_info={1'b1,8'h41,4'd7,2'd2,4'd8}; // BR.NEG
                32'hbcf1dc95: decode_info={1'b1,8'h41,4'd7,2'd2,4'd9}; // BR.POS
                32'h24af496d: decode_info={1'b1,8'h41,4'd7,2'd2,4'd10}; // BR.VALID
                32'hc08f7079: decode_info={1'b1,8'h41,4'd7,2'd2,4'd11}; // BR.NVALID
                default: decode_info=19'd0;
            endcase
        end
    endfunction

    function automatic [31:0] enc_header(
        input [7:0] op,input [3:0] rd,input [3:0] ra,
        input [3:0] rb,input [11:0] imm
    );
        enc_header={op,rd,ra,rb,imm};
    endfunction

    always_comb begin
        req_ready=(state==ST_DONE || state==ST_ERROR);
        req_ret={error_code,out_bytes};

        dma_valid=0;dma_we=0;dma_flash=0;dma_addr=0;dma_wdata=0;dma_wstrb=4'b1111;

        if(state==ST_LOAD_SRC) begin
            dma_valid=1;
            dma_addr=(src_ptr+src_index)&32'hfffffffc;
        end else if(state==ST_WRITE0) begin
            dma_valid=1;dma_we=1;dma_addr=dst_ptr+out_bytes;dma_wdata=emit0;
        end else if(state==ST_WRITE1) begin
            dma_valid=1;dma_we=1;dma_addr=dst_ptr+out_bytes+4;dma_wdata=emit1;
        end
    end

    always_ff @(posedge clk) begin
        if(rst) begin
            state<=ST_IDLE;src_ptr<=0;src_len<=0;dst_ptr<=0;dst_capacity<=0;
            src_index<=0;scan_pos<=0;token_begin<=0;token_length<=0;hash_work<=0;
            current_line<=0;token_count<=0;total_lines<=0;
            line_iter<=0;pc_words<=0;label_count<=0;def_count<=0;
            out_bytes<=0;emit0<=0;emit1<=0;emit_two<=0;
            error_code<=0;error_line<=0;
            for(i=0;i<MAX_LINES;i=i+1) begin line_first[i]<=0;line_tokens[i]<=0;end
            for(i=0;i<MAX_SOURCE;i=i+1) source[i]<=0;
        end else begin
            if((state==ST_DONE || state==ST_ERROR) && !req_valid)
                state<=ST_IDLE;

            case(state)
                ST_IDLE: if(req_valid) begin
                    src_ptr<=req_args[31:0];src_len<=req_args[63:32];
                    dst_ptr<=req_args[95:64];dst_capacity<=req_args[127:96];
                    src_index<=0;out_bytes<=0;error_code<=0;error_line<=0;
                    token_count<=0;current_line<=0;total_lines<=1;
                    label_count<=0;def_count<=0;pc_words<=0;
                    for(i=0;i<MAX_LINES;i=i+1) begin line_first[i]<=0;line_tokens[i]<=0;end
                    if(req_args[63:32]>MAX_SOURCE || req_args[63:32]==0) begin
                        error_code<=1;state<=ST_ERROR;
                    end else state<=ST_LOAD_SRC;
                end

                ST_LOAD_SRC: if(dma_ready) begin
                    source[src_index]<=dma_rdata>>(((src_ptr+src_index)&3)*8);
                    if(src_index+1>=src_len) begin
                        scan_pos<=0;current_line<=0;total_lines<=1;state<=ST_TOK_SCAN;
                    end else src_index<=src_index+1;
                end

                ST_TOK_SCAN: begin
                    if(scan_pos>=src_len) begin
                        line_iter<=0;pc_words<=0;state<=ST_PASS1;
                    end else if(source[scan_pos]==8'h0a) begin
                        scan_pos<=scan_pos+1;
                        if(current_line<MAX_LINES-1) begin
                            current_line<=current_line+1;
                            total_lines<=current_line+2;
                        end
                    end else if(is_space(source[scan_pos])) begin
                        scan_pos<=scan_pos+1;
                    end else if(source[scan_pos]==";" ||
                               (source[scan_pos]=="/" && scan_pos+1<src_len && source[scan_pos+1]=="/")) begin
                        state<=ST_TOK_COMMENT;
                    end else begin
                        token_begin<=scan_pos;token_length<=0;
                        hash_work<=32'h811c9dc5;state<=ST_TOK_ACCUM;
                    end
                end

                ST_TOK_COMMENT: begin
                    if(scan_pos>=src_len) begin line_iter<=0;pc_words<=0;state<=ST_PASS1;end
                    else if(source[scan_pos]==8'h0a) state<=ST_TOK_SCAN;
                    else scan_pos<=scan_pos+1;
                end

                ST_TOK_ACCUM: begin
                    if(scan_pos>=src_len || source[scan_pos]==8'h0a ||
                       is_space(source[scan_pos]) || source[scan_pos]==";" ||
                       (source[scan_pos]=="/" && scan_pos+1<src_len && source[scan_pos+1]=="/")) begin
                        if(token_count>=MAX_TOKENS) begin
                            error_code<=2;error_line<=current_line+1;state<=ST_ERROR;
                        end else begin
                            tok_start[token_count]<=token_begin;
                            tok_len[token_count]<=token_length;
                            tok_hash[token_count]<=hash_work;
                            tok_line[token_count]<=current_line;
                            if(line_tokens[current_line]==0) line_first[current_line]<=token_count;
                            line_tokens[current_line]<=line_tokens[current_line]+1'b1;
                            token_count<=token_count+1'b1;
                            state<=ST_TOK_SCAN;
                        end
                    end else begin
                        hash_work<=(hash_work ^ up(source[scan_pos]))*32'h01000193;
                        token_length<=token_length+1'b1;
                        scan_pos<=scan_pos+1;
                    end
                end

                ST_PASS1: begin
                    if(line_iter>=total_lines) begin
                        line_iter<=0;out_bytes<=0;state<=ST_PASS2;
                    end else if(line_tokens[line_iter]==0) begin
                        line_iter<=line_iter+1'b1;
                    end else begin
                        first_idx=line_first[line_iter];
                        mn_idx=first_idx;

                        if(source[tok_start[mn_idx]]==".") begin
                            if(label_count>=MAX_SYMBOLS) begin
                                error_code<=3;error_line<=line_iter+1;state<=ST_ERROR;
                            end else begin
                                label_hash[label_count]<=tok_hash[mn_idx];
                                label_value[label_count]<=pc_words;
                                label_count<=label_count+1'b1;
                                mn_idx=mn_idx+1;
                            end
                        end

                        if(mn_idx>=first_idx+line_tokens[line_iter]) begin
                            line_iter<=line_iter+1'b1;
                        end else if(tok_hash[mn_idx]==HASH_DEFINE) begin
                            if(mn_idx+2>=first_idx+line_tokens[line_iter] || def_count>=MAX_SYMBOLS) begin
                                error_code<=4;error_line<=line_iter+1;state<=ST_ERROR;
                            end else begin
                                def_hash[def_count]<=tok_hash[mn_idx+1];
                                if(token_is_number(mn_idx+2))
                                    def_value[def_count]<=token_number(mn_idx+2);
                                else if(def_exists(tok_hash[mn_idx+2]))
                                    def_value[def_count]<=def_lookup(tok_hash[mn_idx+2]);
                                else
                                    def_value[def_count]<=0;
                                def_count<=def_count+1'b1;
                                line_iter<=line_iter+1'b1;
                            end
                        end else begin
                            dec=decode_info(tok_hash[mn_idx]);
                            if(!dec[18]) begin
                                error_code<=5;error_line<=line_iter+1;state<=ST_ERROR;
                            end else begin
                                pc_words<=pc_words+dec[5:4];
                                line_iter<=line_iter+1'b1;
                            end
                        end
                    end
                end

                ST_PASS2: begin
                    if(line_iter>=total_lines) begin
                        state<=ST_DONE;
                    end else if(line_tokens[line_iter]==0) begin
                        line_iter<=line_iter+1'b1;
                    end else begin
                        first_idx=line_first[line_iter];
                        mn_idx=first_idx;
                        if(source[tok_start[mn_idx]]==".") mn_idx=mn_idx+1;

                        if(mn_idx>=first_idx+line_tokens[line_iter] || tok_hash[mn_idx]==HASH_DEFINE) begin
                            line_iter<=line_iter+1'b1;
                        end else begin
                            dec=decode_info(tok_hash[mn_idx]);
                            arg_count=(first_idx+line_tokens[line_iter])-(mn_idx+1);
                            args_valid=dec[18];
                            v1=0;v2=0;v3=0;v4=0;r1=0;r2=0;r3=0;r4=0;

                            if(arg_count>0) begin
                                v1=resolve_value(mn_idx+1);
                                r1=token_reg(mn_idx+1);
                            end
                            if(arg_count>1) begin
                                v2=resolve_value(mn_idx+2);
                                r2=token_reg(mn_idx+2);
                            end
                            if(arg_count>2) begin
                                v3=resolve_value(mn_idx+3);
                                r3=token_reg(mn_idx+3);
                            end
                            if(arg_count>3) begin
                                v4=resolve_value(mn_idx+4);
                                r4=token_reg(mn_idx+4);
                            end

                            emit0=0;emit1=0;emit_two=(dec[5:4]==2);

                            case(dec[9:6])
                                FMT_NONE: begin
                                    args_valid=args_valid && arg_count==0;
                                    emit0=enc_header(dec[17:10],0,0,0,0);
                                end
                                FMT_R: begin
                                    args_valid=args_valid && arg_count==1 && token_is_reg(mn_idx+1);
                                    emit0=enc_header(dec[17:10],r1,0,0,0);
                                end
                                FMT_RR: begin
                                    args_valid=args_valid && arg_count==2 &&
                                               token_is_reg(mn_idx+1)&&token_is_reg(mn_idx+2);
                                    emit0=enc_header(dec[17:10],r1,r2,0,0);
                                end
                                FMT_RRR: begin
                                    args_valid=args_valid && arg_count==3 &&
                                               token_is_reg(mn_idx+1)&&token_is_reg(mn_idx+2)&&token_is_reg(mn_idx+3);
                                    emit0=enc_header(dec[17:10],r1,r2,r3,0);
                                end
                                FMT_RRRR: begin
                                    args_valid=args_valid && arg_count==4 &&
                                               token_is_reg(mn_idx+1)&&token_is_reg(mn_idx+2)&&
                                               token_is_reg(mn_idx+3)&&token_is_reg(mn_idx+4);
                                    emit0=enc_header(dec[17:10],r1,r2,r3,{8'd0,r4});
                                end
                                FMT_RI32: begin
                                    args_valid=args_valid && arg_count==2 &&
                                               token_is_reg(mn_idx+1)&&value_exists(mn_idx+2);
                                    emit0=enc_header(dec[17:10],r1,0,0,0);emit1=v2;
                                end
                                FMT_J32: begin
                                    args_valid=args_valid && arg_count==1 && value_exists(mn_idx+1);
                                    emit0=enc_header(dec[17:10],0,0,0,0);emit1=v1;
                                end
                                FMT_BR32: begin
                                    args_valid=args_valid && arg_count==1 && value_exists(mn_idx+1);
                                    emit0=enc_header(8'h41,dec[3:0],0,0,0);emit1=v1;
                                end
                                FMT_MEM: begin
                                    args_valid=args_valid && arg_count==3 &&
                                               token_is_reg(mn_idx+1)&&token_is_reg(mn_idx+2)&&value_exists(mn_idx+3);
                                    if(dec[17:10]>=8'h53 && dec[17:10]<=8'h55)
                                        emit0=enc_header(dec[17:10],0,r1,r2,v3[11:0]);
                                    else
                                        emit0=enc_header(dec[17:10],r1,r2,0,v3[11:0]);
                                end
                                FMT_SYS: begin
                                    args_valid=args_valid && arg_count==1 && value_exists(mn_idx+1);
                                    emit0=enc_header(dec[17:10],0,0,0,v1[11:0]);
                                end
                                FMT_RSYS: begin
                                    args_valid=args_valid && arg_count==2 &&
                                               token_is_reg(mn_idx+1)&&value_exists(mn_idx+2);
                                    emit0=enc_header(dec[17:10],r1,0,0,v2[11:0]);
                                end
                                default: args_valid=0;
                            endcase

                            if(!args_valid) begin
                                error_code<=6;error_line<=line_iter+1;state<=ST_ERROR;
                            end else if(out_bytes+(emit_two?8:4)>dst_capacity) begin
                                error_code<=7;error_line<=line_iter+1;state<=ST_ERROR;
                            end else begin
                                state<=ST_WRITE0;
                            end
                        end
                    end
                end

                ST_WRITE0: if(dma_ready) begin
                    if(emit_two) state<=ST_WRITE1;
                    else begin out_bytes<=out_bytes+4;line_iter<=line_iter+1'b1;state<=ST_PASS2;end
                end

                ST_WRITE1: if(dma_ready) begin
                    out_bytes<=out_bytes+8;line_iter<=line_iter+1'b1;state<=ST_PASS2;
                end

                ST_DONE: begin end
                ST_ERROR: begin end
                default: state<=ST_IDLE;
            endcase
        end
    end
endmodule
