from pathlib import Path
import json

HERE=Path(__file__).resolve().parent
GEN=Path(__import__("os").environ.get("HC_CHAIN_DIR", str(HERE/"generated"))).resolve()
bootfile=GEN/"bootnodes.txt"
netfile=GEN/"native_network.json"

if not netfile.exists():
    raise SystemExit("Missing blockchain/generated/native_network.json")

net=json.loads(netfile.read_text(encoding="utf-8"))
boot=(net.get("bootnode") or "").strip()
if not boot.startswith("enode://"):
    raise SystemExit(f"Invalid bootnode in native_network.json: {boot!r}")

# Write exactly one enode plus a real platform newline.
bootfile.write_text(boot + "\n", encoding="utf-8", newline=None)
net["bootnodes_file"]=str(bootfile.resolve())
netfile.write_text(json.dumps(net,indent=2),encoding="utf-8")

raw=bootfile.read_bytes()
print("Rewrote:",bootfile)
print("Text:",bootfile.read_text(encoding="utf-8").rstrip())
print("Ends with real newline:",raw.endswith(b"\\n"))
print("Contains literal backslash-n:",b"\\\\n" in raw)
