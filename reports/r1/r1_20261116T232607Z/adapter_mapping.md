# Adapter mapping (R1.2) — what is proven vs unresolved

Source receipts: `epdev_AMDGPU_enumerate.json` (all 6 `ort.get_ep_devices()`
entries), `env_fingerprint.json` (WMI order), round-1 `reports/preflight/gpu_adapters.txt`.

## WMI / PnP adapters on this host (Win32_VideoController order)

| # | adapter | driver | PNP |
|---|---|---|---|
| 1 | GameViewer Virtual Display Adapter | 15.6.5.199 | ROOT\DISPLAY\0000 |
| 2 | **AMD Radeon RX 7900 XTX** | 32.0.21030.2001 | PCI\VEN_1002&DEV_744C&SUBSYS_0E3B1002 |
| 3 | Intel(R) UHD Graphics 770 | 32.0.101.6129 | PCI\VEN_8086&DEV_A780 |
| 4 | Microsoft Remote Display Adapter | 10.0.26100.9818 | SWD\REMOTEDISPLAYENUM\... |

## ORT ep-device table (after registering one AMD EP library)

| idx | ep_name | PCI vendor:device | DxgiAdapterNumber | Discrete | DxgiHighPerformanceIndex | LUID | 24560MB |
|---|---|---|---|---|---|---|---|
| 0 | CPUExecutionProvider | 8086:7 (pseudo) | — | — | — | — | — |
| 1 | DmlExecutionProvider | 8086:A780 (Intel) | not exposed | — | — | — | — |
| 2 | DmlExecutionProvider | **1002:744C** | not exposed | — | — | — | — |
| 3 | DmlExecutionProvider | **1002:744C** | not exposed | — | — | — | — |
| 4 | AMDGPUExecutionProvider | **1002:744C** | **1** | absent | 1 | 118045 | yes |
| 5 | AMDGPUExecutionProvider | **1002:744C** | **0** | **1** | 0 | 72346 | yes |

## Proven mapping

- Two distinct DXGI adapters (different LUIDs) both present as
  1002:744C with 24560 MB: the RX 7900 XTX is enumerated **twice** at the
  DXGI level; DML likewise lists it twice (idx 2,3).
- The `DxgiAdapterNumber=0` + `Discrete=1` entry is the physical discrete
  card: selecting it yields `hipSetDevice(0)` success and real GPU execution
  (R1.2 receipts).
- The `DxgiAdapterNumber=1` entry maps to HIP ordinal 1 in the EP's
  `gpu_factory.cc` logic → `hipGetDeviceCount()=1` → HIP 101 (R1.2 c0).
- DML device ids 0/1/2 in round-1's pre-registration probe correspond to the
  three DML-visible adapters (Intel + AMD×2); WinML does not expose the DXGI
  numbers through the DML ep_options, so the DML↔DXGI index correspondence is
  **NOT_EXPOSED** by this API surface.

## Unresolved (stated, not guessed)

- Which driver stack creates the second, non-Discrete DXGI adapter
  (LUID 118045). Most plausible: the GameViewer indirect-display driver
  wrapping the 7900 XTX (present in WMI; indirect adapters inherit the
  backing GPU's PCI IDs). Confirming requires disabling that driver
  (user-authorization experiment, see root-cause report) — not performed.
- Exact DXGI enumeration order of all host adapters (IDXGIFactory numbering
  includes software adapters; no dxdiag dump was collected this round).
