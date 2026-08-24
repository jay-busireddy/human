from pathlib import Path
import hashlib, json, os, time, requests
from web3 import Web3

HERE=Path(__file__).resolve().parent
GEN=Path(os.environ.get('HC_CHAIN_DIR', str(HERE/'generated'))).resolve()
RPC_BASE=int(os.environ.get('HC_RPC_BASE','8545'))
CHAIN_ID=int(os.environ.get('HC_CHAIN_ID','1337'))
# 45 s was too short after repeated QBFT fault/recovery cycles on Windows.
# This is only an infrastructure readiness gate, not a measured hypothesis window.
ATTEMPT_TIMEOUT=max(float(os.environ.get('HC_TX_CANARY_TIMEOUT','45')),120.0)
GAS_PRICE=int(os.environ.get('HC_TX_GAS_PRICE','1000'))
REBROADCAST_S=max(2.0,float(os.environ.get('HC_TX_CANARY_REBROADCAST_S','5')))
MAX_SENDERS=max(2,int(os.environ.get('HC_TX_CANARY_SENDERS','3')))


def rpc(port,method,params=None,timeout=None):
    t=float(timeout if timeout is not None else os.environ.get('HC_RPC_TIMEOUT','8'))
    r=requests.post(
        f'http://127.0.0.1:{port}',
        json={'jsonrpc':'2.0','method':method,'params':params or [],'id':1},
        timeout=t,
    )
    r.raise_for_status();j=r.json()
    if 'error' in j:raise RuntimeError(j['error'])
    return j['result']


def raw_hex(raw):
    h=raw.hex();return h if h.startswith('0x') else '0x'+h


def broadcast(hx):
    accepted=0;errors=[]
    for port in range(RPC_BASE,RPC_BASE+7):
        try:
            rpc(port,'eth_sendRawTransaction',[hx]);accepted+=1
        except Exception as e:
            msg=str(e).lower()
            if any(k in msg for k in ['already known','known transaction','nonce too low','already imported']):
                accepted+=1
            else:
                errors.append((port,str(e)))
    return accepted,errors


def find_receipt(txhash_hex):
    # Query every validator.  Immediately after recovery one RPC can lag even
    # though another already has the finalized block.
    for port in range(RPC_BASE,RPC_BASE+7):
        try:
            rc=rpc(port,'eth_getTransactionReceipt',[txhash_hex],timeout=max(8.0,float(os.environ.get('HC_RPC_TIMEOUT','8'))))
            if rc:
                return rc,port
        except Exception:
            pass
    return None,None


def account_nonce_state(w3,entry):
    acct=w3.eth.account.from_key(entry['private_key'])
    latest=w3.eth.get_transaction_count(acct.address,'latest')
    pending=w3.eth.get_transaction_count(acct.address,'pending')
    return acct,latest,pending


def candidate_accounts(w3):
    """Prefer funded disposable H17 actors with no pending transaction.

    The old canary always reused cognitive account #7.  After many resume/fault
    cycles, that one sender can have client-specific tx-pool history.  The
    canary's purpose is to prove that the lane can finalize *a valid
    transaction*, not to test one particular sender, so use a clean funded
    actor and fall back to another clean actor if needed.
    """
    pools=[]
    hp=GEN/'h17_accounts.json'
    if hp.exists():
        try:pools.extend(json.loads(hp.read_text()))
        except Exception:pass
    cp=GEN/'cognitive_accounts.json'
    if cp.exists():
        try:pools.extend(json.loads(cp.read_text()))
        except Exception:pass

    clean=[];dirty=[]
    for idx,entry in enumerate(pools):
        try:
            acct,latest,pending=account_nonce_state(w3,entry)
            row=(latest,idx,entry,acct,latest,pending)
            if pending==latest:clean.append(row)
            else:dirty.append(row)
        except Exception:
            continue
    # Lowest confirmed nonce first => normally an unused disposable account.
    clean.sort(key=lambda x:(x[0],x[1]))
    dirty.sort(key=lambda x:(x[0],x[1]))
    return clean+dirty


def run_attempt(w3,c,row,attempt):
    _,idx,entry,acct,latest,pending=row
    if pending!=latest:
        raise RuntimeError(
            f'candidate sender {acct.address} has a pre-existing pending nonce '
            f'(latest={latest}, pending={pending})'
        )
    nonce=latest
    tag=bytes.fromhex(hashlib.sha256(f'canary-{attempt}-{time.time_ns()}'.encode()).hexdigest())
    tx=c.functions.ping(tag).build_transaction({
        'from':acct.address,'nonce':nonce,'gas':120000,
        'gasPrice':GAS_PRICE,'chainId':CHAIN_ID,
    })
    signed=acct.sign_transaction(tx);raw=signed.raw_transaction;hx=raw_hex(raw);h=w3.keccak(raw)
    txhex=h.hex() if h.hex().startswith('0x') else '0x'+h.hex()
    accepted,errors=broadcast(hx)
    if accepted==0:
        raise RuntimeError(f'TX_CANARY sender={acct.address} rejected by all validators: {errors}')

    b0=w3.eth.block_number
    end=time.time()+ATTEMPT_TIMEOUT
    next_rebroadcast=time.time()+REBROADCAST_S
    while time.time()<end:
        rc,receipt_port=find_receipt(txhex)
        if rc is not None:
            status=int(rc.get('status','0x0'),16) if isinstance(rc.get('status'),str) else int(rc.get('status',0))
            if status!=1:
                raise RuntimeError(f'TX_CANARY reverted: {txhex}')
            block=int(rc['blockNumber'],16) if isinstance(rc.get('blockNumber'),str) else int(rc['blockNumber'])
            print(
                f'TX_CANARY PASS hash={txhex} block={block} broadcasts={accepted}/7 '
                f'start_block={b0} receipt_rpc={receipt_port} sender={acct.address} attempt={attempt}',
                flush=True,
            )
            return True
        if time.time()>=next_rebroadcast:
            broadcast(hx)
            next_rebroadcast=time.time()+REBROADCAST_S
        time.sleep(.5)

    latest2=w3.eth.get_transaction_count(acct.address,'latest')
    pending2=w3.eth.get_transaction_count(acct.address,'pending')
    b1=w3.eth.block_number
    print(
        f'TX_CANARY sender attempt {attempt} remained pending after {ATTEMPT_TIMEOUT:.0f}s: '
        f'hash={txhex} sender={acct.address} nonce={nonce} latest_nonce={latest2} '
        f'pending_nonce={pending2} start_block={b0} current_block={b1} '
        f'blocks_advanced={b1-b0}; trying another clean funded sender.',
        flush=True,
    )
    return False


def main():
    dep=json.loads((GEN/'deployed.json').read_text())
    w3=Web3(Web3.HTTPProvider(
        f'http://127.0.0.1:{RPC_BASE}',
        request_kwargs={'timeout':max(15.0,float(os.environ.get('HC_RPC_TIMEOUT','8')))},
    ))
    if not w3.is_connected():raise RuntimeError('node1 RPC unavailable for tx canary')
    c=w3.eth.contract(address=dep['address'],abi=dep['abi'])

    candidates=candidate_accounts(w3)
    if not candidates:
        raise RuntimeError('TX_CANARY found no funded account whose nonce state could be read')

    attempted=0;problems=[]
    for row in candidates:
        if attempted>=MAX_SENDERS:break
        _,_,_,acct,latest,pending=row
        if pending!=latest:
            continue
        attempted+=1
        try:
            if run_attempt(w3,c,row,attempted):return
        except Exception as e:
            problems.append(f'{acct.address}: {e}')
            print(f'TX_CANARY sender attempt {attempted} failed: {e}',flush=True)

    raise RuntimeError(
        f'TX_CANARY could not finalize a valid transaction using {attempted} clean funded sender(s). '
        f'Per-attempt timeout={ATTEMPT_TIMEOUT:.0f}s. Details: {problems}'
    )


if __name__=='__main__':main()
