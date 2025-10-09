#!/bin/bash

RYU_URL="http://127.0.0.1:8080/sdrlb/stats/flowentry/add"
DPID=200

# h1 → h2 (IP)
curl -s -X POST -H "Content-Type: application/json" \
-d '{"dpid":'"$DPID"',"match":{"in_port":3,"eth_type":2048},"actions":[{"type":"OUTPUT","port":4}],"priority":1000,"idle_timeout":60}' \
$RYU_URL

# h2 → h1 (IP)
curl -s -X POST -H "Content-Type: application/json" \
-d '{"dpid":'"$DPID"',"match":{"in_port":4,"eth_type":2048},"actions":[{"type":"OUTPUT","port":3}],"priority":1000,"idle_timeout":60}' \
$RYU_URL

# h1 → h2 (ARP)
curl -s -X POST -H "Content-Type: application/json" \
-d '{"dpid":'"$DPID"',"match":{"in_port":3,"eth_type":2054},"actions":[{"type":"OUTPUT","port":4}],"priority":1000,"idle_timeout":60}' \
$RYU_URL

# h2 → h1 (ARP)
curl -s -X POST -H "Content-Type: application/json" \
-d '{"dpid":'"$DPID"',"match":{"in_port":4,"eth_type":2054},"actions":[{"type":"OUTPUT","port":3}],"priority":1000,"idle_timeout":60}' \
$RYU_URL

echo "✅ All 4 flows (IP+ARP) installed for h1 <-> h2"
