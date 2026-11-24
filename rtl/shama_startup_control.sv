// The physical startup island. A reset receipt crosses the actual generated
// core clock. The raw selector reports when its inhibit has reached the gate.
module shama_startup_control (
    input wire clk, power_switch, reset_button, shutdown_ready, divider_high, core_gate,
    output wire clock_hold, core_clk, rst
);
    logic raw_clock_inhibited;
    logic powered = 1'b0;
    logic divided_clock_active = 1'b0;
    logic reset_pulse_request = 1'b0, reset_pulse_received = 1'b0;
    // Turning the lever off requests a clean close. The clock remains held on
    // until filesystem saves, ownership cleanup and volatile clearing finish.
    // The lever requests power for as long as it is held. Keep the power
    // register set while the filesystem closes after the lever falls.
    wire powered_or_start, start_request, stop_request, next_power;
    assign start_request = power_switch;
    assign stop_request = (reset_button && !power_switch) ||
        (!reset_button && powered && divided_clock_active && shutdown_ready);
    CLOCK_OR keep_power(.A(powered),.B(start_request),.Y(powered_or_start));
    WRITE_GUARD stop_power(.A(powered_or_start),.B(stop_request),.Y(next_power));
    always_ff @(posedge clk) begin
        powered <= next_power;
    end
    // Keep reset through a complete high phase when the physical clock
    // starts. Switching sources as powered rises would truncate that very
    // first reset pulse. A reset request still inhibits writes immediately.
    // A completed shutdown can leave its acknowledgment high while stopped.
    // Keep sending raw reset pulses until the service actually clears it.
    // Only powered.Q can enable this clock mode as the unpowered world loads.
    // Return to zero before each request. An old receipt can remain high
    // across a stopped clock. Raw pulses clear it before starting the next
    // handshake. Only a real generated core edge can return the new receipt.
    always_ff @(posedge core_clk)
        reset_pulse_received <= reset_pulse_request;
    always_ff @(posedge clk) begin
        if (!powered || reset_button || (!divided_clock_active && shutdown_ready))
            reset_pulse_request <= 1'b0;
        else if (!reset_pulse_received)
            reset_pulse_request <= 1'b1;
    end
    wire next_divided_mode;
    // Hold request and receipt throughout RUN. Removing the request as
    // mode rises opens a long reconverging feedback path on physical gates.
    WRITE_GUARD next_mode(.A(reset_pulse_received),
        .B(!powered || reset_button || !reset_pulse_request), .Y(next_divided_mode));
    always_ff @(negedge clk)
        divided_clock_active <= next_divided_mode;
    assign rst = reset_button | ~powered | ~divided_clock_active | ~raw_clock_inhibited;
    assign clock_hold = powered;

    // Only the raw clock and the divider's Q can energize these paths.
    // A torch-based Boolean mux can power its output while the world loads,
    // clocking a memory write register before the reset wire reaches it.
    wire raw_pulse, divided_pulse;
    CLOCK_GUARD_ACK raw_clock_path(.A(clk), .B(divided_clock_active),
        .Y(raw_pulse), .Q(raw_clock_inhibited));
    CLOCK_GUARD divided_clock_path(.A(divider_high),
        .B(!(divided_clock_active && core_gate)), .Y(divided_pulse));
    CLOCK_OR core_clock_join(.A(raw_pulse), .B(divided_pulse), .Y(core_clk));

endmodule

`include "shama_native_cells.svh"
