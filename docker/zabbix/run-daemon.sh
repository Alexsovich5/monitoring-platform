#!/bin/bash
# Usage: run-daemon.sh <binary> <conf> <pidfile>
#
# Zabbix 2.4 daemons always fork into the background.  This wrapper starts
# one, stays in the foreground while it lives (so supervisord can track
# it), forwards TERM/INT to it and exits non-zero when it dies so that
# supervisord restarts it.
binary="$1" conf="$2" pidfile="$3"

rm -f "$pidfile"
"$binary" -c "$conf" || exit 1

for i in $(seq 1 30); do
    [ -s "$pidfile" ] && break
    sleep 1
done
if [ ! -s "$pidfile" ]; then
    echo "$binary did not write $pidfile within 30 s" >&2
    exit 1
fi

stop() {
    kill "$(cat "$pidfile")" 2>/dev/null
    for i in $(seq 1 20); do
        kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null || exit 0
        sleep 1
    done
    exit 0
}
trap stop TERM INT

while kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null; do
    sleep 5 &
    wait $!
done
echo "$binary exited" >&2
exit 1
