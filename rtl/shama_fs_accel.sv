module shama_fs_accel(
    input  logic         clk,
    input  logic         rst,

    input  logic         req_valid,
    input  logic [11:0]  req_id,
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
    input  logic [31:0]  dma_rdata,

    output logic [31:0]  flash_used_bytes,
    output logic         mounted
);
    localparam [11:0]
        SYS_BOOT_MOUNT    = 12'h008,
        SYS_FILE_CREATE   = 12'h020,
        SYS_FILE_OPEN     = 12'h021,
        SYS_FILE_CLOSE    = 12'h022,
        SYS_FILE_READ     = 12'h023,
        SYS_FILE_WRITE    = 12'h024,
        SYS_FILE_TRUNCATE = 12'h025,
        SYS_FILE_RENAME   = 12'h026,
        SYS_FILE_DELETE   = 12'h027,
        SYS_FILE_STAT     = 12'h028,
        SYS_FILE_LIST     = 12'h029,
        SYS_FLASH_USAGE   = 12'h02a,
        SYS_MINER_LOG_RESULT   = 12'h050,
        SYS_MINER_SAVE_STATE   = 12'h051,
        SYS_MINER_LOAD_HISTORY = 12'h052;

    localparam integer BLOCK_SIZE = 256;
    localparam integer BLOCK_COUNT = 16384;
    localparam integer BITMAP_WORDS = 512;
    localparam integer MAX_FILES = 64;
    localparam integer NAME_BYTES = 43;
    localparam integer TABLE_WORDS_PER_ENTRY = 16;
    localparam integer BITMAP_FLASH_OFFSET = 256;
    localparam integer TABLE_FLASH_OFFSET = 2304;
    localparam integer DATA_START_BLOCK = 25;
    localparam integer MINER_HISTORY_ENTRY = 13;
    localparam integer MINER_STATE_ENTRY = 14;

    typedef enum logic [6:0] {
        ST_IDLE,
        ST_MOUNT_BITMAP,
        ST_MOUNT_DONE,

        ST_NAME_READ,
        ST_SEARCH_LOAD,
        ST_SEARCH_CHECK,

        ST_ENTRY_LOAD,
        ST_ENTRY_DISPATCH,

        ST_CREATE_PREP,
        ST_RENAME_NAME_READ,

        ST_ALLOC_READ,
        ST_ALLOC_SCAN,
        ST_ALLOC_MARK_READ,
        ST_ALLOC_MARK_WRITE,

        ST_FREE_READ,
        ST_FREE_WRITE,

        ST_COPY_RAM_READ,
        ST_COPY_FLASH_WRITE,
        ST_COPY_FLASH_READ,
        ST_COPY_RAM_WRITE,

        ST_LIST_WRITE,

        ST_ENTRY_FLUSH,
        ST_ENTRY_FLUSH_DONE,

        ST_DONE,
        ST_ERROR
    } state_t;

    typedef enum logic [4:0] {
        ACT_NONE,
        ACT_OPEN,
        ACT_CREATE,
        ACT_READ,
        ACT_WRITE,
        ACT_TRUNCATE,
        ACT_RENAME,
        ACT_DELETE,
        ACT_STAT,
        ACT_LIST,
        ACT_MINER_LOG,
        ACT_MINER_SAVE,
        ACT_MINER_LOAD
    } action_t;

    state_t state;
    action_t action;
    action_t after_flush_action;

    logic [11:0] op;
    logic [31:0] a1,a2,a3,a4,a5,a6;
    logic [63:0] response;

    logic [31:0] used_blocks;
    logic [9:0] mount_bitmap_word;

    // Only one file-table entry is cached at a time.  The persistent 4 MiB
    // flash remains authoritative, avoiding tens of thousands of inferred
    // metadata flip-flops in the physically synthesized accelerator.
    logic entry_used;
    logic [7:0] entry_type;
    logic [15:0] entry_flags;
    logic [31:0] entry_size;
    logic [31:0] entry_start;
    logic [31:0] entry_blocks;
    logic [31:0] entry_generation;
    logic [7:0] entry_name_len;
    logic [7:0] entry_name [0:NAME_BYTES-1];

    logic [6:0] entry_index;
    logic [4:0] entry_word;
    logic [6:0] search_index;
    logic [6:0] first_free_index;
    logic first_free_valid;

    logic [7:0] query_name [0:NAME_BYTES-1];
    logic [7:0] query_len;
    logic [31:0] query_ptr;

    logic [31:0] copy_src;
    logic [31:0] copy_dst;
    logic [31:0] copy_count;
    logic [31:0] copy_index;
    logic [7:0] copy_byte;
    logic copy_src_flash;
    logic copy_dst_flash;

    logic [31:0] old_start;
    logic [31:0] old_blocks;

    logic [31:0] alloc_needed;
    logic [31:0] alloc_word_index;
    logic [5:0] alloc_bit_index;
    logic [31:0] alloc_word_cache;
    logic [31:0] alloc_run_start;
    logic [31:0] alloc_run_len;
    logic [31:0] alloc_start;
    logic [31:0] alloc_mark_offset;
    logic [31:0] bitmap_modify_word;
    logic [5:0] bitmap_modify_bit;

    logic [31:0] free_start;
    logic [31:0] free_count;
    logic [31:0] free_offset;

    logic [7:0] list_name_index;

    integer i;
    integer n;
    integer base_byte;
    logic [7:0] selected_byte;
    logic [31:0] absolute_block;
    logic [31:0] bitmap_word_value;

    function automatic [5:0] popcount32(input logic [31:0] x);
        integer k;
        begin
            popcount32=0;
            for(k=0;k<32;k=k+1)
                popcount32=popcount32+x[k];
        end
    endfunction

    function automatic logic names_equal(input logic _live);
        integer k;
        logic ok;
        begin
            ok=entry_used && (entry_name_len==query_len);
            for(k=0;k<NAME_BYTES;k=k+1)
                if(k<query_len && entry_name[k]!=query_name[k])
                    ok=1'b0;
            names_equal=ok;
        end
    endfunction

    function automatic [31:0] encode_entry_word(input integer word_index);
        integer b;
        integer k;
        logic [31:0] value;
        begin
            value=0;
            case(word_index)
                0: value={entry_flags,entry_type,7'd0,entry_used};
                1: value=entry_size;
                2: value=entry_start;
                3: value=entry_blocks;
                4: value=entry_generation;
                default: begin
                    b=(word_index*4)-21;
                    for(k=0;k<4;k=k+1) begin
                        if(word_index==5 && k==0)
                            value[k*8 +: 8]=entry_name_len;
                        else if((b+k)>=0 && (b+k)<NAME_BYTES)
                            value[k*8 +: 8]=entry_name[b+k];
                    end
                end
            endcase
            encode_entry_word=value;
        end
    endfunction

    task automatic decode_entry_word(
        input integer word_index,
        input logic [31:0] value
    );
        integer b;
        integer k;
        begin
            case(word_index)
                0: begin
                    entry_used<=value[0];
                    entry_type<=value[15:8];
                    entry_flags<=value[31:16];
                end
                1: entry_size<=value;
                2: entry_start<=value;
                3: entry_blocks<=value;
                4: entry_generation<=value;
                default: begin
                    b=(word_index*4)-21;
                    for(k=0;k<4;k=k+1) begin
                        if(word_index==5 && k==0)
                            entry_name_len<=value[7:0];
                        else if((b+k)>=0 && (b+k)<NAME_BYTES)
                            entry_name[b+k]<=value[k*8 +: 8];
                    end
                end
            endcase
        end
    endtask

    task automatic clear_entry;
        integer k;
        begin
            entry_used<=0;
            entry_type<=0;
            entry_flags<=0;
            entry_size<=0;
            entry_start<=0;
            entry_blocks<=0;
            entry_generation<=0;
            entry_name_len<=0;
            for(k=0;k<NAME_BYTES;k=k+1)
                entry_name[k]<=0;
        end
    endtask

    task automatic begin_entry_load(
        input [6:0] idx,
        input action_t next_action
    );
        begin
            entry_index<=idx;
            entry_word<=0;
            action<=next_action;
            state<=ST_ENTRY_LOAD;
        end
    endtask

    task automatic begin_free(
        input [31:0] start_block,
        input [31:0] count_blocks,
        input action_t next_action
    );
        begin
            free_start<=start_block;
            free_count<=count_blocks;
            free_offset<=0;
            action<=next_action;
            if(count_blocks==0)
                state<=ST_ENTRY_DISPATCH;
            else
                state<=ST_FREE_READ;
        end
    endtask

    always_comb begin
        req_ready=(state==ST_DONE || state==ST_ERROR);
        req_ret=response;
        flash_used_bytes=used_blocks<<8;

        dma_valid=1'b0;
        dma_we=1'b0;
        dma_flash=1'b0;
        dma_addr=0;
        dma_wdata=0;
        dma_wstrb=4'b1111;

        selected_byte = 0;
        absolute_block = 0;
        bitmap_word_value = 0;

        case(state)
            ST_MOUNT_BITMAP: begin
                dma_valid=1;
                dma_flash=1;
                dma_addr=BITMAP_FLASH_OFFSET+(mount_bitmap_word<<2);
            end

            ST_NAME_READ, ST_RENAME_NAME_READ: begin
                dma_valid=1;
                dma_flash=0;
                dma_addr=(query_ptr+query_len)&32'hfffffffc;
                case((query_ptr+query_len)&3)
                    0:selected_byte=dma_rdata[7:0];
                    1:selected_byte=dma_rdata[15:8];
                    2:selected_byte=dma_rdata[23:16];
                    default:selected_byte=dma_rdata[31:24];
                endcase
            end

            ST_SEARCH_LOAD, ST_ENTRY_LOAD: begin
                dma_valid=1;
                dma_flash=1;
                dma_addr=TABLE_FLASH_OFFSET+(entry_index<<6)+(entry_word<<2);
            end

            ST_ALLOC_READ: begin
                dma_valid=1;
                dma_flash=1;
                dma_addr=BITMAP_FLASH_OFFSET+(alloc_word_index<<2);
            end

            ST_ALLOC_MARK_READ: begin
                absolute_block=alloc_start+alloc_mark_offset;
                dma_valid=1;
                dma_flash=1;
                dma_addr=BITMAP_FLASH_OFFSET+((absolute_block>>5)<<2);
            end

            ST_ALLOC_MARK_WRITE: begin
                absolute_block=alloc_start+alloc_mark_offset;
                dma_valid=1;
                dma_we=1;
                dma_flash=1;
                dma_addr=BITMAP_FLASH_OFFSET+((absolute_block>>5)<<2);
                dma_wdata=bitmap_modify_word | (32'h1<<bitmap_modify_bit);
            end

            ST_FREE_READ: begin
                absolute_block=free_start+free_offset;
                dma_valid=1;
                dma_flash=1;
                dma_addr=BITMAP_FLASH_OFFSET+((absolute_block>>5)<<2);
            end

            ST_FREE_WRITE: begin
                absolute_block=free_start+free_offset;
                dma_valid=1;
                dma_we=1;
                dma_flash=1;
                dma_addr=BITMAP_FLASH_OFFSET+((absolute_block>>5)<<2);
                dma_wdata=bitmap_modify_word & ~(32'h1<<bitmap_modify_bit);
            end

            ST_COPY_RAM_READ: begin
                dma_valid=1;
                dma_flash=copy_src_flash;
                dma_addr=(copy_src+copy_index)&32'hfffffffc;
                case((copy_src+copy_index)&3)
                    0:selected_byte=dma_rdata[7:0];
                    1:selected_byte=dma_rdata[15:8];
                    2:selected_byte=dma_rdata[23:16];
                    default:selected_byte=dma_rdata[31:24];
                endcase
            end

            ST_COPY_FLASH_WRITE: begin
                dma_valid=1;
                dma_we=1;
                dma_flash=copy_dst_flash;
                dma_addr=copy_dst+copy_index;
                case((copy_dst+copy_index)&3)
                    0:begin dma_wstrb=4'b0001;dma_wdata={24'd0,copy_byte};end
                    1:begin dma_wstrb=4'b0010;dma_wdata={16'd0,copy_byte,8'd0};end
                    2:begin dma_wstrb=4'b0100;dma_wdata={8'd0,copy_byte,16'd0};end
                    default:begin dma_wstrb=4'b1000;dma_wdata={copy_byte,24'd0};end
                endcase
            end

            ST_COPY_FLASH_READ: begin
                dma_valid=1;
                dma_flash=copy_src_flash;
                dma_addr=(copy_src+copy_index)&32'hfffffffc;
                case((copy_src+copy_index)&3)
                    0:selected_byte=dma_rdata[7:0];
                    1:selected_byte=dma_rdata[15:8];
                    2:selected_byte=dma_rdata[23:16];
                    default:selected_byte=dma_rdata[31:24];
                endcase
            end

            ST_COPY_RAM_WRITE: begin
                dma_valid=1;
                dma_we=1;
                dma_flash=copy_dst_flash;
                dma_addr=copy_dst+copy_index;
                case((copy_dst+copy_index)&3)
                    0:begin dma_wstrb=4'b0001;dma_wdata={24'd0,copy_byte};end
                    1:begin dma_wstrb=4'b0010;dma_wdata={16'd0,copy_byte,8'd0};end
                    2:begin dma_wstrb=4'b0100;dma_wdata={8'd0,copy_byte,16'd0};end
                    default:begin dma_wstrb=4'b1000;dma_wdata={copy_byte,24'd0};end
                endcase
            end

            ST_LIST_WRITE: begin
                dma_valid=1;
                dma_we=1;
                dma_flash=0;
                dma_addr=a2+list_name_index;
                case((a2+list_name_index)&3)
                    0:begin dma_wstrb=4'b0001;dma_wdata={24'd0,entry_name[list_name_index]};end
                    1:begin dma_wstrb=4'b0010;dma_wdata={16'd0,entry_name[list_name_index],8'd0};end
                    2:begin dma_wstrb=4'b0100;dma_wdata={8'd0,entry_name[list_name_index],16'd0};end
                    default:begin dma_wstrb=4'b1000;dma_wdata={entry_name[list_name_index],24'd0};end
                endcase
            end

            ST_ENTRY_FLUSH: begin
                dma_valid=1;
                dma_we=1;
                dma_flash=1;
                dma_addr=TABLE_FLASH_OFFSET+(entry_index<<6)+(entry_word<<2);
                dma_wdata=encode_entry_word(entry_word);
            end

            default: begin end
        endcase
    end

    always_ff @(posedge clk) begin
        if(rst) begin
            state<=ST_IDLE;
            action<=ACT_NONE;
            after_flush_action<=ACT_NONE;
            mounted<=0;
            response<=0;
            op<=0;
            a1<=0;a2<=0;a3<=0;a4<=0;a5<=0;a6<=0;
            used_blocks<=0;
            mount_bitmap_word<=0;
            entry_index<=0;
            entry_word<=0;
            search_index<=0;
            first_free_index<=0;
            first_free_valid<=0;
            query_len<=0;
            query_ptr<=0;
            copy_src<=0;copy_dst<=0;copy_count<=0;copy_index<=0;copy_byte<=0;
            copy_src_flash<=0;copy_dst_flash<=0;
            old_start<=0;old_blocks<=0;
            alloc_needed<=0;alloc_word_index<=0;alloc_bit_index<=0;
            alloc_word_cache<=0;alloc_run_start<=0;alloc_run_len<=0;
            alloc_start<=0;alloc_mark_offset<=0;bitmap_modify_word<=0;bitmap_modify_bit<=0;
            free_start<=0;free_count<=0;free_offset<=0;
            list_name_index<=0;
            clear_entry();
            for(i=0;i<NAME_BYTES;i=i+1)
                query_name[i]<=0;
        end else begin
            if((state==ST_DONE || state==ST_ERROR) && !req_valid)
                state<=ST_IDLE;

            case(state)
                ST_IDLE: if(req_valid) begin
                    op<=req_id;
                    a1<=req_args[31:0];
                    a2<=req_args[63:32];
                    a3<=req_args[95:64];
                    a4<=req_args[127:96];
                    a5<=req_args[159:128];
                    a6<=req_args[191:160];
                    response<=0;

                    case(req_id)
                        SYS_BOOT_MOUNT: begin
                            mount_bitmap_word<=0;
                            used_blocks<=0;
                            state<=ST_MOUNT_BITMAP;
                        end

                        SYS_FLASH_USAGE: begin
                            response<={32'd4194304,flash_used_bytes};
                            state<=ST_DONE;
                        end

                        SYS_FILE_CLOSE: begin
                            response<=0;
                            state<=ST_DONE;
                        end

                        SYS_FILE_CREATE, SYS_FILE_OPEN: begin
                            query_ptr<=req_args[31:0];
                            query_len<=0;
                            for(i=0;i<NAME_BYTES;i=i+1)
                                query_name[i]<=0;
                            action<=(req_id==SYS_FILE_CREATE)?ACT_CREATE:ACT_OPEN;
                            state<=ST_NAME_READ;
                        end

                        SYS_FILE_READ,SYS_FILE_WRITE,SYS_FILE_TRUNCATE,
                        SYS_FILE_RENAME,SYS_FILE_DELETE,SYS_FILE_STAT: begin
                            if(req_args[31:0]==0 || req_args[31:0]>MAX_FILES) begin
                                response<=64'hffffffffffffffff;
                                state<=ST_ERROR;
                            end else begin
                                entry_index<=req_args[6:0]-1'b1;
                                entry_word<=0;
                                case(req_id)
                                    SYS_FILE_READ: action<=ACT_READ;
                                    SYS_FILE_WRITE: action<=ACT_WRITE;
                                    SYS_FILE_TRUNCATE: action<=ACT_TRUNCATE;
                                    SYS_FILE_RENAME: action<=ACT_RENAME;
                                    SYS_FILE_DELETE: action<=ACT_DELETE;
                                    default: action<=ACT_STAT;
                                endcase
                                state<=ST_ENTRY_LOAD;
                            end
                        end

                        SYS_FILE_LIST: begin
                            if(req_args[31:0]>=MAX_FILES) begin
                                response<=0;
                                state<=ST_DONE;
                            end else begin
                                entry_index<=req_args[6:0];
                                entry_word<=0;
                                action<=ACT_LIST;
                                state<=ST_ENTRY_LOAD;
                            end
                        end

                        SYS_MINER_LOG_RESULT: begin
                            entry_index<=MINER_HISTORY_ENTRY;
                            entry_word<=0;
                            action<=ACT_MINER_LOG;
                            state<=ST_ENTRY_LOAD;
                        end

                        SYS_MINER_SAVE_STATE: begin
                            entry_index<=MINER_STATE_ENTRY;
                            entry_word<=0;
                            action<=ACT_MINER_SAVE;
                            state<=ST_ENTRY_LOAD;
                        end

                        SYS_MINER_LOAD_HISTORY: begin
                            entry_index<=MINER_HISTORY_ENTRY;
                            entry_word<=0;
                            action<=ACT_MINER_LOAD;
                            state<=ST_ENTRY_LOAD;
                        end

                        default: begin
                            response<=64'hffffffffffffffff;
                            state<=ST_ERROR;
                        end
                    endcase
                end

                ST_MOUNT_BITMAP: if(dma_ready) begin
                    used_blocks<=used_blocks+popcount32(dma_rdata);
                    if(mount_bitmap_word==BITMAP_WORDS-1)
                        state<=ST_MOUNT_DONE;
                    else
                        mount_bitmap_word<=mount_bitmap_word+1'b1;
                end

                ST_MOUNT_DONE: begin
                    mounted<=1;
                    response<=used_blocks<<8;
                    state<=ST_DONE;
                end

                ST_NAME_READ: if(dma_ready) begin
                    if(selected_byte==0 || query_len==NAME_BYTES) begin
                        search_index<=0;
                        first_free_index<=0;
                        first_free_valid<=0;
                        entry_index<=0;
                        entry_word<=0;
                        state<=ST_SEARCH_LOAD;
                    end else begin
                        query_name[query_len]<=selected_byte;
                        query_len<=query_len+1'b1;
                    end
                end

                ST_SEARCH_LOAD: if(dma_ready) begin
                    decode_entry_word(entry_word,dma_rdata);
                    if(entry_word==TABLE_WORDS_PER_ENTRY-1) begin
                        entry_word<=0;
                        state<=ST_SEARCH_CHECK;
                    end else
                        entry_word<=entry_word+1'b1;
                end

                ST_SEARCH_CHECK: begin
                    if(!entry_used && !first_free_valid) begin
                        first_free_valid<=1;
                        first_free_index<=entry_index;
                    end

                    if(names_equal(1'b1)) begin
                        if(action==ACT_OPEN) begin
                            response<={32'd0,25'd0,entry_index+1'b1};
                            state<=ST_DONE;
                        end else begin
                            // CREATE is intentionally exclusive: Editor "New"
                            // must never overwrite/open an existing file by accident.
                            response<=64'hffffffffffffffff;
                            state<=ST_ERROR;
                        end
                    end else if(search_index==MAX_FILES-1) begin
                        if(action==ACT_CREATE && (first_free_valid || !entry_used)) begin
                            entry_index<=first_free_valid ? first_free_index : entry_index;
                            state<=ST_CREATE_PREP;
                        end else begin
                            response<=64'hffffffffffffffff;
                            state<=ST_ERROR;
                        end
                    end else begin
                        search_index<=search_index+1'b1;
                        entry_index<=entry_index+1'b1;
                        entry_word<=0;
                        clear_entry();
                        state<=ST_SEARCH_LOAD;
                    end
                end

                ST_CREATE_PREP: begin
                    clear_entry();
                    entry_used<=1;
                    entry_type<=a2[7:0];
                    entry_generation<=1;
                    entry_name_len<=query_len;
                    for(i=0;i<NAME_BYTES;i=i+1)
                        if(i<query_len)
                            entry_name[i]<=query_name[i];
                    entry_word<=0;
                    after_flush_action<=ACT_CREATE;
                    state<=ST_ENTRY_FLUSH;
                end

                ST_ENTRY_LOAD: if(dma_ready) begin
                    decode_entry_word(entry_word,dma_rdata);
                    if(entry_word==TABLE_WORDS_PER_ENTRY-1) begin
                        entry_word<=0;
                        state<=ST_ENTRY_DISPATCH;
                    end else
                        entry_word<=entry_word+1'b1;
                end

                ST_ENTRY_DISPATCH: begin
                    if(!entry_used && action==ACT_LIST) begin
                        response<=0;
                        state<=ST_DONE;
                    end else if(!entry_used && action!=ACT_NONE) begin
                        response<=64'hffffffffffffffff;
                        state<=ST_ERROR;
                    end else begin
                        case(action)
                            ACT_READ: begin
                                copy_src<=entry_start*BLOCK_SIZE;
                                copy_dst<=a2;
                                copy_count<=(a3<entry_size)?a3:entry_size;
                                copy_index<=0;
                                copy_src_flash<=1;
                                copy_dst_flash<=0;
                                if(((a3<entry_size)?a3:entry_size)==0) begin
                                    response<=0;
                                    state<=ST_DONE;
                                end else
                                    state<=ST_COPY_FLASH_READ;
                            end

                            ACT_WRITE: begin
                                old_start<=entry_start;
                                old_blocks<=entry_blocks;
                                alloc_needed<=(a3+BLOCK_SIZE-1)>>8;
                                alloc_word_index<=DATA_START_BLOCK>>5;
                                alloc_bit_index<=DATA_START_BLOCK&31;
                                alloc_run_start<=0;
                                alloc_run_len<=0;
                                alloc_start<=0;
                                alloc_mark_offset<=0;
                                if(a3==0) begin
                                    entry_size<=0;
                                    entry_start<=0;
                                    entry_blocks<=0;
                                    entry_generation<=entry_generation+1'b1;
                                    free_start<=entry_start;
                                    free_count<=entry_blocks;
                                    free_offset<=0;
                                    after_flush_action<=ACT_WRITE;
                                    entry_word<=0;
                                    state<=ST_ENTRY_FLUSH;
                                end else
                                    state<=ST_ALLOC_READ;
                            end

                            ACT_TRUNCATE: begin
                                if(a2!=0) begin
                                    response<=64'hfffffffffffffffe;
                                    state<=ST_ERROR;
                                end else begin
                                    old_start<=entry_start;
                                    old_blocks<=entry_blocks;
                                    entry_size<=0;
                                    entry_start<=0;
                                    entry_blocks<=0;
                                    entry_generation<=entry_generation+1'b1;
                                    entry_word<=0;
                                    after_flush_action<=ACT_TRUNCATE;
                                    state<=ST_ENTRY_FLUSH;
                                end
                            end

                            ACT_RENAME: begin
                                query_ptr<=a2;
                                query_len<=0;
                                for(i=0;i<NAME_BYTES;i=i+1)
                                    query_name[i]<=0;
                                state<=ST_RENAME_NAME_READ;
                            end

                            ACT_DELETE: begin
                                old_start<=entry_start;
                                old_blocks<=entry_blocks;
                                clear_entry();
                                entry_word<=0;
                                after_flush_action<=ACT_DELETE;
                                state<=ST_ENTRY_FLUSH;
                            end

                            ACT_STAT: begin
                                response<={entry_size,entry_start[15:0],entry_name_len,entry_type};
                                state<=ST_DONE;
                            end

                            ACT_LIST: begin
                                list_name_index<=0;
                                if(entry_name_len==0) begin
                                    response<={entry_size,entry_start[15:0],entry_name_len,entry_type};
                                    state<=ST_DONE;
                                end else
                                    state<=ST_LIST_WRITE;
                            end

                            ACT_MINER_LOG: begin
                                copy_src<=a1;
                                copy_dst<=entry_start*BLOCK_SIZE+((a3[7:0])<<6);
                                copy_count<=(a2>64)?64:a2;
                                copy_index<=0;
                                copy_src_flash<=0;
                                copy_dst_flash<=1;
                                if(((a2>64)?64:a2)==0) begin response<=0;state<=ST_DONE;end
                                else state<=ST_COPY_RAM_READ;
                            end

                            ACT_MINER_SAVE: begin
                                copy_src<=a1;
                                copy_dst<=entry_start*BLOCK_SIZE;
                                copy_count<=(a2>256)?256:a2;
                                copy_index<=0;
                                copy_src_flash<=0;
                                copy_dst_flash<=1;
                                if(((a2>256)?256:a2)==0) begin response<=0;state<=ST_DONE;end
                                else state<=ST_COPY_RAM_READ;
                            end

                            ACT_MINER_LOAD: begin
                                copy_src<=entry_start*BLOCK_SIZE+((a1[7:0])<<6);
                                copy_dst<=a2;
                                copy_count<=(a3>64)?64:a3;
                                copy_index<=0;
                                copy_src_flash<=1;
                                copy_dst_flash<=0;
                                if(((a3>64)?64:a3)==0) begin response<=0;state<=ST_DONE;end
                                else state<=ST_COPY_FLASH_READ;
                            end

                            default: begin
                                response<=64'hffffffffffffffff;
                                state<=ST_ERROR;
                            end
                        endcase
                    end
                end

                ST_RENAME_NAME_READ: if(dma_ready) begin
                    if(selected_byte==0 || query_len==NAME_BYTES) begin
                        entry_name_len<=query_len;
                        for(i=0;i<NAME_BYTES;i=i+1)
                            entry_name[i]<=query_name[i];
                        entry_generation<=entry_generation+1'b1;
                        entry_word<=0;
                        after_flush_action<=ACT_RENAME;
                        state<=ST_ENTRY_FLUSH;
                    end else begin
                        query_name[query_len]<=selected_byte;
                        query_len<=query_len+1'b1;
                    end
                end

                ST_ALLOC_READ: if(dma_ready) begin
                    alloc_word_cache<=dma_rdata;
                    state<=ST_ALLOC_SCAN;
                end

                ST_ALLOC_SCAN: begin
                    absolute_block=(alloc_word_index<<5)+alloc_bit_index;
                    if(absolute_block>=BLOCK_COUNT) begin
                        response<=64'hfffffffffffffffd;
                        state<=ST_ERROR;
                    end else if(
                        absolute_block>=DATA_START_BLOCK &&
                        !alloc_word_cache[alloc_bit_index] &&
                        alloc_run_len+1>=alloc_needed
                    ) begin
                        alloc_start<=(alloc_run_len==0)?absolute_block:alloc_run_start;
                        alloc_mark_offset<=0;
                        state<=ST_ALLOC_MARK_READ;
                    end else begin
                        if(absolute_block<DATA_START_BLOCK || alloc_word_cache[alloc_bit_index]) begin
                            alloc_run_len<=0;
                        end else begin
                            if(alloc_run_len==0)
                                alloc_run_start<=absolute_block;
                            alloc_run_len<=alloc_run_len+1'b1;
                        end

                        if(alloc_bit_index==31) begin
                            alloc_bit_index<=0;
                            alloc_word_index<=alloc_word_index+1'b1;
                            state<=ST_ALLOC_READ;
                        end else begin
                            alloc_bit_index<=alloc_bit_index+1'b1;
                            state<=ST_ALLOC_SCAN;
                        end
                    end
                end

                ST_ALLOC_MARK_READ: if(dma_ready) begin
                    absolute_block=alloc_start+alloc_mark_offset;
                    bitmap_modify_word<=dma_rdata;
                    bitmap_modify_bit<=absolute_block&31;
                    state<=ST_ALLOC_MARK_WRITE;
                end

                ST_ALLOC_MARK_WRITE: if(dma_ready) begin
                    used_blocks<=used_blocks+1'b1;
                    if(alloc_mark_offset+1>=alloc_needed) begin
                        entry_start<=alloc_start;
                        entry_blocks<=alloc_needed;
                        entry_size<=a3;
                        entry_generation<=entry_generation+1'b1;
                        copy_src<=a2;
                        copy_dst<=alloc_start*BLOCK_SIZE;
                        copy_count<=a3;
                        copy_index<=0;
                        copy_src_flash<=0;
                        copy_dst_flash<=1;
                        state<=ST_COPY_RAM_READ;
                    end else begin
                        alloc_mark_offset<=alloc_mark_offset+1'b1;
                        state<=ST_ALLOC_MARK_READ;
                    end
                end

                ST_FREE_READ: if(dma_ready) begin
                    absolute_block=free_start+free_offset;
                    bitmap_modify_word<=dma_rdata;
                    bitmap_modify_bit<=absolute_block&31;
                    state<=ST_FREE_WRITE;
                end

                ST_FREE_WRITE: if(dma_ready) begin
                    if(bitmap_modify_word[bitmap_modify_bit] && used_blocks!=0)
                        used_blocks<=used_blocks-1'b1;
                    if(free_offset+1>=free_count) begin
                        response<=0;
                        state<=ST_DONE;
                    end else begin
                        free_offset<=free_offset+1'b1;
                        state<=ST_FREE_READ;
                    end
                end

                ST_COPY_RAM_READ: if(dma_ready) begin
                    copy_byte<=selected_byte;
                    state<=ST_COPY_FLASH_WRITE;
                end

                ST_COPY_FLASH_WRITE: if(dma_ready) begin
                    if(copy_index+1>=copy_count) begin
                        if(action==ACT_WRITE) begin
                            entry_word<=0;
                            after_flush_action<=ACT_WRITE;
                            state<=ST_ENTRY_FLUSH;
                        end else begin
                            response<=copy_count;
                            state<=ST_DONE;
                        end
                    end else begin
                        copy_index<=copy_index+1'b1;
                        state<=ST_COPY_RAM_READ;
                    end
                end

                ST_COPY_FLASH_READ: if(dma_ready) begin
                    copy_byte<=selected_byte;
                    state<=ST_COPY_RAM_WRITE;
                end

                ST_COPY_RAM_WRITE: if(dma_ready) begin
                    if(copy_index+1>=copy_count) begin
                        response<=copy_count;
                        state<=ST_DONE;
                    end else begin
                        copy_index<=copy_index+1'b1;
                        state<=ST_COPY_FLASH_READ;
                    end
                end

                ST_LIST_WRITE: if(dma_ready) begin
                    if(list_name_index+1>=entry_name_len) begin
                        response<={entry_size,entry_start[15:0],entry_name_len,entry_type};
                        state<=ST_DONE;
                    end else
                        list_name_index<=list_name_index+1'b1;
                end

                ST_ENTRY_FLUSH: if(dma_ready) begin
                    if(entry_word==TABLE_WORDS_PER_ENTRY-1) begin
                        entry_word<=0;
                        state<=ST_ENTRY_FLUSH_DONE;
                    end else
                        entry_word<=entry_word+1'b1;
                end

                ST_ENTRY_FLUSH_DONE: begin
                    case(after_flush_action)
                        ACT_CREATE: begin
                            response<={32'd0,25'd0,entry_index+1'b1};
                            state<=ST_DONE;
                        end
                        ACT_RENAME: begin
                            response<=0;
                            state<=ST_DONE;
                        end
                        ACT_DELETE: begin
                            if(old_blocks==0) begin
                                response<=0;
                                state<=ST_DONE;
                            end else begin
                                free_start<=old_start;
                                free_count<=old_blocks;
                                free_offset<=0;
                                action<=ACT_DELETE;
                                state<=ST_FREE_READ;
                            end
                        end
                        ACT_TRUNCATE: begin
                            if(old_blocks==0) begin
                                response<=0;
                                state<=ST_DONE;
                            end else begin
                                free_start<=old_start;
                                free_count<=old_blocks;
                                free_offset<=0;
                                action<=ACT_TRUNCATE;
                                state<=ST_FREE_READ;
                            end
                        end
                        ACT_WRITE: begin
                            if(old_blocks==0) begin
                                response<=entry_size;
                                state<=ST_DONE;
                            end else begin
                                free_start<=old_start;
                                free_count<=old_blocks;
                                free_offset<=0;
                                action<=ACT_WRITE;
                                state<=ST_FREE_READ;
                            end
                        end
                        default: begin
                            response<=0;
                            state<=ST_DONE;
                        end
                    endcase
                end

                ST_DONE: begin end
                ST_ERROR: begin end
                default: state<=ST_IDLE;
            endcase
        end
    end
endmodule
