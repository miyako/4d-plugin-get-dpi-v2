# 4d-plugin-get-dpi-v2

This plugin exposes a single command, `Get system DPI`, that reads the current Windows display-scaling (DPI) setting using the Win32 per-monitor DPI awareness APIs (`GetDpiForSystem`, `GetThreadDpiAwarenessContext`/`SetThreadDpiAwarenessContext`) and returns it to 4D as a `Longint` — either the raw DPI value or the scaling ratio relative to the 96 DPI baseline, depending on the parameter you pass.

| Command | Returns | Purpose |
|---|---|---|
| [Get system DPI](#get-system-dpi) | Longint | Reads the system's current DPI value or its scaling ratio vs. the 96 DPI baseline. |

**Platforms:** Windows 10 (version 1607, the Anniversary Update) or later only.

---

## Requirements & platform notes

- **Windows only, and not just any Windows 10.** The command uses `GetDpiForSystem` and `GetThreadDpiAwarenessContext`/`SetThreadDpiAwarenessContext`, all three introduced in Windows 10 version 1607. It is not implemented on macOS at all.
- **Unsupported systems get `-1`, not a 4D error.** On macOS, and on any Windows version the command doesn't support, `Get system DPI` returns `-1` silently — there is no thrown error to catch. Always check the result for `-1` before using it.
- **Single mandatory parameter, no optional form.** `mode` is read unconditionally; there's no zero-parameter call.
- **Restart 4D after a scaling change.** If the OS display-scaling setting changes while a 4D session is already open, restart 4D before calling this command again — it does not pick up a mid-session change.

---

## Get system DPI

### Syntax

```
Get system DPI ( mode ) -> Result
```

| Parameter | Type | Description |
|---|---|---|
| `mode` | Longint | Selects what's returned. Pass `1` for the raw system DPI value, or `0` (or any other value — see Description) for the DPI-to-96 scaling ratio, expressed as a percentage. |
| Result | Longint | The requested DPI value or ratio, or `-1` if the current OS/platform isn't supported. |

The command doesn't declare named constants in its own manifest, so `DPI_RATIO` and `DPI_VALUE` (used in the examples below) aren't provided automatically — define them yourself as project constants, or just pass the literal numbers:

| Constant | Value | Meaning |
|---|---|---|
| `DPI_RATIO` | 0 | Ratio of the system DPI to the 96 DPI baseline, as a percentage (e.g. `150` for 150% scaling). This is also what you get if you pass any value other than `1`. |
| `DPI_VALUE` | 1 | The raw system DPI (e.g. `144`). |

### Description

`Get system DPI` reads the **system-wide** DPI setting, not a specific monitor's DPI — it calls `GetDpiForSystem`, not a per-monitor query like `GetDpiForMonitor`. On a multi-monitor setup with different per-monitor scaling factors, this still returns one system-level value, not the value for whichever monitor a window happens to be on. Internally the command temporarily switches the calling thread to a per-monitor-aware DPI context only so the read itself isn't scaled/virtualized by the OS — that context switch doesn't change what's being measured.

`mode` isn't validated against the two documented constants: the `switch` in the plugin treats `0` and *any unrecognized value* identically (both fall into the ratio branch), so passing e.g. `mode = 7` silently returns the ratio rather than raising an error.

**On Windows 10 (1607+) and later**, the command reads the live system DPI and returns either the raw value or the computed ratio.

**On earlier Windows** and **on macOS**, the command is not implemented at all — it returns `-1` with no error and no other side effect.

### Example

From the plugin's own README:

```4d
$ratio:=Get system DPI (DPI_RATIO)
$dpi:=Get system DPI (DPI_VALUE)

ALERT(String($ratio)+"% which is "+String($dpi)+"DPI")
```

Using the literal values and guarding for the unsupported case:

```4d
$dpiRatio:=Get system DPI (0)  // 0 = DPI_RATIO
If ($dpiRatio=-1)
	ALERT("DPI information isn't available on this system.")
Else 
	ALERT("Display is scaled to "+String($dpiRatio)+"%")
End if 
```

Reading both values in one pass, e.g. for a diagnostics log:

```4d
$dpi:=Get system DPI (1)
$ratio:=Get system DPI (0)

If (($dpi#-1) & ($ratio#-1))
	LOG EVENT(Into system standard outputs;"System DPI: "+String($dpi)+" ("+String($ratio)+"%)")
Else 
	LOG EVENT(Into system standard outputs;"System DPI unavailable on this platform/OS version")
End if 
```

---

## Error handling & troubleshooting

- **No 4D error is raised on unsupported systems.** `Get system DPI` returns `-1` silently on macOS and on Windows versions before 10 (1607) — there's nothing to catch with error-handling methods; you must check the returned value yourself.
- **`-1` is a sentinel, not a real ratio/DPI value.** Don't feed it directly into scaling math (e.g. multiplying a coordinate by `$ratio/100`) without checking for it first.
- **`mode` isn't validated.** Any value other than `1` — including typos like `2` or `-1` — silently returns the ratio (`DPI_RATIO` behavior), not an error.
- **A scaling change mid-session isn't picked up.** Restart 4D after changing the display's DPI/scaling setting; don't expect a fresh call to `Get system DPI` to reflect a change made while 4D is still running.
- **macOS builds always return `-1`.** This isn't a bug to work around — there is currently no macOS implementation, so don't expect platform parity.

---

## Quick reference

```4d
$dpi:=Get system DPI (1)      // raw system DPI, or -1 if unsupported
$ratio:=Get system DPI (0)    // % ratio vs. 96 DPI, or -1 if unsupported

If ($ratio=-1)
	ALERT("DPI info unavailable on this system/OS version.")
End if 
```
