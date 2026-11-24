`ifndef SHAMA_NATIVE_CELLS_SVH
`define SHAMA_NATIVE_CELLS_SVH

// Yosys retains this cell so the generator can use a passive subtract
// comparator for the final write gate. RTL simulation uses the same truth table.
`ifdef SYNTHESIS
(* blackbox *)
`endif
module WRITE_GUARD(input wire A, B, output wire Y);
`ifndef SYNTHESIS
    assign Y = A && !B;
`endif
endmodule

// Preserve the directional, unpowered join when ABC maps the surrounding logic.
`ifdef SYNTHESIS
(* blackbox *)
`endif
module CLOCK_OR(input wire A, B, output wire Y);
`ifndef SYNTHESIS
    assign Y = A | B;
`endif
endmodule

// The selector is transparent while its own physical pulse source is low.
// Once the high phase begins, changes arriving on the inhibit route must wait.
`ifdef SYNTHESIS
(* blackbox *)
`endif
module CLOCK_GUARD(input wire A, B, output wire Y);
`ifndef SYNTHESIS
    CLOCK_GUARD_ACK gate(.A(A), .B(B), .Y(Y), .Q());
`endif
endmodule

// Q is an isolated tap of the actual phase-held inhibit at the raw-clock
// selector. Reset stays high until that hardware has disabled raw pulses.
`ifdef SYNTHESIS
(* blackbox *)
`endif
module CLOCK_GUARD_ACK(input wire A, B, output wire Y, Q);
`ifndef SYNTHESIS
    reg inhibit = 1'b0;
    always @* if (!A) inhibit = B;
    assign Y = A && !inhibit;
    assign Q = inhibit;
`endif
endmodule

`endif
