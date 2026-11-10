# 4d-plugin-get-dpi-v2

This plugin exposes a single command, `Get system DPI`, that reads the current Windows display-scaling (DPI) setting using the Win32 per-monitor DPI awareness APIs (`GetDpiForSystem`, `GetThreadDpiAwarenessContext`/`SetThreadDpiAwarenessContext`) and returns it to 4D as a `Longint` — either the raw DPI value or the scaling ratio relative to the 96 DPI baseline, depending on the parameter you pass.

| Command | Returns | Purpose |
|---|---|---|
| [Get system DPI](#get-system-dpi) | Longint | Reads the system's current DPI value or its scaling ratio vs. the 96 DPI baseline. |

**Platforms:** Windows 10 (version 1607, the Anniversary Update) or later only.

## Maintainer workflows

**Bump version** is manual: select a branch and patch/minor/major. It validates
source metadata, commits coordinated `MARKETING_VERSION` and
`CURRENT_PROJECT_VERSION` settings and literal source plist version fields, then
pushes only that selected branch without force or follow-tags. It rejects a
branch that moved since dispatch; dispatch again from its new tip. No tag,
release, or automatic Build is created.

**Build** is a separate manual workflow with no typed inputs: use the branch
selector. Every checkout uses the immutable selected `github.sha`, not a moving
branch or tag. Strict source `X.Y.Z` determines the `vX.Y.Z` release tag.
All Xcode version settings and source `Info.plist` version fields must agree;
recognized `$(MARKETING_VERSION)`, `${MARKETING_VERSION}`,
`$(CURRENT_PROJECT_VERSION)`, and `${CURRENT_PROJECT_VERSION}` plist references
are preserved. Optional absent plist build versions remain absent. The
authoritative version is the marketing/source plist version (currently `1.0.2`);
the old bump contract coordinated current-project versions too, so its stale
`1.0.0` values were repaired without incrementing the release version.
Prebuilt bundles, dependencies, and 4D schema metadata are not version sources.

Build retains Windows x64, then universal macOS (arm64 + x86_64), the merged
cross-platform bundle, Developer ID signing, notarization, and the
`DPI-X.Y.Z.zip` / `DPI-X.Y.Z.dmg` assets. The command itself is still Windows-only.
Existing signing/notary secret names are unchanged.

Publication is serialized repository-wide across branches. Both before builds
and after all build/sign/notary gates, it checks remote tags and every page of
the authenticated releases list, including drafts. Any existing matching tag or
draft/published release blocks publication; API/auth/network/malformed-response
errors fail closed. It nonforce-creates a tag at exactly the selected SHA,
requires a newly-created result (even a same-SHA race fails), verifies the remote
target, then uses a create-only REST release POST with explicit tag, target,
name, `draft=false`, and `prerelease=false`. As before, release notes are empty,
not auto-generated. Assets are POSTed to the returned new release ID without
overwriting.

**Manual recovery:** tag, release creation, or upload failures retain partial
remote state. There is no automatic delete, update, rollback, or retry. Inspect
the tag target, release (including drafts), and uploaded assets manually before
deciding how to recover. Rerunning the same version is blocked by any retained
tag/release; do not blindly retry or overwrite an existing publication.

Local lifecycle regressions: `python3 -m unittest discover -s .github/tests -v`.
Workflow lint: `actionlint`. These checks do not run native builds or publish.

## Native ownership regression

Run `python3 tests/native/run.py --baseline` on macOS with Xcode command-line
tools. The unsigned **Native ownership** pull-request check runs the same command.
It directly compiles the complete unchanged bundled `4DPluginAPI.c`, all 16
first-party wrapper implementations, and `4DPlugin.cpp`, with ASan/UBSan and a
synthetic engine-boundary callback. It never invokes Xcode project phases,
signing, notarization, packaging, releases, or real display/window APIs.
Temporary builds and the immutable `git archive` baseline are cleaned on exit.

The audit baseline is `b01450430591ec8f158d24557fe148b23eee1682`.
The bundled SDK implementation is git blob
`29788ed041effaf67269ccd93f971e8aee2ce5f8`, SHA-256
`2721c3d4338cc2a350599ffcee2a8008abaa756acfd45dd710720c64ca8c8ce3`;
its only path-history commit is `efd310c12d7b393f893344f2041ac4253a936785`
(`mv`). No external SDK dependency or upstream release pin is recorded.
The implementation's `v14` and headers' `v13` comments are not verified upstream
release identities. The regression verifies the SDK hash and preserves every
baseline file except this README and the intentionally corrected first-party
`Classes/C_PICTURE.cpp` and `Classes/ARRAY_TEXT.cpp`; new regression files
are separate.

### Inventory and contracts

The full tracked native surface consists of `4DPlugin.cpp/.h`, the prefix
header, `4DPluginAPI.c/.h/.def`, `EntryPoints.h`, `Flags.h`, `PrivateTypes.h`,
`PublicTypes.h`, and the `.cpp/.h` pairs for `C_INTEGER`, `C_LONGINT`, `C_REAL`,
`C_DATE`, `C_TIME`, `C_TEXT`, `C_BLOB`, `C_POINTER`, `C_PICTURE`,
`ARRAY_BOOLEAN`, `ARRAY_DATE`, `ARRAY_INTEGER`, `ARRAY_LONGINT`, `ARRAY_REAL`,
`ARRAY_TEXT`, and `ARRAY_TIME`. There are no additional tracked native sources
or third-party libraries. The API header includes every wrapper header.
`DPI.sln` selects `DPI.vcxproj`; that project, legacy `DPI.vc10.vcxproj`, and
the Xcode Sources phase all compile the SDK, plugin, and all 16 wrappers.
Their filter/project/header entries do not hide an alternate implementation.

Both source and bundled manifests declare only `Get system DPI(&L):L`.
The actual exported `FourDPackex` in the SDK captures the engine callback and
calls `PluginMain`, which dispatches selector 1 to `Get_system_DPI`.
Only `C_LONGINT` is used by this command. Text, picture, pointer, blob and array
helpers are compiled but dormant from this dispatcher; the audit does not
mistake the scalar manifest for evidence that they are safe.

| Surface | Ownership conclusion |
|---|---|
| `ARRAY_TEXT` | Copies borrowed UTF-16 descriptors into independent C++ strings on input; creates an independent SDK string for each output and does not dispose accepted transfers. Throws `std::invalid_argument` for a supplied null-data text-array output before creating any strings; failed initial array creation throws `std::bad_alloc` before modifying the caller parameter. Existing wrong-type rejection is unchanged. |
| SDK array setter/getter | `PA_SetStringInArray` accepts only ArrayUnicode, non-null data, `0 <= index <= count`, and a non-null input descriptor. Accepted assignments dispose the old element and shallow-transfer the new descriptor; rejection retains caller ownership. Getter results are borrowed. |
| `C_TEXT` | Copies borrowed input; outputs use the SDK content setter, not descriptor transfer. UTF-8 conversion and Objective-C copies release their own temporary owners. |
| `CPicture::fromFileAtPath` | The path's shallow alias is cleared once through its argument. Its borrowed picture output is retained by `setPicture`, then the argument owner is cleared. |
| Other `CPicture` helpers | Undefined destinations avoid overwriting initial empty owners. `setBytes` releases its temporary after retain. Borrowed synchronous inputs in size/thumbnail are detached before argument clear. Grayscale/copy/thumbnail returns remain owning handoffs for consumers to release. |
| Charset conversion | The bundled SDK returns bytes used, not UTF-16 units. `createCopy` divides by `sizeof(PA_Unichar)` before constructing its UTF-16 string. Capacity is bytes; string lengths exclude the terminator. |
| Other scalar/numeric-array wrappers | Primitive or independent C++ value copies; no text descriptor transfer or callback owners. |
| `C_BLOB` / `C_POINTER` | Blob storage is independently copied. Pointer value/reference-metadata ownership is ambiguous and deferred, not changed speculatively. |
| Callbacks and returns | No first-party method callback, JSON, object or collection API use exists here. The harness exercises independent synchronous method inputs and owning returns through the real SDK. Command handle failure returns a raw-kind-zero sentinel; method failure returns Undefined. The sentinel is not normalized or cleared as a string. |

### Evidence and limits

The same harness fails the immutable baseline on eight named cases: `path`
(double disposal plus lost initial string/output picture owners), `bytes`
(unreleased retained temporaries), `copy-units` (ASan heap-buffer-overflow),
`copy-owners`, `gray`, `size`, and `thumbnail` (overwritten initial owners),
and `array-rejection` (lost string owner after null-data setter rejection).
The fixed sources pass all 48 scenarios with explicit zero string, handle and
picture-reference balances. ASCII, BMP, supplementary surrogate pairs and empty
text are asserted exactly, with both allocated and null empty representations.
Array independence, borrowed copies surviving replacement/shrink/clear, all
setter rejection predicates and initial array-handle allocation failure,
scalar content setters, owning return survival,
unchanged/replaced/error command outputs, and poisoned failed picture-metadata
outputs are covered. Four unchanged baseline controls also pass.
macOS LeakSanitizer is unavailable; explicit allocation/ref tracking complements
ASan/UBSan rather than claiming an LSan run.

This is synthetic macOS engine-boundary evidence, not execution in 4D or Windows,
and does not certify the originating Windows Poppler crash. The macOS exported
dispatcher is exercised safely and returns the existing `-1` sentinel. No Windows
display API is called. SDK-only application-path/picture-metadata ownership,
`C_POINTER::getVariable` metadata, and SDK resize-OOM count corruption remain
deferred. Normal successful array operations do not certify resize-OOM handling.
The engine callback models documented create/retain/clear/content-copy contracts;
the actual proprietary host engine is not bundled.

The array failure tests assert the exact exception type and unchanged caller
descriptor (including a deliberately poisoned union), not a success-shaped empty
array. No owner is created or lost on these paths. There is no first-party
logging/error helper to reuse; C++ exceptions already occur on wrapper
`new`/vector allocation failures. The new explicit exceptions extend that channel
to rejected output data. They are not mapped to a new 4D error code. In particular,
the bundled SDK's failed initial `PA_NewHandle` error is overwritten by its own
subsequent lock/unlock calls; the wrapper does not claim `PA_GetLastError` retains
it. The pre-existing `PluginMain` catch-all is unchanged, and the DPI dispatcher
does not call this dormant wrapper.

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
