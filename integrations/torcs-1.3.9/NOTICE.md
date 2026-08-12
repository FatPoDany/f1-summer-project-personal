# SCR bridge provenance

The sensor helpers under `overlay/src/drivers/granite_bridge/` are derived from the
Simulated Car Racing Championship server by Daniele Loiacono, as distributed
in [`fmirus/torcs-1.3.7`](https://github.com/fmirus/torcs-1.3.7) at commit
`8134c27be99bc2039f51fd821129e1ddf7514f64`.

They remain licensed under **GPL-2.0-or-later**, matching the notices in each
source file and TORCS. The new `granite_bridge.cpp` robot was written for the
stock TORCS 1.3.9 robot ABI. It is non-blocking, applies a short action TTL with
a full-brake fallback, binds its UDP actuator port to loopback only, locks the
active peer, and supports an optional `GRANITE_BRIDGE_TOKEN` handshake.

The independent Python Granite sidecar under `src/racecoach/` communicates
with this module over the documented SCR UDP protocol and is not copied into
the TORCS source tree.
