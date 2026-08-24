from pathlib import Path
import os, sys, json, time, signal, shutil, subprocess, socket
from contextlib import contextmanager
import requests

HERE = Path(__file__).resolve().parent
GEN = Path(os.environ.get("HC_CHAIN_DIR", str(HERE / "generated"))).resolve()
NETWORK = GEN / "native_network.json"


def find_besu():
    explicit = os.environ.get("BESU_CMD")
    if explicit:
        p=Path(explicit.strip('"')).expanduser()
        if p.exists(): return str(p.resolve())
        w=shutil.which(explicit)
        if w: return w
        raise FileNotFoundError(f"BESU_CMD does not exist: {explicit}")
    home=os.environ.get("BESU_HOME")
    if home:
        for name in ("besu.bat","besu.cmd","besu"):
            p=Path(home.strip('"'))/"bin"/name
            if p.exists(): return str(p.resolve())
    for name in ("besu","besu.bat"):
        w=shutil.which(name)
        if w:return w
    raise FileNotFoundError(
        "Besu not found. Set BESU_HOME to the unpacked Besu directory, e.g. "
        r"set BESU_HOME=C:\besu\besu-26.5.0, or set BESU_CMD to besu.bat."
    )


def besu_argv(args):
    exe=find_besu()
    if os.name=="nt" and Path(exe).suffix.lower() in (".bat",".cmd"):
        line=subprocess.list2cmdline([exe,*map(str,args)])
        return [os.environ.get("COMSPEC","cmd.exe"),"/d","/s","/c",line]
    return [exe,*map(str,args)]


def run_besu(args, check=True, capture=False):
    cmd=besu_argv(args)
    print("+", subprocess.list2cmdline(cmd))
    return subprocess.run(cmd, check=check, text=True,
                          capture_output=capture)


def load_network():
    if not NETWORK.exists():
        raise FileNotFoundError("Run: python blockchain/setup_qbft.py --generate")
    return json.loads(NETWORK.read_text(encoding="utf-8"))


def _pidfile(i): return GEN/f"node{i}"/"node.pid"
def _logfile(i): return GEN/f"node{i}"/"node.log"

def pid_alive(pid):
    try:
        pid=int(pid)
    except (TypeError,ValueError):
        return False
    if os.name=="nt":
        # os.kill(pid, 0) is not a reliable existence probe on Windows and can
        # raise WinError 87. tasklist is slower but deterministic for 7 nodes.
        r=subprocess.run(
            ["tasklist","/FI",f"PID eq {pid}","/FO","CSV","/NH"],
            capture_output=True,text=True,errors="replace"
        )
        out=(r.stdout or "").strip()
        if not out or out.startswith("INFO:"):
            return False
        return f'"{pid}"' in out or f",{pid}," in out or str(pid) in out
    try:
        os.kill(pid,0)
        return True
    except (OSError,ProcessLookupError,ValueError):
        return False


def node_pid(i):
    p=_pidfile(i)
    if not p.exists():return None
    try:pid=int(p.read_text().strip())
    except Exception:
        p.unlink(missing_ok=True)
        return None
    if pid_alive(pid):
        return pid
    p.unlink(missing_ok=True)
    return None


def node_args(i):
    net=load_network(); node=net["nodes"][str(i)]
    args=[
        f"--data-path={node['data_path']}",
        f"--genesis-file={net['genesis']}",
        f"--p2p-port={node['p2p_port']}",
        "--rpc-http-enabled",
        f"--rpc-http-port={node['rpc_port']}",
        "--rpc-http-api=ADMIN,ETH,NET,QBFT,WEB3",
        "--host-allowlist=*",
        "--rpc-http-cors-origins=all",
        # v1.4.2: keep Besu's permissioned-network ENTERPRISE profile and its
        # SEQUENCED transaction pool.  Do not combine this profile with
        # --tx-pool=layered: the profile injects sequenced/legacy-only tuning
        # (including tx-pool-limit-by-account-percentage), which Besu 26.5.0
        # correctly rejects when the layered implementation is forced.
        "--profile=ENTERPRISE",
        # Make the profile's tx-pool choice explicit.  SEQUENCED is the
        # permissioned-network pool family and is compatible with the
        # account-share tuning below.
        "--tx-pool=sequenced",
        # H23 intentionally submits 50 consecutive transactions from one
        # funded sender.  Allow that sender to occupy the full local pool if
        # needed; pool capacity itself is still bounded by Besu.
        "--tx-pool-limit-by-account-percentage=1",
        # Fees are outside all H17-H24 hypotheses on this funded local chain.
        "--min-gas-price=0",
    ]
    if i>1:
        # On Windows use Besu's documented local bootnodes-file source instead
        # of passing enode://... directly through cmd.exe/besu.bat.
        source=net.get("bootnodes_file")
        if not source:
            source=net["bootnode"]
        args.append(f"--bootnodes={source}")
    return args


def _rpc_tcp_ready(i, timeout=1.0):
    """Return True once the node's local RPC TCP listener accepts connections.

    Process startup readiness must not depend on a 1-second JSON-RPC round trip.
    On the target 8-core Windows host, launching the second 7-validator lane can
    make an otherwise healthy Besu RPC handler take >1s to answer while the JVMs
    finish RocksDB/WAL and BFT initialization.  TCP-listener readiness is the
    correct boundary here; qbft_health.py performs the stronger application and
    consensus checks immediately afterward.
    """
    port=load_network()["nodes"][str(i)]["rpc_port"]
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=float(timeout)):
            return True
    except OSError:
        return False


def wait_rpc(i, timeout=None):
    if timeout is None:
        timeout=float(os.environ.get("HC_BESU_START_TIMEOUT","180"))
    end=time.time()+float(timeout)
    probe=float(os.environ.get("HC_BESU_TCP_PROBE_TIMEOUT","1.5"))
    while time.time()<end:
        if _rpc_tcp_ready(i,probe):return True
        time.sleep(.35)
    return False



def transaction_pool_mode(i=1):
    """Return the most recent transaction-pool implementation reported by Besu."""
    p=_logfile(i)
    if not p.exists(): return None
    try:
        lines=p.read_text(encoding="utf-8",errors="replace").splitlines()
    except Exception:
        return None
    for line in reversed(lines):
        u=line.upper()
        if "TRANSACTION POOL IMPLEMENTATION" in u:
            if "LAYERED" in u:return "layered"
            if "SEQUENCED" in u:return "sequenced"
            if "LEGACY" in u:return "legacy"
            return line.strip()
    return None


def assert_transaction_pool_mode(expected="sequenced"):
    modes={}
    for i in range(1,8):
        modes[i]=transaction_pool_mode(i)
    bad={i:m for i,m in modes.items() if m!=expected}
    if bad:
        raise RuntimeError(
            f"Besu transaction-pool preflight expected {expected!r} on all validators; "
            f"observed {modes}. Inspect node logs and verify --tx-pool={expected}."
        )
    return modes


def _tail_log(i, lines=80):
    p=_logfile(i)
    if not p.exists():
        return f"(no log file at {p})"
    try:
        data=p.read_text(encoding="utf-8",errors="replace").splitlines()
        return "\n".join(data[-lines:])
    except Exception as e:
        return f"(could not read {p}: {e})"


def _port_is_free(port, socktype):
    s=socket.socket(socket.AF_INET,socktype)
    try:
        s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,0)
        s.bind(("127.0.0.1",int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def preflight_node_ports(i):
    node=load_network()["nodes"][str(i)]
    problems=[]
    if not _port_is_free(node["rpc_port"],socket.SOCK_STREAM):
        problems.append(f"RPC TCP {node['rpc_port']}")
    if not _port_is_free(node["p2p_port"],socket.SOCK_STREAM):
        problems.append(f"P2P TCP {node['p2p_port']}")
    if not _port_is_free(node["p2p_port"],socket.SOCK_DGRAM):
        problems.append(f"P2P UDP {node['p2p_port']}")
    if problems:
        raise RuntimeError(
            f"node{i} cannot start because these local ports are already in use: "
            + ", ".join(problems)
            + ". Check with: netstat -ano | findstr :PORT"
        )


def _besu_child_env():
    env=os.environ.copy()
    env["BESU_OPTS"]=os.environ.get("HC_BESU_OPTS","-Xms128m -Xmx512m")
    return env


@contextmanager
def _cross_lane_start_lock(timeout=None):
    """Serialize expensive Besu JVM startups across local parallel lanes.

    Two independent QBFT lanes are scientifically independent, but starting two
    Besu JVMs at the same instant on an 8-core Windows laptop can delay RPC
    readiness enough to look like a network failure.  This lock changes only
    host scheduling, not any fault duration, seed, transaction, or consensus
    condition.
    """
    timeout=float(timeout or os.environ.get("HC_BESU_START_LOCK_TIMEOUT","300"))
    lock=HERE/".besu_parallel_start.lock"
    deadline=time.time()+timeout
    token=f"{os.getpid()} {time.time()}\n"
    while True:
        try:
            fd=os.open(str(lock), os.O_CREAT|os.O_EXCL|os.O_WRONLY)
            try: os.write(fd, token.encode("ascii","ignore"))
            finally: os.close(fd)
            break
        except FileExistsError:
            # Remove an abandoned lock after a generous stale interval.
            try:
                age=time.time()-lock.stat().st_mtime
                if age>600:
                    lock.unlink(missing_ok=True)
                    continue
            except Exception:
                pass
            if time.time()>=deadline:
                raise RuntimeError(f"Timed out waiting for shared Besu startup lock: {lock}")
            time.sleep(.5)
    try:
        yield
    finally:
        try: lock.unlink(missing_ok=True)
        except Exception: pass


def _start_node_once(i, wait=True):
    """Launch one Besu validator process.

    When ``wait=False`` this returns immediately after process creation.  That
    mode is critical for a fresh QBFT network: a quorum of validators must come
    up together so the early validators do not spend minutes repeatedly
    doubling their QBFT round timeout while the remaining JVMs are still being
    started.
    """
    if node_pid(i): return node_pid(i)
    preflight_node_ports(i)
    log=_logfile(i);log.parent.mkdir(parents=True,exist_ok=True)
    fh=open(log,"ab",buffering=0)
    flags=0
    if os.name=="nt":
        flags=getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)|getattr(subprocess,"CREATE_NO_WINDOW",0)
    env=_besu_child_env()
    node=load_network()["nodes"][str(i)]
    print(f"Starting node{i}: RPC {node['rpc_port']}, P2P {node['p2p_port']}, BESU_OPTS={env.get('BESU_OPTS')}", flush=True)
    p=subprocess.Popen(
        besu_argv(node_args(i)),
        cwd=str((GEN/f"node{i}").resolve()),
        stdout=fh,stderr=subprocess.STDOUT,
        creationflags=flags,
        env=env,
    )
    fh.close();_pidfile(i).write_text(str(p.pid))
    if wait and not wait_rpc(i):
        tail=_tail_log(i)
        try: stop_node(i)
        except Exception: pass
        raise RuntimeError(
            f"node{i} RPC did not start within the timeout.\n"
            f"Log: {_logfile(i)}\n\n--- last log lines ---\n{tail}"
        )
    return p.pid


def _wait_rpc_group(nodes, timeout=None):
    """Wait for every launched validator's RPC TCP listener using one deadline.

    A listening socket is intentionally sufficient at this layer.  The separate
    qbft_health gate then verifies web3/eth/qbft JSON-RPC, validator count, block
    advancement, convergence, and transaction-pool mode.
    """
    nodes=sorted(set(map(int,nodes)))
    if not nodes:return True,[]
    if timeout is None:
        timeout=float(os.environ.get("HC_BESU_START_TIMEOUT","180"))
    deadline=time.time()+float(timeout)
    probe=float(os.environ.get("HC_BESU_TCP_PROBE_TIMEOUT","1.5"))
    pending=set(nodes)
    while pending and time.time()<deadline:
        for i in list(pending):
            if _rpc_tcp_ready(i,probe):
                pending.remove(i)
        if pending:time.sleep(.35)
    return not pending,sorted(pending)


def start_node(i, wait=True):
    """Start one validator; serialize only a *single-node* recovery startup."""
    if node_pid(i): return node_pid(i)
    attempts=max(1,int(os.environ.get("HC_BESU_START_RETRIES","2")))
    last=None
    for attempt in range(1,attempts+1):
        try:
            with _cross_lane_start_lock():
                return _start_node_once(i,wait=wait)
        except Exception as e:
            last=e
            try: stop_node(i)
            except Exception: pass
            if attempt<attempts:
                delay=float(os.environ.get("HC_BESU_START_RETRY_DELAY","5"))
                print(f"node{i} startup attempt {attempt}/{attempts} failed; retrying in {delay:.1f}s: {e}", flush=True)
                time.sleep(delay)
    raise last


def _start_nodes_burst(nodes):
    """Launch a validator set as one coordinated burst, then wait for RPCs.

    The cross-lane lock is held for the *whole burst*, not separately for each
    validator.  Thus lane 1 and lane 2 do not launch JVM bursts at the same
    instant, but all validators within one QBFT lane are created quickly enough
    to establish quorum before QBFT round timeouts escalate.
    """
    nodes=sorted(set(map(int,nodes)))
    missing=[i for i in nodes if not node_pid(i)]
    if not missing:return []
    # Preflight all ports before launching anything so a stale process does not
    # leave a half-started validator set.
    for i in missing:preflight_node_ports(i)
    launched=[]
    with _cross_lane_start_lock():
        try:
            for i in missing:
                _start_node_once(i,wait=False)
                launched.append(i)
                # A very small gap avoids Windows process-creation spikes while
                # still bringing a 7-validator quorum up as a coordinated set.
                time.sleep(float(os.environ.get("HC_BESU_BURST_GAP","0.20")))
            ok,pending=_wait_rpc_group(launched)
            if not ok:
                details=[]
                for i in pending:
                    details.append(f"--- node{i} ---\n{_tail_log(i,40)}")
                raise RuntimeError(
                    "Besu burst startup did not bring all validator RPCs online. "
                    f"Pending nodes: {pending}\n"+"\n".join(details)
                )
        except Exception:
            for i in reversed(launched):
                try:stop_node(i)
                except Exception:pass
            raise
    return launched


def start_nodes(nodes):
    """Start one or more validators without serializing a QBFT quorum to death."""
    nodes=sorted(set(map(int,nodes)))
    missing=[i for i in nodes if not node_pid(i)]
    if not missing:return
    attempts=max(1,int(os.environ.get("HC_BESU_START_RETRIES","2")))
    last=None
    for attempt in range(1,attempts+1):
        try:
            if len(missing)==1:
                start_node(missing[0],wait=True)
            else:
                _start_nodes_burst(missing)
            return
        except Exception as e:
            last=e
            # Stop only nodes requested by this operation; existing quorum
            # members outside the requested set remain untouched.
            for i in reversed(missing):
                try:stop_node(i)
                except Exception:pass
            if attempt<attempts:
                delay=float(os.environ.get("HC_BESU_START_RETRY_DELAY","5"))
                print(f"validator burst startup attempt {attempt}/{attempts} failed; retrying in {delay:.1f}s: {e}",flush=True)
                time.sleep(delay)
    raise last

def stop_node(i):
    pid=node_pid(i)
    if not pid:
        _pidfile(i).unlink(missing_ok=True);return
    if os.name=="nt":
        subprocess.run(["taskkill","/PID",str(pid),"/T","/F"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    else:
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:pass
    for _ in range(30):
        if not pid_alive(pid):break
        time.sleep(.2)
    _pidfile(i).unlink(missing_ok=True)


def stop_nodes(nodes):
    for i in sorted(set(map(int,nodes)),reverse=True):stop_node(i)


def start_all():
    start_nodes(range(1,8));time.sleep(float(os.environ.get("HC_BESU_POST_BURST_PAUSE","1.0")))


def stop_all(): stop_nodes(range(1,8))


def rpc(i,method,params=None,timeout=None):
    if timeout is None:
        timeout=float(os.environ.get("HC_RPC_TIMEOUT","8"))
    port=load_network()["nodes"][str(i)]["rpc_port"]
    r=requests.post(f"http://127.0.0.1:{port}",json={"jsonrpc":"2.0","method":method,"params":params or [],"id":1},timeout=float(timeout))
    r.raise_for_status();j=r.json()
    if "error" in j:raise RuntimeError(j["error"])
    return j.get("result")


def status():
    out=[]
    for i in range(1,8):
        pid=node_pid(i); item={"node":i,"pid":pid,"running":bool(pid)}
        if pid:
            try:
                item["client"]=rpc(i,"web3_clientVersion")
                item["block"]=int(rpc(i,"eth_blockNumber"),16)
                item["peers"]=int(rpc(i,"net_peerCount"),16)
            except Exception as e:item["rpc_error"]=str(e)
        out.append(item)
    return out
