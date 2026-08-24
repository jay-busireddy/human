from pathlib import Path
import json, subprocess, shutil, argparse, os
from eth_account import Account
from native_control import GEN, find_besu, run_besu

HERE=Path(__file__).resolve().parent

def main():
    ap=argparse.ArgumentParser(description="Generate a 7-validator QBFT network natively; Docker is not used.")
    ap.add_argument("--generate",action="store_true")
    args=ap.parse_args()
    if not args.generate: ap.error("use --generate")
    besu=find_besu()
    print("Using Besu:",besu)
    try:
        r=run_besu(["--version"],capture=True);print((r.stdout or r.stderr).strip())
    except Exception as e:raise SystemExit(f"Besu failed to run: {e}")
    # Never delete a live network accidentally.
    if GEN.exists() and list(GEN.glob("node*/node.pid")):
        raise SystemExit("PID files exist. First run: python blockchain/manage_qbft.py stop ; then rerun setup.")
    if GEN.exists():shutil.rmtree(GEN)
    GEN.mkdir(parents=True)
    rpc_base=int(os.environ.get("HC_RPC_BASE","8545"))
    p2p_base=int(os.environ.get("HC_P2P_BASE","30303"))
    chain_id=int(os.environ.get("HC_CHAIN_ID","1337"))
    lane=os.environ.get("HC_LANE","1")
    accounts=[Account.create(f"hc-validator-{lane}-{i}") for i in range(7)]
    # H17 deliberately submits a conflicting transaction that must revert.
    # Give every possible confirmatory seed a disposable funded sender so a
    # reverted sender is never reused by a later seed.  These are transaction
    # actors only; they are not QBFT block validators.
    h17_accounts=[Account.create(f"hc-h17-{lane}-{i}") for i in range(64)]
    funded=accounts+h17_accounts
    alloc={a.address[2:]:{"balance":"100000000000000000000000"} for a in funded}
    (GEN/"cognitive_accounts.json").write_text(json.dumps([{"address":a.address,"private_key":a.key.hex()} for a in accounts],indent=2))
    (GEN/"h17_accounts.json").write_text(json.dumps([{"address":a.address,"private_key":a.key.hex()} for a in h17_accounts],indent=2))
    cfg={"genesis":{"config":{"chainId":chain_id,"berlinBlock":0,"qbft":{"blockperiodseconds":2,"epochlength":30000,"requesttimeoutseconds":4}},
         "nonce":"0x0","timestamp":"0x58ee40ba","gasLimit":"0x1fffffffffffff","difficulty":"0x1",
         "mixHash":"0x63746963616c2062797a616e74696e65206661756c7420746f6c6572616e6365",
         "coinbase":"0x0000000000000000000000000000000000000000","alloc":alloc},
         "blockchain":{"nodes":{"generate":True,"count":7}}}
    (GEN/"qbftConfigFile.json").write_text(json.dumps(cfg,indent=2))
    run_besu(["operator","generate-blockchain-config",f"--config-file={GEN/'qbftConfigFile.json'}",f"--to={GEN/'networkFiles'}","--private-key-file-name=key"])
    shutil.copy(GEN/"networkFiles/genesis.json",GEN/"genesis.json")
    keys=sorted((GEN/"networkFiles/keys").iterdir(),key=lambda p:p.name)
    if len(keys)!=7:raise RuntimeError(f"Expected 7 generated validators, got {len(keys)}")
    for i,k in enumerate(keys,1):
        d=GEN/f"node{i}/data";d.mkdir(parents=True)
        shutil.copy(k/"key",d/"key");shutil.copy(k/"key.pub",d/"key.pub")
    pub=(GEN/"node1/data/key.pub").read_text().strip().removeprefix("0x")
    boot=f"enode://{pub}@127.0.0.1:{p2p_base}"
    # Besu supports --bootnodes pointing at a local text file containing one
    # enode/ENR per line. This avoids Windows ambiguity when a direct enode://
    # value is parsed as a filesystem source.
    bootfile=GEN/"bootnodes.txt"
    bootfile.write_text(boot+"\n",encoding="utf-8")
    network={
        "mode":"windows-native-local-processes",
        "genesis":str((GEN/'genesis.json').resolve()),
        "bootnode":boot,
        "bootnodes_file":str(bootfile.resolve()),
        "chain_id":chain_id,"rpc_base":rpc_base,"p2p_base":p2p_base,"lane":lane,"nodes":{}
    }
    for i in range(1,8):
        network["nodes"][str(i)]={"data_path":str((GEN/f'node{i}/data').resolve()),"p2p_port":p2p_base+i-1,"rpc_port":rpc_base+i-1}
    (GEN/"native_network.json").write_text(json.dumps(network,indent=2))
    print("\nGenerated native QBFT network:",GEN)
    print("Next: python blockchain/manage_qbft.py start")

if __name__=="__main__":main()
