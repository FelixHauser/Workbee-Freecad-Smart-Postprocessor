# Workbee Smart Post-Process

A patched FreeCAD post-processor + companion macro for CNC routers running RepRapFirmware (Duet boards), built around **manual tool changes** and **batch G-code export**.

Instead of manually toggling operations Active/Inactive and re-typing Post Processor Args every time you export, this gives you a settings dialog that remembers your preferences and exports correctly-grouped, standalone-runnable G-code files in one click.

Based on [my first Workbee_post.py`](https://github.com/FelixHauser/Workbee-Freecad-Postprocessor) (LGPL-2.1-or-later), itself derived from the original FreeCAD RRF post processor (sliptonic, Gauthier Briere, Schildkroet, Gary L Hasson).

---

## What it does

- Exports a FreeCAD CAM Job to one or more `.nc` files, in one of three modes:
  - **Single combined file** — everything in one file.
  - **One file per operation** — every operation gets its own file.
  - **Grouped by consecutive same tool** — consecutive operations sharing a Tool Controller are merged into one file; the file boundary falls wherever the Tool Controller actually changes.
- **Every exported file is standalone-runnable.** Each file gets its own `T<number>` + `M3`/`M4 S<speed>` + a short spin-up wait injected into its preamble, resolved from that file's actual Tool Controller — not dependent on FreeCAD's "first use in the job" logic, which otherwise leaves later operations with no tool/spindle setup at all if exported on their own.
- Correctly handles **Path Dressups** (Ramp Entry, etc.) for both tool-controller resolution and Active-toggling, since Dressups don't expose these properties directly and need to be resolved through the operation they wrap.
- No FreeCAD save-dialog or G-code preview popup interrupts the batch — files are written straight to a folder you configure once.
- Dialog settings (tool override, spindle text, end-of-job choice, export mode) are **remembered between runs and across FreeCAD restarts**.

## What it deliberately does NOT do

- **It does not set or touch the work coordinate system (G54–G59).** Which WCS is active is left entirely to you, on the machine — the dialog just shows a reminder before every export. This is intentional: hardcoding a WCS into every file would silently override a WCS you'd deliberately selected by hand for a second fixture/stock on the spoilboard.

---

## How it works

Two files, used together:

1. **`Workbee_Smart_post.py`** — a post-processor script for FreeCAD's CAM workbench. It's a patched copy of the standard RRF/Duet post, with one addition: a `--force-tool-number` argument that lets the caller override the tool number written to G-code, instead of always using whatever the Tool Controller/library carries. (The upstream/original post hardcodes this to `T1`; this version makes it optional and configurable — useful since a hobby machine only ever has one physical tool loaded at a time, regardless of how many Tool Bits exist in the library.)

2. **`SmartPostProcess.FCMacro`** — a FreeCAD macro with a PySide settings dialog. It imports `Workbee_Smart_post.py` directly by file path and calls its `export()` function itself, bypassing FreeCAD's normal Post Process button entirely. This is what enables silent batch export (no repeated save dialogs) and lets the macro build a different `--preamble`/`--postamble`/`--force-tool-number` argument string for every group it exports.

The macro walks the Job's `Operations` list top to bottom (the literal order in the FreeCAD tree — it does **not** sort by operation label/number), resolves each item's Tool Controller (unwrapping Dressups where needed), splits the list into groups according to the chosen export mode, and for each group:

- Toggles the correct operations Active/Inactive (also resolved through Dressups, since Dressups don't have their own `Active` property — the wrapped base operation does).
- Builds that group's preamble/postamble from the dialog settings + that group's actual Tool Controller.
- Calls `export()` to write the file.
- Restores every operation's original Active state when done, even if an error occurs mid-export.

---

## Installation

### 1. Install the post-processor script

Copy `Workbee_Smart_post.py` into FreeCAD's user post-processor scripts folder:

| OS | Path |
|---|---|
| macOS (installed as `.app`) | `/Applications/FreeCAD.app/Contents/Resources/Mod/CAM/Path/Post/scripts/` |
| macOS (user config, some setups) | `~/Library/Application Support/FreeCAD/v1-1/Mod/CAM/Path/Post/scripts/` |
| Windows | `%APPDATA%\FreeCAD\Mod\CAM\Path\Post\scripts\` |
| Linux | `~/.local/share/FreeCAD/Mod/CAM/Path/Post/scripts/` |

Your exact path may differ by FreeCAD version — if unsure, check in FreeCAD's Python console:
```python
import Path.Post
print(Path.Post.__path__)
```

The filename **must end in `_post.py`** (lowercase) or FreeCAD won't list it as an available post-processor.

### 2. Install the macro

**Macro → Macros...** → Create a new macro → paste in the full contents of `SmartPostProcess.FCMacro` → Save.

(Or move/copy the `.FCMacro` file directly into your FreeCAD user Macro folder — same parent directory family as above, e.g. `~/Library/Application Support/FreeCAD/v1-1/Macro/` on macOS.)

### 3. Edit two lines at the top of the macro

```python
POST_SCRIPT_PATH = "/path/to/Workbee_Smart_post.py"   # match wherever you put it in step 1
OUTPUT_FOLDER = os.path.expanduser("~/Desktop/gcode_export")  # wherever you want files written
```

### 4. Check your Qt binding

Some FreeCAD builds ship with `PySide2`, others with a newer binding accessed only through FreeCAD's own compatibility shim. If the macro fails immediately with `ModuleNotFoundError: No module named 'PySide2'`, change:
```python
from PySide2 import QtWidgets, QtCore
```
to:
```python
from PySide import QtWidgets, QtCore
```
(no "2" — this lets FreeCAD forward to whichever binding is actually installed).

### 5. Select the post processor on your Job

In your CAM Job's Output settings, set **Processor** to the new script (it'll show up under the name set by its internal `prog=` value, e.g. `Workbee_Smart`).

---

## How to run

1. Open your FreeCAD document, with the CAM Job set up as normal (operations, tools, etc.).
2. **Macro → Macros...** → select `SmartPostProcess` → **Execute** (or use a toolbar button/shortcut if you've bound one).
3. Configure the dialog (see Options below) and click **OK**.
4. Files are written silently to `OUTPUT_FOLDER`. A summary popup lists what was exported, along with the WCS reminder.
5. **Before running any file on the machine: confirm the correct work coordinate system (G54/G55/...) is selected** — this macro never sets it for you.

---

## Options

### Tool number
- **Force all tools to number: [N]** — when checked, every exported file uses `T<N>` regardless of what the Tool Controller/library actually specifies. Useful on a manual-tool-change machine where the firmware only ever has one tool slot defined, so `T2`, `T3`, etc. from FreeCAD's internal numbering would otherwise be meaningless (or throw an error) on the controller.

### Spindle control
- **GCode controls spindle** — when checked, reveals two fields:
  - **Spindle activation** — raw G-code inserted before the tool-select/spindle-start block (e.g. `M106 P0 S255`, for hardware like a Super PID spindle controller wired to a fan output pin).
  - **Spindle deactivation** — raw G-code inserted after the spindle is stopped at the end of the file.
  - Note: the actual `T<number>` + `M3/M4 S<speed>` + wait is injected **regardless of this checkbox** — this option only adds the extra hardware-enable/disable lines around it.

### End of job
One of:
- **None** — no extra movement.
- **Home Z** — `G28 Z` appended to the postamble.
- **Go Home (all axes)** — `G28` appended.
- **Go to work 0** — `G0 X0 Y0` appended.

### Export mode
- **Single combined file** — one `.nc` file containing every active operation.
- **One file per operation** — every operation, including each Dressup, gets its own file.
- **Grouped by consecutive same tool** *(default)* — operations are merged into a file for as long as consecutive operations share the same Tool Controller; a new file starts wherever the Tool Controller changes. This is the mode that maps most naturally onto real manual tool-change workflow: one file per physical tool swap.

### Settings persistence
All of the above is saved via `QSettings` (a native per-OS preferences store — a `.plist` on macOS, the Registry on Windows, an `.ini` file on Linux) whenever you click **OK**. Clicking **Cancel** does not save, so you can try different settings without committing them. Settings persist across FreeCAD restarts.

---

## Known limitations / things to double-check

- **Operation order in the exported files follows the literal FreeCAD tree order**, not the numbers in your operation labels. If a Dressup gets inserted somewhere unexpected in the tree (this can happen when first creating one), it will export out of the sequence its label suggests. After adding any Dressup, glance at the Operations list to confirm it landed where you expect.
- **Dressup handling has been verified against `RampEntryDressup` only.** Other Dressup types (Dogbone, Tag, Drag Knife) follow the same underlying pattern (`.Base` pointing to the wrapped operation, no direct `ToolController`/`Active` of their own) and should work identically, but haven't been individually tested.
- **Work coordinate system is never touched** — see "What it deliberately does NOT do" above. Always double check.
- **Requires FreeCAD's own Python environment.** The macro imports `Path.Base.Util`, `Path.Post.Utils`, etc. from the post script, so it can't be run as a standalone script outside FreeCAD.
