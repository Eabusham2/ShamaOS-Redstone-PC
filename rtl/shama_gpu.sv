module shama_gpu #(
    parameter integer WIDTH = 320,
    parameter integer HEIGHT = 180,
    parameter integer QUEUE_DEPTH = 8
)(
    input  logic        clk,
    input  logic        rst,

    input  logic        mmio_valid,
    input  logic        mmio_we,
    input  logic [11:0] mmio_addr,
    input  logic [31:0] mmio_wdata,
    input  logic [3:0]  mmio_wstrb,
    output logic        mmio_ready,
    output logic [31:0] mmio_rdata,

    output logic        vram_valid,
    output logic        vram_we,
    output logic [14:0] vram_addr,
    output logic [31:0] vram_wdata,
    output logic [3:0]  vram_wstrb,
    input  logic        vram_ready,
    input  logic [31:0] vram_rdata,

    output logic        disp_valid,
    output logic [15:0] disp_index,
    output logic        disp_bit,
    input  logic        disp_ready
);
    localparam integer PIXELS = WIDTH*HEIGHT;
    localparam integer FRAME_BYTES = (PIXELS+7)/8;
    localparam integer FRAME_WORDS = (FRAME_BYTES+3)/4;

    // 32 KiB physical VRAM map.
    localparam [14:0]
        FB0_BASE     = 15'h0000,
        FB1_BASE     = 15'h2000,
        TEXT_BASE    = 15'h4000,
        SPRITE_BASE  = 15'h4100,
        SCRATCH_BASE = 15'h5000;

    localparam [7:0]
        GPU_NOP=8'h00, GPU_CLEAR=8'h01, GPU_SET_PIXEL=8'h02,
        GPU_CLEAR_PIXEL=8'h03, GPU_INVERT_PIXEL=8'h04, GPU_READ_PIXEL=8'h05,
        GPU_LINE=8'h06, GPU_RECT=8'h07, GPU_FILL_RECT=8'h08,
        GPU_BLIT=8'h09, GPU_SPRITE=8'h0a, GPU_DRAW_CHAR=8'h0b,
        GPU_DRAW_TEXT=8'h0c, GPU_SCROLL=8'h0d, GPU_SET_CLIP=8'h0e,
        GPU_RESET_CLIP=8'h0f, GPU_SET_FONT=8'h10, GPU_SET_COLOR=8'h11,
        GPU_SET_CURSOR=8'h12, GPU_COPY_BUFFER=8'h13, GPU_SWAP_BUFFER=8'h14,
        GPU_PUSH_DIRTY=8'h15, GPU_FENCE=8'h16, GPU_CIRCLE=8'h17;

    typedef enum logic [5:0] {
        ST_IDLE, ST_DISPATCH,
        ST_CLEAR_WRITE,

        ST_PIXEL_READ, ST_PIXEL_WRITE,
        ST_LINE_PIXEL, ST_LINE_ADV,
        ST_RECT_PIXEL, ST_RECT_ADV,
        ST_CIRCLE_PIXEL, ST_CIRCLE_ADV,

        ST_BLIT_READ, ST_BLIT_PIXEL, ST_BLIT_ADV,
        ST_CHAR_PIXEL, ST_CHAR_ADV,
        ST_TEXT_CHAR_READ, ST_TEXT_PIXEL, ST_TEXT_ADV,

        ST_COPY_READ, ST_COPY_WRITE,
        ST_SCROLL_COPY_READ, ST_SCROLL_COPY_WRITE,
        ST_SCROLL_CLEAR,
        ST_SCROLL_SRC_READ, ST_SCROLL_DST_PIXEL, ST_SCROLL_ADV,

        ST_PUSH_READ, ST_PUSH_BITS
    } state_t;

    localparam [2:0]
        PIX_SET=0, PIX_CLEAR=1, PIX_INVERT=2, PIX_READ=3, PIX_VALUE=4;

    state_t state;
    state_t pixel_return;

    logic front_sel;
    logic draw_color;
    logic [7:0] font_id;
    integer cursor_x,cursor_y;
    integer clip_x0,clip_y0,clip_x1,clip_y1;
    logic [31:0] readback;
    logic [31:0] fence_count;
    logic queue_error;

    logic [7:0] command_reg;
    logic [31:0] arg_reg [0:7];

    logic [7:0] q_op [0:QUEUE_DEPTH-1];
    logic [31:0] q0[0:QUEUE_DEPTH-1],q1[0:QUEUE_DEPTH-1],q2[0:QUEUE_DEPTH-1],q3[0:QUEUE_DEPTH-1];
    logic [31:0] q4[0:QUEUE_DEPTH-1],q5[0:QUEUE_DEPTH-1],q6[0:QUEUE_DEPTH-1],q7[0:QUEUE_DEPTH-1];
    logic [$clog2(QUEUE_DEPTH)-1:0] q_head,q_tail;
    logic [$clog2(QUEUE_DEPTH+1)-1:0] q_count;

    logic [7:0] cur_op;
    logic [31:0] ca[0:7];

    // Generic pixel RMW engine.
    integer pix_x,pix_y;
    logic [2:0] pixel_action;
    logic pixel_value;
    logic [14:0] pixel_base;
    logic [14:0] pixel_word_addr;
    logic [4:0] pixel_bit_index;
    logic [31:0] pixel_word;
    logic pixel_read_value;

    // Clear/copy.
    integer work_word;
    logic [31:0] copy_word;
    logic clear_value;

    // Line.
    integer lx,ly,lx1,ly1,ldx,ldy,lsx,lsy,lerr,line_e2;

    // Rectangle.
    integer rx,ry,rw,rh;
    logic rect_fill;

    // Circle.
    integer ccx,ccy,cr,cxo,cyo,circle_dist,r2,inner2;

    // Blit/sprite.
    integer bsrc,bx,by,bw,bh,bdx,bdy;
    logic blit_transparent;
    logic [7:0] sprite_value;

    // Character/text.
    logic [7:0] char_code;
    integer char_x,char_y,char_row,char_col;
    integer text_start,text_len,text_index,text_x,text_y,text_row,text_col;
    logic [7:0] text_char;

    // Scroll.
    integer scroll_dx,scroll_dy;
    integer scroll_index,scroll_sx,scroll_sy;
    logic scroll_source_bit;

    // Display push.
    integer push_word_index;
    logic [5:0] push_bit_index;
    logic [31:0] push_word;

    logic [7:0] font_char;
    logic [2:0] font_row;
    logic [4:0] font_bits;

    shama_font5x7 u_font(
        .ch_in(font_char),
        .row(font_row),
        .bits(font_bits)
    );

    function automatic [14:0] front_base;
        front_base = front_sel ? FB1_BASE : FB0_BASE;
    endfunction

    function automatic [14:0] back_base;
        back_base = front_sel ? FB0_BASE : FB1_BASE;
    endfunction

    function automatic logic in_screen(input integer x,input integer y);
        in_screen=(x>=0 && x<WIDTH && y>=0 && y<HEIGHT);
    endfunction

    function automatic logic in_clip(input integer x,input integer y);
        in_clip=in_screen(x,y) && x>=clip_x0 && x<=clip_x1 && y>=clip_y0 && y<=clip_y1;
    endfunction

    function automatic integer linear_index(input integer x,input integer y);
        linear_index=y*WIDTH+x;
    endfunction

    function automatic [14:0] packed_word_addr(
        input [14:0] base,
        input integer x,
        input integer y
    );
        integer idx;
        begin
            idx=linear_index(x,y);
            packed_word_addr=base+((idx>>5)<<2);
        end
    endfunction

    function automatic [4:0] packed_bit_index(input integer x,input integer y);
        integer idx;
        begin
            idx=linear_index(x,y);
            packed_bit_index=idx & 31;
        end
    endfunction

    wire push_event=mmio_valid && mmio_we && mmio_addr==12'h028 && q_count<QUEUE_DEPTH;
    wire pop_event=(state==ST_IDLE) && (q_count!=0) && !(
        mmio_valid && mmio_addr>=12'h400
    );

    // Direct text/sprite RAM writes are serviced through the physical VRAM
    // interface only while the raster engine is idle and the queue is empty.
    logic direct_vram_access;
    logic [14:0] direct_vram_addr;
    assign direct_vram_access =
        mmio_valid && (mmio_addr>=12'h400 && mmio_addr<12'hc00)
        && state==ST_IDLE && q_count==0;
    assign direct_vram_addr =
        (mmio_addr<12'h500)
        ? TEXT_BASE + (mmio_addr-12'h400)
        : SPRITE_BASE + (mmio_addr-12'h800);

    always_comb begin
        mmio_ready=1'b0;
        mmio_rdata=32'd0;

        vram_valid=1'b0;
        vram_we=1'b0;
        vram_addr=15'd0;
        vram_wdata=32'd0;
        vram_wstrb=4'b1111;

        disp_valid=1'b0;
        disp_index=(push_word_index*32+push_bit_index);
        disp_bit=push_word[push_bit_index];

        font_char=(state==ST_TEXT_PIXEL || state==ST_TEXT_ADV)
            ? text_char : char_code;
        font_row=(state==ST_TEXT_PIXEL || state==ST_TEXT_ADV)
            ? text_row[2:0] : char_row[2:0];

        // Register MMIO.
        if(mmio_valid && !(mmio_addr>=12'h400 && mmio_addr<12'hc00)) begin
            mmio_ready=1'b1;
            case(mmio_addr)
                12'h000:mmio_rdata={20'd0,queue_error,front_sel,2'd0,q_count[3:0],3'd0,(state!=ST_IDLE)};
                12'h004:mmio_rdata={24'd0,command_reg};
                12'h008:mmio_rdata=arg_reg[0];
                12'h00c:mmio_rdata=arg_reg[1];
                12'h010:mmio_rdata=arg_reg[2];
                12'h014:mmio_rdata=arg_reg[3];
                12'h018:mmio_rdata=arg_reg[4];
                12'h01c:mmio_rdata=arg_reg[5];
                12'h020:mmio_rdata=arg_reg[6];
                12'h024:mmio_rdata=arg_reg[7];
                12'h02c:mmio_rdata=readback;
                12'h030:mmio_rdata=fence_count;
                12'h034:mmio_rdata=cursor_x;
                12'h038:mmio_rdata=cursor_y;
                default:mmio_rdata=0;
            endcase
        end

        // Direct VRAM MMIO.
        if(direct_vram_access) begin
            vram_valid=1'b1;
            vram_we=mmio_we;
            vram_addr={direct_vram_addr[14:2],2'b00};
            vram_wdata=mmio_wdata;
            vram_wstrb=mmio_wstrb;
            mmio_ready=vram_ready;
            mmio_rdata=vram_rdata;
        end else begin
            // Raster engine VRAM transactions.
            case(state)
                ST_CLEAR_WRITE: begin
                    vram_valid=1;vram_we=1;
                    vram_addr=back_base()+(work_word<<2);
                    vram_wdata=clear_value?32'hffffffff:32'd0;
                end

                ST_PIXEL_READ: begin
                    vram_valid=1;vram_we=0;
                    vram_addr=pixel_word_addr;
                end

                ST_PIXEL_WRITE: begin
                    vram_valid=1;vram_we=1;
                    vram_addr=pixel_word_addr;
                    vram_wdata=pixel_word;
                end

                ST_BLIT_READ: begin
                    vram_valid=1;vram_we=0;
                    vram_addr={((SPRITE_BASE+bsrc+by*bw+bx)>>2),2'b00};
                end

                ST_TEXT_CHAR_READ: begin
                    vram_valid=1;vram_we=0;
                    vram_addr={((TEXT_BASE+text_start+text_index)>>2),2'b00};
                end

                ST_COPY_READ: begin
                    vram_valid=1;vram_we=0;
                    vram_addr=front_base()+(work_word<<2);
                end
                ST_COPY_WRITE: begin
                    vram_valid=1;vram_we=1;
                    vram_addr=back_base()+(work_word<<2);
                    vram_wdata=copy_word;
                end

                ST_SCROLL_COPY_READ: begin
                    vram_valid=1;vram_we=0;
                    vram_addr=back_base()+(work_word<<2);
                end
                ST_SCROLL_COPY_WRITE: begin
                    vram_valid=1;vram_we=1;
                    vram_addr=SCRATCH_BASE+(work_word<<2);
                    vram_wdata=copy_word;
                end
                ST_SCROLL_CLEAR: begin
                    vram_valid=1;vram_we=1;
                    vram_addr=back_base()+(work_word<<2);
                    vram_wdata=32'd0;
                end
                ST_SCROLL_SRC_READ: begin
                    vram_valid=1;vram_we=0;
                    vram_addr=packed_word_addr(SCRATCH_BASE,scroll_sx,scroll_sy);
                end

                ST_PUSH_READ: begin
                    vram_valid=1;vram_we=0;
                    vram_addr=front_base()+(push_word_index<<2);
                end

                default: begin end
            endcase
        end

        if(state==ST_PUSH_BITS && push_word_index<FRAME_WORDS) begin
            if(push_word_index*32+push_bit_index<PIXELS) begin
                disp_valid=1'b1;
                disp_index=push_word_index*32+push_bit_index;
                disp_bit=push_word[push_bit_index];
            end
        end
    end

    task automatic start_pixel(
        input integer x,input integer y,input logic [2:0] action,
        input logic value,input [14:0] base,input state_t ret
    );
        begin
            pix_x<=x;pix_y<=y;
            pixel_action<=action;pixel_value<=value;
            pixel_base<=base;
            pixel_word_addr<=packed_word_addr(base,x,y);
            pixel_bit_index<=packed_bit_index(x,y);
            pixel_return<=ret;
            state<=ST_PIXEL_READ;
        end
    endtask

    integer i;
    always_ff @(posedge clk) begin
        if(rst) begin
            state<=ST_IDLE;
            front_sel<=0;
            draw_color<=1;
            font_id<=0;
            cursor_x<=0;cursor_y<=0;
            clip_x0<=0;clip_y0<=0;clip_x1<=WIDTH-1;clip_y1<=HEIGHT-1;
            readback<=0;fence_count<=0;queue_error<=0;
            command_reg<=0;
            for(i=0;i<8;i=i+1) begin arg_reg[i]<=0;ca[i]<=0;end
            q_head<=0;q_tail<=0;q_count<=0;cur_op<=0;

            pix_x<=0;pix_y<=0;pixel_action<=0;pixel_value<=0;pixel_base<=0;
            pixel_word_addr<=0;pixel_bit_index<=0;pixel_word<=0;pixel_read_value<=0;
            pixel_return<=ST_IDLE;

            work_word<=0;copy_word<=0;clear_value<=0;
            lx<=0;ly<=0;lx1<=0;ly1<=0;ldx<=0;ldy<=0;lsx<=0;lsy<=0;lerr<=0;line_e2<=0;
            rx<=0;ry<=0;rw<=0;rh<=0;rect_fill<=0;
            ccx<=0;ccy<=0;cr<=0;cxo<=0;cyo<=0;circle_dist<=0;r2<=0;inner2<=0;
            bsrc<=0;bx<=0;by<=0;bw<=0;bh<=0;bdx<=0;bdy<=0;blit_transparent<=0;sprite_value<=0;
            char_code<=0;char_x<=0;char_y<=0;char_row<=0;char_col<=0;
            text_start<=0;text_len<=0;text_index<=0;text_x<=0;text_y<=0;text_row<=0;text_col<=0;text_char<=0;
            scroll_dx<=0;scroll_dy<=0;scroll_index<=0;scroll_sx<=0;scroll_sy<=0;scroll_source_bit<=0;
            push_word_index<=0;push_bit_index<=0;push_word<=0;
        end else begin
            // Register MMIO writes.
            if(mmio_valid && mmio_we && mmio_ready && !(mmio_addr>=12'h400 && mmio_addr<12'hc00)) begin
                case(mmio_addr)
                    12'h004:command_reg<=mmio_wdata[7:0];
                    12'h008:arg_reg[0]<=mmio_wdata;
                    12'h00c:arg_reg[1]<=mmio_wdata;
                    12'h010:arg_reg[2]<=mmio_wdata;
                    12'h014:arg_reg[3]<=mmio_wdata;
                    12'h018:arg_reg[4]<=mmio_wdata;
                    12'h01c:arg_reg[5]<=mmio_wdata;
                    12'h020:arg_reg[6]<=mmio_wdata;
                    12'h024:arg_reg[7]<=mmio_wdata;
                    default:begin end
                endcase
                if(mmio_addr==12'h028 && q_count>=QUEUE_DEPTH)
                    queue_error<=1;
            end

            if(push_event && mmio_ready) begin
                q_op[q_tail]<=command_reg;
                q0[q_tail]<=arg_reg[0];q1[q_tail]<=arg_reg[1];
                q2[q_tail]<=arg_reg[2];q3[q_tail]<=arg_reg[3];
                q4[q_tail]<=arg_reg[4];q5[q_tail]<=arg_reg[5];
                q6[q_tail]<=arg_reg[6];q7[q_tail]<=arg_reg[7];
                q_tail<=q_tail+1'b1;
            end

            if(pop_event) begin
                cur_op<=q_op[q_head];
                ca[0]<=q0[q_head];ca[1]<=q1[q_head];ca[2]<=q2[q_head];ca[3]<=q3[q_head];
                ca[4]<=q4[q_head];ca[5]<=q5[q_head];ca[6]<=q6[q_head];ca[7]<=q7[q_head];
                q_head<=q_head+1'b1;
                state<=ST_DISPATCH;
            end

            case({push_event&&mmio_ready,pop_event})
                2'b10:q_count<=q_count+1'b1;
                2'b01:q_count<=q_count-1'b1;
                default:q_count<=q_count;
            endcase

            case(state)
                ST_IDLE: begin end

                ST_DISPATCH: begin
                    case(cur_op)
                        GPU_NOP:state<=ST_IDLE;
                        GPU_CLEAR:begin clear_value<=ca[0][0];work_word<=0;state<=ST_CLEAR_WRITE;end

                        GPU_SET_PIXEL:begin
                            if(in_clip(ca[0],ca[1]))
                                start_pixel(ca[0],ca[1],PIX_SET,1,back_base(),ST_IDLE);
                            else state<=ST_IDLE;
                        end
                        GPU_CLEAR_PIXEL:begin
                            if(in_clip(ca[0],ca[1]))
                                start_pixel(ca[0],ca[1],PIX_CLEAR,0,back_base(),ST_IDLE);
                            else state<=ST_IDLE;
                        end
                        GPU_INVERT_PIXEL:begin
                            if(in_clip(ca[0],ca[1]))
                                start_pixel(ca[0],ca[1],PIX_INVERT,0,back_base(),ST_IDLE);
                            else state<=ST_IDLE;
                        end
                        GPU_READ_PIXEL:begin
                            if(in_screen(ca[0],ca[1]))
                                start_pixel(ca[0],ca[1],PIX_READ,0,back_base(),ST_IDLE);
                            else begin readback<=0;state<=ST_IDLE;end
                        end

                        GPU_LINE:begin
                            lx<=ca[0];ly<=ca[1];lx1<=ca[2];ly1<=ca[3];
                            ldx<=(ca[2]>=ca[0])?(ca[2]-ca[0]):(ca[0]-ca[2]);
                            ldy<=-((ca[3]>=ca[1])?(ca[3]-ca[1]):(ca[1]-ca[3]));
                            lsx<=(ca[0]<ca[2])?1:-1;lsy<=(ca[1]<ca[3])?1:-1;
                            lerr<=((ca[2]>=ca[0])?(ca[2]-ca[0]):(ca[0]-ca[2]))
                                  -((ca[3]>=ca[1])?(ca[3]-ca[1]):(ca[1]-ca[3]));
                            state<=ST_LINE_PIXEL;
                        end

                        GPU_RECT,GPU_FILL_RECT:begin
                            rx<=0;ry<=0;rw<=ca[2];rh<=ca[3];
                            rect_fill<=(cur_op==GPU_FILL_RECT);
                            state<=ST_RECT_PIXEL;
                        end

                        GPU_CIRCLE:begin
                            ccx<=ca[0];ccy<=ca[1];cr<=ca[2];cxo<=-ca[2];cyo<=-ca[2];
                            r2<=ca[2]*ca[2];
                            inner2<=(ca[2]>0)?((ca[2]-1)*(ca[2]-1)):0;
                            state<=ST_CIRCLE_PIXEL;
                        end

                        GPU_BLIT,GPU_SPRITE:begin
                            bsrc<=ca[0];bdx<=ca[1];bdy<=ca[2];bw<=ca[3];bh<=ca[4];
                            bx<=0;by<=0;blit_transparent<=(cur_op==GPU_SPRITE);
                            state<=ST_BLIT_READ;
                        end

                        GPU_DRAW_CHAR:begin
                            char_code<=ca[0][7:0];char_x<=ca[1];char_y<=ca[2];
                            char_row<=0;char_col<=0;state<=ST_CHAR_PIXEL;
                        end

                        GPU_DRAW_TEXT:begin
                            text_start<=ca[0];text_len<=ca[1];text_x<=ca[2];text_y<=ca[3];
                            text_index<=0;text_row<=0;text_col<=0;
                            state<=ST_TEXT_CHAR_READ;
                        end

                        GPU_SCROLL:begin
                            scroll_dx<=ca[0];scroll_dy<=ca[1];
                            work_word<=0;state<=ST_SCROLL_COPY_READ;
                        end

                        GPU_SET_CLIP:begin
                            clip_x0<=ca[0];clip_y0<=ca[1];clip_x1<=ca[2];clip_y1<=ca[3];state<=ST_IDLE;
                        end
                        GPU_RESET_CLIP:begin
                            clip_x0<=0;clip_y0<=0;clip_x1<=WIDTH-1;clip_y1<=HEIGHT-1;state<=ST_IDLE;
                        end
                        GPU_SET_FONT:begin font_id<=ca[0][7:0];state<=ST_IDLE;end
                        GPU_SET_COLOR:begin draw_color<=ca[0][0];state<=ST_IDLE;end
                        GPU_SET_CURSOR:begin cursor_x<=ca[0];cursor_y<=ca[1];state<=ST_IDLE;end

                        GPU_COPY_BUFFER:begin work_word<=0;state<=ST_COPY_READ;end
                        GPU_SWAP_BUFFER:begin front_sel<=~front_sel;push_word_index<=0;push_bit_index<=0;state<=ST_PUSH_READ;end
                        GPU_PUSH_DIRTY:begin push_word_index<=0;push_bit_index<=0;state<=ST_PUSH_READ;end
                        GPU_FENCE:begin fence_count<=fence_count+1'b1;readback<=fence_count+1'b1;state<=ST_IDLE;end
                        default:begin queue_error<=1;state<=ST_IDLE;end
                    endcase
                end

                ST_CLEAR_WRITE:if(vram_ready) begin
                    if(work_word==FRAME_WORDS-1) state<=ST_IDLE;
                    else work_word<=work_word+1;
                end

                ST_PIXEL_READ:if(vram_ready) begin
                    pixel_read_value<=vram_rdata[pixel_bit_index];
                    if(pixel_action==PIX_READ) begin
                        readback<={31'd0,vram_rdata[pixel_bit_index]};
                        state<=pixel_return;
                    end else begin
                        pixel_word<=vram_rdata;
                        case(pixel_action)
                            PIX_SET:pixel_word[pixel_bit_index]<=1'b1;
                            PIX_CLEAR:pixel_word[pixel_bit_index]<=1'b0;
                            PIX_INVERT:pixel_word[pixel_bit_index]<=~vram_rdata[pixel_bit_index];
                            PIX_VALUE:pixel_word[pixel_bit_index]<=pixel_value;
                            default:begin end
                        endcase
                        state<=ST_PIXEL_WRITE;
                    end
                end
                ST_PIXEL_WRITE:if(vram_ready) state<=pixel_return;

                ST_LINE_PIXEL:begin
                    if(in_clip(lx,ly))
                        start_pixel(lx,ly,PIX_VALUE,draw_color,back_base(),ST_LINE_ADV);
                    else state<=ST_LINE_ADV;
                end
                ST_LINE_ADV:begin
                    if(lx==lx1 && ly==ly1) state<=ST_IDLE;
                    else begin
                        line_e2=2*lerr;
                        if(line_e2>=ldy) begin lerr<=lerr+ldy;lx<=lx+lsx;end
                        if(line_e2<=ldx) begin
                            if(line_e2>=ldy) lerr<=lerr+ldy+ldx;
                            else lerr<=lerr+ldx;
                            ly<=ly+lsy;
                        end
                        state<=ST_LINE_PIXEL;
                    end
                end

                ST_RECT_PIXEL:begin
                    if(rw<=0 || rh<=0) state<=ST_IDLE;
                    else if(rect_fill || rx==0 || ry==0 || rx==rw-1 || ry==rh-1) begin
                        if(in_clip(ca[0]+rx,ca[1]+ry))
                            start_pixel(ca[0]+rx,ca[1]+ry,PIX_VALUE,draw_color,back_base(),ST_RECT_ADV);
                        else state<=ST_RECT_ADV;
                    end else state<=ST_RECT_ADV;
                end
                ST_RECT_ADV:begin
                    if(rx==rw-1) begin
                        rx<=0;
                        if(ry==rh-1) state<=ST_IDLE;
                        else begin ry<=ry+1;state<=ST_RECT_PIXEL;end
                    end else begin rx<=rx+1;state<=ST_RECT_PIXEL;end
                end

                ST_CIRCLE_PIXEL:begin
                    circle_dist=cxo*cxo+cyo*cyo;
                    if(circle_dist<=r2 && circle_dist>=inner2 && in_clip(ccx+cxo,ccy+cyo))
                        start_pixel(ccx+cxo,ccy+cyo,PIX_VALUE,draw_color,back_base(),ST_CIRCLE_ADV);
                    else state<=ST_CIRCLE_ADV;
                end
                ST_CIRCLE_ADV:begin
                    if(cxo==cr) begin
                        cxo<=-cr;
                        if(cyo==cr) state<=ST_IDLE;
                        else begin cyo<=cyo+1;state<=ST_CIRCLE_PIXEL;end
                    end else begin cxo<=cxo+1;state<=ST_CIRCLE_PIXEL;end
                end

                ST_BLIT_READ:begin
                    if(bw<=0 || bh<=0 || (bsrc+by*bw+bx)>=1024) state<=ST_IDLE;
                    else if(vram_ready) begin
                        sprite_value<=vram_rdata[((SPRITE_BASE+bsrc+by*bw+bx)&3)*8 +: 8];
                        state<=ST_BLIT_PIXEL;
                    end
                end
                ST_BLIT_PIXEL:begin
                    if(in_clip(bdx+bx,bdy+by)) begin
                        if(!blit_transparent || sprite_value[0])
                            start_pixel(
                                bdx+bx,bdy+by,PIX_VALUE,
                                sprite_value[0]?draw_color:~draw_color,
                                back_base(),ST_BLIT_ADV
                            );
                        else state<=ST_BLIT_ADV;
                    end else state<=ST_BLIT_ADV;
                end
                ST_BLIT_ADV:begin
                    if(bx==bw-1) begin
                        bx<=0;
                        if(by==bh-1) state<=ST_IDLE;
                        else begin by<=by+1;state<=ST_BLIT_READ;end
                    end else begin bx<=bx+1;state<=ST_BLIT_READ;end
                end

                ST_CHAR_PIXEL:begin
                    if(font_bits[4-char_col] && in_clip(char_x+char_col,char_y+char_row))
                        start_pixel(char_x+char_col,char_y+char_row,PIX_VALUE,draw_color,back_base(),ST_CHAR_ADV);
                    else state<=ST_CHAR_ADV;
                end
                ST_CHAR_ADV:begin
                    if(char_col==4) begin
                        char_col<=0;
                        if(char_row==6) state<=ST_IDLE;
                        else begin char_row<=char_row+1;state<=ST_CHAR_PIXEL;end
                    end else begin char_col<=char_col+1;state<=ST_CHAR_PIXEL;end
                end

                ST_TEXT_CHAR_READ:begin
                    if(text_index>=text_len || text_index>=256) state<=ST_IDLE;
                    else if(vram_ready) begin
                        text_char<=vram_rdata[((TEXT_BASE+text_start+text_index)&3)*8 +: 8];
                        text_row<=0;text_col<=0;state<=ST_TEXT_PIXEL;
                    end
                end
                ST_TEXT_PIXEL:begin
                    if(font_bits[4-text_col] &&
                       in_clip(text_x+text_index*6+text_col,text_y+text_row))
                        start_pixel(
                            text_x+text_index*6+text_col,text_y+text_row,
                            PIX_VALUE,draw_color,back_base(),ST_TEXT_ADV
                        );
                    else state<=ST_TEXT_ADV;
                end
                ST_TEXT_ADV:begin
                    if(text_col==4) begin
                        text_col<=0;
                        if(text_row==6) begin
                            text_row<=0;text_index<=text_index+1;state<=ST_TEXT_CHAR_READ;
                        end else begin text_row<=text_row+1;state<=ST_TEXT_PIXEL;end
                    end else begin text_col<=text_col+1;state<=ST_TEXT_PIXEL;end
                end

                ST_COPY_READ:if(vram_ready) begin copy_word<=vram_rdata;state<=ST_COPY_WRITE;end
                ST_COPY_WRITE:if(vram_ready) begin
                    if(work_word==FRAME_WORDS-1) state<=ST_IDLE;
                    else begin work_word<=work_word+1;state<=ST_COPY_READ;end
                end

                ST_SCROLL_COPY_READ:if(vram_ready) begin copy_word<=vram_rdata;state<=ST_SCROLL_COPY_WRITE;end
                ST_SCROLL_COPY_WRITE:if(vram_ready) begin
                    if(work_word==FRAME_WORDS-1) begin work_word<=0;state<=ST_SCROLL_CLEAR;end
                    else begin work_word<=work_word+1;state<=ST_SCROLL_COPY_READ;end
                end
                ST_SCROLL_CLEAR:if(vram_ready) begin
                    if(work_word==FRAME_WORDS-1) begin scroll_index<=0;state<=ST_SCROLL_ADV;end
                    else work_word<=work_word+1;
                end
                ST_SCROLL_ADV:begin
                    if(scroll_index>=PIXELS) state<=ST_IDLE;
                    else begin
                        scroll_sx=(scroll_index%WIDTH)-scroll_dx;
                        scroll_sy=(scroll_index/WIDTH)-scroll_dy;
                        if(in_screen(scroll_sx,scroll_sy)) state<=ST_SCROLL_SRC_READ;
                        else scroll_index<=scroll_index+1;
                    end
                end
                ST_SCROLL_SRC_READ:if(vram_ready) begin
                    scroll_source_bit<=vram_rdata[packed_bit_index(scroll_sx,scroll_sy)];
                    state<=ST_SCROLL_DST_PIXEL;
                end
                ST_SCROLL_DST_PIXEL:begin
                    if(scroll_source_bit)
                        start_pixel(
                            scroll_index%WIDTH,scroll_index/WIDTH,
                            PIX_SET,1,back_base(),ST_SCROLL_ADV
                        );
                    else begin scroll_index<=scroll_index+1;state<=ST_SCROLL_ADV;end
                    if(scroll_source_bit) scroll_index<=scroll_index+1;
                end

                ST_PUSH_READ:if(vram_ready) begin
                    push_word<=vram_rdata;push_bit_index<=0;state<=ST_PUSH_BITS;
                end
                ST_PUSH_BITS:if(disp_ready) begin
                    if(push_bit_index==31 ||
                       push_word_index*32+push_bit_index+1>=PIXELS) begin
                        if(push_word_index==FRAME_WORDS-1) state<=ST_IDLE;
                        else begin
                            push_word_index<=push_word_index+1;
                            push_bit_index<=0;
                            state<=ST_PUSH_READ;
                        end
                    end else push_bit_index<=push_bit_index+1'b1;
                end

                default:state<=ST_IDLE;
            endcase
        end
    end
endmodule
