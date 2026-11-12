`include "shama_boot_params.svh"

// OS service sequencer. A replacement/shutdown first completes requested saves,
// then closes app-owned handles and zeroes allocations/workspaces. Refused saves
// preserve the foreground program and expose an error; no forced teardown occurs.
module shama_services #(
    parameter integer APP_SLOT_BYTES=16*1024
)(
    input logic clk,rst,
    input logic sys_valid,
    input logic [11:0] sys_id,
    input logic [191:0] sys_args,
    output logic sys_ready,
    output logic [63:0] sys_ret,
    output logic sys_jump_valid,
    output logic [31:0] sys_jump_pc,
    input logic event_valid,
    input logic [7:0] event_code,key_code,
    input logic [9:0] controller_latched,
    output logic event_ack,
    input logic input_overflow,
    input logic [7:0] input_queued_count,
    input logic keyboard_shift,
    input logic shutdown_request,
    output logic shutdown_ready,
    output logic [31:0] lifecycle_error,
    output logic fs_cleanup_valid,
    output logic [3:0] fs_cleanup_owner,
    input logic fs_cleanup_ready,
    input logic [31:0] flash_used_bytes,
    input logic [63:0] time_counter,
    output logic [31:0] ram_used_bytes,cache_used_bytes,
    output logic dma_valid,dma_we,dma_flash,dma_cache,
    output logic [31:0] dma_addr,dma_wdata,
    output logic [3:0] dma_wstrb,
    input logic dma_ready,
    input logic [31:0] dma_rdata,
    output logic ext_valid,
    output logic [11:0] ext_id,
    output logic [191:0] ext_args,
    input logic ext_ready,
    input logic [63:0] ext_ret,
    input logic ext_jump_valid,
    input logic [31:0] ext_jump_pc,
    input logic ext_load_app,
    input logic [3:0] ext_app_id,
    output logic os_loaded,
    output logic [3:0] foreground_app,
    output logic cpu_hold,cpu_force_jump,
    output logic [31:0] cpu_force_pc
);
    localparam [11:0]
        SYS_EXIT=12'h001,SYS_YIELD=12'h002,SYS_GET_EVENT=12'h003,
        SYS_GET_TIME=12'h004,SYS_GET_COUNTER=12'h005,SYS_APP_LAUNCH=12'h006,
        SYS_APP_EXIT_FOREGROUND=12'h007,SYS_BOOT_LOAD_OS=12'h009,
        SYS_LIFECYCLE_STATUS=12'h00b,SYS_SHUTDOWN=12'h00c,
        SYS_ALLOC=12'h010,SYS_FREE=12'h011,SYS_RAM_USAGE=12'h012,
        SYS_CACHE_USAGE=12'h013,SYS_CACHE_FLUSH_APP=12'h014,
        SYS_FLASH_USAGE=12'h02a,SYS_GET_KEY=12'h030,
        SYS_GET_CONTROLLER=12'h031,SYS_INPUT_STATUS=12'h032,
        SYS_RUN_BUFFER=12'h061;
    localparam [7:0] EVT_HOME=8'h16,EVT_EXIT=8'h17,EVT_EDITOR=8'h18,EVT_FILES=8'h19;
    localparam integer HEAP_BASE=32'h00010000,HEAP_PAGE_BYTES=4096,HEAP_PAGES=160,MAX_ALLOCS=16;
    localparam [31:0] EDIT_CTX=32'h000b1000,EDIT_BUF=32'h000c0000,MINER_RECORD=32'h000b2200;
    typedef enum logic [5:0] {
        ST_IDLE,ST_SLOT_LENGTH,ST_COPY_READ,ST_COPY_RAM_WRITE,ST_COPY_CACHE_WRITE,
        ST_APP_CLEAR_RAM,ST_APP_CLEAR_CACHE,ST_EXT,ST_RESP,
        ST_CLOSE_BEGIN,ST_CLOSE_EDITOR_HANDLE,ST_CLOSE_EDITOR_LENGTH,
        ST_CLOSE_EDITOR_DIRTY,ST_CLOSE_MINER_STOP,ST_CLOSE_SAVE,ST_CLOSE_SAVE_DROP,
        ST_CLOSE_HANDLES,ST_HEAP_NEXT,ST_HEAP_CLEAR,ST_HEAP_INIT,ST_WORK_NEXT,ST_WORK_CLEAR,
        ST_FLUSH_READ,ST_FLUSH_WRITE,ST_SHUTDOWN_DONE,ST_SNAPSHOT_READ,ST_SNAPSHOT_DELAY
    } state_t;
    state_t state;
    logic [11:0] latched_id;
    logic [191:0] latched_args;
    logic [31:0] copy_offset,copy_word,copy_source_base,copy_ram_base,copy_bytes;
    logic copy_source_flash,copy_to_cache,copy_is_kernel,boot_after_kernel;
    logic [3:0] target_app;
    logic run_staged;
    logic [31:0] staged_source_ptr,staged_bytes;
    logic [31:0] kernel_loaded_bytes,app_loaded_bytes,heap_used_bytes;
    logic heap_bitmap [0:HEAP_PAGES-1];
    logic alloc_used [0:MAX_ALLOCS-1];
    logic [31:0] alloc_ptr [0:MAX_ALLOCS-1];
    logic [7:0] alloc_pages [0:MAX_ALLOCS-1];
    logic [63:0] response;
    logic response_jump;
    logic [31:0] response_pc;
    logic async_switch,force_jump_pulse,close_shutdown,force_discard;
    logic shutdown_pending,shutdown_prev;
    logic [31:0] close_handle,close_length;
    logic [4:0] clear_slot;
    logic free_one;
    logic [3:0] workspace_index;
    logic [31:0] clear_base,clear_bytes;
    logic [3:0] snapshot_delay;
    integer i;

    function automatic [31:0] app_flash(input logic [3:0] app_id);
        begin
            case(app_id)
                4'd0: app_flash = `SHAMA_DESKTOP_FLASH;
                4'd1: app_flash = `SHAMA_EDITOR_FLASH;
                4'd2: app_flash = `SHAMA_FILES_FLASH;
                4'd3: app_flash = `SHAMA_MINER_FLASH;
                4'd4: app_flash = `SHAMA_MONITOR_FLASH;
                4'd5: app_flash = `SHAMA_TERMINAL_FLASH;
                4'd6: app_flash = `SHAMA_CALCULATOR_FLASH;
                4'd7: app_flash = `SHAMA_PAINT_FLASH;
                4'd8: app_flash = `SHAMA_SETTINGS_FLASH;
                default: app_flash = `SHAMA_DESKTOP_FLASH;
            endcase
        end
    endfunction

    function automatic logic valid_app(input logic [31:0] app_id);
        valid_app = app_id <= 8;
    endfunction

    function automatic logic universal_event(input logic [7:0] code);
        universal_event =
            code==EVT_HOME || code==EVT_EXIT ||
            code==EVT_EDITOR || code==EVT_FILES;
    endfunction

    function automatic [3:0] universal_app(input logic [7:0] code);
        begin
            case(code)
                EVT_EDITOR: universal_app=4'd1;
                EVT_FILES:  universal_app=4'd2;
                default:    universal_app=4'd0;
            endcase
        end
    endfunction

    function automatic integer find_free_alloc_slot;
        integer k;
        begin
            find_free_alloc_slot=-1;
            for(k=0;k<MAX_ALLOCS;k=k+1)
                if(!alloc_used[k] && find_free_alloc_slot<0)
                    find_free_alloc_slot=k;
        end
    endfunction

    function automatic integer find_heap_run(input integer needed);
        integer k,run,start;
        begin
            run=0;start=0;find_heap_run=-1;
            for(k=0;k<HEAP_PAGES;k=k+1) begin
                if(!heap_bitmap[k]) begin
                    if(run==0) start=k;
                    run=run+1;
                    if(run>=needed && find_heap_run<0)
                        find_heap_run=start;
                end else run=0;
            end
        end
    endfunction

    function automatic integer find_alloc_by_ptr(input logic [31:0] ptr);
        integer k;
        begin
            find_alloc_by_ptr=-1;
            for(k=0;k<MAX_ALLOCS;k=k+1)
                if(alloc_used[k] && alloc_ptr[k]==ptr && find_alloc_by_ptr<0)
                    find_alloc_by_ptr=k;
        end
    endfunction


    function automatic [31:0] workspace_bytes(input logic [3:0] app_id);
        case(app_id)
            1,2:workspace_bytes=32'd66112+APP_SLOT_BYTES;
            3:workspace_bytes=5376;
            5:workspace_bytes=1024;
            7,8:workspace_bytes=272; // config record plus filename staging at +0x100
            15:workspace_bytes=APP_SLOT_BYTES;
            default:workspace_bytes=0;
        endcase
    endfunction
    function automatic [31:0] workspace_base(input logic [3:0] idx);
        case(idx)
            0:workspace_base=32'h000b0000;
            1:workspace_base=32'h000b1000;
            2:workspace_base=32'h000c0000;
            3:workspace_base=32'h000e0000;
            4:workspace_base=32'h000b2000;
            5:workspace_base=32'h000b3000;
            6:workspace_base=32'h000b4000;
            7:workspace_base=32'h000b5000;
            default:workspace_base=0;
        endcase
    endfunction
    function automatic [31:0] workspace_size(input logic [3:0] idx);
        logic handoff;
        begin
            handoff=(foreground_app==2 && target_app==1 && !close_shutdown);
            workspace_size=0;
            case(idx)
                0:if((foreground_app==1 || foreground_app==2) && !handoff) workspace_size=512;
                1:if((foreground_app==1 || foreground_app==2) && !handoff) workspace_size=64;
                2:if((foreground_app==1 || foreground_app==2) && !handoff) workspace_size=65536;
                3:if((foreground_app==1 || foreground_app==2 || foreground_app==15) && !run_staged) workspace_size=APP_SLOT_BYTES;
                4:if(foreground_app==3) workspace_size=4096;
                5:if(foreground_app==3) workspace_size=1280;
                  else if(foreground_app==5) workspace_size=1024;
                6:if(foreground_app==8) workspace_size=272;
                7:if(foreground_app==7) workspace_size=272;
                default:begin end
            endcase
        end
    endfunction
    function automatic logic is_local(input logic [11:0] id);
        case(id)
            SYS_EXIT,SYS_YIELD,SYS_GET_EVENT,SYS_GET_TIME,SYS_GET_COUNTER,
            SYS_APP_LAUNCH,SYS_APP_EXIT_FOREGROUND,SYS_BOOT_LOAD_OS,
            SYS_LIFECYCLE_STATUS,SYS_SHUTDOWN,SYS_ALLOC,SYS_FREE,
            SYS_RAM_USAGE,SYS_CACHE_USAGE,SYS_CACHE_FLUSH_APP,
            SYS_FLASH_USAGE,SYS_GET_KEY,SYS_GET_CONTROLLER,SYS_INPUT_STATUS,
            SYS_RUN_BUFFER:is_local=1;
            default:is_local=0;
        endcase
    endfunction
    task automatic begin_app_replace(input logic [3:0] app_id);
        begin
            target_app<=app_id;async_switch<=0;run_staged<=0;close_shutdown<=0;
            force_discard<=0;copy_offset<=0;lifecycle_error<=0;state<=ST_CLOSE_BEGIN;
        end
    endtask
    task automatic refuse_close(input logic [31:0] error_code);
        begin
            lifecycle_error<=error_code;response<={32'hffffffff,error_code};
            close_shutdown<=0;shutdown_pending<=0;run_staged<=0;
            if(async_switch) begin async_switch<=0;state<=ST_IDLE;end
            else state<=ST_RESP;
        end
    endtask
    task automatic finish_heap_clear;
        integer first_page;
        begin
            first_page=(alloc_ptr[clear_slot]-HEAP_BASE)/HEAP_PAGE_BYTES;
            for(integer k=0;k<HEAP_PAGES;k=k+1)
                if(k>=first_page && k<first_page+alloc_pages[clear_slot]) heap_bitmap[k]<=0;
            heap_used_bytes<=heap_used_bytes-alloc_pages[clear_slot]*HEAP_PAGE_BYTES;
            alloc_used[clear_slot]<=0;alloc_ptr[clear_slot]<=0;alloc_pages[clear_slot]<=0;
            copy_offset<=0;
            if(free_one) begin free_one<=0;response<=0;state<=ST_RESP;end
            else begin clear_slot<=clear_slot+1'b1;state<=ST_HEAP_NEXT;end
        end
    endtask

    always_comb begin
        sys_ready=state==ST_RESP;sys_ret=response;
        sys_jump_valid=state==ST_RESP && response_jump;sys_jump_pc=response_pc;
        cpu_hold=(async_switch && state!=ST_SNAPSHOT_READ && state!=ST_SNAPSHOT_DELAY) || shutdown_ready;
        cpu_force_jump=force_jump_pulse;cpu_force_pc=`SHAMA_PC_APP;
        ext_valid=state==ST_EXT || state==ST_CLOSE_SAVE;
        ext_id=latched_id;ext_args=latched_args;
        fs_cleanup_valid=state==ST_CLOSE_HANDLES;
        fs_cleanup_owner=foreground_app;
        ram_used_bytes=kernel_loaded_bytes+app_loaded_bytes+heap_used_bytes+
                       (os_loaded?workspace_bytes(foreground_app):0);
        cache_used_bytes=app_loaded_bytes;
        dma_valid=0;dma_we=0;dma_flash=0;dma_cache=0;
        dma_addr=0;dma_wdata=copy_word;dma_wstrb=4'b1111;
        case(state)
            ST_SLOT_LENGTH:begin dma_valid=1;dma_flash=1;dma_addr=copy_source_base;end
            ST_COPY_READ:begin dma_valid=1;dma_flash=copy_source_flash;dma_addr=copy_source_base+copy_offset;end
            ST_COPY_RAM_WRITE:begin dma_valid=1;dma_we=1;dma_addr=copy_ram_base+copy_offset;end
            ST_COPY_CACHE_WRITE:begin dma_valid=1;dma_we=1;dma_cache=1;dma_addr=copy_offset;end
            ST_APP_CLEAR_RAM:begin dma_valid=1;dma_we=1;dma_addr=`SHAMA_APP_RAM_BASE+copy_offset;dma_wdata=0;end
            ST_APP_CLEAR_CACHE:begin dma_valid=1;dma_we=1;dma_cache=1;dma_addr=copy_offset;dma_wdata=0;end
            ST_SNAPSHOT_READ:begin dma_valid=1;dma_addr=(foreground_app==3)?MINER_RECORD+252:EDIT_CTX+36;end
            ST_CLOSE_EDITOR_HANDLE:begin dma_valid=1;dma_addr=EDIT_CTX;end
            ST_CLOSE_EDITOR_LENGTH:begin dma_valid=1;dma_addr=EDIT_CTX+8;end
            ST_CLOSE_EDITOR_DIRTY:begin dma_valid=1;dma_addr=EDIT_CTX+20;end
            ST_CLOSE_MINER_STOP:begin dma_valid=1;dma_we=1;dma_addr=MINER_RECORD+24;dma_wdata=0;end
            ST_HEAP_CLEAR,ST_HEAP_INIT,ST_WORK_CLEAR:begin dma_valid=1;dma_we=1;dma_addr=clear_base+copy_offset;dma_wdata=0;end
            ST_FLUSH_READ:begin dma_valid=1;dma_cache=1;dma_addr=copy_offset;end
            ST_FLUSH_WRITE:begin dma_valid=1;dma_we=1;dma_addr=`SHAMA_APP_RAM_BASE+copy_offset;end
            default:begin end
        endcase
    end

    always_ff @(posedge clk) begin
        if(rst) begin
            state<=ST_IDLE;latched_id<=0;latched_args<=0;
            copy_offset<=0;copy_word<=0;copy_source_base<=0;copy_ram_base<=0;copy_bytes<=0;
            copy_source_flash<=1;copy_to_cache<=0;copy_is_kernel<=0;boot_after_kernel<=0;
            target_app<=0;run_staged<=0;staged_source_ptr<=0;staged_bytes<=0;
            kernel_loaded_bytes<=0;app_loaded_bytes<=0;heap_used_bytes<=0;
            for(i=0;i<HEAP_PAGES;i=i+1) heap_bitmap[i]<=0;
            for(i=0;i<MAX_ALLOCS;i=i+1) begin alloc_used[i]<=0;alloc_ptr[i]<=0;alloc_pages[i]<=0;end
            response<=0;response_jump<=0;response_pc<=0;async_switch<=0;force_jump_pulse<=0;
            event_ack<=0;os_loaded<=0;foreground_app<=0;
            close_shutdown<=0;force_discard<=0;shutdown_pending<=0;shutdown_prev<=0;shutdown_ready<=0;
            lifecycle_error<=0;close_handle<=0;close_length<=0;clear_slot<=0;free_one<=0;
            workspace_index<=0;clear_base<=0;clear_bytes<=0;snapshot_delay<=0;
        end else begin
            event_ack<=0;force_jump_pulse<=0;shutdown_prev<=shutdown_request;
            if(shutdown_request && !shutdown_prev && !shutdown_ready) shutdown_pending<=1;
            if(!shutdown_request && !close_shutdown) shutdown_ready<=0;
            if(state==ST_RESP && !sys_valid) begin state<=ST_IDLE;response_jump<=0;end
            case(state)
                ST_IDLE:begin
                    if(shutdown_pending) begin
                        async_switch<=1;close_shutdown<=1;force_discard<=0;run_staged<=0;target_app<=0;
                        lifecycle_error<=0;state<=(foreground_app==1 || foreground_app==3)?ST_SNAPSHOT_READ:ST_CLOSE_BEGIN;
                    end else if(event_valid && universal_event(event_code)) begin
                        event_ack<=1;async_switch<=1;target_app<=universal_app(event_code);
                        close_shutdown<=0;force_discard<=0;run_staged<=0;lifecycle_error<=0;state<=(foreground_app==1 || foreground_app==3)?ST_SNAPSHOT_READ:ST_CLOSE_BEGIN;
                    end else if(sys_valid) begin
                        latched_id<=sys_id;latched_args<=sys_args;response<=0;response_jump<=0;response_pc<=0;
                        if(!is_local(sys_id)) state<=ST_EXT;
                        else case(sys_id)
                            SYS_EXIT,SYS_APP_EXIT_FOREGROUND:begin_app_replace(0);
                            SYS_YIELD:state<=ST_RESP;
                            SYS_SHUTDOWN:begin
                                async_switch<=0;close_shutdown<=1;force_discard<=0;run_staged<=0;target_app<=0;
                                lifecycle_error<=0;state<=ST_CLOSE_BEGIN;
                            end
                            SYS_LIFECYCLE_STATUS:begin response<={27'd0,close_shutdown,target_app,lifecycle_error};state<=ST_RESP;end
                            SYS_BOOT_LOAD_OS:begin
                                copy_source_base<=`SHAMA_KERNEL_FLASH;copy_ram_base<=`SHAMA_KERNEL_RAM_BASE;copy_offset<=0;
                                copy_source_flash<=1;copy_to_cache<=0;copy_is_kernel<=1;boot_after_kernel<=1;
                                close_shutdown<=0;state<=ST_SLOT_LENGTH;
                            end
                            SYS_APP_LAUNCH:begin
                                if(valid_app(sys_args[31:0])) begin begin_app_replace(sys_args[3:0]);force_discard<=sys_args[32];end
                                else begin response<=64'hffffffffffffffff;state<=ST_RESP;end
                            end
                            SYS_RUN_BUFFER:begin
                                if(sys_args[63:32]==0 || sys_args[63:32]>APP_SLOT_BYTES-4 ||
                                   sys_args[31:0]<32'h000e0000 || sys_args[31:0]>32'h000e0000+APP_SLOT_BYTES-sys_args[63:32] ||
                                   sys_args[1:0]!=0 || sys_args[33:32]!=0) begin
                                    response<=64'hfffffffffffffffa;state<=ST_RESP;
                                end else begin
                                    target_app<=15;async_switch<=0;run_staged<=1;close_shutdown<=0;force_discard<=0;
                                    staged_source_ptr<=sys_args[31:0];staged_bytes<=sys_args[63:32];lifecycle_error<=0;state<=ST_CLOSE_BEGIN;
                                end
                            end
                            SYS_GET_EVENT:begin
                                if(event_valid) begin response<={28'd0,controller_latched,key_code,event_code,10'd0};event_ack<=1;end
                                state<=ST_RESP;
                            end
                            SYS_GET_KEY:begin
                                if(event_valid && event_code==8'h01) begin response<={56'd0,key_code};event_ack<=1;end
                                state<=ST_RESP;
                            end
                            SYS_GET_CONTROLLER:begin response<={54'd0,controller_latched};state<=ST_RESP;end
                            SYS_INPUT_STATUS:begin response<={54'd0,keyboard_shift,input_overflow,input_queued_count};state<=ST_RESP;end
                            SYS_GET_TIME,SYS_GET_COUNTER:begin response<=time_counter;state<=ST_RESP;end
                            SYS_ALLOC:begin : allocate
                                integer needed_pages,run_start,slot;
                                needed_pages=(sys_args[31:0]+HEAP_PAGE_BYTES-1)/HEAP_PAGE_BYTES;
                                run_start=find_heap_run(needed_pages);slot=find_free_alloc_slot();
                                if(sys_args[31:0]==0 || sys_args[31:0]>HEAP_PAGES*HEAP_PAGE_BYTES ||
                                   needed_pages<=0 || run_start<0 || slot<0) response<=64'hffffffffffffffff;
                                else begin
                                    for(i=0;i<HEAP_PAGES;i=i+1) if(i>=run_start && i<run_start+needed_pages) heap_bitmap[i]<=1;
                                    alloc_used[slot]<=1;alloc_ptr[slot]<=HEAP_BASE+run_start*HEAP_PAGE_BYTES;alloc_pages[slot]<=needed_pages;
                                    heap_used_bytes<=heap_used_bytes+needed_pages*HEAP_PAGE_BYTES;response<=HEAP_BASE+run_start*HEAP_PAGE_BYTES;
                                    clear_base<=HEAP_BASE+run_start*HEAP_PAGE_BYTES;clear_bytes<=needed_pages*HEAP_PAGE_BYTES;
                                    copy_offset<=0;state<=ST_HEAP_INIT;
                                end
                                if(sys_args[31:0]==0 || sys_args[31:0]>HEAP_PAGES*HEAP_PAGE_BYTES ||
                                   needed_pages<=0 || run_start<0 || slot<0) state<=ST_RESP;
                            end
                            SYS_FREE:begin : release_allocation
                                integer slot;
                                slot=find_alloc_by_ptr(sys_args[31:0]);
                                if(slot<0) begin response<=64'hffffffffffffffff;state<=ST_RESP;end
                                else begin
                                    clear_slot<=slot;free_one<=1;clear_base<=alloc_ptr[slot];clear_bytes<=alloc_pages[slot]*HEAP_PAGE_BYTES;
                                    copy_offset<=0;state<=ST_HEAP_CLEAR;
                                end
                            end
                            SYS_RAM_USAGE:begin response<={32'd1048576,ram_used_bytes};state<=ST_RESP;end
                            SYS_CACHE_USAGE:begin response<={32'd16384,cache_used_bytes};state<=ST_RESP;end
                            SYS_FLASH_USAGE:begin response<={32'd4194304,flash_used_bytes};state<=ST_RESP;end
                            SYS_CACHE_FLUSH_APP:begin copy_offset<=0;if(app_loaded_bytes==0) state<=ST_RESP;else state<=ST_FLUSH_READ;end
                            default:state<=ST_RESP;
                        endcase
                    end
                end
                ST_SNAPSHOT_READ:if(dma_ready) begin
                    if(!dma_rdata[0]) state<=ST_CLOSE_BEGIN;
                    else begin snapshot_delay<=15;state<=ST_SNAPSHOT_DELAY;end
                end
                ST_SNAPSHOT_DELAY:begin
                    if(snapshot_delay==0) state<=ST_SNAPSHOT_READ;
                    else snapshot_delay<=snapshot_delay-1'b1;
                end
                ST_CLOSE_BEGIN:begin
                    if(foreground_app==1 && os_loaded && !force_discard) state<=ST_CLOSE_EDITOR_HANDLE;
                    else if(foreground_app==3 && os_loaded && !force_discard) state<=ST_CLOSE_MINER_STOP;
                    else state<=ST_CLOSE_HANDLES;
                end
                ST_CLOSE_EDITOR_HANDLE:if(dma_ready) begin close_handle<=dma_rdata;state<=ST_CLOSE_EDITOR_LENGTH;end
                ST_CLOSE_EDITOR_LENGTH:if(dma_ready) begin close_length<=dma_rdata;state<=ST_CLOSE_EDITOR_DIRTY;end
                ST_CLOSE_EDITOR_DIRTY:if(dma_ready) begin
                    if(dma_rdata==0) state<=ST_CLOSE_HANDLES;
                    else if(close_handle==0 || close_length>65535) refuse_close(32'hfffffffa);
                    else begin
                        latched_id<=12'h024;latched_args<=0;
                        latched_args[31:0]<=close_handle;latched_args[63:32]<=EDIT_BUF;latched_args[95:64]<=close_length;
                        state<=ST_CLOSE_SAVE;
                    end
                end
                ST_CLOSE_MINER_STOP:if(dma_ready) begin
                    latched_id<=12'h051;latched_args<=0;latched_args[31:0]<=MINER_RECORD;latched_args[63:32]<=256;state<=ST_CLOSE_SAVE;
                end
                ST_CLOSE_SAVE:if(ext_ready) begin
                    if(ext_ret[63:32]==32'hffffffff) refuse_close(ext_ret[31:0]);
                    else state<=ST_CLOSE_SAVE_DROP;
                end
                ST_CLOSE_SAVE_DROP:if(!ext_ready) state<=ST_CLOSE_HANDLES;
                ST_CLOSE_HANDLES:if(fs_cleanup_ready) begin clear_slot<=0;free_one<=0;state<=ST_HEAP_NEXT;end
                ST_HEAP_NEXT:begin
                    if(clear_slot>=MAX_ALLOCS) begin workspace_index<=0;state<=ST_WORK_NEXT;end
                    else if(alloc_used[clear_slot]) begin
                        clear_base<=alloc_ptr[clear_slot];clear_bytes<=alloc_pages[clear_slot]*HEAP_PAGE_BYTES;copy_offset<=0;state<=ST_HEAP_CLEAR;
                    end else clear_slot<=clear_slot+1'b1;
                end
                ST_HEAP_INIT:if(dma_ready) begin
                    if(copy_offset+4>=clear_bytes) begin copy_offset<=0;state<=ST_RESP;end
                    else copy_offset<=copy_offset+4;
                end
                ST_HEAP_CLEAR:if(dma_ready) begin
                    if(copy_offset+4>=clear_bytes) finish_heap_clear();
                    else copy_offset<=copy_offset+4;
                end
                ST_WORK_NEXT:begin
                    if(workspace_index>=8) begin copy_offset<=0;state<=ST_APP_CLEAR_RAM;end
                    else if(workspace_size(workspace_index)!=0) begin
                        clear_base<=workspace_base(workspace_index);clear_bytes<=workspace_size(workspace_index);copy_offset<=0;state<=ST_WORK_CLEAR;
                    end else workspace_index<=workspace_index+1'b1;
                end
                ST_WORK_CLEAR:if(dma_ready) begin
                    if(copy_offset+4>=clear_bytes) begin workspace_index<=workspace_index+1'b1;copy_offset<=0;state<=ST_WORK_NEXT;end
                    else copy_offset<=copy_offset+4;
                end
                ST_APP_CLEAR_RAM:if(dma_ready) begin
                    if(copy_offset+4>=APP_SLOT_BYTES) begin copy_offset<=0;state<=ST_APP_CLEAR_CACHE;end
                    else copy_offset<=copy_offset+4;
                end
                ST_APP_CLEAR_CACHE:if(dma_ready) begin
                    if(copy_offset+4>=APP_SLOT_BYTES) begin
                        copy_offset<=0;app_loaded_bytes<=0;
                        if(close_shutdown) begin os_loaded<=0;state<=ST_SHUTDOWN_DONE;end
                        else if(run_staged) begin
                            copy_source_base<=staged_source_ptr;copy_source_flash<=0;copy_ram_base<=`SHAMA_APP_RAM_BASE;
                            copy_bytes<=staged_bytes;copy_to_cache<=1;copy_is_kernel<=0;boot_after_kernel<=0;state<=ST_COPY_READ;
                        end else begin
                            copy_source_base<=app_flash(target_app);copy_source_flash<=1;copy_ram_base<=`SHAMA_APP_RAM_BASE;
                            copy_to_cache<=1;copy_is_kernel<=0;boot_after_kernel<=0;state<=ST_SLOT_LENGTH;
                        end
                    end else copy_offset<=copy_offset+4;
                end
                ST_SLOT_LENGTH:if(dma_ready) begin
                    if(dma_rdata==0 || dma_rdata>APP_SLOT_BYTES-4 || dma_rdata[1:0]!=0) begin
                        response<=64'hfffffffffffffff9;lifecycle_error<=32'hfffffff9;
                        if(async_switch) begin async_switch<=0;state<=ST_IDLE;end else state<=ST_RESP;
                    end else begin copy_bytes<=dma_rdata;copy_source_base<=copy_source_base+`SHAMA_SLOT_DATA_OFFSET;copy_offset<=0;state<=ST_COPY_READ;end
                end
                ST_COPY_READ:if(dma_ready) begin copy_word<=dma_rdata;state<=ST_COPY_RAM_WRITE;end
                ST_COPY_RAM_WRITE:if(dma_ready) begin
                    if(copy_to_cache) state<=ST_COPY_CACHE_WRITE;
                    else if(copy_offset+4>=copy_bytes) begin
                        kernel_loaded_bytes<=copy_bytes;
                        if(copy_is_kernel && boot_after_kernel) begin
                            target_app<=0;run_staged<=0;copy_offset<=0;copy_is_kernel<=0;boot_after_kernel<=0;state<=ST_APP_CLEAR_RAM;
                        end else state<=ST_RESP;
                    end else begin copy_offset<=copy_offset+4;state<=ST_COPY_READ;end
                end
                ST_COPY_CACHE_WRITE:if(dma_ready) begin
                    if(copy_offset+4>=copy_bytes) begin
                        app_loaded_bytes<=copy_bytes;foreground_app<=target_app;os_loaded<=1;run_staged<=0;
                        if(async_switch) begin force_jump_pulse<=1;async_switch<=0;state<=ST_IDLE;end
                        else begin response<=copy_bytes;response_jump<=1;response_pc<=`SHAMA_PC_APP;state<=ST_RESP;end
                    end else begin copy_offset<=copy_offset+4;state<=ST_COPY_READ;end
                end
                ST_FLUSH_READ:if(dma_ready) begin copy_word<=dma_rdata;state<=ST_FLUSH_WRITE;end
                ST_FLUSH_WRITE:if(dma_ready) begin
                    if(copy_offset+4>=app_loaded_bytes) begin response<=app_loaded_bytes;state<=ST_RESP;end
                    else begin copy_offset<=copy_offset+4;state<=ST_FLUSH_READ;end
                end
                ST_EXT:if(ext_ready) begin
                    if(ext_load_app) begin
                        if(valid_app(ext_app_id)) begin_app_replace(ext_app_id);
                        else begin response<=64'hffffffffffffffff;state<=ST_RESP;end
                    end else begin response<=ext_ret;response_jump<=ext_jump_valid;response_pc<=ext_jump_pc;state<=ST_RESP;end
                end
                ST_SHUTDOWN_DONE:begin
                    shutdown_ready<=1;shutdown_pending<=0;
                    if(!async_switch) begin response<=0;state<=ST_RESP;end
                end
                ST_RESP:begin end
                default:state<=ST_IDLE;
            endcase
        end
    end
endmodule
