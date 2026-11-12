`include "shama_boot_params.svh"
`ifndef SHAMA_MINER_HISTORY_ENTRY
`define SHAMA_MINER_HISTORY_ENTRY 13
`endif
`ifndef SHAMA_MINER_STATE_ENTRY
`define SHAMA_MINER_STATE_ENTRY 14
`endif

// Streaming ShamaFS controller. Every durable file-table change is copy-on-write
// with a single-entry redo journal in superblock bytes 64..139. Mount replays a
// committed journal and rebuilds the allocation bitmap from validated extents.
// Reset may interrupt between acknowledged DMA words without losing either the
// old or the complete new file. Corrupt metadata fails mount explicitly.
module shama_fs_accel(
    input logic clk,rst,
    input logic req_valid,
    input logic [11:0] req_id,
    input logic [191:0] req_args,
    input logic [3:0] req_owner,
    output logic req_ready,
    output logic [63:0] req_ret,
    input logic cleanup_valid,
    input logic [3:0] cleanup_owner,
    output logic cleanup_ready,
    output logic dma_valid,dma_we,dma_flash,
    output logic [31:0] dma_addr,dma_wdata,
    output logic [3:0] dma_wstrb,
    input logic dma_ready,
    input logic [31:0] dma_rdata,
    output logic [31:0] flash_used_bytes,
    output logic mounted
);
    localparam [11:0]
        SYS_BOOT_MOUNT=12'h008, SYS_FILE_CREATE=12'h020,
        SYS_FILE_OPEN=12'h021, SYS_FILE_CLOSE=12'h022,
        SYS_FILE_READ=12'h023, SYS_FILE_WRITE=12'h024,
        SYS_FILE_TRUNCATE=12'h025, SYS_FILE_RENAME=12'h026,
        SYS_FILE_DELETE=12'h027, SYS_FILE_STAT=12'h028,
        SYS_FILE_LIST=12'h029, SYS_FLASH_USAGE=12'h02a,
        SYS_FILE_TRANSFER=12'h02b, SYS_FS_SYNC=12'h02c,
        SYS_MINER_LOG_RESULT=12'h050, SYS_MINER_SAVE_STATE=12'h051,
        SYS_MINER_LOAD_HISTORY=12'h052, SYS_MINER_LOAD_STATE=12'h053;
    localparam integer BLOCK_SIZE=256, BLOCK_COUNT=16384, BITMAP_WORDS=512;
    localparam integer MAX_FILES=64, NAME_BYTES=43, DATA_START_BLOCK=25;
    // A power-of-two backing array gives every synthesized read-mux leaf a
    // defined source. The public on-disk/path limit remains exactly 43 bytes;
    // spare leaves are zeroed and are never accepted by the range checks.
    localparam integer NAME_STORAGE_BYTES=64;
    localparam integer BITMAP_FLASH_OFFSET=256, TABLE_FLASH_OFFSET=2304;
    localparam [31:0] JOURNAL_MAGIC=32'h314e524a, JOURNAL_SEED=32'h53464a31;
    localparam [63:0] E_HANDLE=64'hffffffffffffffff,
        E_RANGE=64'hfffffffffffffffe, E_FULL=64'hfffffffffffffffd,
        E_EXISTS=64'hfffffffffffffffc, E_IO=64'hfffffffffffffffb,
        E_PATH=64'hfffffffffffffffa, E_BUSY=64'hfffffffffffffff9;

    typedef enum logic [6:0] {
        ST_IDLE, ST_SUPER, ST_JCHECK, ST_JINDEX_READ, ST_JPAYLOAD_READ,
        ST_JCHECKSUM_READ, ST_RECOVER_WRITE, ST_REBUILD_CLEAR,
        ST_REBUILD_LOAD, ST_REBUILD_CHECK, ST_REBUILD_MARK_READ,
        ST_REBUILD_MARK_WRITE, ST_MOUNT_DONE,
        ST_NAME_READ, ST_SEARCH_LOAD, ST_SEARCH_CHECK, ST_CREATE_PREP,
        ST_ENTRY_LOAD, ST_ENTRY_DISPATCH, ST_ALLOC_READ, ST_ALLOC_SCAN,
        ST_ALLOC_MARK_READ, ST_ALLOC_MARK_WRITE, ST_FREE_READ, ST_FREE_WRITE,
        ST_COPY_READ, ST_COPY_WRITE, ST_LIST_WRITE,
        ST_JINDEX_WRITE, ST_JPAYLOAD_WRITE, ST_JCHECKSUM_WRITE, ST_JCOMMIT,
        ST_ENTRY_FLUSH, ST_JCLEAR, ST_ENTRY_FLUSH_DONE, ST_NS_LOAD, ST_NS_PREP,
        ST_NS_BEGIN, ST_NS_SCAN, ST_NS_CHECK, ST_DONE, ST_ERROR
    } state_t;
    typedef enum logic [4:0] {
        ACT_NONE,ACT_OPEN,ACT_CREATE,ACT_READ,ACT_WRITE,ACT_TRUNCATE,
        ACT_RENAME,ACT_RENAME_SEARCH,ACT_RENAME_COMMIT,ACT_DELETE,
        ACT_DELETE_COMMIT,ACT_DIR_DELETE_CHECK,ACT_DIR_RENAME_CHECK,
        ACT_RENAME_NAME,ACT_STAT,ACT_LIST,ACT_MINER_LOG,ACT_MINER_SAVE,
        ACT_MINER_LOAD,ACT_MINER_LOAD_STATE
    } action_t;
    state_t state;
    action_t action,after_flush_action;
    logic [31:0] a1,a2,a3;
    logic [3:0] owner;
    logic [63:0] response;
    logic [15:0] handle_owners [0:MAX_FILES-1];
    logic [31:0] used_blocks;
    logic [9:0] bitmap_index;
    logic [3:0] super_word;
    logic entry_used;
    logic [7:0] entry_type,entry_name_len;
    logic [15:0] entry_flags;
    logic [31:0] entry_size,entry_start,entry_blocks,entry_generation;
    logic [7:0] entry_name [0:NAME_STORAGE_BYTES-1];
    logic [6:0] entry_index,original_index,first_free_index;
    logic [4:0] entry_word;
    logic first_free_valid,parent_found;
    logic [7:0] query_name [0:NAME_BYTES-1];
    logic [7:0] query_len,parent_len;
    logic [31:0] query_ptr;
    logic leading_slash;
    logic [31:0] copy_src,copy_dst,copy_count,copy_index;
    logic [31:0] copy_keep,copy_overlay,copy_payload_len;
    logic [7:0] copy_byte;
    logic copy_src_flash,copy_dst_flash,copy_commit,copy_overlay_mode;
    logic [31:0] old_start,old_blocks,old_size,new_size;
    logic [31:0] alloc_needed,alloc_word_index,alloc_word_cache;
    logic [5:0] alloc_bit_index,bitmap_modify_bit;
    logic [31:0] alloc_run_start,alloc_run_len,alloc_start,alloc_mark_offset;
    logic [31:0] bitmap_modify_word,free_start,free_count,free_offset;
    logic [7:0] list_name_index;
    logic [31:0] journal_checksum;
    logic recovering;
    logic [31:0] copy_read_address;
    logic copy_read_flash,copy_zero;
    logic [7:0] selected_byte;
    logic [31:0] absolute_block;
    logic [63:0] successful_result;
    integer i;

    function automatic logic ram_range(input logic [31:0] ptr,count);
        ram_range=(ptr<=32'h100000 && count<=32'h100000-ptr);
    endfunction
    function automatic logic valid_entry(input logic live);
        begin
            valid_entry = !entry_used ||
                (entry_type<=7 && entry_name_len>0 && entry_name_len<=NAME_BYTES &&
                 entry_size<=32'h400000 &&
                 entry_blocks==((entry_size+255)>>8) &&
                 ((entry_blocks==0 && entry_start==0) ||
                  (entry_blocks!=0 && entry_start>=DATA_START_BLOCK &&
                   entry_start<BLOCK_COUNT && entry_blocks<=BLOCK_COUNT-entry_start)) &&
                 (entry_type!=7 || entry_size==0));
        end
    endfunction
    function automatic logic names_equal(input logic live);
        logic ok;
        begin
            ok=entry_used && entry_name_len==query_len;
            for(integer k=0;k<NAME_BYTES;k=k+1)
                if(k<query_len && entry_name[k]!=query_name[k]) ok=0;
            names_equal=ok;
        end
    endfunction
    function automatic logic is_parent(input logic live);
        logic ok;
        begin
            ok=entry_used && entry_type==7 && entry_name_len==parent_len;
            for(integer k=0;k<NAME_BYTES;k=k+1)
                if(k<parent_len && entry_name[k]!=query_name[k]) ok=0;
            is_parent=ok;
        end
    endfunction
    function automatic logic is_child(input logic live);
        logic ok;
        begin
            ok=entry_used && entry_name_len>query_len && query_len<NAME_BYTES;
            for(integer k=0;k<NAME_BYTES;k=k+1) begin
                if(k<query_len && entry_name[k]!=query_name[k]) ok=0;
                if(k==query_len && entry_name[k]!="/") ok=0;
            end
            is_child=ok;
        end
    endfunction
    function automatic logic valid_path(input logic live);
        integer component_length;
        logic dots,ok;
        begin
            component_length=0;dots=1;ok=query_len>0 && query_len<=NAME_BYTES;
            for(integer k=0;k<NAME_BYTES;k=k+1) begin
                if(k<query_len) begin
                    if(query_name[k]=="/") begin
                        if(component_length==0 || (dots && component_length<=2)) ok=0;
                        component_length=0;dots=1;
                    end else begin
                        if(query_name[k]<32 || query_name[k]==8'h5c) ok=0;
                        if(query_name[k]!=".") dots=0;
                        component_length=component_length+1;
                    end
                end
            end
            if(component_length==0 || (dots && component_length<=2)) ok=0;
            valid_path=ok;
        end
    endfunction
    function automatic [31:0] encode_entry_word(input integer idx);
        integer b;
        logic [31:0] v;
        begin
            v=0;
            case(idx)
                0:v={entry_flags,entry_type,7'd0,entry_used};
                1:v=entry_size;
                2:v=entry_start;
                3:v=entry_blocks;
                4:v=entry_generation;
                default: begin
                    b=idx*4-21;
                    for(integer k=0;k<4;k=k+1) begin
                        if(idx==5 && k==0) v[k*8 +:8]=entry_name_len;
                        else if(b+k>=0 && b+k<NAME_BYTES) v[k*8 +:8]=entry_name[b+k];
                    end
                end
            endcase
            encode_entry_word=v;
        end
    endfunction
    task automatic decode_entry_word(input integer idx,input logic [31:0] v);
        integer b;
        begin
            case(idx)
                0:begin entry_used<=v[0];entry_type<=v[15:8];entry_flags<=v[31:16];end
                1:entry_size<=v;
                2:entry_start<=v;
                3:entry_blocks<=v;
                4:entry_generation<=v;
                default:begin
                    b=idx*4-21;
                    for(integer k=0;k<4;k=k+1) begin
                        if(idx==5 && k==0) entry_name_len<=v[7:0];
                        else if(b+k>=0 && b+k<NAME_BYTES) entry_name[b+k]<=v[k*8 +:8];
                    end
                end
            endcase
        end
    endtask
    task automatic clear_entry;
        begin
            entry_used<=0;entry_type<=0;entry_flags<=0;entry_size<=0;
            entry_start<=0;entry_blocks<=0;entry_generation<=0;entry_name_len<=0;
            for(integer k=0;k<NAME_STORAGE_BYTES;k=k+1) entry_name[k]<=0;
        end
    endtask
    task automatic begin_load(input logic [6:0] idx,input action_t next_action);
        begin entry_index<=idx;entry_word<=0;action<=next_action;state<=ST_ENTRY_LOAD;end
    endtask
    task automatic begin_journal(input action_t next_action);
        begin
            after_flush_action<=next_action;entry_word<=0;
            journal_checksum<=JOURNAL_SEED ^ entry_index;
            recovering<=0;state<=ST_JINDEX_WRITE;
        end
    endtask
    task automatic start_search;
        begin
            entry_index<=0;entry_word<=0;first_free_valid<=0;
            first_free_index<=0;parent_found<=parent_len==0;state<=ST_SEARCH_LOAD;
        end
    endtask
    task automatic begin_name(input logic [31:0] ptr,input action_t next_action);
        begin
            query_ptr<=ptr;query_len<=0;leading_slash<=0;action<=next_action;
            for(integer k=0;k<NAME_BYTES;k=k+1) query_name[k]<=0;
            state<=ST_NAME_READ;
        end
    endtask
    task automatic start_allocation(input logic [31:0] bytes_to_write);
        begin
            old_start<=entry_start;old_blocks<=entry_blocks;old_size<=entry_size;
            new_size<=bytes_to_write;alloc_needed<=(bytes_to_write+255)>>8;
            alloc_word_index<=DATA_START_BLOCK>>5;alloc_bit_index<=DATA_START_BLOCK&31;
            alloc_run_start<=0;alloc_run_len<=0;alloc_start<=0;alloc_mark_offset<=0;
            if(bytes_to_write==0) begin
                entry_size<=0;entry_start<=0;entry_blocks<=0;
                entry_generation<=entry_generation+1'b1;
                begin_journal(action);
            end else state<=ST_ALLOC_READ;
        end
    endtask
    task automatic next_rebuild_entry;
        begin
            if(entry_index==MAX_FILES-1) begin entry_index<=0;original_index<=0;entry_word<=0;state<=ST_NS_LOAD;end
            else begin entry_index<=entry_index+1'b1;entry_word<=0;state<=ST_REBUILD_LOAD;end
        end
    endtask

    always_comb begin
        req_ready=(state==ST_DONE || state==ST_ERROR);
        req_ret=response;
        cleanup_ready=(state==ST_IDLE);
        flash_used_bytes=used_blocks<<8;
        parent_len=0;
        for(integer k=0;k<NAME_BYTES;k=k+1)
            if(k<query_len && query_name[k]=="/") parent_len=k;
        dma_valid=0;dma_we=0;dma_flash=0;dma_addr=0;dma_wdata=0;dma_wstrb=4'b1111;
        selected_byte=0;absolute_block=0;
        copy_read_address=copy_src+copy_index;
        copy_read_flash=copy_src_flash;
        copy_zero=copy_index>=copy_keep;
        if(copy_overlay_mode && copy_index>=copy_overlay && copy_index<copy_overlay+128) begin
            copy_read_flash=0;
            copy_read_address=a1+copy_index-copy_overlay;
            copy_zero=(copy_index-copy_overlay>=copy_payload_len);
        end
        successful_result=0;
        case(after_flush_action)
            ACT_WRITE:successful_result={32'd0,entry_size};
            ACT_MINER_SAVE,ACT_MINER_LOG:successful_result={32'd0,a2};
            default:successful_result=0;
        endcase
        case(state)
            ST_SUPER:begin dma_valid=1;dma_flash=1;dma_addr={26'd0,super_word,2'b00};end
            ST_JCHECK:begin dma_valid=1;dma_flash=1;dma_addr=64;end
            ST_JINDEX_READ:begin dma_valid=1;dma_flash=1;dma_addr=68;end
            ST_JPAYLOAD_READ:begin dma_valid=1;dma_flash=1;dma_addr=72+(entry_word<<2);end
            ST_JCHECKSUM_READ:begin dma_valid=1;dma_flash=1;dma_addr=136;end
            ST_REBUILD_CLEAR:begin
                dma_valid=1;dma_we=1;dma_flash=1;dma_addr=BITMAP_FLASH_OFFSET+(bitmap_index<<2);
                dma_wdata=(bitmap_index==0)?32'h01ffffff:0;
            end
            ST_REBUILD_LOAD,ST_SEARCH_LOAD,ST_ENTRY_LOAD,ST_NS_LOAD,ST_NS_SCAN:begin
                dma_valid=1;dma_flash=1;dma_addr=TABLE_FLASH_OFFSET+(entry_index<<6)+(entry_word<<2);
            end
            ST_REBUILD_MARK_READ,ST_REBUILD_MARK_WRITE:begin
                absolute_block=entry_start+free_offset;
                dma_valid=1;dma_flash=1;dma_addr=BITMAP_FLASH_OFFSET+((absolute_block>>5)<<2);
                if(state==ST_REBUILD_MARK_WRITE) begin
                    dma_we=1;dma_wdata=bitmap_modify_word | (32'h1<<bitmap_modify_bit);
                end
            end
            ST_NAME_READ:begin
                // A stripped leading slash advances query_ptr beyond the
                // original 44-byte range check. Validate the final terminator
                // byte as well before it can alias address zero in RAM.
                dma_addr=query_ptr+query_len;dma_valid=ram_range(dma_addr,1);
                selected_byte=dma_rdata >> ((dma_addr&3)*8);
            end
            ST_ALLOC_READ:begin dma_valid=1;dma_flash=1;dma_addr=BITMAP_FLASH_OFFSET+(alloc_word_index<<2);end
            ST_ALLOC_MARK_READ,ST_ALLOC_MARK_WRITE:begin
                absolute_block=alloc_start+alloc_mark_offset;
                dma_valid=1;dma_flash=1;dma_addr=BITMAP_FLASH_OFFSET+((absolute_block>>5)<<2);
                if(state==ST_ALLOC_MARK_WRITE) begin
                    dma_we=1;dma_wdata=bitmap_modify_word | (32'h1<<bitmap_modify_bit);
                end
            end
            ST_FREE_READ,ST_FREE_WRITE:begin
                absolute_block=free_start+free_offset;
                dma_valid=1;dma_flash=1;dma_addr=BITMAP_FLASH_OFFSET+((absolute_block>>5)<<2);
                if(state==ST_FREE_WRITE) begin dma_we=1;dma_wdata=bitmap_modify_word & ~(32'h1<<bitmap_modify_bit);end
            end
            ST_COPY_READ:begin
                dma_valid=!copy_zero;dma_flash=copy_read_flash;dma_addr=copy_read_address;
                selected_byte=dma_rdata >> ((dma_addr&3)*8);
            end
            ST_COPY_WRITE:begin
                dma_valid=1;dma_we=1;dma_flash=copy_dst_flash;dma_addr=copy_dst+copy_index;
                dma_wstrb=4'b0001 << dma_addr[1:0];dma_wdata={24'd0,copy_byte} << (dma_addr[1:0]*8);
            end
            ST_LIST_WRITE:begin
                dma_valid=1;dma_we=1;dma_addr=a2+list_name_index;
                dma_wstrb=4'b0001 << dma_addr[1:0];
                dma_wdata=(list_name_index<entry_name_len)?{24'd0,entry_name[list_name_index]} << (dma_addr[1:0]*8):0;
            end
            ST_JINDEX_WRITE:begin dma_valid=1;dma_we=1;dma_flash=1;dma_addr=68;dma_wdata=entry_index;end
            ST_JPAYLOAD_WRITE:begin
                dma_valid=1;dma_we=1;dma_flash=1;dma_addr=72+(entry_word<<2);dma_wdata=encode_entry_word(entry_word);
            end
            ST_JCHECKSUM_WRITE:begin dma_valid=1;dma_we=1;dma_flash=1;dma_addr=136;dma_wdata=journal_checksum;end
            ST_JCOMMIT:begin dma_valid=1;dma_we=1;dma_flash=1;dma_addr=64;dma_wdata=JOURNAL_MAGIC;end
            ST_ENTRY_FLUSH,ST_RECOVER_WRITE:begin
                dma_valid=1;dma_we=1;dma_flash=1;
                dma_addr=TABLE_FLASH_OFFSET+(entry_index<<6)+(entry_word<<2);dma_wdata=encode_entry_word(entry_word);
            end
            ST_JCLEAR:begin dma_valid=1;dma_we=1;dma_flash=1;dma_addr=64;dma_wdata=0;end
            default:begin end
        endcase
    end

    always_ff @(posedge clk) begin
        if(rst) begin
            state<=ST_IDLE;action<=ACT_NONE;after_flush_action<=ACT_NONE;
            mounted<=0;response<=0;owner<=0;a1<=0;a2<=0;a3<=0;
            used_blocks<=0;bitmap_index<=0;super_word<=0;
            entry_index<=0;original_index<=0;entry_word<=0;
            first_free_index<=0;first_free_valid<=0;parent_found<=0;
            query_len<=0;query_ptr<=0;leading_slash<=0;
            copy_src<=0;copy_dst<=0;copy_count<=0;copy_index<=0;copy_byte<=0;
            copy_src_flash<=0;copy_dst_flash<=0;copy_commit<=0;copy_overlay_mode<=0;
            copy_keep<=0;copy_overlay<=0;copy_payload_len<=0;
            old_start<=0;old_blocks<=0;old_size<=0;new_size<=0;
            alloc_needed<=0;alloc_word_index<=0;alloc_bit_index<=0;
            alloc_word_cache<=0;alloc_run_start<=0;alloc_run_len<=0;alloc_start<=0;alloc_mark_offset<=0;
            bitmap_modify_word<=0;bitmap_modify_bit<=0;
            free_start<=0;free_count<=0;free_offset<=0;list_name_index<=0;
            journal_checksum<=0;recovering<=0;
            clear_entry();
            for(i=0;i<NAME_BYTES;i=i+1) query_name[i]<=0;
            for(i=0;i<MAX_FILES;i=i+1) handle_owners[i]<=0;
        end else begin
            if((state==ST_DONE || state==ST_ERROR) && !req_valid) state<=ST_IDLE;
            case(state)
                ST_IDLE:begin
                    if(cleanup_valid) begin
                        for(i=0;i<MAX_FILES;i=i+1) handle_owners[i][cleanup_owner]<=0;
                    end else if(req_valid) begin
                        a1<=req_args[31:0];a2<=req_args[63:32];a3<=req_args[95:64];
                        owner<=req_owner;response<=0;copy_commit<=0;copy_overlay_mode<=0;
                        if(req_id==SYS_BOOT_MOUNT) begin
                            mounted<=0;super_word<=0;state<=ST_SUPER;
                            for(i=0;i<MAX_FILES;i=i+1) handle_owners[i]<=0;
                        end else if(!mounted) begin response<=E_IO;state<=ST_ERROR;end
                        else case(req_id)
                            SYS_FLASH_USAGE:begin response<={32'd4194304,flash_used_bytes};state<=ST_DONE;end
                            SYS_FS_SYNC:state<=ST_DONE;
                            SYS_FILE_CLOSE,SYS_FILE_TRANSFER:begin
                                if(req_args[31:0]==0 || req_args[31:0]>MAX_FILES ||
                                   !handle_owners[req_args[6:0]-1'b1][req_owner] ||
                                   (req_id==SYS_FILE_TRANSFER && req_args[63:32]>15)) begin response<=E_HANDLE;state<=ST_ERROR;end
                                else begin
                                    handle_owners[req_args[6:0]-1'b1][req_owner]<=0;
                                    if(req_id==SYS_FILE_TRANSFER) handle_owners[req_args[6:0]-1'b1][req_args[35:32]]<=1;
                                    state<=ST_DONE;
                                end
                            end
                            SYS_FILE_CREATE,SYS_FILE_OPEN:begin
                                if(!ram_range(req_args[31:0],44) || (req_id==SYS_FILE_CREATE && req_args[63:32]>7)) begin response<=E_RANGE;state<=ST_ERROR;end
                                else begin_name(req_args[31:0],req_id==SYS_FILE_CREATE?ACT_CREATE:ACT_OPEN);
                            end
                            SYS_FILE_READ,SYS_FILE_WRITE,SYS_FILE_TRUNCATE,SYS_FILE_RENAME,SYS_FILE_DELETE,SYS_FILE_STAT:begin
                                if(req_args[31:0]==0 || req_args[31:0]>MAX_FILES ||
                                   !handle_owners[req_args[6:0]-1'b1][req_owner]) begin response<=E_HANDLE;state<=ST_ERROR;end
                                else begin
                                    original_index<=req_args[6:0]-1'b1;entry_index<=req_args[6:0]-1'b1;entry_word<=0;
                                    case(req_id)
                                        SYS_FILE_READ:action<=ACT_READ;
                                        SYS_FILE_WRITE:action<=ACT_WRITE;
                                        SYS_FILE_TRUNCATE:action<=ACT_TRUNCATE;
                                        SYS_FILE_RENAME:action<=ACT_RENAME;
                                        SYS_FILE_DELETE:action<=ACT_DELETE;
                                        default:action<=ACT_STAT;
                                    endcase
                                    state<=ST_ENTRY_LOAD;
                                end
                            end
                            SYS_FILE_LIST:begin
                                if(req_args[31:0]>=MAX_FILES) state<=ST_DONE;
                                else if(!ram_range(req_args[63:32],44)) begin response<=E_RANGE;state<=ST_ERROR;end
                                else begin_load(req_args[6:0],ACT_LIST);
                            end
                            SYS_MINER_LOG_RESULT:begin_load(`SHAMA_MINER_HISTORY_ENTRY,ACT_MINER_LOG);
                            SYS_MINER_SAVE_STATE:begin_load(`SHAMA_MINER_STATE_ENTRY,ACT_MINER_SAVE);
                            SYS_MINER_LOAD_HISTORY:begin_load(`SHAMA_MINER_HISTORY_ENTRY,ACT_MINER_LOAD);
                            SYS_MINER_LOAD_STATE:begin_load(`SHAMA_MINER_STATE_ENTRY,ACT_MINER_LOAD_STATE);
                            default:begin response<=E_HANDLE;state<=ST_ERROR;end
                        endcase
                    end
                end
                ST_SUPER:if(dma_ready) begin
                    if((super_word==0 && dma_rdata!=32'h4d414853) ||
                       (super_word==1 && dma_rdata!=32'h31534641) ||
                       (super_word==2 && dma_rdata!=1) ||
                       (super_word==3 && dma_rdata!=256) ||
                       (super_word==4 && dma_rdata!=BLOCK_COUNT) ||
                       (super_word==5 && dma_rdata!=1) ||
                       (super_word==6 && dma_rdata!=9) ||
                       (super_word==7 && dma_rdata!=DATA_START_BLOCK)) begin response<=E_IO;state<=ST_ERROR;end
                    else if(super_word==9) state<=ST_JCHECK;
                    else super_word<=super_word+1'b1;
                end
                ST_JCHECK:if(dma_ready) begin
                    if(dma_rdata==0) begin bitmap_index<=0;used_blocks<=DATA_START_BLOCK;state<=ST_REBUILD_CLEAR;end
                    else if(dma_rdata==JOURNAL_MAGIC) state<=ST_JINDEX_READ;
                    else begin response<=E_IO;state<=ST_ERROR;end
                end
                ST_JINDEX_READ:if(dma_ready) begin
                    if(dma_rdata>=MAX_FILES) begin response<=E_IO;state<=ST_ERROR;end
                    else begin entry_index<=dma_rdata[6:0];entry_word<=0;journal_checksum<=JOURNAL_SEED^dma_rdata;state<=ST_JPAYLOAD_READ;end
                end
                ST_JPAYLOAD_READ:if(dma_ready) begin
                    decode_entry_word(entry_word,dma_rdata);journal_checksum<=journal_checksum^dma_rdata;
                    if(entry_word==15) state<=ST_JCHECKSUM_READ;
                    else entry_word<=entry_word+1'b1;
                end
                ST_JCHECKSUM_READ:if(dma_ready) begin
                    if(journal_checksum!=dma_rdata || !valid_entry(1'b1)) begin response<=E_IO;state<=ST_ERROR;end
                    else begin entry_word<=0;recovering<=1;state<=ST_RECOVER_WRITE;end
                end
                ST_RECOVER_WRITE:if(dma_ready) begin
                    if(entry_word==15) state<=ST_JCLEAR;
                    else entry_word<=entry_word+1'b1;
                end
                ST_REBUILD_CLEAR:if(dma_ready) begin
                    if(bitmap_index==BITMAP_WORDS-1) begin entry_index<=0;entry_word<=0;state<=ST_REBUILD_LOAD;end
                    else bitmap_index<=bitmap_index+1'b1;
                end
                ST_REBUILD_LOAD:if(dma_ready) begin
                    decode_entry_word(entry_word,dma_rdata);
                    if(entry_word==15) state<=ST_REBUILD_CHECK;
                    else entry_word<=entry_word+1'b1;
                end
                ST_REBUILD_CHECK:begin
                    if(!valid_entry(1'b1)) begin response<=E_IO;state<=ST_ERROR;end
                    else if(entry_used && entry_blocks!=0) begin free_offset<=0;state<=ST_REBUILD_MARK_READ;end
                    else next_rebuild_entry();
                end
                ST_REBUILD_MARK_READ:if(dma_ready) begin
                    if(dma_rdata[(entry_start+free_offset)&31]) begin response<=E_IO;state<=ST_ERROR;end
                    else begin bitmap_modify_word<=dma_rdata;bitmap_modify_bit<=(entry_start+free_offset)&31;state<=ST_REBUILD_MARK_WRITE;end
                end
                ST_REBUILD_MARK_WRITE:if(dma_ready) begin
                    used_blocks<=used_blocks+1'b1;
                    if(free_offset+1>=entry_blocks) next_rebuild_entry();
                    else begin free_offset<=free_offset+1'b1;state<=ST_REBUILD_MARK_READ;end
                end
                ST_NS_LOAD:if(dma_ready) begin
                    decode_entry_word(entry_word,dma_rdata);
                    if(entry_word==15) state<=ST_NS_PREP;
                    else entry_word<=entry_word+1'b1;
                end
                ST_NS_PREP:begin
                    if(!entry_used) begin
                        if(original_index==MAX_FILES-1) state<=ST_MOUNT_DONE;
                        else begin original_index<=original_index+1'b1;entry_index<=original_index+1'b1;entry_word<=0;state<=ST_NS_LOAD;end
                    end else begin
                        query_len<=entry_name_len;
                        for(i=0;i<NAME_BYTES;i=i+1) query_name[i]<=entry_name[i];
                        state<=ST_NS_BEGIN;
                    end
                end
                ST_NS_BEGIN:begin
                    if(!valid_path(1'b1)) begin response<=E_PATH;state<=ST_ERROR;end
                    else begin parent_found<=parent_len==0;entry_index<=0;entry_word<=0;state<=ST_NS_SCAN;end
                end
                ST_NS_SCAN:if(dma_ready) begin
                    decode_entry_word(entry_word,dma_rdata);
                    if(entry_word==15) state<=ST_NS_CHECK;
                    else entry_word<=entry_word+1'b1;
                end
                ST_NS_CHECK:begin
                    if(names_equal(1'b1) && entry_index!=original_index) begin response<=E_EXISTS;state<=ST_ERROR;end
                    else begin
                        if(is_parent(1'b1)) parent_found<=1;
                        if(entry_index==MAX_FILES-1) begin
                            if(!(parent_found || is_parent(1'b1))) begin response<=E_PATH;state<=ST_ERROR;end
                            else if(original_index==MAX_FILES-1) state<=ST_MOUNT_DONE;
                            else begin original_index<=original_index+1'b1;entry_index<=original_index+1'b1;entry_word<=0;state<=ST_NS_LOAD;end
                        end else begin entry_index<=entry_index+1'b1;entry_word<=0;state<=ST_NS_SCAN;end
                    end
                end
                ST_MOUNT_DONE:begin mounted<=1;response<=used_blocks<<8;state<=ST_DONE;end
                ST_NAME_READ:begin
                    if(!ram_range(query_ptr+query_len,1)) begin response<=E_RANGE;state<=ST_ERROR;end
                    else if(dma_ready) begin
                    if(selected_byte==0) begin
                        if(!valid_path(1'b1)) begin response<=E_PATH;state<=ST_ERROR;end
                        else start_search();
                    end else if(query_len==0 && selected_byte=="/" && !leading_slash) begin
                        leading_slash<=1;query_ptr<=query_ptr+1'b1;
                    end else if(query_len>=NAME_BYTES || selected_byte<32) begin response<=E_PATH;state<=ST_ERROR;end
                    else begin query_name[query_len]<=selected_byte;query_len<=query_len+1'b1;end
                    end
                end
                ST_SEARCH_LOAD:if(dma_ready) begin
                    decode_entry_word(entry_word,dma_rdata);
                    if(entry_word==15) begin entry_word<=0;state<=ST_SEARCH_CHECK;end
                    else entry_word<=entry_word+1'b1;
                end
                ST_SEARCH_CHECK:begin
                    if(!valid_entry(1'b1)) begin response<=E_IO;state<=ST_ERROR;end
                    else if(action==ACT_DIR_DELETE_CHECK || action==ACT_DIR_RENAME_CHECK) begin
                        if(is_child(1'b1)) begin response<=E_BUSY;state<=ST_ERROR;end
                        else if(entry_index==MAX_FILES-1) begin_load(original_index,action==ACT_DIR_DELETE_CHECK?ACT_DELETE_COMMIT:ACT_RENAME_NAME);
                        else begin entry_index<=entry_index+1'b1;entry_word<=0;state<=ST_SEARCH_LOAD;end
                    end else begin
                        if(!entry_used && !first_free_valid) begin first_free_valid<=1;first_free_index<=entry_index;end
                        if(is_parent(1'b1)) parent_found<=1;
                        if(names_equal(1'b1) && action==ACT_OPEN) begin
                            handle_owners[entry_index][owner]<=1;response<=entry_index+1'b1;state<=ST_DONE;
                        end else if(names_equal(1'b1) && (action!=ACT_RENAME_SEARCH || entry_index!=original_index)) begin
                            response<=E_EXISTS;state<=ST_ERROR;
                        end else if(entry_index==MAX_FILES-1) begin
                            if(!(parent_found || is_parent(1'b1))) begin response<=E_PATH;state<=ST_ERROR;end
                            else if(action==ACT_CREATE && (first_free_valid || !entry_used)) begin
                                entry_index<=first_free_valid?first_free_index:entry_index;state<=ST_CREATE_PREP;
                            end else if(action==ACT_RENAME_SEARCH) begin_load(original_index,ACT_RENAME_COMMIT);
                            else begin response<=action==ACT_CREATE?E_FULL:E_HANDLE;state<=ST_ERROR;end
                        end else begin entry_index<=entry_index+1'b1;entry_word<=0;state<=ST_SEARCH_LOAD;end
                    end
                end
                ST_CREATE_PREP:begin
                    clear_entry();entry_used<=1;entry_type<=a2[7:0];entry_generation<=1;entry_name_len<=query_len;
                    for(i=0;i<NAME_BYTES;i=i+1) entry_name[i]<=query_name[i];
                    begin_journal(ACT_CREATE);
                end
                ST_ENTRY_LOAD:if(dma_ready) begin
                    decode_entry_word(entry_word,dma_rdata);
                    if(entry_word==15) begin entry_word<=0;state<=ST_ENTRY_DISPATCH;end
                    else entry_word<=entry_word+1'b1;
                end
                ST_ENTRY_DISPATCH:begin
                    if(!valid_entry(1'b1)) begin response<=E_IO;state<=ST_ERROR;end
                    else if(!entry_used && action==ACT_LIST) begin response<=0;state<=ST_DONE;end
                    else if(!entry_used) begin response<=E_HANDLE;state<=ST_ERROR;end
                    else case(action)
                        ACT_READ:begin
                            // FILE_READ is the original three-argument ABI.
                            // r4 is ordinary caller scratch, never an offset.
                            if(entry_type==7 || !ram_range(a2,a3)) begin response<=E_RANGE;state<=ST_ERROR;end
                            else begin
                                copy_src<=entry_start*BLOCK_SIZE;copy_dst<=a2;
                                copy_count<=(a3<entry_size)?a3:entry_size;
                                copy_keep<=(a3<entry_size)?a3:entry_size;
                                copy_index<=0;copy_src_flash<=1;copy_dst_flash<=0;copy_commit<=0;
                                if(a3==0 || entry_size==0) begin response<=0;state<=ST_DONE;end
                                else state<=ST_COPY_READ;
                            end
                        end
                        ACT_WRITE:begin
                            if(entry_type==7 || !ram_range(a2,a3) || a3>32'h400000) begin response<=E_RANGE;state<=ST_ERROR;end
                            else start_allocation(a3);
                        end
                        ACT_TRUNCATE:begin
                            if(entry_type==7 || a2>32'h400000) begin response<=E_RANGE;state<=ST_ERROR;end
                            else start_allocation(a2);
                        end
                        ACT_RENAME,ACT_DELETE:begin
                            if(entry_type==7) begin
                                query_len<=entry_name_len;
                                for(i=0;i<NAME_BYTES;i=i+1) query_name[i]<=entry_name[i];
                                action<=action==ACT_DELETE?ACT_DIR_DELETE_CHECK:ACT_DIR_RENAME_CHECK;
                                entry_index<=0;entry_word<=0;state<=ST_SEARCH_LOAD;
                            end else if(action==ACT_RENAME) begin
                                if(!ram_range(a2,44)) begin response<=E_RANGE;state<=ST_ERROR;end
                                else begin_name(a2,ACT_RENAME_SEARCH);
                            end else begin
                                old_start<=entry_start;old_blocks<=entry_blocks;clear_entry();begin_journal(ACT_DELETE);
                            end
                        end
                        ACT_RENAME_NAME:begin
                            if(!ram_range(a2,44)) begin response<=E_RANGE;state<=ST_ERROR;end
                            else begin_name(a2,ACT_RENAME_SEARCH);
                        end
                        ACT_RENAME_COMMIT:begin
                            entry_name_len<=query_len;entry_generation<=entry_generation+1'b1;
                            for(i=0;i<NAME_BYTES;i=i+1) entry_name[i]<=query_name[i];
                            begin_journal(ACT_RENAME);
                        end
                        ACT_DELETE_COMMIT:begin old_start<=entry_start;old_blocks<=entry_blocks;clear_entry();begin_journal(ACT_DELETE);end
                        ACT_STAT:begin response<={entry_size,entry_start[15:0],entry_name_len,entry_type};state<=ST_DONE;end
                        ACT_LIST:begin list_name_index<=0;state<=ST_LIST_WRITE;end
                        ACT_MINER_SAVE:begin
                            if(a2>256 || !ram_range(a1,a2) || entry_type==7) begin response<=E_RANGE;state<=ST_ERROR;end
                            else start_allocation(256);
                        end
                        ACT_MINER_LOG:begin
                            if(a3>=256 || a2>128 || !ram_range(a1,a2) || entry_size!=32768) begin response<=E_RANGE;state<=ST_ERROR;end
                            else start_allocation(entry_size);
                        end
                        ACT_MINER_LOAD:begin
                            if(a1>=256 || a3>128 || !ram_range(a2,a3) || entry_size!=32768) begin response<=E_RANGE;state<=ST_ERROR;end
                            else begin
                                copy_src<=entry_start*BLOCK_SIZE+(a1<<7);copy_dst<=a2;copy_count<=a3;copy_keep<=a3;
                                copy_index<=0;copy_src_flash<=1;copy_dst_flash<=0;copy_commit<=0;
                                if(a3==0) state<=ST_DONE;else state<=ST_COPY_READ;
                            end
                        end
                        ACT_MINER_LOAD_STATE:begin
                            if(a2>256 || !ram_range(a1,a2) || entry_size!=256) begin response<=E_RANGE;state<=ST_ERROR;end
                            else begin
                                copy_src<=entry_start*BLOCK_SIZE;copy_dst<=a1;copy_count<=a2;copy_keep<=a2;
                                copy_index<=0;copy_src_flash<=1;copy_dst_flash<=0;copy_commit<=0;
                                if(a2==0) state<=ST_DONE;else state<=ST_COPY_READ;
                            end
                        end
                        default:begin response<=E_HANDLE;state<=ST_ERROR;end
                    endcase
                end
                ST_ALLOC_READ:if(dma_ready) begin alloc_word_cache<=dma_rdata;state<=ST_ALLOC_SCAN;end
                ST_ALLOC_SCAN:begin : scan_alloc
                    logic [31:0] candidate;
                    candidate=(alloc_word_index<<5)+alloc_bit_index;
                    if(candidate>=BLOCK_COUNT) begin response<=E_FULL;state<=ST_ERROR;end
                    else if(candidate>=DATA_START_BLOCK && !alloc_word_cache[alloc_bit_index] && alloc_run_len+1>=alloc_needed) begin
                        alloc_start<=alloc_run_len==0?candidate:alloc_run_start;alloc_mark_offset<=0;state<=ST_ALLOC_MARK_READ;
                    end else begin
                        if(candidate<DATA_START_BLOCK || alloc_word_cache[alloc_bit_index]) alloc_run_len<=0;
                        else begin if(alloc_run_len==0) alloc_run_start<=candidate;alloc_run_len<=alloc_run_len+1'b1;end
                        if(alloc_bit_index==31) begin alloc_bit_index<=0;alloc_word_index<=alloc_word_index+1'b1;state<=ST_ALLOC_READ;end
                        else alloc_bit_index<=alloc_bit_index+1'b1;
                    end
                end
                ST_ALLOC_MARK_READ:if(dma_ready) begin
                    bitmap_modify_word<=dma_rdata;bitmap_modify_bit<=(alloc_start+alloc_mark_offset)&31;state<=ST_ALLOC_MARK_WRITE;
                end
                ST_ALLOC_MARK_WRITE:if(dma_ready) begin
                    used_blocks<=used_blocks+1'b1;
                    if(alloc_mark_offset+1>=alloc_needed) begin
                        entry_start<=alloc_start;entry_blocks<=alloc_needed;entry_size<=new_size;entry_generation<=entry_generation+1'b1;
                        copy_dst<=alloc_start*BLOCK_SIZE;copy_count<=new_size;copy_index<=0;copy_dst_flash<=1;copy_commit<=1;
                        copy_overlay_mode<=action==ACT_MINER_LOG;copy_overlay<=a3<<7;copy_payload_len<=a2;
                        if(action==ACT_TRUNCATE || action==ACT_MINER_LOG) begin
                            copy_src<=old_start*BLOCK_SIZE;copy_src_flash<=1;copy_keep<=old_size<new_size?old_size:new_size;
                        end else begin
                            copy_src<=action==ACT_MINER_SAVE?a1:a2;copy_src_flash<=0;
                            copy_keep<=action==ACT_MINER_SAVE?a2:new_size;
                        end
                        state<=ST_COPY_READ;
                    end else begin alloc_mark_offset<=alloc_mark_offset+1'b1;state<=ST_ALLOC_MARK_READ;end
                end
                ST_COPY_READ:if(copy_zero || dma_ready) begin copy_byte<=copy_zero?8'd0:selected_byte;state<=ST_COPY_WRITE;end
                ST_COPY_WRITE:if(dma_ready) begin
                    if(copy_index+1>=copy_count) begin
                        if(copy_commit) begin_journal(action);
                        else begin response<=copy_count;state<=ST_DONE;end
                    end else begin copy_index<=copy_index+1'b1;state<=ST_COPY_READ;end
                end
                ST_FREE_READ:if(dma_ready) begin
                    bitmap_modify_word<=dma_rdata;bitmap_modify_bit<=(free_start+free_offset)&31;state<=ST_FREE_WRITE;
                end
                ST_FREE_WRITE:if(dma_ready) begin
                    if(bitmap_modify_word[bitmap_modify_bit]) used_blocks<=used_blocks-1'b1;
                    if(free_offset+1>=free_count) begin response<=successful_result;state<=ST_DONE;end
                    else begin free_offset<=free_offset+1'b1;state<=ST_FREE_READ;end
                end
                ST_LIST_WRITE:if(dma_ready) begin
                    if(list_name_index>=entry_name_len) begin response<={entry_size,entry_start[15:0],entry_name_len,entry_type};state<=ST_DONE;end
                    else list_name_index<=list_name_index+1'b1;
                end
                ST_JINDEX_WRITE:if(dma_ready) begin entry_word<=0;state<=ST_JPAYLOAD_WRITE;end
                ST_JPAYLOAD_WRITE:if(dma_ready) begin
                    journal_checksum<=journal_checksum^encode_entry_word(entry_word);
                    if(entry_word==15) state<=ST_JCHECKSUM_WRITE;
                    else entry_word<=entry_word+1'b1;
                end
                ST_JCHECKSUM_WRITE:if(dma_ready) state<=ST_JCOMMIT;
                ST_JCOMMIT:if(dma_ready) begin entry_word<=0;state<=ST_ENTRY_FLUSH;end
                ST_ENTRY_FLUSH:if(dma_ready) begin
                    if(entry_word==15) state<=ST_JCLEAR;
                    else entry_word<=entry_word+1'b1;
                end
                ST_JCLEAR:if(dma_ready) begin
                    if(recovering) begin recovering<=0;bitmap_index<=0;used_blocks<=DATA_START_BLOCK;state<=ST_REBUILD_CLEAR;end
                    else state<=ST_ENTRY_FLUSH_DONE;
                end
                ST_ENTRY_FLUSH_DONE:begin
                    case(after_flush_action)
                        ACT_CREATE:begin handle_owners[entry_index][owner]<=1;response<=entry_index+1'b1;state<=ST_DONE;end
                        ACT_RENAME:begin response<=0;state<=ST_DONE;end
                        default:begin
                            if(after_flush_action==ACT_DELETE) handle_owners[entry_index]<=0;
                            if(old_blocks==0) begin response<=successful_result;state<=ST_DONE;end
                            else begin free_start<=old_start;free_count<=old_blocks;free_offset<=0;state<=ST_FREE_READ;end
                        end
                    endcase
                end
                ST_DONE,ST_ERROR:begin end
                default:state<=ST_IDLE;
            endcase
        end
    end
endmodule
