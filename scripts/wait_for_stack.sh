#!/bin/sh
# Poll the Zabbix API (apiinfo.version) until it answers, for up to
# WAIT_TIMEOUT seconds (default 180).
url="${MONPLAT_ZABBIX_URL:-http://zabbix/zabbix}/api_jsonrpc.php"
timeout="${WAIT_TIMEOUT:-180}"
body='{"jsonrpc":"2.0","method":"apiinfo.version","params":{},"id":1}'

start=$(date +%s)
while :; do
    reply=$(curl -s -m 5 -H 'Content-Type: application/json-rpc' \
                 -d "$body" "$url" 2>/dev/null)
    case "$reply" in
        *'"result"'*)
            echo "Zabbix API ready: $reply"
            exit 0 ;;
    esac
    if [ $(( $(date +%s) - start )) -ge "$timeout" ]; then
        echo "Zabbix API at $url not ready after ${timeout}s" >&2
        echo "last reply: $reply" >&2
        exit 1
    fi
    sleep 3
done
