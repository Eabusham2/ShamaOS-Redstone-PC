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
    // boot image list_files() sorts these fixed files into entries 13 and 14.
    localparam integer MINER_HISTORY_ENTRY = 13;
    localparam integer MINER_STATE_ENTRY = 14;

    typedef enum logic [6:0] {
        ST_IDLE,
        ST_MOUNT_BITMAP,
        ST_MOUNT_TABLE,
        ST_MOUNT_DONE,

        ST_NAME_READ,
        ST_NAME_BYTES,
        ST_SEARCH,
        ST_FIND_FREE_ENTRY,

        ST_CREATE_PREP,
        ST_OPEN_DONE,
        ST_CLOSE_DONE,
        ST_STAT_DONE,

        ST_RENAME_NAME_READ,
        ST_RENAME_NAME_BYTES,
        ST_RENAME_PREP,

        ST_DELETE_FREE,
        ST_TRUNCATE_FREE,

        ST_ALLOC_SCAN,
        ST_ALLOC_MARK,
        ST_FLUSH_BITMAP,
        ST_WRITE_RAM_READ,
        ST_WRITE_FLASH_BYTE,
        ST_WRITE_UPDATE_ENTRY,
        ST_WRITE_FREE_OLD,

        ST_READ_FLASH_BYTE,
        ST_READ_RAM_WRITE,

        ST_LIST_NAME_WRITE,

        ST_FLUSH_ENTRY,
        ST_DONE,
        ST_ERROR
    } state_t;

    state_t state;
    state_t after_name_state;
    state_t after_bitmap_state;
    state_t after_entry_state;

    // Cached ShamaFS metadata.
    logic file_used [0:MAX_FILES-1];
    logic [7:0] file_type [0:MAX_FILES-1];
    logic [15:0] file_flags [0:MAX_FILES-1];
    logic [31:0] file_size [0:MAX_FILES-1];
    logic [31:0] file_start [0:MAX_FILES-1];
    logic [31:0] file_blocks [0:MAX_FILES-1];
    logic [31:0] file_generation [0:MAX_FILES-1];
    logic [7:0] file_name_len [0:MAX_FILES-1];
    logic [7:0] file_name [0:MAX_FILES*NAME_BYTES-1];

    logic [31:0] bitmap [0:BITMAP_WORDS-1];
    logic [31:0] used_blocks;

    // Request context.
    logic [11:0] op;
    logic [31:0] a1,a2,a3,a4,a5,a6;
    logic [63:0] response;

    logic [7:0] query_name [0:NAME_BYTES-1];
    logic [7:0] query_len;
    logic [31:0] query_ptr;
    logic [31:0] name_word;
    logic [1:0] name_byte_index;

    logic [6:0] scan_entry;
    logic [6:0] target_entry;
    logic [6:0] flush_entry_index;
    logic [4:0] flush_entry_word;

    logic [9:0] mount_bitmap_word;
    logic [10:0] mount_table_word;

    logic [9:0] flush_bitmap_word;

    logic [31:0] alloc_needed;
    logic [31:0] alloc_cursor;
    logic [31:0] alloc_run_start;
    logic [31:0] alloc_run_len;
    logic [31:0] alloc_start;
    logic [31:0] alloc_mark_index;

    logic [31:0] old_start;
    logic [31:0] old_blocks;
    logic [31:0] free_index;

    logic [31:0] copy_index;
    logic [31:0] copy_count;
    logic [31:0] copy_src;
    logic [31:0] copy_dst;
    logic [7:0] copy_byte;
    logic direct_write;

    logic [7:0] list_name_index;

    integer i;
    integer j;

    function automatic [5:0] popcount32(input logic [31:0] x);
        integer k;
        begin
            popcount32=0;
            for(k=0;k<32;k=k+1)
                popcount32=popcount32+x[k];
        end
    endfunction

    function automatic logic bitmap_bit(input integer block);
        bitmap_bit = bitmap[block>>5][block&31];
    endfunction

    function automatic logic names_equal(input integer entry);
        integer k;
        logic ok;
        begin
            ok=file_used[entry] && file_name_len[entry]==query_len;
            for(k=0;k<NAME_BYTES;k=k+1)
                if(k<query_len && file_name[entry*NAME_BYTES+k]!=query_name[k])
                    ok=1'b0;
            names_equal=ok;
        end
    endfunction

    function automatic [31:0] encode_entry_word(
        input integer entry,
        input integer word_index
    );
        integer base_byte;
        integer n;
        logic [31:0] value;
        begin
            value=0;
            case(word_index)
                0: value={file_flags[entry],file_type[entry],7'd0,file_used[entry]};
                1: value=file_size[entry];
                2: value=file_start[entry];
                3: value=file_blocks[entry];
                4: value=file_generation[entry];
                default: begin
                    base_byte=(word_index*4)-21;
                    for(n=0;n<4;n=n+1) begin
                        if(word_index==5 && n==0)
                            value[n*8 +: 8]=file_name_len[entry];
                        else if((base_byte+n)>=0 && (base_byte+n)<NAME_BYTES)
                            value[n*8 +: 8]=file_name[entry*NAME_BYTES+base_byte+n];
                    end
                end
            endcase
            encode_entry_word=value;
        end
    endfunction

    task automatic decode_entry_word(
        input integer entry,
        input integer word_index,
        input logic [31:0] value
    );
        integer base_byte;
        integer n;
        begin
            case(word_index)
                0: begin
                    file_used[entry]<=value[0];
                    file_type[entry]<=value[15:8];
                    file_flags[entry]<=value[31:16];
                end
                1:file_size[entry]<=value;
                2:file_start[entry]<=value;
                3:file_blocks[entry]<=value;
                4:file_generation[entry]<=value;
                default: begin
                    base_byte=(word_index*4)-21;
                    for(n=0;n<4;n=n+1) begin
                        if(word_index==5 && n==0)
                            file_name_len[entry]<=value[7:0];
                        else if((base_byte+n)>=0 && (base_byte+n)<NAME_BYTES)
                            file_name[entry*NAME_BYTES+base_byte+n]<=value[n*8 +: 8];
                    end
                end
            endcase
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

        case(state)
            ST_MOUNT_BITMAP: begin
                dma_valid=1;dma_flash=1;
                dma_addr=BITMAP_FLASH_OFFSET+(mount_bitmap_word<<2);
            end
            ST_MOUNT_TABLE: begin
                dma_valid=1;dma_flash=1;
                dma_addr=TABLE_FLASH_OFFSET+(mount_table_word<<2);
            end
            ST_NAME_READ,ST_RENAME_NAME_READ: begin
                dma_valid=1;dma_flash=0;
                dma_addr=(query_ptr+query_len)&32'hfffffffc;
            end
            ST_FLUSH_BITMAP: begin
                dma_valid=1;dma_we=1;dma_flash=1;
                dma_addr=BITMAP_FLASH_OFFSET+(flush_bitmap_word<<2);
                dma_wdata=bitmap[flush_bitmap_word];
            end
            ST_FLUSH_ENTRY: begin
                dma_valid=1;dma_we=1;dma_flash=1;
                dma_addr=TABLE_FLASH_OFFSET+(flush_entry_index<<6)+(flush_entry_word<<2);
                dma_wdata=encode_entry_word(flush_entry_index,flush_entry_word);
            end
            ST_WRITE_RAM_READ: begin
                dma_valid=1;dma_flash=0;
                dma_addr=(copy_src+copy_index)&32'hfffffffc;
            end
            ST_WRITE_FLASH_BYTE: begin
                dma_valid=1;dma_we=1;dma_flash=1;
                dma_addr=copy_dst+copy_index;
                dma_wdata={4{copy_byte}};
                case((copy_dst+copy_index)&3)
                    0: begin dma_wstrb=4'b0001;dma_wdata={24'd0,copy_byte};end
                    1: begin dma_wstrb=4'b0010;dma_wdata={16'd0,copy_byte,8'd0};end
                    2: begin dma_wstrb=4'b0100;dma_wdata={8'd0,copy_byte,16'd0};end
                    default: begin dma_wstrb=4'b1000;dma_wdata={copy_byte,24'd0};end
                endcase
            end
            ST_READ_FLASH_BYTE: begin
                dma_valid=1;dma_flash=1;
                dma_addr=(copy_src+copy_index)&32'hfffffffc;
            end
            ST_READ_RAM_WRITE: begin
                dma_valid=1;dma_we=1;dma_flash=0;
                dma_addr=copy_dst+copy_index;
                case((copy_dst+copy_index)&3)
                    0: begin dma_wstrb=4'b0001;dma_wdata={24'd0,copy_byte};end
                    1: begin dma_wstrb=4'b0010;dma_wdata={16'd0,copy_byte,8'd0};end
                    2: begin dma_wstrb=4'b0100;dma_wdata={8'd0,copy_byte,16'd0};end
                    default: begin dma_wstrb=4'b1000;dma_wdata={copy_byte,24'd0};end
                endcase
            end
            ST_LIST_NAME_WRITE: begin
                dma_valid=1;dma_we=1;dma_flash=0;
                dma_addr=a2+list_name_index;
                case((a2+list_name_index)&3)
                    0: begin dma_wstrb=4'b0001;dma_wdata={24'd0,file_name[target_entry*NAME_BYTES+list_name_index]};end
                    1: begin dma_wstrb=4'b0010;dma_wdata={16'd0,file_name[target_entry*NAME_BYTES+list_name_index],8'd0};end
                    2: begin dma_wstrb=4'b0100;dma_wdata={8'd0,file_name[target_entry*NAME_BYTES+list_name_index],16'd0};end
                    default: begin dma_wstrb=4'b1000;dma_wdata={file_name[target_entry*NAME_BYTES+list_name_index],24'd0};end
                endcase
            end
            default: begin end
        endcase
    end

    always_ff @(posedge clk) begin
        if(rst) begin
            state<=ST_IDLE;mounted<=0;response<=0;op<=0;
            a1<=0;a2<=0;a3<=0;a4<=0;a5<=0;a6<=0;
            query_len<=0;query_ptr<=0;name_word<=0;name_byte_index<=0;
            scan_entry<=0;target_entry<=0;flush_entry_index<=0;flush_entry_word<=0;
            mount_bitmap_word<=0;mount_table_word<=0;flush_bitmap_word<=0;
            used_blocks<=0;
            alloc_needed<=0;alloc_cursor<=DATA_START_BLOCK;
            alloc_run_start<=0;alloc_run_len<=0;alloc_start<=0;alloc_mark_index<=0;
            old_start<=0;old_blocks<=0;free_index<=0;
            copy_index<=0;copy_count<=0;copy_src<=0;copy_dst<=0;copy_byte<=0;
            direct_write<=0;
            list_name_index<=0;
            for(i=0;i<MAX_FILES;i=i+1) begin
                file_used[i]<=0;file_type[i]<=0;file_flags[i]<=0;file_size[i]<=0;
                file_start[i]<=0;file_blocks[i]<=0;file_generation[i]<=0;file_name_len[i]<=0;
            end
            for(i=0;i<MAX_FILES*NAME_BYTES;i=i+1) file_name[i]<=0;
            for(i=0;i<BITMAP_WORDS;i=i+1) bitmap[i]<=0;
            for(i=0;i<NAME_BYTES;i=i+1) query_name[i]<=0;
        end else begin
            if((state==ST_DONE || state==ST_ERROR) && !req_valid)
                state<=ST_IDLE;

            case(state)
                ST_IDLE: if(req_valid) begin
                    op<=req_id;
                    a1<=req_args[31:0];a2<=req_args[63:32];a3<=req_args[95:64];
                    a4<=req_args[127:96];a5<=req_args[159:128];a6<=req_args[191:160];
                    response<=0;

                    case(req_id)
                        SYS_BOOT_MOUNT: begin
                            mount_bitmap_word<=0;used_blocks<=0;state<=ST_MOUNT_BITMAP;
                        end
                        SYS_FLASH_USAGE: begin
                            response<={32'd4194304,flash_used_bytes};state<=ST_DONE;
                        end
                        SYS_FILE_CLOSE: begin response<=0;state<=ST_DONE;end
                        SYS_FILE_READ,SYS_FILE_WRITE,SYS_FILE_TRUNCATE,SYS_FILE_RENAME,
                        SYS_FILE_DELETE,SYS_FILE_STAT: begin
                            if(req_args[31:0]==0 || req_args[31:0]>MAX_FILES) begin
                                response<=64'hffffffffffffffff;state<=ST_ERROR;
                            end else begin
                                target_entry<=req_args[31:0]-1;
                                if(req_id==SYS_FILE_READ) begin
                                    copy_index<=0;
                                    copy_count<=(req_args[95:64]<file_size[req_args[31:0]-1])?
                                                req_args[95:64]:file_size[req_args[31:0]-1];
                                    copy_src<=file_start[req_args[31:0]-1]*BLOCK_SIZE;
                                    copy_dst<=req_args[63:32];
                                    state<=ST_READ_FLASH_BYTE;
                                end else if(req_id==SYS_FILE_WRITE) begin
                                    old_start<=file_start[req_args[31:0]-1];
                                    old_blocks<=file_blocks[req_args[31:0]-1];
                                    free_index<=0;
                                    copy_count<=req_args[95:64];
                                    copy_src<=req_args[63:32];
                                    direct_write<=0;
                                    alloc_needed<=(req_args[95:64]+BLOCK_SIZE-1)>>8;
                                    alloc_cursor<=DATA_START_BLOCK;
                                    alloc_run_len<=0;
                                    state<=ST_ALLOC_SCAN;
                                end else if(req_id==SYS_FILE_RENAME) begin
                                    query_ptr<=req_args[63:32];query_len<=0;
                                    after_name_state<=ST_RENAME_PREP;
                                    state<=ST_RENAME_NAME_READ;
                                end else if(req_id==SYS_FILE_DELETE) begin
                                    old_start<=file_start[req_args[31:0]-1];
                                    old_blocks<=file_blocks[req_args[31:0]-1];
                                    free_index<=0;state<=ST_DELETE_FREE;
                                end else if(req_id==SYS_FILE_TRUNCATE) begin
                                    if(req_args[63:32]==0) begin
                                        old_start<=file_start[req_args[31:0]-1];
                                        old_blocks<=file_blocks[req_args[31:0]-1];
                                        free_index<=0;state<=ST_TRUNCATE_FREE;
                                    end else begin
                                        response<=64'hfffffffffffffffe;state<=ST_ERROR;
                                    end
                                end else begin
                                    response<={file_size[req_args[31:0]-1],
                                              file_start[req_args[31:0]-1][15:0],
                                              file_name_len[req_args[31:0]-1],
                                              file_type[req_args[31:0]-1]};
                                    state<=ST_DONE;
                                end
                            end
                        end
                        SYS_FILE_LIST: begin
                            if(req_args[31:0]>=MAX_FILES) begin response<=0;state<=ST_DONE;end
                            else begin
                                target_entry<=req_args[6:0];
                                list_name_index<=0;
                                if(file_used[req_args[6:0]]) state<=ST_LIST_NAME_WRITE;
                                else begin response<=0;state<=ST_DONE;end
                            end
                        end
                        SYS_FILE_CREATE,SYS_FILE_OPEN: begin
                            query_ptr<=req_args[31:0];query_len<=0;name_byte_index<=0;
                            after_name_state<=ST_SEARCH;
                            scan_entry<=0;
                            state<=ST_NAME_READ;
                        end

                        SYS_MINER_LOG_RESULT: begin
                            // a1 RAM record ptr, a2 byte length <=64,
                            // a3 ring slot 0..255.
                            copy_src<=req_args[31:0];
                            copy_count<=(req_args[63:32]>64)?64:req_args[63:32];
                            copy_dst<=file_start[MINER_HISTORY_ENTRY]*BLOCK_SIZE
                                     + ((req_args[71:64])<<6);
                            copy_index<=0;
                            direct_write<=1;
                            state<=ST_WRITE_RAM_READ;
                        end

                        SYS_MINER_SAVE_STATE: begin
                            // a1 RAM state ptr, a2 byte length <=256.
                            copy_src<=req_args[31:0];
                            copy_count<=(req_args[63:32]>256)?256:req_args[63:32];
                            copy_dst<=file_start[MINER_STATE_ENTRY]*BLOCK_SIZE;
                            copy_index<=0;
                            direct_write<=1;
                            state<=ST_WRITE_RAM_READ;
                        end

                        SYS_MINER_LOAD_HISTORY: begin
                            // a1 ring slot, a2 RAM destination, a3 length <=64.
                            copy_src<=file_start[MINER_HISTORY_ENTRY]*BLOCK_SIZE
                                     + ((req_args[7:0])<<6);
                            copy_dst<=req_args[63:32];
                            copy_count<=(req_args[95:64]>64)?64:req_args[95:64];
                            copy_index<=0;
                            direct_write<=0;
                            state<=ST_READ_FLASH_BYTE;
                        end

                        default: begin response<=64'hffffffffffffffff;state<=ST_ERROR;end
                    endcase
                end

                ST_MOUNT_BITMAP: if(dma_ready) begin
                    bitmap[mount_bitmap_word]<=dma_rdata;
                    used_blocks<=used_blocks+popcount32(dma_rdata);
                    if(mount_bitmap_word==BITMAP_WORDS-1) begin
                        mount_table_word<=0;state<=ST_MOUNT_TABLE;
                    end else mount_bitmap_word<=mount_bitmap_word+1'b1;
                end

                ST_MOUNT_TABLE: if(dma_ready) begin
                    decode_entry_word(mount_table_word>>4,mount_table_word&15,dma_rdata);
                    if(mount_table_word==1023) state<=ST_MOUNT_DONE;
                    else mount_table_word<=mount_table_word+1'b1;
                end

                ST_MOUNT_DONE: begin mounted<=1;response<=used_blocks<<8;state<=ST_DONE;end

                ST_NAME_READ,ST_RENAME_NAME_READ: if(dma_ready) begin
                    name_word<=dma_rdata;
                    name_byte_index<=(query_ptr+query_len)&3;
                    state<=(state==ST_NAME_READ)?ST_NAME_BYTES:ST_RENAME_NAME_BYTES;
                end

                ST_NAME_BYTES,ST_RENAME_NAME_BYTES: begin
                    copy_byte<=name_word>>(name_byte_index*8);
                    if((name_word>>(name_byte_index*8))==0 || query_len==NAME_BYTES) begin
                        scan_entry<=0;
                        state<=after_name_state;
                    end else begin
                        query_name[query_len]<=name_word>>(name_byte_index*8);
                        query_len<=query_len+1'b1;
                        state<=(state==ST_NAME_BYTES)?ST_NAME_READ:ST_RENAME_NAME_READ;
                    end
                end

                ST_SEARCH: begin
                    if(scan_entry<MAX_FILES && names_equal(scan_entry)) begin
                        target_entry<=scan_entry;
                        if(op==SYS_FILE_OPEN) begin response<=scan_entry+1;state<=ST_DONE;end
                        else begin response<=64'hfffffffffffffffd;state<=ST_ERROR;end
                    end else if(scan_entry==MAX_FILES-1) begin
                        if(op==SYS_FILE_CREATE) begin scan_entry<=0;state<=ST_FIND_FREE_ENTRY;end
                        else begin response<=0;state<=ST_DONE;end
                    end else scan_entry<=scan_entry+1'b1;
                end

                ST_FIND_FREE_ENTRY: begin
                    if(!file_used[scan_entry]) begin
                        target_entry<=scan_entry;state<=ST_CREATE_PREP;
                    end else if(scan_entry==MAX_FILES-1) begin
                        response<=64'hfffffffffffffffc;state<=ST_ERROR;
                    end else scan_entry<=scan_entry+1'b1;
                end

                ST_CREATE_PREP: begin
                    file_used[target_entry]<=1;
                    file_type[target_entry]<=a2[7:0];
                    file_flags[target_entry]<=0;file_size[target_entry]<=0;
                    file_start[target_entry]<=0;file_blocks[target_entry]<=0;
                    file_generation[target_entry]<=file_generation[target_entry]+1'b1;
                    file_name_len[target_entry]<=query_len;
                    for(j=0;j<NAME_BYTES;j=j+1)
                        file_name[target_entry*NAME_BYTES+j]<=query_name[j];
                    flush_entry_index<=target_entry;flush_entry_word<=0;
                    after_entry_state<=ST_OPEN_DONE;state<=ST_FLUSH_ENTRY;
                end

                ST_OPEN_DONE: begin response<=target_entry+1;state<=ST_DONE;end

                ST_RENAME_PREP: begin
                    file_name_len[target_entry]<=query_len;
                    file_generation[target_entry]<=file_generation[target_entry]+1'b1;
                    for(j=0;j<NAME_BYTES;j=j+1)
                        file_name[target_entry*NAME_BYTES+j]<=query_name[j];
                    flush_entry_index<=target_entry;flush_entry_word<=0;
                    after_entry_state<=ST_DONE;state<=ST_FLUSH_ENTRY;
                end

                ST_ALLOC_SCAN: begin
                    if(alloc_needed==0) begin
                        alloc_start<=0;state<=ST_WRITE_UPDATE_ENTRY;
                    end else if(alloc_cursor>=BLOCK_COUNT) begin
                        response<=64'hfffffffffffffffb;state<=ST_ERROR;
                    end else if(!bitmap_bit(alloc_cursor)) begin
                        if(alloc_run_len==0) alloc_run_start<=alloc_cursor;
                        if(alloc_run_len+1>=alloc_needed) begin
                            alloc_start<=(alloc_run_len==0)?alloc_cursor:alloc_run_start;
                            alloc_mark_index<=0;state<=ST_ALLOC_MARK;
                        end else begin
                            alloc_run_len<=alloc_run_len+1;alloc_cursor<=alloc_cursor+1;
                        end
                    end else begin
                        alloc_run_len<=0;alloc_cursor<=alloc_cursor+1;
                    end
                end

                ST_ALLOC_MARK: begin
                    bitmap[(alloc_start+alloc_mark_index)>>5][(alloc_start+alloc_mark_index)&31]<=1;
                    if(alloc_mark_index+1>=alloc_needed) begin
                        used_blocks<=used_blocks+alloc_needed;
                        flush_bitmap_word<=0;
                        after_bitmap_state<=ST_WRITE_RAM_READ;
                        copy_index<=0;copy_dst<=alloc_start*BLOCK_SIZE;
                        state<=ST_FLUSH_BITMAP;
                    end else alloc_mark_index<=alloc_mark_index+1;
                end

                ST_WRITE_RAM_READ: begin
                    if(copy_index>=copy_count) begin
                        if(direct_write) begin
                            response<=copy_count;
                            direct_write<=0;
                            state<=ST_DONE;
                        end else state<=ST_WRITE_UPDATE_ENTRY;
                    end else if(dma_ready) begin
                        copy_byte<=dma_rdata>>(((copy_src+copy_index)&3)*8);
                        state<=ST_WRITE_FLASH_BYTE;
                    end
                end

                ST_WRITE_FLASH_BYTE: if(dma_ready) begin
                    copy_index<=copy_index+1;state<=ST_WRITE_RAM_READ;
                end

                ST_WRITE_UPDATE_ENTRY: begin
                    file_size[target_entry]<=copy_count;
                    file_start[target_entry]<=alloc_start;
                    file_blocks[target_entry]<=alloc_needed;
                    file_generation[target_entry]<=file_generation[target_entry]+1'b1;
                    flush_entry_index<=target_entry;flush_entry_word<=0;
                    after_entry_state<=ST_WRITE_FREE_OLD;state<=ST_FLUSH_ENTRY;
                end

                ST_WRITE_FREE_OLD: begin
                    if(free_index>=old_blocks) begin
                        flush_bitmap_word<=0;after_bitmap_state<=ST_DONE;state<=ST_FLUSH_BITMAP;
                    end else begin
                        bitmap[(old_start+free_index)>>5][(old_start+free_index)&31]<=0;
                        free_index<=free_index+1;
                        if(free_index+1>=old_blocks) used_blocks<=used_blocks-old_blocks;
                    end
                end

                ST_READ_FLASH_BYTE: begin
                    if(copy_index>=copy_count) begin response<=copy_count;state<=ST_DONE;end
                    else if(dma_ready) begin
                        copy_byte<=dma_rdata>>(((copy_src+copy_index)&3)*8);
                        state<=ST_READ_RAM_WRITE;
                    end
                end

                ST_READ_RAM_WRITE: if(dma_ready) begin
                    copy_index<=copy_index+1;state<=ST_READ_FLASH_BYTE;
                end

                ST_DELETE_FREE,ST_TRUNCATE_FREE: begin
                    if(free_index>=old_blocks) begin
                        if(state==ST_DELETE_FREE) begin
                            file_used[target_entry]<=0;file_size[target_entry]<=0;
                            file_start[target_entry]<=0;file_blocks[target_entry]<=0;
                        end else begin
                            file_size[target_entry]<=0;file_start[target_entry]<=0;file_blocks[target_entry]<=0;
                        end
                        flush_entry_index<=target_entry;flush_entry_word<=0;
                        flush_bitmap_word<=0;
                        after_bitmap_state<=ST_DONE;
                        after_entry_state<=ST_FLUSH_BITMAP;
                        state<=ST_FLUSH_ENTRY;
                    end else begin
                        bitmap[(old_start+free_index)>>5][(old_start+free_index)&31]<=0;
                        free_index<=free_index+1;
                        if(free_index+1>=old_blocks) used_blocks<=used_blocks-old_blocks;
                    end
                end

                ST_LIST_NAME_WRITE: begin
                    if(list_name_index>=file_name_len[target_entry]) begin
                        response<={file_size[target_entry],16'd0,
                                  file_name_len[target_entry],file_type[target_entry]};
                        state<=ST_DONE;
                    end else if(dma_ready) list_name_index<=list_name_index+1'b1;
                end

                ST_FLUSH_BITMAP: if(dma_ready) begin
                    if(flush_bitmap_word==BITMAP_WORDS-1) state<=after_bitmap_state;
                    else flush_bitmap_word<=flush_bitmap_word+1'b1;
                end

                ST_FLUSH_ENTRY: if(dma_ready) begin
                    if(flush_entry_word==TABLE_WORDS_PER_ENTRY-1) state<=after_entry_state;
                    else flush_entry_word<=flush_entry_word+1'b1;
                end

                ST_DONE: begin end
                ST_ERROR: begin end
                default: state<=ST_IDLE;
            endcase
        end
    end
endmodule
