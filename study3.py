from pathlib import Path
from contextlib import contextmanager
import json,time,hashlib,os,subprocess
import numpy as np,pandas as pd,requests
from .common import *
from blockchain.native_control import start_nodes, stop_nodes, start_all, status as network_status

BGEN=Path(os.environ.get("HC_CHAIN_DIR", str(ROOT/"blockchain/generated"))).resolve()
RPC_BASE=int(os.environ.get("HC_RPC_BASE","8545"))
CHAIN_ID=int(os.environ.get("HC_CHAIN_ID","1337"))


def _local_pid_alive(pid):
    """Best-effort host PID check used only to recover an abandoned lock."""
    try:
        pid=int(pid)
    except Exception:
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            cp=subprocess.run(
                ["tasklist","/FI",f"PID eq {pid}","/FO","CSV","/NH"],
                capture_output=True,text=True,timeout=5
            )
            out=(cp.stdout or "").lower()
            return str(pid) in out and "no tasks are running" not in out
        except Exception:
            # Do not steal a live lock merely because tasklist was unavailable.
            return True
    try:
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return True


@contextmanager
def _cross_lane_recovery_lock(timeout=None):
    """Serialize the *whole* H18/H22 recovery phase across local QBFT lanes.

    The scientific fault windows may still execute in parallel.  Only the host-
    intensive restoration/restart phase is serialized, preventing two lanes from
    replaying RocksDB/WAL and starting validators at the same time on Windows.
    Waiting for this host-scheduling lock is intentionally excluded from the
    measured ``recovery_s`` value.
    """
    timeout=float(timeout or os.environ.get("HC_STUDY3_RECOVERY_LOCK_TIMEOUT","900"))
    lock=ROOT/"blockchain"/".study3_recovery.lock"
    deadline=time.time()+timeout
    token=f"{os.getpid()} {time.time()}"
    while True:
        try:
            fd=os.open(str(lock),os.O_CREAT|os.O_EXCL|os.O_WRONLY)
            try: os.write(fd,(token+"\n").encode("ascii","ignore"))
            finally: os.close(fd)
            break
        except FileExistsError:
            stale=False
            try:
                txt=lock.read_text(encoding="ascii",errors="ignore").strip().split()
                owner=int(txt[0]) if txt else -1
                age=time.time()-lock.stat().st_mtime
                stale=(not _local_pid_alive(owner)) or age>1800
            except Exception:
                stale=False
            if stale:
                try:
                    lock.unlink(missing_ok=True)
                    continue
                except Exception:
                    pass
            if time.time()>=deadline:
                raise RuntimeError(f"Timed out waiting for Study3 cross-lane recovery lock: {lock}")
            time.sleep(.5)
    try:
        yield
    finally:
        try:
            if lock.exists() and lock.read_text(encoding="ascii",errors="ignore").strip()==token:
                lock.unlink(missing_ok=True)
        except Exception:
            pass


@contextmanager
def _recovery_start_timeout():
    """Guarantee a generous validator-start allowance during fault recovery.

    Earlier CMD sessions may still carry HC_BESU_START_TIMEOUT=60/90.  Those
    values are adequate for a clean launch but too short when Besu must replay a
    validator WAL during H18/H22.  This override is process-local and applies
    only while recovery is running.
    """
    key="HC_BESU_START_TIMEOUT"
    old=os.environ.get(key)
    try:
        old_v=float(old) if old is not None else 0.0
    except Exception:
        old_v=0.0
    try:
        minimum=float(os.environ.get("HC_BESU_RECOVERY_START_TIMEOUT","300"))
    except Exception:
        minimum=300.0
    os.environ[key]=str(max(old_v,minimum))
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(key,None)
        else:
            os.environ[key]=old

def rpc(port,method,params=None):
    r=requests.post(f"http://127.0.0.1:{port}",json={"jsonrpc":"2.0","method":method,"params":params or [],"id":1},timeout=float(os.environ.get('HC_RPC_TIMEOUT','8')))
    r.raise_for_status();j=r.json()
    if "error" in j: raise RuntimeError(j["error"])
    return j["result"]

def load_chain():
    from web3 import Web3
    dep=json.loads((BGEN/"deployed.json").read_text()); accts=json.loads((BGEN/"cognitive_accounts.json").read_text())
    w3=Web3(Web3.HTTPProvider(f"http://127.0.0.1:{RPC_BASE}")); assert w3.is_connected(),"Native Besu node1 unavailable"
    c=w3.eth.contract(address=dep["address"],abi=dep["abi"])
    return w3,c,accts

def _raw_hex(raw):
    h=raw.hex()
    return h if h.startswith("0x") else "0x"+h


def broadcast_raw_transaction(w3, raw):
    """Submit the same signed transaction to every local validator RPC.

    This does not change Ethereum/QBFT semantics: the signed raw transaction and
    hash are identical on every node.  It removes P2P propagation as a confounder
    from H17/H21/H23/H24, whose hypotheses concern contract/provenance semantics.
    H18/H22 fault workloads deliberately keep their original node1 submission path.
    """
    hx=_raw_hex(raw); txhash=w3.keccak(raw)
    accepted=0; errors=[]
    for port in range(RPC_BASE,RPC_BASE+7):
        try:
            rpc(port,"eth_sendRawTransaction",[hx]);accepted+=1
        except Exception as e:
            msg=str(e).lower()
            # It may already have propagated or even been mined before the next
            # local RPC receives the duplicate submission.  Those are success.
            if any(k in msg for k in ["already known","known transaction","nonce too low","already imported"]):
                accepted+=1
            else:
                errors.append((port,str(e)))
    if accepted==0:
        raise RuntimeError(f"raw transaction was rejected by every validator RPC: {errors}")
    return txhash


def _wait_receipt_with_rebroadcast(w3, h, raw, timeout):
    end=time.time()+float(timeout); next_rebroadcast=time.time()+4.0
    while time.time()<end:
        try:
            rc=w3.eth.get_transaction_receipt(h)
            if rc is not None:return rc
        except Exception:
            pass
        if time.time()>=next_rebroadcast:
            try:broadcast_raw_transaction(w3,raw)
            except Exception:pass
            next_rebroadcast=time.time()+4.0
        time.sleep(.35)
    return None


def tx_call(w3,c,acct,fn,timeout=None):
    a=w3.eth.account.from_key(acct["private_key"])
    timeout=float(timeout if timeout is not None else os.environ.get("HC_TX_TIMEOUT","60"))
    nonce=w3.eth.get_transaction_count(a.address,"pending")
    gas_price=int(os.environ.get("HC_TX_GAS_PRICE","1000"))
    tx=fn.build_transaction({"from":a.address,"nonce":nonce,"gas":500000,"gasPrice":gas_price,"chainId":CHAIN_ID})
    signed=a.sign_transaction(tx);raw=signed.raw_transaction;h=broadcast_raw_transaction(w3,raw)
    start_block=w3.eth.block_number
    rc=_wait_receipt_with_rebroadcast(w3,h,raw,timeout)
    if rc is None:
        latest=w3.eth.get_transaction_count(a.address,"latest")
        pending=w3.eth.get_transaction_count(a.address,"pending")
        block=w3.eth.block_number
        raise RuntimeError(
            f"transaction {h.hex()} was not finalized within {timeout:.0f}s; "
            f"sender={a.address}, nonce={nonce}, latest_nonce={latest}, "
            f"pending_nonce={pending}, start_block={start_block}, current_block={block}, "
            f"blocks_advanced={block-start_block}. The transaction was rebroadcast to all 7 validators. "
            f"If blocks advanced while the nonce remained pending, inspect node logs for transaction-pool selection."
        )
    if rc.status != 1:raise RuntimeError(f"transaction reverted: {h.hex()}")
    return rc

def b32(s): return bytes.fromhex(hashlib.sha256(str(s).encode()).hexdigest())

def cognitive_votes(seed,n,hetero=False,malicious=0):
    rng=np.random.default_rng(seed); truth=rng.random(n)>.5; common=rng.normal(size=n);votes=[]
    for j in range(7):
        if hetero: latent=.25*common+rng.normal(size=n);err=.08+.02*(j%3)
        else: latent=.75*common+.45*rng.normal(size=n);err=.10
        flip=(rng.random(n)<err)|(latent>2.1);v=np.logical_xor(truth,flip)
        if j<malicious:v=np.ones(n,dtype=bool)
        votes.append(v)
    return truth,np.stack(votes,1)

def h17(seed,rows,real):
    if not real:raise RuntimeError("H17 requires --real-besu")
    w3,c,accts=load_chain()
    h17_path=BGEN/"h17_accounts.json"
    if not h17_path.exists():
        raise RuntimeError("H17 disposable account pool missing; regenerate this lane with v1.4.1 --fresh")
    disposable=json.loads(h17_path.read_text())
    voter=disposable[int(seed)%len(disposable)]
    owner=accts[0]
    pid=b32(f"h17-{seed}-{time.time_ns()}")

    # Activate a one-seed disposable validator, commit the valid vote, then
    # submit a *real on-chain* conflicting vote that must revert.  The sender
    # that experiences the intentional revert is never reused in another seed,
    # preventing client tx-pool state from becoming a cross-seed confounder.
    tx_call(w3,c,owner,c.functions.setValidator(voter["address"],True))
    tx_call(w3,c,owner,c.functions.submitProposal(pid,b32("s"),b32("a")))
    tx_call(w3,c,voter,c.functions.castVote(pid,True,9000,b32("e1")))
    rejected=0
    try:
        tx_call(w3,c,voter,c.functions.castVote(pid,False,9000,b32("e2")))
    except RuntimeError as e:
        # The expected contract revert is the positive H17 observation.
        if "transaction reverted" in str(e).lower():rejected=1
        else:raise
    stored=c.functions.votes(pid,voter["address"]).call()
    invariant=float(bool(stored[0]) and int(stored[1])==9000 and stored[2]==b32("e1") and bool(stored[3]))
    tx_call(w3,c,owner,c.functions.setValidator(voter["address"],False))
    rows += [
        primary_rows(3,"H17",seed,"blockchain","negative_conflicting_acceptance",-float(1-rejected),state_invariant=invariant),
        primary_rows(3,"H17",seed,"mutable_store","negative_conflicting_acceptance",-1.0,state_invariant=0.0),
    ]

def submit_ping_workload(duration):
    w3,c,accts=load_chain();a=w3.eth.account.from_key(accts[0]["private_key"]);base=w3.eth.get_transaction_count(a.address,"pending")
    count=max(6,int(duration*2));sent=[];t0=time.time()
    for k in range(count):
        tx=c.functions.ping(b32(f"ping-{t0}-{k}")).build_transaction({"from":a.address,"nonce":base+k,"gas":120000,"gasPrice":int(os.environ.get("HC_TX_GAS_PRICE","1000")),"chainId":CHAIN_ID})
        s=a.sign_transaction(tx)
        try:h=w3.eth.send_raw_transaction(s.raw_transaction);sent.append((h,time.time()))
        except Exception:pass
    deadline=t0+duration;done={};
    while time.time()<deadline:
        for h,ts in sent:
            hx=h.hex()
            if hx in done:continue
            try:
                rc=w3.eth.get_transaction_receipt(h)
                if rc and rc.status==1:done[hx]=(time.time()-ts)*1000
            except Exception:pass
        time.sleep(.2)
    lats=list(done.values());return len(done)/max(duration,1e-9),float(np.percentile(lats,95)) if lats else 999999.

def h18(seed,rows,traj,real,window):
    if not real:raise RuntimeError("H18 requires --real-besu")
    start_all()
    for k in [0,1,2,3]:
        stopped=list(range(8-k,8)) if k else []
        if stopped:stop_nodes(stopped);time.sleep(2)
        tps,p95=submit_ping_workload(window)
        traj += [{"study_id":3,"hypothesis_id":"H18","seed":seed,"series":"tps","x":k,"value":tps},{"study_id":3,"hypothesis_id":"H18","seed":seed,"series":"p95_ms","x":k,"value":p95}]
        if k in (2,3):rows.append(primary_rows(3,"H18",seed,f"unavailable_{k}","throughput_tps",tps))
        if stopped:
            # recover_qbft_after_fault() restores stopped validators itself.
            # Keeping startup in one recovery path avoids duplicate concurrent
            # node launches across parallel lanes on Windows.
            rec=recover_qbft_after_fault(
                auto_timeout=20 if window<=10 else 60,
                hard_timeout=120,
                stopped_count=len(stopped)
            )
            traj += [
                {"study_id":3,"hypothesis_id":"H18","seed":seed,
                 "series":"recovery_s","x":k,"value":rec["recovery_s"]},
                {"study_id":3,"hypothesis_id":"H18","seed":seed,
                 "series":"operator_restart_required","x":k,
                 "value":rec["operator_restart_required"]},
            ]

def h19(seed,rows,traj,n):
    for mal in [0,1,2]:
        truth,v=cognitive_votes(seed+mal,n,False,mal);fs=[]
        for q,name in [(4,"Q51"),(5,"Q2F1")]:
            dec=v.sum(1)>=q;rate=float(np.mean(dec&(~truth)));fs.append(rate);traj.append({"study_id":3,"hypothesis_id":"H19","seed":seed,"series":name,"x":mal,"value":rate})
        if mal==2:rows += [primary_rows(3,"H19",seed,"Q2F1","negative_false_safe",-fs[1]),primary_rows(3,"H19",seed,"Q51","negative_false_safe",-fs[0])]

def h20(seed,rows,traj,n):
    vals={}
    for hetero,name in [(False,"homogeneous"),(True,"heterogeneous")]:
        truth,v=cognitive_votes(seed,n,hetero,0);dec=v.sum(1)>=5;rate=float(np.mean(dec&(~truth)));vals[name]=rate
        err=(v!=truth[:,None]).astype(float);corr=np.corrcoef(err,rowvar=False);off=corr[np.triu_indices(7,1)]
        traj.append({"study_id":3,"hypothesis_id":"H20","seed":seed,"series":name,"x":"mean_error_corr","value":float(np.nanmean(off))})
    rows += [primary_rows(3,"H20",seed,"heterogeneous","negative_joint_false_safe",-vals["heterogeneous"]),primary_rows(3,"H20",seed,"homogeneous","negative_joint_false_safe",-vals["homogeneous"])]

def h21(seed,rows,real):
    if not real:raise RuntimeError("H21 requires --real-besu")
    w3,c,accts=load_chain();pid=b32(f"h21-{seed}-{time.time_ns()}");state=b32("state");action=b32("action")
    tx_call(w3,c,accts[0],c.functions.submitProposal(pid,state,action));on=c.functions.proposals(pid).call();tampered=[b32("changed"),action,True]
    detected=float(on[0]!=tampered[0] or on[1]!=tampered[1]);rows += [primary_rows(3,"H21",seed,"blockchain","tamper_detection",detected),primary_rows(3,"H21",seed,"mutable_log","tamper_detection",0.0)]

def block_number(port=None):return int(rpc(RPC_BASE if port is None else port,"eth_blockNumber"),16)


def ensure_qbft_live(timeout=120):
    """Require all 7 local validators to be reachable and prove block production."""
    start_all()
    st=network_status()
    bad=[x for x in st if (not x.get("running")) or x.get("rpc_error")]
    if bad:
        raise RuntimeError(f"QBFT health gate failed: unavailable validators: {bad}")

    vals=rpc(RPC_BASE,"qbft_getValidatorsByBlockNumber",["latest"])
    if len(vals)!=7:
        raise RuntimeError(f"QBFT health gate expected 7 validators, got {len(vals)}: {vals}")

    b0=block_number()
    end=time.time()+timeout
    while time.time()<end:
        time.sleep(2)
        b1=block_number()
        if b1>b0:
            return {"start_block":b0,"end_block":b1,"validators":len(vals)}
    raise RuntimeError(
        f"QBFT RPC is reachable but the chain did not produce a new block "
        f"within {timeout}s (block stayed at {b0}). Restore quorum before H24."
    )



def wait_block_advance(timeout=30, port=None):
    port=RPC_BASE if port is None else port
    b0=block_number(port)
    end=time.time()+timeout
    while time.time()<end:
        time.sleep(2)
        b1=block_number(port)
        if b1>b0:
            return b0,b1
    return b0,b0


def recover_qbft_after_fault(auto_timeout=30, hard_timeout=120, stopped_count=0):
    """Restore validators and prove real post-fault block production.

    Recovery is serialized across the two local lanes because simultaneous Besu
    WAL replay/JVM startup on the same Windows host is an execution artifact,
    not part of the QBFT hypothesis.  The lock wait is excluded from recovery_s.

    For a 3-of-7 outage (>1/3 unavailable), go directly to a coordinated restart
    of all seven validators.  Besu documents this as the quickest way to reset an
    escalated QBFT request timeout after loss of more than one third of validators.
    This avoids the old wasteful sequence: start 5/6/7, wait, then stop/restart
    those same validators again.
    """
    with _cross_lane_recovery_lock():
        with _recovery_start_timeout():
            t0=time.time()

            if int(stopped_count) >= 3:
                stop_nodes(range(1,8))
                time.sleep(2)
                start_all()
                health=ensure_qbft_live(timeout=hard_timeout)
                return {
                    "operator_restart_required":1.0,
                    "recovery_s":time.time()-t0,
                    "start_block":health["start_block"],
                    "end_block":health["end_block"],
                }

            # At 1/7 or 2/7 unavailable, quorum remains possible. Restore only
            # the stopped validator(s) first and accept automatic recovery if a
            # new block is produced.
            start_all()
            b0,b1=wait_block_advance(auto_timeout)
            if b1>b0:
                return {
                    "operator_restart_required":0.0,
                    "recovery_s":time.time()-t0,
                    "start_block":b0,
                    "end_block":b1,
                }

            # RPCs can all be alive while QBFT remains in an escalated round.
            stop_nodes(range(1,8))
            time.sleep(2)
            start_all()
            health=ensure_qbft_live(timeout=hard_timeout)
            return {
                "operator_restart_required":1.0,
                "recovery_s":time.time()-t0,
                "start_block":health["start_block"],
                "end_block":health["end_block"],
            }


def wait_convergence(timeout=30):
    end=time.time()+timeout
    while time.time()<end:
        vals=[]
        for port in range(RPC_BASE,RPC_BASE+7):
            try:vals.append(block_number(port))
            except Exception:vals.append(None)
        ok=[v for v in vals if v is not None]
        if len(ok)==7 and max(ok)-min(ok)<=1:return vals
        time.sleep(1)
    return vals

def h22(seed,rows,traj,real,durations):
    if not real:raise RuntimeError("H22 requires --real-besu")
    # Windows-native fault injection. We emulate peer isolation by stopping the isolated group.
    # This tests quorum liveness and post-isolation convergence, but is not labeled a packet-level partition.
    patterns=[("5|2",[6,7]),("4|3",[5,6,7])]
    conflicts=[]
    for pattern,isolated in patterns:
        for dur in durations:
            start_all();time.sleep(2);start_bn=block_number();stop_nodes(isolated);time.sleep(dur);end_bn=block_number();advance=end_bn-start_bn
            # Recovery restores the isolated validators; do not pre-start
            # them here because that duplicates the startup/recovery path.
            rec=recover_qbft_after_fault(
                auto_timeout=20 if max(durations)<=10 else 60,
                hard_timeout=120,
                stopped_count=len(isolated)
            )
            vals=wait_convergence(timeout=30)
            if any(v is None for v in vals):
                raise RuntimeError(f"H22 recovery failed: not all validators reachable: {vals}")
            common=min(vals);hashes=[]
            for port in range(RPC_BASE,RPC_BASE+7):
                try:hashes.append(rpc(port,"eth_getBlockByNumber",[hex(common),False])["hash"])
                except Exception:pass
            if len(hashes)!=7:
                raise RuntimeError(f"H22 recovery failed: could not read common block {common} from all 7 validators")
            conflict=float(len(set(hashes))>1);conflicts.append(conflict)
            traj += [
                {"study_id":3,"hypothesis_id":"H22","seed":seed,"series":f"{pattern}_block_advance","x":dur,"value":advance},
                {"study_id":3,"hypothesis_id":"H22","seed":seed,"series":f"{pattern}_recovery_s","x":dur,"value":rec["recovery_s"]},
                {"study_id":3,"hypothesis_id":"H22","seed":seed,"series":f"{pattern}_operator_restart_required","x":dur,"value":rec["operator_restart_required"]},
                {"study_id":3,"hypothesis_id":"H22","seed":seed,"series":"conflict","x":dur,"value":conflict},
            ]
    rows.append(primary_rows(3,"H22",seed,"QBFT","negative_conflicting_finality",-max(conflicts,default=0)))

def h23(seed,rows,real):
    if not real:raise RuntimeError("H23 requires --real-besu")
    w3,c,accts=load_chain();rng=np.random.default_rng(seed);a=w3.eth.account.from_key(accts[0]["private_key"]);n=50;base=w3.eth.get_transaction_count(a.address,"pending");items=[]
    # Submit all 50 independent audit transactions with consecutive nonces, then wait collectively.
    for i in range(n):
        pid=b32(f"h23-{seed}-{i}-{time.time_ns()}");s=b32(f"s{i}");act=b32(f"a{i}");gas_price=int(os.environ.get("HC_TX_GAS_PRICE","1000"));tx=c.functions.submitProposal(pid,s,act).build_transaction({"from":a.address,"nonce":base+i,"gas":300000,"gasPrice":gas_price,"chainId":CHAIN_ID});signed=a.sign_transaction(tx);h=broadcast_raw_transaction(w3,signed.raw_transaction);items.append((pid,s,act,h))
    deadline=time.time()+float(os.environ.get("HC_H23_TX_TIMEOUT","180"));pending={h.hex():h for *_,h in items}
    while pending and time.time()<deadline:
        for hx,h in list(pending.items()):
            try:
                rc=w3.eth.get_transaction_receipt(h)
                if rc and rc.status==1:pending.pop(hx,None)
                elif rc and rc.status!=1:raise RuntimeError(f"H23 transaction reverted: {hx}")
            except Exception as e:
                if "not found" not in str(e).lower():pass
        if pending:time.sleep(.25)
    if pending:raise RuntimeError(f"H23 {len(pending)}/{n} audit transactions not finalized before HC_H23_TX_TIMEOUT")
    correct_chain=correct_log=0
    for pid,s,act,h in items:
        stored=c.functions.proposals(pid).call();tamper=rng.random()<.2;local_s=b32("tampered") if tamper else s;correct_chain+=int(stored[0]==s and stored[1]==act);correct_log+=int(local_s==s)
    rows += [primary_rows(3,"H23",seed,"blockchain","audit_reconstruction",correct_chain/n),primary_rows(3,"H23",seed,"ordinary_log","audit_reconstruction",correct_log/n)]

def h24(seed,rows,traj,n,real):
    rng=np.random.default_rng(seed);truth,v=cognitive_votes(seed,n,True,0);faulty=[0,1]
    for j in faulty:
        wrong=rng.random(n)<.70;v[:,j]=np.where(wrong,~truth,v[:,j])
    active=np.ones(7,bool);strikes=np.zeros(7,int);rolling=[];removal_at=None;false=[]
    for i in range(n):
        dec=v[i,active].sum()>=max(1,int(np.ceil(active.sum()*2/3)));false.append(bool(dec and not truth[i]))
        for j in np.where(active)[0]:
            if v[i,j]!=truth[i]:strikes[j]+=1
            if strikes[j]>=20 and active.sum()>4:active[j]=False;removal_at=i if removal_at is None else removal_at
        if i%50==0:rolling.append((i,np.mean(false[-200:])))
    governed=float(np.mean(false[n//2:]));truth2,v2=cognitive_votes(seed,n,True,0)
    for j in faulty:
        wrong=rng.random(n)<.70;v2[:,j]=np.where(wrong,~truth2,v2[:,j])
    fixed=float(np.mean((v2.sum(1)>=5)&(~truth2)));rows += [primary_rows(3,"H24",seed,"governance","negative_false_safe",-governed),primary_rows(3,"H24",seed,"frozen","negative_false_safe",-fixed)]
    for x,y in rolling:traj.append({"study_id":3,"hypothesis_id":"H24","seed":seed,"series":"rolling_false_safe","x":x,"value":y})
    if real and removal_at is not None:
        health=ensure_qbft_live(timeout=120)
        w3,c,accts=load_chain();owner=accts[0]
        receipts=[]
        for j in faulty:
            receipts.append(
                tx_call(w3,c,owner,c.functions.setValidator(accts[j]["address"],False),timeout=120)
            )
        # The contract-level cognitive validator status must actually be committed.
        for j in faulty:
            if c.functions.activeValidator(accts[j]["address"]).call():
                raise RuntimeError(
                    f"H24 governance record verification failed for validator {j}"
                )
        traj.append({
            "study_id":3,"hypothesis_id":"H24","seed":seed,
            "series":"onchain_governance_recorded","x":removal_at,"value":1.0
        })

def _checkpoint_dir(mode):
    p=RESULTS/"checkpoints"/"study3"/mode;p.mkdir(parents=True,exist_ok=True);return p
def _done(mode,h,seed):return (_checkpoint_dir(mode)/f"{h}_{seed}.done").exists()
def _mark(mode,h,seed):(_checkpoint_dir(mode)/f"{h}_{seed}.done").write_text("ok\n")

def run(mode="smoke",hypotheses=None,real_besu=False):
    hs=hypotheses or [f"H{i}" for i in range(17,25)];cfg=load_config()["study3"];n=cfg["proposals_smoke"] if mode=="smoke" else cfg["proposals_confirmatory"];window=cfg["network_window_smoke_s"] if mode=="smoke" else cfg["network_window_confirmatory_s"];durations=cfg["partition_durations_smoke_s"] if mode=="smoke" else cfg["partition_durations_confirmatory_s"]
    seeds=[int(x) for x in os.environ.get("HC_SEEDS","").split(",") if x.strip()] or seeds_for(3,mode)
    for h in hs:
        for idx,seed in enumerate(seeds,1):
            if _done(mode,h,seed):continue
            seed_all(seed);rows=[];traj=[];print(f"[Study3 {h}] {idx}/{len(seeds)} seed {seed}: START lane={os.environ.get('HC_LANE','1')}",flush=True)
            if h=="H17":h17(seed,rows,real_besu)
            elif h=="H18":h18(seed,rows,traj,real_besu,window)
            elif h=="H19":h19(seed,rows,traj,n)
            elif h=="H20":h20(seed,rows,traj,n)
            elif h=="H21":h21(seed,rows,real_besu)
            elif h=="H22":h22(seed,rows,traj,real_besu,durations)
            elif h=="H23":h23(seed,rows,real_besu)
            elif h=="H24":h24(seed,rows,traj,n,real_besu)
            if rows:append_csv(RESULTS/"primary_seed_metrics.csv",rows)
            if traj:append_csv(RESULTS/"study3_trajectories.csv",traj)
            _mark(mode,h,seed);print(f"[Study3 {h}] seed {seed}: SAVED",flush=True)
    return []
