# Named physical startup controller

The SoC instantiates one `shama_startup_control`. Its four native registers implement the held power request, the falling-edge clock mode, and the reset request and receipt. The mode selector exposes its physical held inhibit before releasing reset. Both generator backends use the same mapped child, one native logic tile, and the original nine external port contacts.

On Yosys 0.33 this child has 29 cells. The default matched layout missed the reset-request register's 8-redstone-tick setup requirement by 3 ticks on six edges across five stimuli. The local adjustment selects the receiving DFF through the logical request signal, checks the clock is raw, and turns one existing 1-tick repeater into a 4-tick repeater on that DFF's private branch. It verifies exclusive ownership and emits the original position, facing and neighboring blocks. The source name of the synthesized DFF is immaterial.

Run from the checkout with the terrain runner and Yosys configured.

```sh
PYTHONPATH=src python experiments/named_startup_domain/measure.py /tmp/startup-positive
PYTHONPATH=src python experiments/named_startup_domain/measure.py /tmp/startup-negative --baseline
PYTHONPATH=src:. python experiments/named_startup_domain/check_binding.py /path/to/shama_soc-shell-mapped.json
```

The 28,860-tick lifecycle includes cold power, manual reset, power withdrawal, delayed shutdown acknowledgment, standing acknowledgment through repower, and a 2,048-tick final drain. The delivered core output made 118 complete 35-tick pulses, each entirely covered by reset. Four short-lead scenes used 12, 55, 95 and 157 ticks of POWER before raw clock. Each produced eight complete raw reset pulses and one complete 1,024-tick divided pulse after release. All five ended with zero pending work.

| Local register | Default setup/hold | Adjusted setup/hold |
| --- | ---: | ---: |
| Power hold | 24/46 | 24/46 |
| Divided mode | 22/36 | 22/36 |
| Reset request | 5/38 | 8/35 |
| Reset receipt | 14/56 | 11/59 |

The evidence archive contains both native worlds and exported streams, the mapped child and integrated shell, all ten scenarios, all raw C/D/Q traces, and the graded reports. The shell map reads production child interfaces as black boxes and fully maps the real top-level source. It binds the generated core clock to eleven children, the raw clock to input, and generated reset to all twelve existing children. This native component trial drives its six input contacts. Whole-machine oscillator/hold feedback, divider and 64-bit time counter, downstream clock/reset distribution, real bank timing, and firmware execution in the emitted world still require separate proof.

The older 6,963-cell shell and the new shell each place the raw time counter and divider's least-significant Q→NOR→D feedback on default global routes. Each link has at least 580 repeater ticks and the reference period is 70. The isolated startup correction leaves those two feedback loops open.
