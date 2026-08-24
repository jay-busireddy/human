import argparse
import json
import os
import socket
import sys

from native_control import (
    start_all,
    stop_all,
    start_nodes,
    stop_nodes,
    status,
    node_pid,
    load_network,
)


def startup_status():
    """Lightweight process/TCP startup status.

    Starting a second 7-validator lane can temporarily make Besu JSON-RPC
    handlers slow while RocksDB/WAL and BFT initialization finish.  The caller
    must not convert that transient application-level slowness into a startup
    failure after native_control.start_all() has already proved that every RPC
    TCP listener is present.

    Full JSON-RPC/validator/block-production readiness is checked immediately
    afterward by qbft_health.py.
    """
    net = load_network()
    probe_timeout = float(os.environ.get("HC_BESU_TCP_PROBE_TIMEOUT", "1.5"))
    out = []

    for i in range(1, 8):
        pid = node_pid(i)
        port = int(net["nodes"][str(i)]["rpc_port"])
        tcp_ready = False

        if pid:
            try:
                with socket.create_connection(
                    ("127.0.0.1", port), timeout=probe_timeout
                ):
                    tcp_ready = True
            except OSError:
                tcp_ready = False

        out.append(
            {
                "node": i,
                "pid": pid,
                "running": bool(pid),
                "rpc_port": port,
                "tcp_ready": tcp_ready,
            }
        )

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "action",
        choices=["start", "stop", "status", "start-node", "stop-node", "restart"],
    )
    ap.add_argument("nodes", nargs="*", type=int)
    a = ap.parse_args()

    if a.action == "start":
        start_all()
        st = startup_status()
        print(json.dumps(st, indent=2))
        if not all(x["running"] and x["tcp_ready"] for x in st):
            sys.exit(2)
        return

    if a.action == "restart":
        stop_all()
        start_all()
        st = startup_status()
        print(json.dumps(st, indent=2))
        if not all(x["running"] and x["tcp_ready"] for x in st):
            sys.exit(2)
        return

    if a.action == "start-node":
        if not a.nodes:
            ap.error("give node numbers")
        start_nodes(a.nodes)
        # Use the same lightweight readiness rule for recovery starts.  H18/H22
        # perform their own stronger post-recovery liveness checks.
        st = startup_status()
        print(json.dumps(st, indent=2))
        requested = set(a.nodes)
        bad = [
            x
            for x in st
            if x["node"] in requested and not (x["running"] and x["tcp_ready"])
        ]
        if bad:
            sys.exit(2)
        return

    if a.action == "stop":
        stop_all()
    elif a.action == "stop-node":
        if not a.nodes:
            ap.error("give node numbers")
        stop_nodes(a.nodes)
    elif a.action == "status":
        pass

    # Full status is appropriate for explicit diagnostics and after stops; it
    # is deliberately NOT used as the acceptance gate for startup.
    print(json.dumps(status(), indent=2))


if __name__ == "__main__":
    main()
