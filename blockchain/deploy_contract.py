from pathlib import Path
import json, os
from web3 import Web3
from web3.exceptions import TimeExhausted
from solcx import compile_source, install_solc, set_solc_version

HERE=Path(__file__).resolve().parent
GEN=Path(os.environ.get("HC_CHAIN_DIR", str(HERE/"generated"))).resolve()
RPC_BASE=int(os.environ.get("HC_RPC_BASE","8545"))
CHAIN_ID=int(os.environ.get("HC_CHAIN_ID","1337"))
RPC=f"http://127.0.0.1:{RPC_BASE}"

def main():
    accts=json.loads((GEN/"cognitive_accounts.json").read_text())
    w3=Web3(Web3.HTTPProvider(RPC)); assert w3.is_connected(),"Besu RPC unavailable; run manage_qbft.py start"
    install_solc("0.8.24");set_solc_version("0.8.24")
    src=(HERE/"SafetyConsensus.sol").read_text()
    # The test genesis activates Berlin; compile compatible bytecode (no Shanghai PUSH0).
    comp=compile_source(src,output_values=["abi","bin"],evm_version="berlin"); _,art=next(iter(comp.items()))
    sender=accts[0]; account=w3.eth.account.from_key(sender["private_key"])
    Contract=w3.eth.contract(abi=art["abi"],bytecode=art["bin"])
    tx=Contract.constructor([a["address"] for a in accts]).build_transaction({
        "from":account.address,"nonce":w3.eth.get_transaction_count(account.address,"pending"),
        "gas":5_000_000,"gasPrice":int(os.environ.get("HC_TX_GAS_PRICE","1000")),"chainId":CHAIN_ID})
    signed=account.sign_transaction(tx);h=w3.eth.send_raw_transaction(signed.raw_transaction)
    timeout=float(os.environ.get("HC_DEPLOY_TIMEOUT","180"))
    try:
        rc=w3.eth.wait_for_transaction_receipt(h,timeout=timeout)
    except TimeExhausted as e:
        latest=w3.eth.get_transaction_count(account.address,"latest")
        pending=w3.eth.get_transaction_count(account.address,"pending")
        block=w3.eth.block_number
        raise RuntimeError(
            f"SafetyConsensus deployment {h.hex()} was submitted but not finalized "
            f"within {timeout:.0f}s; current_block={block}, latest_nonce={latest}, "
            f"pending_nonce={pending}. The runner now performs a consensus-health "
            f"gate before deployment; inspect blockchain/generated_lane*/node*/node.log "
            f"if this still occurs."
        ) from e
    if rc.status != 1:raise RuntimeError("SafetyConsensus deployment reverted")
    out={"address":rc.contractAddress,"abi":art["abi"],"deployer":account.address,"tx_hash":h.hex(),"evm_version":"berlin"}
    (GEN/"deployed.json").write_text(json.dumps(out,indent=2))
    print("Contract:",rc.contractAddress)

if __name__=="__main__":main()
