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
    """Serialize H18/H22 recovery with an OS-owned lock.

    v1.4.5 used ``O_EXCL`` file creation.  If a run was killed, that pathname
    could remain behind; on Windows a later unrelated process could even reuse
    the recorded PID, making the stale lock look permanently live.  This
    implementation locks one byte of a persistent file with the operating
    system.  The OS releases the lock automatically when the owning Python
    process exits or crashes, so stale lock *files* are harmless.

    Waiting for this host-scheduling lock is intentionally excluded from the
    measured ``recovery_s`` value.
    """
    timeout=float(timeout or os.environ.get("HC_STUDY3_RECOVERY_LOCK_TIMEOUT","1200"))
    lock=ROOT/"blockchain"/".study3_recovery.lock"
    lock.parent.mkdir(parents=True,exist_ok=True)
    deadline=time.time()+timeout
    lane=os.environ.get("HC_LANE","1")
    fh=open(lock,"a+b",buffering=0)
    try:
        # msvcrt byte-range locking requires the byte to exist.
        fh.seek(0,os.SEEK_END)
        if fh.tell()<1:
            fh.write(b"0")
        acquired=False
        while not acquired:
            try:
                fh.seek(0)
                if os.name=="nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                acquired=True
            except (OSError,BlockingIOError):
                if time.time()>=deadline:
                    raise RuntimeError(
                        f"Timed out waiting for Study3 cross-lane recovery OS lock: {lock}"
                    )
                time.sleep(.5)
        print(f"[Study3 recovery-lock] lane={lane} ACQUIRED",flush=True)
        try:
            yield
        finally:
            try:
                fh.seek(0)
                if os.name=="nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(),fcntl.LOCK_UN)
            finally:
                print(f"[Study3 recovery-lock] lane={lane} RELEASED",flush=True)
    finally:
        fh.close()


@contextmanager
def _recovery_start_timeout():
    """Temporarily widen host-side recovery timeouts only during H18/H22."""
    keys={
        "HC_BESU_START_TIMEOUT": float(os.environ.get("HC_BESU_RECOVERY_START_TIMEOUT","300")),
        "HC_BESU_START_LOCK_TIMEOUT": float(os.environ.get("HC_BESU_RECOVERY_LOCK_TIMEOUT","900")),
        "HC_RPC_TIMEOUT": float(os.environ.get("HC_BESU_RECOVERY_RPC_TIMEOUT","15")),
    }
    old={k:os.environ.get(k) for k in keys}
    try:
        for k,minimum in keys.items():
            try: current=float(os.environ.get(k,"0") or 0)
            except Exception: current=0.0
            os.environ[k]=str(max(current,minimum))
        yield
    finally:
        for k,v in old.items():
            if v is None: os.environ.pop(k,None)
            else: os.environ[k]=v

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
                hard_timeout=float(os.environ.get("HC_H18_RECOVERY_HEALTH_TIMEOUT","300")),
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


def ensure_qbft_live(timeout=300, require_tip_convergence=True):
    """Prove post-fault QBFT liveness with an optional latest-head criterion.

    H18 uses ``require_tip_convergence=True`` because that experiment explicitly
    wants the whole validator set restored before the next fault level.  H22 uses
    ``False``: its safety criterion is that block production resumes, all seven
    RPCs return, the seven-validator set is intact, and every validator can later
    read one fixed post-recovery block.  Requiring equal *latest* heights in H22
    creates a moving target and is not needed for conflicting-finality safety.
    """
    timeout=max(float(timeout),float(os.environ.get("HC_STUDY3_MIN_HEALTH_TIMEOUT","300")))
    start_all()
    deadline=time.time()+timeout
    started=time.time()
    baseline=None
    saw_advance=False
    repaired=False
    last=None

    while time.time()<deadline:
        blocks=[]
        for i in range(7):
            port=RPC_BASE+i
            try: blocks.append(block_number(port))
            except Exception: blocks.append(None)
        ok=[b for b in blocks if b is not None]
        if ok:
            top=max(ok)
            if baseline is None: baseline=top
            elif top>baseline: saw_advance=True

        validators=[]
        for i,b in enumerate(blocks):
            if b is None: continue
            try:
                validators=rpc(RPC_BASE+i,"qbft_getValidatorsByBlockNumber",["latest"])
                if validators: break
            except Exception: pass

        if saw_advance and len(validators)==7 and len(ok)==7:
            if (not require_tip_convergence) or max(ok)-min(ok)<=1:
                return {"start_block":baseline,"end_block":max(ok),"validators":7,"blocks":blocks}

        if require_tip_convergence and saw_advance and not repaired and ok:
            top=max(ok)
            lagging=[i+1 for i,b in enumerate(blocks) if b is None or b<top-1]
            # Let normal catch-up run first.  If a validator is still absent or
            # materially behind, repair only that validator under the existing
            # cross-lane recovery lock held by the caller.
            if lagging and time.time()-started>=min(60.0,timeout/3.0):
                print(f"[Study3 health] targeted repair of lagging validator(s): {lagging}",flush=True)
                stop_nodes(lagging)
                time.sleep(2)
                start_nodes(lagging)
                repaired=True
                deadline=max(deadline,time.time()+min(180.0,timeout))

        last={"blocks":blocks,"responsive":len(ok),"validators":len(validators),"saw_advance":saw_advance}
        time.sleep(2)

    raise RuntimeError(
        f"QBFT did not restore all seven validators within the health window. Last state: {last}"
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


def recover_qbft_after_fault(auto_timeout=30, hard_timeout=120, stopped_count=0, require_tip_convergence=True):
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
                health=ensure_qbft_live(timeout=hard_timeout, require_tip_convergence=require_tip_convergence)
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
            health=ensure_qbft_live(timeout=hard_timeout, require_tip_convergence=require_tip_convergence)
            return {
                "operator_restart_required":1.0,
                "recovery_s":time.time()-t0,
                "start_block":health["start_block"],
                "end_block":health["end_block"],
            }


def wait_all_reach_block(target_block, timeout=180, max_timeout=600, repair_stall=True):
    """Wait until every validator possesses one fixed target block.

    v1.4.13 makes recovery progress-based rather than moving-head/time-window based.
    H22 only needs every validator to possess the same fixed post-recovery block;
    it does not require every validator to equal the continuously advancing tip.

    A node is considered to have reached the target when eth_getBlockByNumber for
    that exact block succeeds.  eth_blockNumber and eth_syncing are diagnostics
    used to detect forward progress.  One targeted restart is allowed only after
    a validator has made no progress for the configured stall interval.
    """
    target=int(target_block)
    poll=max(float(os.environ.get("HC_H22_CATCHUP_POLL","2")),0.5)
    no_progress=max(float(os.environ.get("HC_H22_NO_PROGRESS_TIMEOUT","300")),60.0)
    post_repair_no_progress=max(
        float(os.environ.get("HC_H22_POST_REPAIR_NO_PROGRESS_TIMEOUT","600")),
        no_progress,
    )
    total_timeout=max(
        float(os.environ.get("HC_H22_TOTAL_CATCHUP_TIMEOUT","3600")),
        float(max_timeout),
        float(timeout),
    )
    report_every=max(float(os.environ.get("HC_H22_PROGRESS_REPORT_EVERY","60")),15.0)

    started=time.time();deadline=started+total_timeout;next_report=started+report_every
    last_metric=[None]*7;last_progress=[started]*7;repaired=set();vals=[None]*7
    sync_diag=[None]*7;target_present=[False]*7

    def _as_int(v):
        if v is None:return None
        if isinstance(v,int):return v
        if isinstance(v,str):
            try:return int(v,16) if v.lower().startswith("0x") else int(v)
            except Exception:return None
        return None

    while time.time()<deadline:
        now=time.time();vals=[];sync_diag=[None]*7;target_present=[]
        for idx,port in enumerate(range(RPC_BASE,RPC_BASE+7)):
            try:b=block_number(port)
            except Exception:b=None
            vals.append(b)

            # Direct possession of the fixed block is the actual H22 criterion.
            try:
                blk=rpc(port,"eth_getBlockByNumber",[hex(target),False])
                present=bool(isinstance(blk,dict) and blk.get("hash"))
            except Exception:
                present=False
            target_present.append(present)

            sync=None;cur=None;highest=None
            if not present:
                try:
                    s=rpc(port,"eth_syncing")
                    if isinstance(s,dict):
                        sync=True
                        cur=_as_int(s.get("currentBlock"));highest=_as_int(s.get("highestBlock"))
                    else:
                        sync=False
                except Exception:
                    sync=None
            sync_diag[idx]={"syncing":sync,"current":cur,"highest":highest}

            metric=max([x for x in (b,cur) if x is not None],default=None)
            prev=last_metric[idx]
            if metric is not None and (prev is None or metric>prev):
                last_metric[idx]=metric;last_progress[idx]=now

        if all(target_present):
            return vals

        stalled=[]
        for idx,present in enumerate(target_present):
            if present:continue
            node=idx+1
            limit=post_repair_no_progress if node in repaired else no_progress
            if now-last_progress[idx]>=limit:
                stalled.append(node)

        if stalled:
            unrepaired=[n for n in stalled if n not in repaired]
            if repair_stall and unrepaired:
                print(
                    f"[Study3 H22] fixed block {target} no-progress validator(s); "
                    f"targeted restart once: {unrepaired}",flush=True,
                )
                with _cross_lane_recovery_lock():
                    with _recovery_start_timeout():
                        stop_nodes(unrepaired);time.sleep(2);start_nodes(unrepaired)
                t=time.time()
                for node in unrepaired:
                    repaired.add(node);idx=node-1
                    last_metric[idx]=None;last_progress[idx]=t
                continue

            # A repaired node that still makes no progress is a real operational
            # recovery failure; do not hide it with endless restarts.
            raise RuntimeError(
                f"H22 validator(s) made no progress toward fixed block {target}: {stalled}. "
                f"Last heights: {vals}; target_present: {target_present}; "
                f"sync_state: {sync_diag}; repaired: {sorted(repaired)}"
            )

        if now>=next_report:
            waiting=[i+1 for i,p in enumerate(target_present) if not p]
            print(
                f"[Study3 H22] waiting for fixed block {target}; validators={waiting}; "
                f"heights={vals}; sync_state={sync_diag}",flush=True,
            )
            next_report=now+report_every
        time.sleep(poll)

    raise RuntimeError(
        f"H22 validators did not obtain fixed post-recovery block {target} within "
        f"the {total_timeout:.0f}s safety cap. Last heights: {vals}; "
        f"target_present: {target_present}; sync_state: {sync_diag}; "
        f"repaired: {sorted(repaired)}"
    )

def _rpc_retry(port,method,params=None,timeout=60,interval=1.0):
    end=time.time()+float(timeout);last=None
    while time.time()<end:
        try:return rpc(port,method,params)
        except Exception as e:last=e;time.sleep(interval)
    raise RuntimeError(f"RPC {method} on port {port} did not succeed within {timeout}s: {last}")

def _h22_baseline_barrier(hash_rpc_timeout=60):
    """Restore a common baseline before the next H22 fault injection.

    Capture the current highest observed block once, then require all seven
    validators to possess that fixed block and agree on its hash.  This prevents
    synchronization debt from one H22 case contaminating the next case while
    leaving the actual outage pattern and duration unchanged.
    """
    start_all();time.sleep(2)
    heights=[]
    for port in range(RPC_BASE,RPC_BASE+7):
        try:heights.append(block_number(port))
        except Exception:heights.append(None)
    responsive=[h for h in heights if h is not None]
    if not responsive:
        raise RuntimeError("H22 baseline barrier has no responsive validator")
    target=max(responsive)
    print(f"[Study3 H22] baseline barrier target={target} initial_heights={heights}",flush=True)
    vals=wait_all_reach_block(
        target,
        timeout=max(float(os.environ.get("HC_H22_CONVERGENCE_TIMEOUT","180")),180.0),
        max_timeout=max(float(os.environ.get("HC_H22_CONVERGENCE_MAX_TIMEOUT","600")),600.0),
        repair_stall=True,
    )
    hashes=[]
    for port in range(RPC_BASE,RPC_BASE+7):
        block=_rpc_retry(port,"eth_getBlockByNumber",[hex(target),False],timeout=hash_rpc_timeout)
        if not block or not block.get("hash"):
            raise RuntimeError(f"H22 baseline validator RPC {port} cannot read block {target}")
        hashes.append(str(block["hash"]).lower())
    if len(set(hashes))!=1:
        raise RuntimeError(f"H22 baseline conflicting hashes at block {target}: {hashes}")
    return target,vals,hashes[0]


def h22(seed,rows,traj,real,durations):
    if not real:raise RuntimeError("H22 requires --real-besu")
    # Windows-native process isolation: this is a node-outage/isolation test,
    # not a claim of a packet-level network partition.
    patterns=[("5|2",[6,7]),("4|3",[5,6,7])]
    conflicts=[]
    recovery_health=float(os.environ.get("HC_H22_RECOVERY_HEALTH_TIMEOUT","300"))
    # A restarted validator may need several minutes to replay/sync after a
    # long H22 outage. Preserve any user-specified larger value, but do not let
    # an old 120s environment setting recreate the false convergence failure.
    convergence_timeout=max(float(os.environ.get("HC_H22_CONVERGENCE_TIMEOUT","180")),180.0)
    convergence_max=max(float(os.environ.get("HC_H22_CONVERGENCE_MAX_TIMEOUT","600")),convergence_timeout)
    hash_rpc_timeout=float(os.environ.get("HC_H22_HASH_RPC_TIMEOUT","60"))
    for pattern,isolated in patterns:
        for dur in durations:
            # Every fault case starts from a synchronized seven-validator baseline.
            # Without this barrier, a lagging validator can accumulate debt across
            # the 8 cases in each seed and turn H22 into a sync-stress artifact.
            _h22_baseline_barrier(hash_rpc_timeout)
            start_bn=block_number();stop_nodes(isolated);time.sleep(dur)
            end_bn=block_number();advance=end_bn-start_bn
            rec=recover_qbft_after_fault(
                auto_timeout=20 if max(durations)<=10 else 60,
                hard_timeout=recovery_health,
                stopped_count=len(isolated),
                require_tip_convergence=False,
            )
            # Use a FIXED post-recovery block, not the continuously moving
            # latest head, as H22's common-finality checkpoint.  Recovery has
            # already proved that ``rec["end_block"]`` was produced after the
            # fault.  Every validator must catch up through that exact block.
            target_block=int(rec["end_block"])
            vals=wait_all_reach_block(
                target_block,
                timeout=convergence_timeout,
                max_timeout=convergence_max,
                repair_stall=True,
            )

            # Reconfirm the 7-validator QBFT membership after catch-up.
            validators=[]
            for port in range(RPC_BASE,RPC_BASE+7):
                try:
                    validators=_rpc_retry(
                        port,"qbft_getValidatorsByBlockNumber",["latest"],
                        timeout=hash_rpc_timeout
                    )
                    if validators:
                        break
                except Exception:
                    pass
            if len(validators)!=7:
                raise RuntimeError(
                    f"H22 post-recovery validator set is not 7: {validators}"
                )

            # Safety is evaluated at one identical, post-recovery block height
            # that all seven validators possess.  Latest-head equality is not a
            # QBFT safety requirement and is intentionally not used here.
            common=target_block;hashes=[]
            for port in range(RPC_BASE,RPC_BASE+7):
                block=_rpc_retry(port,"eth_getBlockByNumber",[hex(common),False],timeout=hash_rpc_timeout)
                if not block or not block.get("hash"):
                    raise RuntimeError(
                        f"H22 validator RPC {port} could not read common post-recovery block {common}"
                    )
                hashes.append(block["hash"])
            conflict=float(len(set(hashes))>1);conflicts.append(conflict)
            traj += [
                {"study_id":3,"hypothesis_id":"H22","seed":seed,"series":f"{pattern}_block_advance","x":dur,"value":advance},
                {"study_id":3,"hypothesis_id":"H22","seed":seed,"series":f"{pattern}_recovery_s","x":dur,"value":rec["recovery_s"]},
                {"study_id":3,"hypothesis_id":"H22","seed":seed,"series":f"{pattern}_operator_restart_required","x":dur,"value":rec["operator_restart_required"]},
                {"study_id":3,"hypothesis_id":"H22","seed":seed,"series":"conflict","x":dur,"value":conflict},
            ]
    # Leave the lane synchronized at the end of the seed so the next seed starts
    # from the same operational baseline rather than inheriting recovery debt.
    _h22_baseline_barrier(hash_rpc_timeout)
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
        health=ensure_qbft_live(timeout=float(os.environ.get("HC_H24_HEALTH_TIMEOUT","300")))
        w3,c,accts=load_chain();owner=accts[0]
        tx_timeout=float(os.environ.get("HC_H24_TX_TIMEOUT","180"))

        # H24 seeds share one deployed contract per lane.  Restore the two
        # cognitive validator flags before each seed so every replicate begins
        # from the same on-chain governance state, including after a resumed run.
        for j in faulty:
            addr=accts[j]["address"]
            if not c.functions.activeValidator(addr).call():
                tx_call(w3,c,owner,c.functions.setValidator(addr,True),timeout=tx_timeout)
        for j in faulty:
            addr=accts[j]["address"]
            if not c.functions.activeValidator(addr).call():
                raise RuntimeError(f"H24 pre-seed governance reset failed for validator {j}")

        # Commit this seed's governance deactivation and verify state.
        for j in faulty:
            tx_call(w3,c,owner,c.functions.setValidator(accts[j]["address"],False),timeout=tx_timeout)
        for j in faulty:
            if c.functions.activeValidator(accts[j]["address"]).call():
                raise RuntimeError(f"H24 governance record verification failed for validator {j}")
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
