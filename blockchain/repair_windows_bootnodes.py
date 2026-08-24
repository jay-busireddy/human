from pathlib import Path
import json, sys

HERE=Path(__file__).resolve().parent
GEN=Path(__import__("os").environ.get("HC_CHAIN_DIR", str(HERE/"generated"))).resolve()
netp=GEN/"native_network.json"
if not netp.exists():
    raise SystemExit("No generated network. Run: python blockchain\\setup_qbft.py --generate")

net=json.loads(netp.read_text(encoding="utf-8"))
boot=net.get("bootnode")
if not boot:
    raise SystemExit("native_network.json has no bootnode entry")

bootfile=GEN/"bootnodes.txt"
bootfile.write_text(boot+"\n",encoding="utf-8")
net["bootnodes_file"]=str(bootfile.resolve())
netp.write_text(json.dumps(net,indent=2),encoding="utf-8")

# Remove stale PID files only; this does not kill any process.
removed=[]
for p in GEN.glob("node*/node.pid"):
    try:
        pid=int(p.read_text().strip())
    except Exception:
        p.unlink(missing_ok=True); removed.append(str(p)); continue
    # The launcher will re-check live PIDs. Keep PID files for now.
print("Created:",bootfile)
print("Updated:",netp)
print("Bootnode source is now a local text file.")
