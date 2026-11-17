# Experiment requiring user authorization (NOT executed)

## Purpose

Convert the strongest remaining correlation in the R1/R2 device-identity
findings into causal confirmation: attribute DXGI mirror LUID 118045 (and
617528) to their owning PnP adapters (GameViewer Virtual Display Adapter
`ROOT\DISPLAY\0000` vs Microsoft Remote Display Adapter
`SWD\REMOTEDISPLAYENUM\RDPIDD_INDIRECTDISPLAY&SESSIONID_0002`).

Why read-only APIs are not enough: both mirrors are zero-output DXGI
adapters with flags=0; no public API maps an output-less adapter's LUID
to a PnP instance ID, so the (order-based) attribution stays
GAMEVIEWER_CAUSALITY_UNCONFIRMED.

## Proposed steps (user executes; needs admin + reboot)

1. Device Manager → Display adapters → disable **GameViewer Virtual
   Display Adapter** (or `pnputil /disable-device "ROOT\DISPLAY\0000"`).
2. Reboot (LUIDs are re-allocated at boot; keep the RDP session detached
   during boot if possible).
3. Re-run the non-destructive audit:
   `venv-cpu\Scripts\python.exe runner\r2\device_identity_supervisor.py
   --run-id r2_<new>` in a LOCAL session (not RDP) so the remote display
   adapter is absent too, giving the cleanest table.
4. Compare `device_identity/identity_map.json` before/after.

## Expected outcomes

- If GameViewer owned LUID 118045: with it disabled, only ONE 1002:744c
  DXGI adapter remains (the physical XTX), ORT exposes a single AMD
  ep-device (Dxgi=0), and even the R1-era `devs[0]` default would work.
- If the mirror persists, GameViewer was not the owner → RDP/session
  state is the source (test by running the audit in a local session).

## Risks & rollback

- Risk: if any display input path relies on the GameViewer adapter, its
  virtual display disappears until re-enabled. No data loss expected;
  the physical XTX + Intel iGPU outputs are untouched.
- Rollback: re-enable the adapter in Device Manager (or
  `pnputil /enable-device ...`) and reboot.

## Non-destructive evidence already in hand

- ORT core `device_discovery.cc` dedup heuristic only erases the LAST
  high-performance-order adapter (source-verified, R2).
- Three 1002:744c DXGI adapters with identical VRAM/IDs; only one has an
  active output and Discrete=1 (validated probe S1–S6).
- All GDI `\\.\DISPLAY*` devices are RdpIdd in this session.
