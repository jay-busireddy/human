from pathlib import Path
import argparse, json, os, sys, time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from native_control import start_all, stop_all, start_nodes, stop_nodes, status, rpc, assert_transaction_pool_mode


def _block(i=1):
    return int(rpc(i, "eth_blockNumber"), 16)


def _all_blocks():
    vals = []
    for i in range(1, 8):
        try:
            vals.append(_block(i))
        except Exception:
            vals.append(None)
    return vals


def _validators_from_any(blocks):
    """Return the QBFT validator list from any responsive validator."""
    rpc_timeout = float(os.environ.get("HC_QBFT_RPC_TIMEOUT", "15"))
    for i, b in enumerate(blocks, 1):
        if b is None:
            continue
        try:
            vals = rpc(
                i,
                "qbft_getValidatorsByBlockNumber",
                ["latest"],
                timeout=rpc_timeout,
            )
            if vals:
                return vals
        except Exception:
            pass
    return []


def _sync_state(i):
    """Best-effort sync diagnostics. Never used as the sole success criterion."""
    try:
        return rpc(
            i,
            "eth_syncing",
            [],
            timeout=float(os.environ.get("HC_QBFT_RPC_TIMEOUT", "15")),
        )
    except Exception:
        return None


def _fixed_block_hashes(block_number):
    """Read one fixed block from all seven validators.

    Returns (hashes, missing_nodes).  Reading and agreeing on a single fixed
    post-start block is a stronger and more stable consistency test than
    requiring seven sequential eth_blockNumber calls to report the same moving
    latest head.
    """
    hashes = []
    missing = []
    timeout = float(os.environ.get("HC_QBFT_FIXED_BLOCK_RPC_TIMEOUT", "30"))
    tag = hex(int(block_number))
    for i in range(1, 8):
        try:
            block = rpc(i, "eth_getBlockByNumber", [tag, False], timeout=timeout)
            h = block.get("hash") if isinstance(block, dict) else None
            if not h:
                missing.append(i)
                hashes.append(None)
            else:
                hashes.append(str(h).lower())
        except Exception:
            missing.append(i)
            hashes.append(None)
    return hashes, missing


def _wait_consensus_ready(timeout=300.0, warmup=3.0):
    """Prove seven-validator QBFT liveness using a fixed post-start checkpoint.

    Why fixed checkpoint instead of latest-head equality:
      * Besu validators recovering RocksDB/WAL can be hundreds/thousands of
        blocks behind while the other validators continue finalizing blocks.
      * Comparing seven independently sampled *latest* heights creates a moving
        target and can keep a healthy, syncing validator behind forever.
      * We instead freeze one block produced after this health check started and
        require every validator to reach it and return the same block hash.

    Success still requires:
      1. the chain advances after this check begins;
      2. QBFT membership contains seven validators;
      3. all seven RPCs are responsive;
      4. all seven validators reach the fixed post-start checkpoint; and
      5. all seven return the identical hash for that checkpoint.
    """
    base_timeout = max(
        float(timeout),
        float(os.environ.get("HC_QBFT_MIN_HEALTH_TIMEOUT", "300")),
    )
    # Preparation may resume a lane whose validator was intentionally offline
    # during H22. Give active FULL sync enough time to reach one fixed checkpoint;
    # this is bounded but deliberately larger than the normal startup window.
    max_timeout = max(
        base_timeout,
        float(os.environ.get("HC_QBFT_MAX_CATCHUP_TIMEOUT", "3600")),
        3600.0,
    )
    progress_grace = max(float(os.environ.get("HC_QBFT_PROGRESS_GRACE", "300")), 300.0)

    if warmup > 0:
        time.sleep(warmup)

    started = time.time()
    soft_deadline = started + base_timeout
    hard_deadline = started + max_timeout

    start_block = None
    checkpoint_block = None
    saw_advance = False
    last_blocks = [None] * 7
    last = None

    # Per-node progress tracking.  A validator that is demonstrably catching up
    # must not be restarted merely because it is behind the current head.
    last_seen_block = [None] * 7
    last_progress_at = [started] * 7
    targeted_restarted = set()

    while time.time() < hard_deadline:
        now = time.time()
        blocks = _all_blocks()
        last_blocks = blocks
        responsive = [b for b in blocks if b is not None]

        for idx, b in enumerate(blocks):
            if b is None:
                continue
            prev = last_seen_block[idx]
            if prev is None or b > prev:
                last_seen_block[idx] = b
                last_progress_at[idx] = now

        if responsive:
            top = max(responsive)
            if start_block is None:
                start_block = top
            elif top > start_block:
                saw_advance = True
                # Freeze the FIRST post-start checkpoint.  Do not move it later.
                if checkpoint_block is None:
                    checkpoint_block = int(start_block) + 1

        validators = _validators_from_any(blocks)

        if (
            saw_advance
            and checkpoint_block is not None
            and len(validators) == 7
            and all(b is not None for b in blocks)
            and all(int(b) >= checkpoint_block for b in blocks)
        ):
            hashes, missing = _fixed_block_hashes(checkpoint_block)
            present = [h for h in hashes if h]
            if not missing and len(present) == 7 and len(set(present)) == 1:
                try:
                    st = status()
                except Exception:
                    st = []
                peers = [
                    int(x.get("peers", 0))
                    for x in st
                    if isinstance(x, dict) and "peers" in x
                ]
                return {
                    "ok": True,
                    "start_block": start_block,
                    "checkpoint_block": checkpoint_block,
                    "checkpoint_hash": present[0],
                    "end_block": max(int(b) for b in blocks if b is not None),
                    "blocks": blocks,
                    "validators": 7,
                    "min_peers": min(peers) if peers else None,
                    "status": st,
                    "saw_advance": True,
                    "readiness_mode": "fixed_post_start_checkpoint",
                }

        # Extend beyond the ordinary health window only when a lagging node is
        # actually progressing toward the fixed checkpoint.  The hard deadline
        # remains absolute.
        if now >= soft_deadline and checkpoint_block is not None:
            laggers = [
                i + 1
                for i, b in enumerate(blocks)
                if b is None or int(b) < checkpoint_block
            ]
            progressing = any(
                blocks[i - 1] is not None
                and (now - last_progress_at[i - 1]) < progress_grace
                for i in laggers
            )
            if progressing and soft_deadline < hard_deadline:
                soft_deadline = min(hard_deadline, now + progress_grace)
                print(
                    "QBFT fixed-checkpoint catch-up still progressing; "
                    f"waiting for validators {laggers} to reach block {checkpoint_block}. "
                    f"Current blocks: {blocks}",
                    flush=True,
                )

        # Targeted restart is reserved for a validator that is not responsive,
        # or that has made no block progress for a substantial grace period.
        # Never restart an actively advancing lagger.
        if checkpoint_block is not None:
            stalled = []
            for idx, b in enumerate(blocks):
                node = idx + 1
                if node in targeted_restarted:
                    continue
                if b is not None and int(b) >= checkpoint_block:
                    continue
                if now - last_progress_at[idx] < progress_grace:
                    continue
                sync = _sync_state(node)
                # If Besu explicitly says it is syncing, leave it alone even if
                # eth_blockNumber has not changed during this sample window.
                if isinstance(sync, dict):
                    continue
                stalled.append(node)

            if stalled:
                print(
                    "QBFT fixed-checkpoint validator(s) stalled; targeted restart: "
                    f"{stalled}",
                    flush=True,
                )
                stop_nodes(stalled)
                time.sleep(float(os.environ.get("HC_QBFT_TARGETED_RESTART_PAUSE", "2")))
                start_nodes(stalled)
                for node in stalled:
                    targeted_restarted.add(node)
                    last_progress_at[node - 1] = time.time()
                # Give restarted validators a fresh grace interval but do not
                # reset the fixed checkpoint or restart healthy validators.
                soft_deadline = min(hard_deadline, time.time() + progress_grace)

        last = {
            "reason": (
                "waiting_for_fixed_checkpoint"
                if saw_advance and checkpoint_block is not None
                else "waiting_for_advance"
            ),
            "start_block": start_block,
            "checkpoint_block": checkpoint_block,
            "blocks": blocks,
            "responsive": len(responsive),
            "validators": len(validators),
            "saw_advance": saw_advance,
            "targeted_restarted": sorted(targeted_restarted),
        }

        # Before the soft deadline, ordinary polling.  After it, continue only
        # while the hard catch-up deadline still permits it.
        if now >= soft_deadline and now >= hard_deadline:
            break
        time.sleep(2)

    return {
        "ok": False,
        "last": last,
        "start_block": start_block,
        "checkpoint_block": checkpoint_block,
        "blocks": last_blocks,
        "saw_advance": saw_advance,
        "readiness_mode": "fixed_post_start_checkpoint",
    }


def _txpool_modes_with_retry(expected, timeout=120.0):
    deadline = time.time() + float(timeout)
    last = None
    while time.time() < deadline:
        try:
            return assert_transaction_pool_mode(expected)
        except Exception as e:
            last = e
            time.sleep(2)
    raise RuntimeError(
        f"transaction-pool mode check did not stabilize within {timeout}s: {last}"
    )


def ensure_live(timeout=300.0, repair_restarts=2):
    """Start and prove QBFT liveness without destroying an actively syncing lane.

    Coordinated seven-node restarts are now used only when the lane fails to
    demonstrate *any* consensus progress.  If blocks are advancing, recovery is
    evaluated against one fixed post-start checkpoint instead of a moving head.
    """
    effective = max(
        float(timeout),
        float(os.environ.get("HC_QBFT_MIN_HEALTH_TIMEOUT", "300")),
    )
    start_all()
    result = _wait_consensus_ready(timeout=effective)

    if result.get("ok"):
        result["tx_pool_modes"] = _txpool_modes_with_retry(
            os.environ.get("HC_BESU_TX_POOL_MODE", "sequenced").lower()
        )
        result["repair_restart_count"] = 0
        return result

    # Critical rule: if the lane has already advanced, do NOT coordinated-restart
    # all seven validators.  A lagging validator may be deep in FULL/WAL catch-up;
    # restarting the lane repeatedly throws away useful progress and recreates
    # the exact failure mode seen on Windows.  _wait_consensus_ready already
    # performs at most one targeted restart per genuinely stalled lagger.
    if result.get("saw_advance"):
        raise RuntimeError(
            "QBFT lane advanced but not every validator reached the fixed "
            "post-start checkpoint before the bounded catch-up safety cap. Healthy "
            "validators were intentionally NOT restarted. Last health state:\n"
            + json.dumps(result, indent=2, default=str)
        )

    # Only a lane that cannot prove ANY block progress is eligible for a full,
    # coordinated restart.  This retains the QBFT round-timeout recovery path
    # without punishing an already-live chain.
    for attempt in range(1, int(repair_restarts) + 1):
        print(
            f"QBFT cannot prove any consensus progress; coordinated lane restart "
            f"{attempt}/{repair_restarts}",
            flush=True,
        )
        print(json.dumps(result, indent=2, default=str), flush=True)
        stop_all()
        time.sleep(float(os.environ.get("HC_QBFT_RESTART_PAUSE", "4")))
        old = os.environ.get("HC_BESU_START_TIMEOUT")
        try:
            current = float(old or 0)
            os.environ["HC_BESU_START_TIMEOUT"] = str(
                max(
                    current,
                    float(os.environ.get("HC_QBFT_RESTART_START_TIMEOUT", "300")),
                )
            )
            start_all()
        finally:
            if old is None:
                os.environ.pop("HC_BESU_START_TIMEOUT", None)
            else:
                os.environ["HC_BESU_START_TIMEOUT"] = old

        result = _wait_consensus_ready(timeout=effective, warmup=5.0)
        if result.get("ok"):
            result["tx_pool_modes"] = _txpool_modes_with_retry(
                os.environ.get("HC_BESU_TX_POOL_MODE", "sequenced").lower()
            )
            result["repair_restart_count"] = attempt
            return result
        if result.get("saw_advance"):
            raise RuntimeError(
                "QBFT recovered block production after coordinated restart, but "
                "not every validator reached the fixed post-start checkpoint "
                "before the catch-up deadline. No further full restart will be "
                "performed. Last health state:\n"
                + json.dumps(result, indent=2, default=str)
            )

    raise RuntimeError(
        "QBFT lane did not prove consensus liveness after restart paths. Last "
        "health state:\n" + json.dumps(result, indent=2, default=str)
    )


def main():
    ap = argparse.ArgumentParser(
        description="Prove a native 7-validator QBFT lane can actually finalize blocks."
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("HC_QBFT_HEALTH_TIMEOUT", "90")),
    )
    ap.add_argument(
        "--repair-restarts",
        type=int,
        default=int(os.environ.get("HC_QBFT_HEALTH_RESTARTS", "2")),
    )
    args = ap.parse_args()
    out = ensure_live(timeout=args.timeout, repair_restarts=args.repair_restarts)
    print("QBFT CONSENSUS_READY")
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
