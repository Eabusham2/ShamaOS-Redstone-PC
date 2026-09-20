module shama_font5x7(
    input  logic [7:0] ch_in,
    input  logic [2:0] row,
    output logic [4:0] bits
);
    logic [7:0] ch;
    always_comb begin
        ch = (ch_in >= "a" && ch_in <= "z") ? (ch_in - 8'd32) : ch_in;
        bits = 5'b00000;
        case (ch)
            "A": case(row) 0:bits=5'b01110;1:bits=5'b10001;2:bits=5'b10001;3:bits=5'b11111;4:bits=5'b10001;5:bits=5'b10001;6:bits=5'b10001;default:bits=0;endcase
            "B": case(row) 0:bits=5'b11110;1:bits=5'b10001;2:bits=5'b10001;3:bits=5'b11110;4:bits=5'b10001;5:bits=5'b10001;6:bits=5'b11110;default:bits=0;endcase
            "C": case(row) 0:bits=5'b01111;1:bits=5'b10000;2:bits=5'b10000;3:bits=5'b10000;4:bits=5'b10000;5:bits=5'b10000;6:bits=5'b01111;default:bits=0;endcase
            "D": case(row) 0:bits=5'b11110;1:bits=5'b10001;2:bits=5'b10001;3:bits=5'b10001;4:bits=5'b10001;5:bits=5'b10001;6:bits=5'b11110;default:bits=0;endcase
            "E": case(row) 0:bits=5'b11111;1:bits=5'b10000;2:bits=5'b10000;3:bits=5'b11110;4:bits=5'b10000;5:bits=5'b10000;6:bits=5'b11111;default:bits=0;endcase
            "F": case(row) 0:bits=5'b11111;1:bits=5'b10000;2:bits=5'b10000;3:bits=5'b11110;4:bits=5'b10000;5:bits=5'b10000;6:bits=5'b10000;default:bits=0;endcase
            "G": case(row) 0:bits=5'b01111;1:bits=5'b10000;2:bits=5'b10000;3:bits=5'b10111;4:bits=5'b10001;5:bits=5'b10001;6:bits=5'b01111;default:bits=0;endcase
            "H": case(row) 0:bits=5'b10001;1:bits=5'b10001;2:bits=5'b10001;3:bits=5'b11111;4:bits=5'b10001;5:bits=5'b10001;6:bits=5'b10001;default:bits=0;endcase
            "I": case(row) 0:bits=5'b11111;1:bits=5'b00100;2:bits=5'b00100;3:bits=5'b00100;4:bits=5'b00100;5:bits=5'b00100;6:bits=5'b11111;default:bits=0;endcase
            "J": case(row) 0:bits=5'b00111;1:bits=5'b00010;2:bits=5'b00010;3:bits=5'b00010;4:bits=5'b10010;5:bits=5'b10010;6:bits=5'b01100;default:bits=0;endcase
            "K": case(row) 0:bits=5'b10001;1:bits=5'b10010;2:bits=5'b10100;3:bits=5'b11000;4:bits=5'b10100;5:bits=5'b10010;6:bits=5'b10001;default:bits=0;endcase
            "L": case(row) 0:bits=5'b10000;1:bits=5'b10000;2:bits=5'b10000;3:bits=5'b10000;4:bits=5'b10000;5:bits=5'b10000;6:bits=5'b11111;default:bits=0;endcase
            "M": case(row) 0:bits=5'b10001;1:bits=5'b11011;2:bits=5'b10101;3:bits=5'b10101;4:bits=5'b10001;5:bits=5'b10001;6:bits=5'b10001;default:bits=0;endcase
            "N": case(row) 0:bits=5'b10001;1:bits=5'b11001;2:bits=5'b10101;3:bits=5'b10011;4:bits=5'b10001;5:bits=5'b10001;6:bits=5'b10001;default:bits=0;endcase
            "O": case(row) 0:bits=5'b01110;1:bits=5'b10001;2:bits=5'b10001;3:bits=5'b10001;4:bits=5'b10001;5:bits=5'b10001;6:bits=5'b01110;default:bits=0;endcase
            "P": case(row) 0:bits=5'b11110;1:bits=5'b10001;2:bits=5'b10001;3:bits=5'b11110;4:bits=5'b10000;5:bits=5'b10000;6:bits=5'b10000;default:bits=0;endcase
            "Q": case(row) 0:bits=5'b01110;1:bits=5'b10001;2:bits=5'b10001;3:bits=5'b10001;4:bits=5'b10101;5:bits=5'b10010;6:bits=5'b01101;default:bits=0;endcase
            "R": case(row) 0:bits=5'b11110;1:bits=5'b10001;2:bits=5'b10001;3:bits=5'b11110;4:bits=5'b10100;5:bits=5'b10010;6:bits=5'b10001;default:bits=0;endcase
            "S": case(row) 0:bits=5'b01111;1:bits=5'b10000;2:bits=5'b10000;3:bits=5'b01110;4:bits=5'b00001;5:bits=5'b00001;6:bits=5'b11110;default:bits=0;endcase
            "T": case(row) 0:bits=5'b11111;1:bits=5'b00100;2:bits=5'b00100;3:bits=5'b00100;4:bits=5'b00100;5:bits=5'b00100;6:bits=5'b00100;default:bits=0;endcase
            "U": case(row) 0:bits=5'b10001;1:bits=5'b10001;2:bits=5'b10001;3:bits=5'b10001;4:bits=5'b10001;5:bits=5'b10001;6:bits=5'b01110;default:bits=0;endcase
            "V": case(row) 0:bits=5'b10001;1:bits=5'b10001;2:bits=5'b10001;3:bits=5'b10001;4:bits=5'b10001;5:bits=5'b01010;6:bits=5'b00100;default:bits=0;endcase
            "W": case(row) 0:bits=5'b10001;1:bits=5'b10001;2:bits=5'b10001;3:bits=5'b10101;4:bits=5'b10101;5:bits=5'b10101;6:bits=5'b01010;default:bits=0;endcase
            "X": case(row) 0:bits=5'b10001;1:bits=5'b10001;2:bits=5'b01010;3:bits=5'b00100;4:bits=5'b01010;5:bits=5'b10001;6:bits=5'b10001;default:bits=0;endcase
            "Y": case(row) 0:bits=5'b10001;1:bits=5'b10001;2:bits=5'b01010;3:bits=5'b00100;4:bits=5'b00100;5:bits=5'b00100;6:bits=5'b00100;default:bits=0;endcase
            "Z": case(row) 0:bits=5'b11111;1:bits=5'b00001;2:bits=5'b00010;3:bits=5'b00100;4:bits=5'b01000;5:bits=5'b10000;6:bits=5'b11111;default:bits=0;endcase
            "0": case(row) 0:bits=5'b01110;1:bits=5'b10001;2:bits=5'b10011;3:bits=5'b10101;4:bits=5'b11001;5:bits=5'b10001;6:bits=5'b01110;default:bits=0;endcase
            "1": case(row) 0:bits=5'b00100;1:bits=5'b01100;2:bits=5'b00100;3:bits=5'b00100;4:bits=5'b00100;5:bits=5'b00100;6:bits=5'b01110;default:bits=0;endcase
            "2": case(row) 0:bits=5'b01110;1:bits=5'b10001;2:bits=5'b00001;3:bits=5'b00010;4:bits=5'b00100;5:bits=5'b01000;6:bits=5'b11111;default:bits=0;endcase
            "3": case(row) 0:bits=5'b11110;1:bits=5'b00001;2:bits=5'b00001;3:bits=5'b01110;4:bits=5'b00001;5:bits=5'b00001;6:bits=5'b11110;default:bits=0;endcase
            "4": case(row) 0:bits=5'b00010;1:bits=5'b00110;2:bits=5'b01010;3:bits=5'b10010;4:bits=5'b11111;5:bits=5'b00010;6:bits=5'b00010;default:bits=0;endcase
            "5": case(row) 0:bits=5'b11111;1:bits=5'b10000;2:bits=5'b10000;3:bits=5'b11110;4:bits=5'b00001;5:bits=5'b00001;6:bits=5'b11110;default:bits=0;endcase
            "6": case(row) 0:bits=5'b01110;1:bits=5'b10000;2:bits=5'b10000;3:bits=5'b11110;4:bits=5'b10001;5:bits=5'b10001;6:bits=5'b01110;default:bits=0;endcase
            "7": case(row) 0:bits=5'b11111;1:bits=5'b00001;2:bits=5'b00010;3:bits=5'b00100;4:bits=5'b01000;5:bits=5'b01000;6:bits=5'b01000;default:bits=0;endcase
            "8": case(row) 0:bits=5'b01110;1:bits=5'b10001;2:bits=5'b10001;3:bits=5'b01110;4:bits=5'b10001;5:bits=5'b10001;6:bits=5'b01110;default:bits=0;endcase
            "9": case(row) 0:bits=5'b01110;1:bits=5'b10001;2:bits=5'b10001;3:bits=5'b01111;4:bits=5'b00001;5:bits=5'b00001;6:bits=5'b01110;default:bits=0;endcase
            "-": if(row==3) bits=5'b11111;
            "_": if(row==6) bits=5'b11111;
            ".": if(row==6) bits=5'b00100;
            ":": if(row==2 || row==5) bits=5'b00100;
            "/": case(row) 0:bits=5'b00001;1:bits=5'b00010;2:bits=5'b00010;3:bits=5'b00100;4:bits=5'b01000;5:bits=5'b01000;6:bits=5'b10000;default:bits=0;endcase
            "<": case(row) 1:bits=5'b00010;2:bits=5'b00100;3:bits=5'b01000;4:bits=5'b00100;5:bits=5'b00010;default:bits=0;endcase
            ">": case(row) 1:bits=5'b01000;2:bits=5'b00100;3:bits=5'b00010;4:bits=5'b00100;5:bits=5'b01000;default:bits=0;endcase
            "=": begin if(row==2 || row==4) bits=5'b11111; end
            "+": begin if(row==3) bits=5'b11111; else if(row>=1 && row<=5) bits=5'b00100; end
            "*": begin if(row==2 || row==4) bits=5'b10101; else if(row==3) bits=5'b01110; end
            "(": case(row) 0:bits=5'b00010;1:bits=5'b00100;2:bits=5'b01000;3:bits=5'b01000;4:bits=5'b01000;5:bits=5'b00100;6:bits=5'b00010;default:bits=0;endcase
            ")": case(row) 0:bits=5'b01000;1:bits=5'b00100;2:bits=5'b00010;3:bits=5'b00010;4:bits=5'b00010;5:bits=5'b00100;6:bits=5'b01000;default:bits=0;endcase
            "[": case(row) 0:bits=5'b01110;1:bits=5'b01000;2:bits=5'b01000;3:bits=5'b01000;4:bits=5'b01000;5:bits=5'b01000;6:bits=5'b01110;default:bits=0;endcase
            "]": case(row) 0:bits=5'b01110;1:bits=5'b00010;2:bits=5'b00010;3:bits=5'b00010;4:bits=5'b00010;5:bits=5'b00010;6:bits=5'b01110;default:bits=0;endcase
            "!": begin if(row<=4) bits=5'b00100; else if(row==6) bits=5'b00100; end
            "?": case(row) 0:bits=5'b01110;1:bits=5'b10001;2:bits=5'b00001;3:bits=5'b00010;4:bits=5'b00100;6:bits=5'b00100;default:bits=0;endcase
            default: bits=5'b00000;
        endcase
    end
endmodule
