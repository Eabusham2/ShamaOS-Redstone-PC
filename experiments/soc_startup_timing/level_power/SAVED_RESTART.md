# Shutdown acknowledgment across a saved-world restart

Both experiments use the same 177-cell mapped power/reset and cache write cut with four physical 32-bit words. The first reuses the original switched-off Anvil world byte for byte. The second adds a real Java lever to the service acknowledgment input before writing the first row and shutting down. Both use the pinned terrain-aware MCHPRS binary and the 35/35 RT raw clock.

## Static service acknowledgment

The unchanged restored world completed 6,348 RT in a fresh process. The saved core-gate and acknowledgment power blocks remained high. The scenario drove only the initially empty POWER and raw clock contacts. A 12 RT POWER lead produced 32 complete 35 RT raw reset pulses at all six cache registers. All ten local clocks and data inputs met the 8 RT setup and 2 RT hold requirements. The four modified/preloaded words retained all 128 bits throughout the sustained-reset case. The engine ended with zero queued events.

The original two-restart scenario remains an independently recorded preflight rejection. That historical case attempted to declare the two persisted power blocks as externally mutable contacts. The successful static case uses a different, fully retained scenario.

## Physically switched acknowledgment

The initial Anvil world had 4,566 saved chunks. All 109,719 initial stream records were compared. Only a floor lever at (5345, −45, 448) and its supporting stone at (5345, −46, 448) were added. The lever touches only the pre-existing ACK dust terminal. Its support touches only the terminal’s existing stone support. The complete original physical cut, three private clock repeater settings and delayed final write guard were preserved.

The first native process executed 417,248 RT and 22,041 assertions. It changed row zero and then actuated the actual ACK lever during shutdown. The engine wrote its quiescent snapshot with zero pending ticks. Independent native Anvil readback covered 4,569 live chunks, 109,656 sections and 133 comparator entities. The restored input was a powered, floor-mounted lever. Direct storage readback recovered 00000000, 12345678, 11223344 and ABCDEF01.

The fresh process executed 19,800 RT and 59,212 assertions. The saved ACK remained high during the first 32 raw cycles of each short-lead restart. Three actual lever uses cleared ACK, asserted it during the second shutdown, and cleared it after repower. 8/8 settled power/reset state checkpoints matched. The generated clock produced 112 highs with widths [35] RT. Source reset covered every high. The physical row-write and pending-write intervals were [] and [].

| Physical register | Rising edges | Minimum setup (RT) | Minimum hold (RT) | Edges below 8/2 RT |
| --- | ---: | ---: | ---: | ---: |
| clock_hold | 219 | 8 | 27 | 0 |
| divided_clock_active | 217 | 13 | 13 | 0 |
| reset_pulse_request | 224 | 54 | 12 | 0 |
| reset_pulse_received | 112 | 38 | 32 | 0 |
| state_0 | 112 | 2802 | 1708 | 0 |
| state_1 | 112 | 2900 | 1610 | 0 |
| state_2 | 112 | 2747 | 1763 | 0 |
| bank | 112 | 2547 | 1963 | 0 |
| write_enable | 112 | 2857 | 1653 | 0 |
| pending | 112 | 2883 | 1627 | 0 |

All ten local registers met the setup and hold requirements. The native waveform contains the actual lever output, shell gate outputs, all original cache reset qualifier pins and the C/D/Q pins of the six storage receivers. The executable verifier checks source/receiver pulse widths and local reset qualification and independently recalculates the ten setup and hold results.

## Reproduction and scope

The lossless continuation package references the previously hashed source-study files. Extract its standalone restore_continuations.py, then run python3 restore_continuations.py CONTINUATIONS.tar.xz RESTORED_ORIGINAL_STUDY NEW_DIRECTORY. The restore program checks every reference and every reconstructed file’s complete SHA-256. Run python3 NEW_DIRECTORY/code/verify_continuations.py NEW_DIRECTORY RESTORED_ORIGINAL_STUDY to rederive the electrical and persistence results from the actual native bytes.

The raw oscillator and divider remain fixture boundaries. These results concern the saved four-word physical cut and its actual restart input protocol. Full-computer routed timing and whole-machine software boot remain open.
