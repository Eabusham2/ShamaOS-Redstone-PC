"""Small real-Yosys/real-Icarus equivalence test; runs where tools are installed."""
import json
import shutil
import subprocess

import pytest

from shamaos.hardware.logic import normalize_cell_type
from shamaos.synthesis import _expand_sequential_cells, synthesize_json


def original_register_bank():
    statements = []
    for clock in ("posedge", "negedge"):
        statements.append(f"always @({clock} C) Q[{len(statements)}] <= D;")
        for enabled in (0, 1):
            statements.append(f"always @({clock} C) if (E == 1'b{enabled}) Q[{len(statements)}] <= D;")
        for reset in (0, 1):
            for value in (0, 1):
                statements.append(f"always @({clock} C) if (R == 1'b{reset}) Q[{len(statements)}] <= 1'b{value}; else Q[{len(statements)}] <= D;")
                for enabled in (0, 1):
                    i = len(statements)
                    statements.append(f"always @({clock} C) if (R == 1'b{reset}) Q[{i}] <= 1'b{value}; else if (E == 1'b{enabled}) Q[{i}] <= D;")
                    i = len(statements)
                    statements.append(f"always @({clock} C) if (E == 1'b{enabled}) begin if (R == 1'b{reset}) Q[{i}] <= 1'b{value}; else Q[{i}] <= D; end")
    return f"module original(input C,D,E,R, output reg [{len(statements)-1}:0] Q);\n" + "\n".join(statements) + "\nendmodule\n", len(statements)


def lowered_verilog(module, width):
    max_bit = max(bit for cell in module["cells"].values() for bits in cell["connections"].values() for bit in bits if isinstance(bit, int))
    lines = [f"module lowered(input C,D,E,R,output [{width-1}:0] Q);", f"wire [{max_bit}:0] n;"]

    def wire(bit):
        return f"n[{bit}]" if isinstance(bit, int) else f"1'b{bit}"

    for name, port in module["ports"].items():
        for i, bit in enumerate(port["bits"]):
            value = name if len(port["bits"]) == 1 else f"{name}[{i}]"
            if port["direction"] == "input":
                lines.append(f"assign {wire(bit)} = {value};")
            else:
                lines.append(f"assign {value} = {wire(bit)};")
    for i, cell in enumerate(module["cells"].values()):
        kind = normalize_cell_type(cell["type"])
        pins = {pin: wire(bits[0]) for pin, bits in cell["connections"].items()}
        if kind == "DFF":
            lines += [f"reg q{i};", f"always @(posedge {pins['C']}) q{i} <= {pins['D']};", f"assign {pins['Q']} = q{i};"]
        else:
            expressions = {
                "BUF": pins["A"], "NOT": f"~{pins['A']}",
                "AND": f"{pins['A']} & {pins.get('B', 'x')}",
                "NAND": f"~({pins['A']} & {pins.get('B', 'x')})",
                "NOR": f"~({pins['A']} | {pins.get('B', 'x')})",
            }
            assert kind in expressions
            lines.append(f"assign {pins['Y']} = {expressions[kind]};")
    return "\n".join(lines) + "\nendmodule\n"


@pytest.mark.parametrize("mapping", ["abc", "aig"])
def test_real_synthesis_preserves_register_bank_behavior(tmp_path, mapping):
    yosys = shutil.which("yosys") or shutil.which("yowasp-yosys")
    iverilog = shutil.which("iverilog")
    vvp = shutil.which("vvp")
    if not (yosys and iverilog and vvp):
        pytest.skip("real Yosys, Icarus and vvp are required; bounded semantic tests run separately")
    original, width = original_register_bank()
    source = tmp_path / "original.v"
    source.write_text(original)
    mapped = synthesize_json([source], top="original", output_json=tmp_path / "mapped.json", yosys=yosys, mapping_mode=mapping)
    module = json.loads(mapped.read_text())["modules"]["original"]
    _expand_sequential_cells(module)
    testbench = f"""
module tb;
reg C=0,D=0,E=0,R=0;
wire [{width-1}:0] original_q,lowered_q;
original a(C,D,E,R,original_q);
lowered b(C,D,E,R,lowered_q);
integer i;
initial begin
  // Exercise reset, enable, data and both clock edges repeatedly. The first 32
  // transitions initialize every variant; startup X-to-clock scheduling is not
  // an architectural edge and can differ between behavioral and gate models.
  for(i=0;i<256;i=i+1) begin
    #2; D=(i>>1)&1; E=(i>>2)&1; R=(i>>3)&1;
    #2; C=i&1;
    #2; if(i>=32 && original_q !== lowered_q) begin
      $display("mismatch i=%0d expected=%b got=%b",i,original_q,lowered_q);
      $fatal(1);
    end
  end
  $finish;
end
endmodule
"""
    simulation = tmp_path / "simulation.v"
    simulation.write_text(original + lowered_verilog(module, width) + testbench)
    output = tmp_path / "simulation.vvp"
    subprocess.run([iverilog, "-g2012", "-s", "tb", "-o", str(output), str(simulation)], check=True, capture_output=True, text=True)
    result = subprocess.run([vvp, str(output)], check=True, capture_output=True, text=True, timeout=30)
    assert "mismatch" not in result.stdout
