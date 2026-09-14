#!/usr/bin/env bash
# =============================================================================
# File: _k8s_diagnostics.sh
# Version: 1
# Path: infra/scripts/_k8s_diagnostics.sh
# Description: Dump why a namespace failed to come up, for the kind-based CI
#              scripts. Sourced, never executed directly (leading underscore).
#
#              WHY IT EXISTS. Both kind harnesses tear the cluster down from a
#              `trap … EXIT`, so a failed `kubectl wait` produced exactly one
#              line of evidence — "timed out waiting for the condition on
#              deployments/<name>" — and then deleted everything that could
#              explain it. On v0.1.0-beta.14 that made a c4-orchestrator
#              failure undiagnosable after the fact: the pod's events, its
#              logs, and whether it was Pending / CrashLoopBackOff /
#              ImagePullBackOff all went away with the cluster.
#
#              WHAT IT PRINTS, in the order you actually read it:
#                1. every pod with its phase + restart count — tells you the
#                   FAILURE CLASS in one line ;
#                2. `describe` for each not-ready pod — the Events block is
#                   where scheduling, probe and image failures say why ;
#                3. current and previous container logs — the previous ones
#                   matter most in a crash loop, where the live container is
#                   too young to have failed yet.
#
#              It NEVER changes the exit status: it runs on the failure path
#              and must not mask the original error, nor turn a passing run
#              red because a describe failed.
# =============================================================================

# Usage: dump_k8s_diagnostics <namespace>
dump_k8s_diagnostics() {
    local ns="${1:-aywizz}"
    echo ""
    echo "================ DIAGNOSTICS: namespace ${ns} ================" >&2

    echo "--- pods ---" >&2
    kubectl get pods -n "${ns}" -o wide 2>&1 || true

    echo "--- events (most recent last) ---" >&2
    kubectl get events -n "${ns}" --sort-by=.lastTimestamp 2>&1 | tail -40 || true

    # Not-ready pods only: a full describe of a healthy namespace buries the
    # one pod that matters under thousands of lines of CI log.
    #
    # `Succeeded` is excluded, not merely tolerated: a Completed Job or
    # CronJob pod reports `ready=false` forever because its container exited,
    # so filtering on readiness alone would describe every bootstrap Job and
    # every historical CronJob run on each failure — exactly the noise this
    # filter exists to remove. (Found by running it against a healthy cluster:
    # three `c8-storage-metering` pods surfaced as "not ready".)
    local not_ready
    not_ready=$(kubectl get pods -n "${ns}" \
        -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.phase}{" "}{.status.containerStatuses[*].ready}{"\n"}{end}' \
        2>/dev/null | awk '$2 != "Succeeded" && $3 != "true" {print $1}')

    if [ -z "${not_ready}" ]; then
        echo "--- every pod reports ready; the failure is elsewhere ---" >&2
        echo "=============================================================" >&2
        return 0
    fi

    for pod in ${not_ready}; do
        echo "--- describe ${pod} ---" >&2
        kubectl describe pod -n "${ns}" "${pod}" 2>&1 || true
        echo "--- logs ${pod} (current, last 80) ---" >&2
        kubectl logs -n "${ns}" "${pod}" --tail=80 --all-containers 2>&1 || true
        echo "--- logs ${pod} (PREVIOUS, last 80 — the crash-loop evidence) ---" >&2
        kubectl logs -n "${ns}" "${pod}" --tail=80 --all-containers --previous 2>&1 \
            || echo "    (no previous container — not a restart)" >&2
    done
    echo "=============================================================" >&2
}
