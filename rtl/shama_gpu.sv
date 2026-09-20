module shama_gpu #(
    parameter integer WIDTH = 192,
    parameter integer HEIGHT = 108,
    parameter integer QUEUE_DEPTH = 8
)(
    input  logic        clk,
    input  logic        rst,

    input  logic        mmio_valid,
    input  logic        mmio_we,
    input  logic [11:0] mmio_addr,
    input  logic [31:0] mmio_wdata,
    output logic        mmio_ready,
    output logic [31:0] mmio_rdata,

    output logic        disp_valid,
    output logic [15:0] disp_index,
    output logic        disp_bit,
    input  logic        disp_ready
);
    localparam integer PIXELS = WIDTH*HEIGHT;

    localparam [7:0]
        GPU_NOP=8'h00, GPU_CLEAR=8'h01, GPU_SET_PIXEL=8'h02,
        GPU_CLEAR_PIXEL=8'h03, GPU_INVERT_PIXEL=8'h04, GPU_READ_PIXEL=8'h05,
        GPU_LINE=8'h06, GPU_RECT=8'h07, GPU_FILL_RECT=8'h08,
        GPU_BLIT=8'h09, GPU_SPRITE=8'h0a, GPU_DRAW_CHAR=8'h0b,
        GPU_DRAW_TEXT=8'h0c, GPU_SCROLL=8'h0d, GPU_SET_CLIP=8'h0e,
        GPU_RESET_CLIP=8'h0f, GPU_SET_FONT=8'h10, GPU_SET_COLOR=8'h11,
        GPU_SET_CURSOR=8'h12, GPU_COPY_BUFFER=8'h13, GPU_SWAP_BUFFER=8'h14,
        GPU_PUSH_DIRTY=8'h15, GPU_FENCE=8'h16, GPU_CIRCLE=8'h17;

    typedef enum logic [4:0] {
        ST_IDLE, ST_DISPATCH, ST_CLEAR, ST_LINE, ST_RECT, ST_CIRCLE,
        ST_BLIT, ST_CHAR, ST_TEXT, ST_COPY, ST_SCROLL_SAVE,
        ST_SCROLL_APPLY, ST_PUSH
    } state_t;

    state_t state;

    logic fb0 [0:PIXELS-1];
    logic fb1 [0:PIXELS-1];
    logic scratch [0:PIXELS-1];
    logic shadow [0:PIXELS-1];
    logic front_sel;

    logic [7:0] text_ram [0:255];
    logic sprite_ram [0:1023];

    logic [7:0] command_reg;
    logic [31:0] arg_reg [0:7];

    logic [7:0] q_op [0:QUEUE_DEPTH-1];
    logic [31:0] q0[0:QUEUE_DEPTH-1],q1[0:QUEUE_DEPTH-1],q2[0:QUEUE_DEPTH-1],q3[0:QUEUE_DEPTH-1];
    logic [31:0] q4[0:QUEUE_DEPTH-1],q5[0:QUEUE_DEPTH-1],q6[0:QUEUE_DEPTH-1],q7[0:QUEUE_DEPTH-1];
    logic [$clog2(QUEUE_DEPTH)-1:0] q_head,q_tail;
    logic [$clog2(QUEUE_DEPTH+1)-1:0] q_count;

    logic [7:0] cur_op;
    logic [31:0] ca[0:7];

    integer clip_x0,clip_y0,clip_x1,clip_y1;
    logic draw_color;
    logic [7:0] font_id;
    integer cursor_x,cursor_y;
    logic [31:0] readback;
    logic [31:0] fence_count;
    logic queue_error;

    integer work_idx;
    integer lx,ly,lx1,ly1,ldx,ldy,lsx,lsy,lerr,e2;
    integer rx,ry,rw,rh;
    logic rect_fill;
    integer ccx,ccy,cr,cxo,cyo,circle_dist,r2,inner2;
    integer bsrc,bx,by,bw,bh,bdx,bdy;
    logic blit_transparent;
    logic [7:0] char_code;
    integer char_x,char_y,char_row,char_col;
    integer text_start,text_len,text_index,text_x,text_y,text_row,text_col;
    integer scroll_dx,scroll_dy;
    integer sx_tmp,sy_tmp;
    integer push_idx;

    logic [7:0] font_char;
    logic [2:0] font_row;
    logic [4:0] font_bits;

    shama_font5x7 u_font(.ch_in(font_char),.row(font_row),.bits(font_bits));

    function automatic integer pindex(input integer x,input integer y);
        pindex = y*WIDTH+x;
    endfunction

    function automatic logic in_screen(input integer x,input integer y);
        in_screen = (x>=0 && x<WIDTH && y>=0 && y<HEIGHT);
    endfunction

    function automatic logic in_clip(input integer x,input integer y);
        in_clip = in_screen(x,y) && x>=clip_x0 && x<=clip_x1 && y>=clip_y0 && y<=clip_y1;
    endfunction

    function automatic logic front_bit(input integer idx);
        front_bit = front_sel ? fb1[idx] : fb0[idx];
    endfunction

    function automatic logic back_bit(input integer idx);
        back_bit = front_sel ? fb0[idx] : fb1[idx];
    endfunction

    task automatic back_write(input integer idx,input logic value);
        begin
            if (idx>=0 && idx<PIXELS) begin
                if (front_sel) fb0[idx] <= value;
                else fb1[idx] <= value;
            end
        end
    endtask

    wire push_event = mmio_valid && mmio_we && (mmio_addr==12'h028) && (q_count<QUEUE_DEPTH);
    wire pop_event = (state==ST_IDLE) && (q_count!=0);

    always_comb begin
        mmio_ready = mmio_valid;
        mmio_rdata = 32'd0;
        case(mmio_addr)
            12'h000: mmio_rdata = {20'd0,queue_error,front_sel,2'd0,q_count,4'd0,(state!=ST_IDLE)};
            12'h004: mmio_rdata = {24'd0,command_reg};
            12'h008: mmio_rdata = arg_reg[0];
            12'h00c: mmio_rdata = arg_reg[1];
            12'h010: mmio_rdata = arg_reg[2];
            12'h014: mmio_rdata = arg_reg[3];
            12'h018: mmio_rdata = arg_reg[4];
            12'h01c: mmio_rdata = arg_reg[5];
            12'h020: mmio_rdata = arg_reg[6];
            12'h024: mmio_rdata = arg_reg[7];
            12'h02c: mmio_rdata = readback;
            12'h030: mmio_rdata = fence_count;
            12'h034: mmio_rdata = cursor_x;
            12'h038: mmio_rdata = cursor_y;
            default: begin
                if(mmio_addr>=12'h400 && mmio_addr<12'h500)
                    mmio_rdata = {24'd0,text_ram[mmio_addr[7:0]]};
                else if(mmio_addr>=12'h800 && mmio_addr<12'hc00)
                    mmio_rdata = {31'd0,sprite_ram[mmio_addr[9:0]]};
            end
        endcase

        font_char = (state==ST_CHAR) ? char_code :
                    (state==ST_TEXT ? text_ram[(text_start+text_index)&8'hff] : 8'd32);
        font_row = (state==ST_CHAR) ? char_row[2:0] : text_row[2:0];

        disp_valid = 1'b0;
        disp_index = push_idx[15:0];
        disp_bit = 1'b0;
        if(state==ST_PUSH && push_idx<PIXELS) begin
            // Emit the complete frame in row-major order.  The physical
            // display bridge converts this serial stream into 192-bit row
            // commits, which is far smaller than random-address pixel wiring.
            disp_bit = front_bit(push_idx);
            disp_valid = 1'b1;
        end
    end

    integer i;
    always_ff @(posedge clk) begin
        if(rst) begin
            state<=ST_IDLE;
            front_sel<=0;
            command_reg<=0;
            for(i=0;i<8;i=i+1) arg_reg[i]<=0;
            q_head<=0;q_tail<=0;q_count<=0;
            cur_op<=0;
            for(i=0;i<8;i=i+1) ca[i]<=0;
            clip_x0<=0;clip_y0<=0;clip_x1<=WIDTH-1;clip_y1<=HEIGHT-1;
            draw_color<=1;
            font_id<=0;
            cursor_x<=0;cursor_y<=0;
            readback<=0;fence_count<=0;queue_error<=0;
            work_idx<=0;push_idx<=0;
            for(i=0;i<PIXELS;i=i+1) begin
                fb0[i]<=0;fb1[i]<=0;scratch[i]<=0;shadow[i]<=0;
            end
            for(i=0;i<256;i=i+1) text_ram[i]<=0;
            for(i=0;i<1024;i=i+1) sprite_ram[i]<=0;
        end else begin
            // MMIO writes.
            if(mmio_valid && mmio_we) begin
                case(mmio_addr)
                    12'h004: command_reg<=mmio_wdata[7:0];
                    12'h008: arg_reg[0]<=mmio_wdata;
                    12'h00c: arg_reg[1]<=mmio_wdata;
                    12'h010: arg_reg[2]<=mmio_wdata;
                    12'h014: arg_reg[3]<=mmio_wdata;
                    12'h018: arg_reg[4]<=mmio_wdata;
                    12'h01c: arg_reg[5]<=mmio_wdata;
                    12'h020: arg_reg[6]<=mmio_wdata;
                    12'h024: arg_reg[7]<=mmio_wdata;
                    default: begin
                        if(mmio_addr>=12'h400 && mmio_addr<12'h500)
                            text_ram[mmio_addr[7:0]]<=mmio_wdata[7:0];
                        else if(mmio_addr>=12'h800 && mmio_addr<12'hc00)
                            sprite_ram[mmio_addr[9:0]]<=mmio_wdata[0];
                    end
                endcase
                if(mmio_addr==12'h028 && q_count>=QUEUE_DEPTH)
                    queue_error<=1;
            end

            if(push_event) begin
                q_op[q_tail]<=command_reg;
                q0[q_tail]<=arg_reg[0]; q1[q_tail]<=arg_reg[1]; q2[q_tail]<=arg_reg[2]; q3[q_tail]<=arg_reg[3];
                q4[q_tail]<=arg_reg[4]; q5[q_tail]<=arg_reg[5]; q6[q_tail]<=arg_reg[6]; q7[q_tail]<=arg_reg[7];
                q_tail<=q_tail+1'b1;
            end

            if(pop_event) begin
                cur_op<=q_op[q_head];
                ca[0]<=q0[q_head]; ca[1]<=q1[q_head]; ca[2]<=q2[q_head]; ca[3]<=q3[q_head];
                ca[4]<=q4[q_head]; ca[5]<=q5[q_head]; ca[6]<=q6[q_head]; ca[7]<=q7[q_head];
                q_head<=q_head+1'b1;
                state<=ST_DISPATCH;
            end

            case({push_event,pop_event})
                2'b10: q_count<=q_count+1'b1;
                2'b01: q_count<=q_count-1'b1;
                default: q_count<=q_count;
            endcase

            case(state)
                ST_IDLE: begin end

                ST_DISPATCH: begin
                    case(cur_op)
                        GPU_NOP: state<=ST_IDLE;
                        GPU_CLEAR: begin work_idx<=0; state<=ST_CLEAR; end
                        GPU_SET_PIXEL: begin
                            if(in_clip(ca[0],ca[1])) back_write(pindex(ca[0],ca[1]),draw_color);
                            state<=ST_IDLE;
                        end
                        GPU_CLEAR_PIXEL: begin
                            if(in_clip(ca[0],ca[1])) back_write(pindex(ca[0],ca[1]),0);
                            state<=ST_IDLE;
                        end
                        GPU_INVERT_PIXEL: begin
                            if(in_clip(ca[0],ca[1])) back_write(pindex(ca[0],ca[1]),~back_bit(pindex(ca[0],ca[1])));
                            state<=ST_IDLE;
                        end
                        GPU_READ_PIXEL: begin
                            readback<=in_screen(ca[0],ca[1]) ? back_bit(pindex(ca[0],ca[1])) : 0;
                            state<=ST_IDLE;
                        end
                        GPU_LINE: begin
                            lx<=ca[0];ly<=ca[1];lx1<=ca[2];ly1<=ca[3];
                            ldx<=(ca[2]>=ca[0])?(ca[2]-ca[0]):(ca[0]-ca[2]);
                            ldy<=-((ca[3]>=ca[1])?(ca[3]-ca[1]):(ca[1]-ca[3]));
                            lsx<=(ca[0]<ca[2])?1:-1; lsy<=(ca[1]<ca[3])?1:-1;
                            lerr<=((ca[2]>=ca[0])?(ca[2]-ca[0]):(ca[0]-ca[2]))
                                 -((ca[3]>=ca[1])?(ca[3]-ca[1]):(ca[1]-ca[3]));
                            state<=ST_LINE;
                        end
                        GPU_RECT,GPU_FILL_RECT: begin
                            rx<=0;ry<=0;rw<=ca[2];rh<=ca[3];rect_fill<=(cur_op==GPU_FILL_RECT);state<=ST_RECT;
                        end
                        GPU_CIRCLE: begin
                            ccx<=ca[0];ccy<=ca[1];cr<=ca[2];cxo<=-ca[2];cyo<=-ca[2];
                            r2<=ca[2]*ca[2];inner2<=(ca[2]>0)?((ca[2]-1)*(ca[2]-1)):0;
                            state<=ST_CIRCLE;
                        end
                        GPU_BLIT,GPU_SPRITE: begin
                            bsrc<=ca[0];bdx<=ca[1];bdy<=ca[2];bw<=ca[3];bh<=ca[4];bx<=0;by<=0;
                            blit_transparent<=(cur_op==GPU_SPRITE);state<=ST_BLIT;
                        end
                        GPU_DRAW_CHAR: begin
                            char_code<=ca[0][7:0];char_x<=ca[1];char_y<=ca[2];char_row<=0;char_col<=0;state<=ST_CHAR;
                        end
                        GPU_DRAW_TEXT: begin
                            text_start<=ca[0];text_len<=ca[1];text_x<=ca[2];text_y<=ca[3];
                            text_index<=0;text_row<=0;text_col<=0;state<=ST_TEXT;
                        end
                        GPU_SCROLL: begin scroll_dx<=ca[0];scroll_dy<=ca[1];work_idx<=0;state<=ST_SCROLL_SAVE; end
                        GPU_SET_CLIP: begin
                            clip_x0<=ca[0];clip_y0<=ca[1];clip_x1<=ca[2];clip_y1<=ca[3];state<=ST_IDLE;
                        end
                        GPU_RESET_CLIP: begin clip_x0<=0;clip_y0<=0;clip_x1<=WIDTH-1;clip_y1<=HEIGHT-1;state<=ST_IDLE; end
                        GPU_SET_FONT: begin font_id<=ca[0][7:0];state<=ST_IDLE;end
                        GPU_SET_COLOR: begin draw_color<=ca[0][0];state<=ST_IDLE;end
                        GPU_SET_CURSOR: begin cursor_x<=ca[0];cursor_y<=ca[1];state<=ST_IDLE;end
                        GPU_COPY_BUFFER: begin work_idx<=0;state<=ST_COPY;end
                        GPU_SWAP_BUFFER: begin front_sel<=~front_sel;push_idx<=0;state<=ST_PUSH;end
                        GPU_PUSH_DIRTY: begin push_idx<=0;state<=ST_PUSH;end
                        GPU_FENCE: begin fence_count<=fence_count+1'b1;readback<=fence_count+1'b1;state<=ST_IDLE;end
                        default: begin queue_error<=1;state<=ST_IDLE;end
                    endcase
                end

                ST_CLEAR: begin
                    back_write(work_idx,ca[0][0]);
                    if(work_idx==PIXELS-1) state<=ST_IDLE;
                    else work_idx<=work_idx+1;
                end

                ST_LINE: begin
                    if(in_clip(lx,ly)) back_write(pindex(lx,ly),draw_color);
                    if(lx==lx1 && ly==ly1) state<=ST_IDLE;
                    else begin
                        e2 = 2*lerr;
                        if(e2>=ldy) begin lerr<=lerr+ldy; lx<=lx+lsx; end
                        if(e2<=ldx) begin
                            if(e2>=ldy) lerr<=lerr+ldy+ldx;
                            else lerr<=lerr+ldx;
                            ly<=ly+lsy;
                        end
                    end
                end

                ST_RECT: begin
                    if(in_clip(ca[0]+rx,ca[1]+ry)) begin
                        if(rect_fill || rx==0 || ry==0 || rx==rw-1 || ry==rh-1)
                            back_write(pindex(ca[0]+rx,ca[1]+ry),draw_color);
                    end
                    if(rw<=0 || rh<=0) state<=ST_IDLE;
                    else if(rx==rw-1) begin
                        rx<=0;
                        if(ry==rh-1) state<=ST_IDLE; else ry<=ry+1;
                    end else rx<=rx+1;
                end

                ST_CIRCLE: begin
                    circle_dist = cxo*cxo + cyo*cyo;
                    if(circle_dist<=r2 && circle_dist>=inner2 && in_clip(ccx+cxo,ccy+cyo))
                        back_write(pindex(ccx+cxo,ccy+cyo),draw_color);
                    if(cxo==cr) begin
                        cxo<=-cr;
                        if(cyo==cr) state<=ST_IDLE; else cyo<=cyo+1;
                    end else cxo<=cxo+1;
                end

                ST_BLIT: begin
                    if((bsrc+by*bw+bx)<1024 && in_clip(bdx+bx,bdy+by)) begin
                        if(!blit_transparent || sprite_ram[bsrc+by*bw+bx])
                            back_write(pindex(bdx+bx,bdy+by),sprite_ram[bsrc+by*bw+bx] ? draw_color : ~draw_color);
                    end
                    if(bw<=0 || bh<=0) state<=ST_IDLE;
                    else if(bx==bw-1) begin bx<=0;if(by==bh-1)state<=ST_IDLE;else by<=by+1;end
                    else bx<=bx+1;
                end

                ST_CHAR: begin
                    if(font_bits[4-char_col] && in_clip(char_x+char_col,char_y+char_row))
                        back_write(pindex(char_x+char_col,char_y+char_row),draw_color);
                    if(char_col==4) begin
                        char_col<=0;
                        if(char_row==6) state<=ST_IDLE; else char_row<=char_row+1;
                    end else char_col<=char_col+1;
                end

                ST_TEXT: begin
                    if(text_index>=text_len || text_index>=256) state<=ST_IDLE;
                    else begin
                        if(font_bits[4-text_col] && in_clip(text_x+text_index*6+text_col,text_y+text_row))
                            back_write(pindex(text_x+text_index*6+text_col,text_y+text_row),draw_color);
                        if(text_col==4) begin
                            text_col<=0;
                            if(text_row==6) begin
                                text_row<=0;text_index<=text_index+1;
                            end else text_row<=text_row+1;
                        end else text_col<=text_col+1;
                    end
                end

                ST_COPY: begin
                    back_write(work_idx,front_bit(work_idx));
                    if(work_idx==PIXELS-1) state<=ST_IDLE; else work_idx<=work_idx+1;
                end

                ST_SCROLL_SAVE: begin
                    scratch[work_idx]<=back_bit(work_idx);
                    if(work_idx==PIXELS-1) begin work_idx<=0;state<=ST_SCROLL_APPLY;end
                    else work_idx<=work_idx+1;
                end

                ST_SCROLL_APPLY: begin
                    sx_tmp=(work_idx%WIDTH)-scroll_dx;
                    sy_tmp=(work_idx/WIDTH)-scroll_dy;
                    if(in_screen(sx_tmp,sy_tmp)) back_write(work_idx,scratch[pindex(sx_tmp,sy_tmp)]);
                    else back_write(work_idx,0);
                    if(work_idx==PIXELS-1) state<=ST_IDLE; else work_idx<=work_idx+1;
                end

                ST_PUSH: begin
                    if(push_idx>=PIXELS) state<=ST_IDLE;
                    else if(disp_ready) begin
                        shadow[push_idx]<=front_bit(push_idx);
                        if(push_idx==PIXELS-1) state<=ST_IDLE; else push_idx<=push_idx+1;
                    end
                end

                default: state<=ST_IDLE;
            endcase
        end
    end
endmodule
