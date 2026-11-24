// Fixed physical payload slots; only narrow chronological handles move on removal.
// The storage bus holds a transfer until ready. One response retires one command.
module input_slot_worker #(parameter integer DEPTH=16, parameter integer IDX=(DEPTH<2)?1:$clog2(DEPTH))(
    input wire clk,rst,cmd_valid,resp_ready,
    input wire [1:0] cmd,                       // observe, ACK, ordered arrivals, overflow summary
    input wire [25:0] cmd_ack_data,
    input wire [9:0] cmd_levels,cmd_edges,
    input wire cmd_key_valid,cmd_shift,
    input wire [7:0] cmd_key,
    output wire cmd_ready,resp_valid,
    output reg [36:0] snapshot,
    output wire mem_valid,mem_write,
    output wire [IDX-1:0] mem_addr,
    output wire [25:0] mem_wdata,
    input wire mem_ready,
    input wire [25:0] mem_rdata
);
    localparam OBSERVE=0,ACK=1,ARRIVE=2,LOST=3;
    localparam IDLE=0,ACK_SCAN=1,EVENT=2,FREE=3,VICTIM=4,COMPACT=5,STORE=6,
               HEAD_SCAN=7,HEAD_READ=8,DONE=9;
    reg [3:0] state,priority_index;
    reg [IDX:0] count, scan;
    reg [IDX-1:0] order[0:DEPTH-1], slot;
    reg [DEPTH-1:0] valid, urgent_tag;
    reg overflow,evicted,replacing;
    reg [9:0] levels,edges;
    reg [7:0] key;
    reg key_valid,shift;
    reg [25:0] ack_data, record;

    function automatic [3:0] code_index(input [3:0] k);
        case(k)
            0:code_index=8;1:code_index=9;2:code_index=7;3:code_index=6;
            default:code_index=k-4;
        endcase
    endfunction

    wire [3:0] ctl=code_index(priority_index);
    wire present=priority_index==10 ? key_valid : edges[ctl];
    wire [25:0] arrival=priority_index==10 ? {levels,key,8'h01} : {levels,8'h00,4'h1,ctl};
    wire arrives_urgent=priority_index<4;
    wire [7:0] count_byte=count;
    wire [IDX-1:0] current=order[scan[IDX-1:0]];
    assign cmd_ready=state==IDLE;
    assign resp_valid=state==DONE;
    assign mem_valid=(state==ACK_SCAN && scan<count) || state==HEAD_READ || state==STORE;
    assign mem_write=state==STORE;
    assign mem_addr=state==ACK_SCAN ? current : slot;
    assign mem_wdata=record;

    always @(posedge clk) begin
        if(rst) begin
            state<=IDLE;count<=0;valid<=0;urgent_tag<=0;overflow<=0;snapshot<=0;
            for(integer i=0;i<DEPTH;i=i+1) order[i]<=0;
        end else case(state)
            IDLE:if(cmd_valid) begin
                levels<=cmd_levels;edges<=cmd_edges;key<=cmd_key;shift<=cmd_shift;
                key_valid<=cmd_key_valid;ack_data<=cmd_ack_data;
                priority_index<=0;scan<=0;replacing<=0;evicted<=0;
                case(cmd)
                    OBSERVE:if(count==0) begin
                        snapshot<={cmd_shift,8'd0,overflow,cmd_levels,16'd0,1'b0};
                        state<=DONE;
                    end else state<=HEAD_SCAN;
                    ACK:state<=ACK_SCAN;
                    ARRIVE:state<=EVENT;
                    LOST:begin overflow<=1;state<=DONE;end
                endcase
            end
            ACK_SCAN:if(scan>=count) state<=DONE;
                else if(mem_ready) begin
                    if(mem_rdata==ack_data) begin slot<=current;state<=COMPACT;end
                    else scan<=scan+1'b1;
                end
            EVENT:if(priority_index>10) state<=DONE;
                else if(!present) priority_index<=priority_index+1'b1;
                else begin
                    record<=arrival;
                    if(count<DEPTH) begin scan<=0;replacing<=0;state<=FREE;end
                    else begin
                        overflow<=1;
                        if(arrives_urgent && !evicted) begin scan<=count-1'b1;state<=VICTIM;end
                        else priority_index<=priority_index+1'b1;
                    end
                end
            FREE:if(!valid[scan[IDX-1:0]]) begin slot<=scan[IDX-1:0];state<=STORE;end
                else scan<=scan+1'b1;
            VICTIM:if(!urgent_tag[current]) begin
                    slot<=current;replacing<=1;evicted<=1;state<=COMPACT;
                end else if(scan==0) begin priority_index<=priority_index+1'b1;state<=EVENT;end
                else scan<=scan-1'b1;
            COMPACT:if(scan+1'b1<count) begin
                    order[scan[IDX-1:0]]<=order[scan+1'b1];scan<=scan+1'b1;
                end else if(replacing) state<=STORE;
                else begin valid[slot]<=0;count<=count-1'b1;state<=DONE;end
            STORE:if(mem_ready) begin
                if(replacing) order[count-1'b1]<=slot;
                else begin order[count[IDX-1:0]]<=slot;count<=count+1'b1;end
                valid[slot]<=1;urgent_tag[slot]<=arrives_urgent;
                priority_index<=priority_index+1'b1;state<=EVENT;
            end
            HEAD_SCAN:if(urgent_tag[current]) begin slot<=current;state<=HEAD_READ;end
                else if(scan+1'b1==count) begin slot<=order[0];state<=HEAD_READ;end
                else scan<=scan+1'b1;
            HEAD_READ:if(mem_ready) begin
                snapshot<={shift,count_byte,overflow,mem_rdata,1'b1};state<=DONE;
            end
            DONE:if(resp_ready) state<=IDLE;
            default:state<=IDLE;
        endcase
    end
endmodule
