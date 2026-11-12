module tb_kernel_ui;
    reg clk=0,rst=1,req_valid=0;
    reg [11:0] req_id=0;
    reg [191:0] req_args=0;
    wire req_ready,req_jump_valid,req_load_app;
    wire [63:0] req_ret;
    wire [31:0] req_jump_pc;
    wire [3:0] req_app_id;
    reg [31:0] ram_used_bytes=32'h12345,cache_used_bytes=32'h1200,flash_used_bytes=32'h45678;
    reg cpu_halted=0,sha_busy=0,gpu_busy=0;
    reg gpu_capacity_fault=0;
    reg [7:0] notice_code=0;
    wire notice_busy,notice_ready;
    wire gpu_valid,gpu_we;
    wire [11:0] gpu_addr;
    wire [31:0] gpu_wdata;
    wire [3:0] gpu_wstrb;
    wire gpu_ready=gpu_valid;
    reg [31:0] fence=0;
    wire [31:0] gpu_rdata=fence;
    wire fs_valid,asm_valid;
    wire [11:0] fs_id;
    wire [191:0] fs_args,asm_args;
    reg fs_ready=0,asm_ready=0;
    reg [63:0] fs_ret=0,asm_ret=0;
    reg [7:0] textmem[0:255];
    reg [31:0] a[0:7];
    reg [7:0] command;
    reg [255:0] title,help,ramline,cacheline,flashline,popup_title,popup_body,popup_help;
    integer i,k,j,cycles,clears=0,copies=0,clears_before;
    always #5 clk=~clk;
    shama_kernel_accel dut(.*);
    always @(posedge clk) if(gpu_valid && gpu_we) begin
        if(gpu_addr>=12'h400 && gpu_addr<12'h500) begin
            for(k=0;k<4;k=k+1)
                if(gpu_wstrb[k]) textmem[gpu_addr-12'h400+k]<=gpu_wdata[k*8 +: 8];
        end else if(gpu_addr==4) command<=gpu_wdata[7:0];
        else if(gpu_addr>=8 && gpu_addr<=36) a[(gpu_addr-8)>>2]<=gpu_wdata;
        else if(gpu_addr==40) begin
            if(command==8'h16) fence<=fence+1;
            if(command==1) clears<=clears+1;
            if(command==8'h13) copies<=copies+1;
            if(command==12) begin
                for(j=0;j<32;j=j+1) begin
                    if(a[3]==4) title[(31-j)*8 +: 8]<=textmem[a[0]+j];
                    if(a[3]==28) help[(31-j)*8 +: 8]<=textmem[a[0]+j];
                    if(a[3]==144) ramline[(31-j)*8 +: 8]<=textmem[a[0]+j];
                    if(a[3]==156) cacheline[(31-j)*8 +: 8]<=textmem[a[0]+j];
                    if(a[3]==168) flashline[(31-j)*8 +: 8]<=textmem[a[0]+j];
                    if(a[3]==56) popup_title[(31-j)*8 +: 8]<=textmem[a[0]+j];
                    if(a[3]==72) popup_body[(31-j)*8 +: 8]<=textmem[a[0]+j];
                    if(a[3]==88) popup_help[(31-j)*8 +: 8]<=textmem[a[0]+j];
                end
            end
        end
    end
    task view(input [11:0] id,input [31:0] arg1,input [31:0] arg2,input [31:0] arg3);
        reg [31:0] before_fence;
        begin
            before_fence=fence;
            @(negedge clk);req_id=id;req_args={96'd0,arg3,arg2,arg1};req_valid=1;
            cycles=0;
            while(!req_ready && cycles<3000) begin @(negedge clk);cycles=cycles+1;end
            if(!req_ready) $fatal(1,"UI timeout");
            if(fence!=before_fence+1) $fatal(1,"UI returned before GPU fence completion");
            @(negedge clk);req_valid=0;
            repeat(3) @(negedge clk);
        end
    endtask
    initial begin
        for(i=0;i<256;i=i+1) textmem[i]=0;
        for(i=0;i<8;i=i+1) a[i]=0;
        repeat(3) @(negedge clk);rst=0;
        view(12'h048,4,2,0);
        if(title!=="ACTION > RENAME                 ") $fatal(1,"selected action invisible: %s",title);
        if(ramline[255:56]!=="RAM     00012345/00030000") $fatal(1,"RAM used/total: %s",ramline);
        if(cacheline[191:120]!==72'h30303030313230302f) $fatal(1,"cache bytes: %s",cacheline);
        if(cacheline[119:56]!=="00002000") $fatal(1,"cache total: %s",cacheline);
        if(flashline[191:120]!==72'h30303034353637382f) $fatal(1,"flash bytes: %s",flashline);
        if(flashline[119:56]!=="0007D000") $fatal(1,"flash total: %s",flashline);
        view(12'h048,5,0,0);
        if(title!=="DELETE > CANCEL   A SELECT      ") $fatal(1,"delete default is not Cancel: %s",title);
        view(12'h048,5,1,0);
        if(title!=="DELETE > CONFIRM   B CANCEL     ") $fatal(1,"delete selected label: %s",title);
        view(12'h048,8,0,32'h00000c02);
        if(help!=="INVALID TOKEN OR NUMBER         ") $fatal(1,"diagnostic not rendered: %s",help);
        clears_before=clears;
        ram_used_bytes=32'h56789;
        view(12'h046,0,0,0);
        if(clears!=clears_before || copies==0) $fatal(1,"status refresh destroyed foreground canvas");
        if(ramline[191:120]!==72'h30303035363738392f) $fatal(1,"status refresh did not update usage: %s",ramline);
        if(fence!=5) $fatal(1,"missing UI completion fence");
        view(12'h04e,1,32'h25,0);
        if(title!=="CALCULATOR > SECOND OPERAND     " || help!=="SIGNED + - * / % ENTER B CLEAR  ")
            $fatal(1,"calculator input mode/controls not visible: %s / %s",title,help);
        view(12'h04e,2,0,0);
        if(title!=="CALCULATOR > RESULT             ") $fatal(1,"calculator result mode missing");
        view(12'h04e,1,32'h2f,1);
        if(title!=="CALCULATOR > ERROR              " || help!=="ZERO DIVISOR - BACKSPACE/B CLEAR")
            $fatal(1,"calculator zero error invisible: %s / %s",title,help);
        view(12'h04e,1,32'h2a,2);
        if(help!=="OVERFLOW - BACKSPACE OR B CLEAR ") $fatal(1,"calculator overflow invisible: %s",help);
        notice_code=1;
        wait(notice_ready);
        if(!notice_busy || req_ready || popup_title!=="FLASH WRITES PAUSED AT 95%      " ||
           popup_body!=="DELETE FILES TO RESTORE WRITING " || popup_help!=="A/B CLOSE THIS MESSAGE          ")
            $fatal(1,"asynchronous storage popup missing/wrong");
        notice_code=0;
        wait(!notice_busy);
        notice_code=8'h82;
        wait(notice_ready);
        if(popup_title!=="RAM LIMIT - OS RESTARTED        " || popup_body!=="UNSAVED EDITS COULD NOT BE SAVED")
            $fatal(1,"recovery popup hid unsaved-data failure");
        notice_code=0;
        wait(!notice_busy);
        $display("KERNEL UI BEHAVIOR PASS");$finish;
    end
endmodule
