# -*- coding: utf-8 -*-
"""
SmartPostProcess.FCMacro

A FreeCAD macro that gives you a settings dialog for post-processing a CAM
Job, then exports G-code directly (no FreeCAD save-dialog or editor popups
in between), using one of three modes:

    - Single file    : all active operations, one .nc file
    - Separate files : one .nc file per operation
    - Grouped by tool : one .nc file per run of consecutive operations
                         that share the same Tool Controller

Every exported file gets its own T-number + M3/M4 spindle-start + a short
wait injected into its preamble, resolved from that file's actual Tool
Controller -- so each file is a standalone, runnable program on its own.
Handles Dressups (RampEntryDressup etc.) correctly for tool-controller
resolution and Active toggling. Operations that were already switched off
before running this macro are never included in any export. Dialog
settings are remembered between runs.
"""

import os
import re
import importlib.util

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtWidgets, QtCore


# =========================================================================
# CONFIG -- adjust these two paths for your setup
# =========================================================================

POST_SCRIPT_PATH = "/Applications/FreeCAD.app/Contents/Resources/Mod/CAM/Path/Post/scripts/Workbee_Smart_post.py"
OUTPUT_FOLDER = os.path.expanduser("~/Desktop/gcode_export")


# =========================================================================
# Helpers
# =========================================================================

def get_active_job():
    doc = App.ActiveDocument
    if doc is None:
        return None
    for obj in doc.Objects:
        if hasattr(obj, "Operations") and hasattr(obj, "PostProcessor"):
            return obj
    return None


def resolve_tool_controller(obj, _seen=None):
    if _seen is None:
        _seen = set()
    if obj is None or obj.Name in _seen:
        return None
    _seen.add(obj.Name)

    tc = getattr(obj, "ToolController", None)
    if tc is not None:
        return tc

    base = getattr(obj, "Base", None)
    if base is not None:
        return resolve_tool_controller(base, _seen)

    return None


def resolve_active_target(obj, _seen=None):
    """
    Dressups (RampEntryDressup, etc.) don't have their own Active property
    -- only real operations do. Walk down through .Base until we find an
    object that actually has Active, and use that one instead.
    """
    if _seen is None:
        _seen = set()
    if obj is None or obj.Name in _seen:
        return None
    _seen.add(obj.Name)

    if hasattr(obj, "Active"):
        return obj

    base = getattr(obj, "Base", None)
    if base is not None:
        return resolve_active_target(base, _seen)

    return None


def sanitize_filename(text):
    text = re.sub(r"[^\w\-. ]", "_", text)
    return text.strip().replace(" ", "_")


def load_post_module(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(
            "Post-processor script not found at:\n{}\n\n"
            "Edit POST_SCRIPT_PATH at the top of this macro.".format(path)
        )
    spec = importlib.util.spec_from_file_location("smart_post_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_groups(items, mode):
    if mode == "single":
        return [items] if items else []

    if mode == "separate":
        return [[item] for item in items]

    if mode == "grouped":
        groups = []
        current = []
        current_tc_name = None
        for item in items:
            tc = resolve_tool_controller(item)
            tc_name = tc.Name if tc is not None else None
            if current and tc_name != current_tc_name:
                groups.append(current)
                current = []
            current.append(item)
            current_tc_name = tc_name
        if current:
            groups.append(current)
        return groups

    raise ValueError("Unknown export mode: {}".format(mode))


def group_label(group):
    tc = resolve_tool_controller(group[0]) if group else None
    tc_label = sanitize_filename(tc.Label) if tc is not None else "unknown_tool"
    if len(group) == 1:
        return "{}_{}".format(sanitize_filename(group[0].Label), tc_label)
    return "{}_thru_{}_{}".format(
        sanitize_filename(group[0].Label),
        sanitize_filename(group[-1].Label),
        tc_label,
    )


# =========================================================================
# Dialog
# =========================================================================

class SmartPostDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Smart Post-Process")
        self.setMinimumWidth(420)

        layout = QtWidgets.QVBoxLayout(self)

        # --- Tool number override -------------------------------------
        tool_box = QtWidgets.QGroupBox("Tool number")
        tool_layout = QtWidgets.QHBoxLayout(tool_box)
        self.force_tool_check = QtWidgets.QCheckBox("Force all tools to number:")
        self.force_tool_value = QtWidgets.QLineEdit("1")
        self.force_tool_value.setFixedWidth(50)
        self.force_tool_value.setEnabled(False)
        self.force_tool_check.toggled.connect(self.force_tool_value.setEnabled)
        tool_layout.addWidget(self.force_tool_check)
        tool_layout.addWidget(self.force_tool_value)
        tool_layout.addStretch()
        layout.addWidget(tool_box)

        # --- Spindle control ---------------------------------------------
        spindle_box = QtWidgets.QGroupBox("Spindle control")
        spindle_layout = QtWidgets.QFormLayout(spindle_box)
        self.spindle_check = QtWidgets.QCheckBox("GCode controls spindle")
        self.spindle_activate = QtWidgets.QLineEdit("M106 P0 S255")
        self.spindle_deactivate = QtWidgets.QLineEdit("M106 P0 S0")
        self.spindle_activate_label = QtWidgets.QLabel("Spindle activation:")
        self.spindle_deactivate_label = QtWidgets.QLabel("Spindle deactivation:")
        spindle_layout.addRow(self.spindle_check)
        spindle_layout.addRow(self.spindle_activate_label, self.spindle_activate)
        spindle_layout.addRow(self.spindle_deactivate_label, self.spindle_deactivate)

        def toggle_spindle_fields(checked):
            self.spindle_activate.setVisible(checked)
            self.spindle_deactivate.setVisible(checked)
            self.spindle_activate_label.setVisible(checked)
            self.spindle_deactivate_label.setVisible(checked)

        self.spindle_check.toggled.connect(toggle_spindle_fields)
        toggle_spindle_fields(False)
        layout.addWidget(spindle_box)

        # --- End of job -----------------------------------------------
        eoj_box = QtWidgets.QGroupBox("End of job")
        eoj_layout = QtWidgets.QVBoxLayout(eoj_box)
        self.eoj_none = QtWidgets.QRadioButton("None")
        self.eoj_home_z = QtWidgets.QRadioButton("Home Z")
        self.eoj_go_home = QtWidgets.QRadioButton("Go Home (all axes)")
        self.eoj_work_zero = QtWidgets.QRadioButton("Go to work 0")
        self.eoj_none.setChecked(True)
        for w in (self.eoj_none, self.eoj_home_z, self.eoj_go_home, self.eoj_work_zero):
            eoj_layout.addWidget(w)
        layout.addWidget(eoj_box)

        # --- Export mode -------------------------------------------------
        mode_box = QtWidgets.QGroupBox("Export mode")
        mode_layout = QtWidgets.QVBoxLayout(mode_box)
        self.mode_single = QtWidgets.QRadioButton("Single combined file")
        self.mode_separate = QtWidgets.QRadioButton("One file per operation")
        self.mode_grouped = QtWidgets.QRadioButton("Grouped by consecutive same tool")
        self.mode_grouped.setChecked(True)
        for w in (self.mode_single, self.mode_separate, self.mode_grouped):
            mode_layout.addWidget(w)
        layout.addWidget(mode_box)

        # --- WCS reminder --------------------------------------------------
        wcs_note = QtWidgets.QLabel(
            "⚠ Work coordinate system (G54/G55/...) is NOT set by this macro.\n"
            "Confirm the correct WCS is selected on the machine before running."
        )
        wcs_note.setStyleSheet("color: #a05a00;")
        wcs_note.setWordWrap(True)
        layout.addWidget(wcs_note)

        # --- Buttons -----------------------------------------------------
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.settings = QtCore.QSettings("Workbee", "SmartPostProcess")
        self._load_settings()

    def _on_accept(self):
        self._save_settings()
        self.accept()

    def _load_settings(self):
        s = self.settings
        self.force_tool_check.setChecked(s.value("force_tool_check", False, type=bool))
        self.force_tool_value.setText(s.value("force_tool_value", "1"))
        self.spindle_check.setChecked(s.value("spindle_check", False, type=bool))
        self.spindle_activate.setText(s.value("spindle_activate", "M106 P0 S255"))
        self.spindle_deactivate.setText(s.value("spindle_deactivate", "M106 P0 S0"))

        eoj = s.value("end_of_job", "none")
        {"none": self.eoj_none, "home_z": self.eoj_home_z,
         "go_home": self.eoj_go_home, "work_zero": self.eoj_work_zero}.get(
            eoj, self.eoj_none
        ).setChecked(True)

        mode = s.value("export_mode", "grouped")
        {"single": self.mode_single, "separate": self.mode_separate,
         "grouped": self.mode_grouped}.get(mode, self.mode_grouped).setChecked(True)

    def _save_settings(self):
        s = self.settings
        s.setValue("force_tool_check", self.force_tool_check.isChecked())
        s.setValue("force_tool_value", self.force_tool_value.text())
        s.setValue("spindle_check", self.spindle_check.isChecked())
        s.setValue("spindle_activate", self.spindle_activate.text())
        s.setValue("spindle_deactivate", self.spindle_deactivate.text())

        if self.eoj_home_z.isChecked():
            eoj = "home_z"
        elif self.eoj_go_home.isChecked():
            eoj = "go_home"
        elif self.eoj_work_zero.isChecked():
            eoj = "work_zero"
        else:
            eoj = "none"
        s.setValue("end_of_job", eoj)

        s.setValue("export_mode", self.get_export_mode())

    def get_export_mode(self):
        if self.mode_single.isChecked():
            return "single"
        if self.mode_separate.isChecked():
            return "separate"
        return "grouped"

    def get_postamble_extra(self):
        if self.eoj_home_z.isChecked():
            return "G28 Z"
        if self.eoj_go_home.isChecked():
            return "G28"
        if self.eoj_work_zero.isChecked():
            return "G0 X0 Y0"
        return ""

    def get_forced_tool_number(self):
        if self.force_tool_check.isChecked():
            value = self.force_tool_value.text().strip()
            if value:
                return value
        return None

    def build_argstring(self, tool_number, spindle_speed, spindle_dir):
        args = ["--no-show-editor"]

        forced = self.get_forced_tool_number()
        if forced:
            args.append("--force-tool-number={}".format(forced))
            tool_number = forced

        preamble_lines = []
        if self.spindle_check.isChecked():
            activate = self.spindle_activate.text().strip()
            if activate:
                preamble_lines.append(activate)

        preamble_lines.append("T{}".format(tool_number))
        if spindle_speed:
            m_code = "M4" if str(spindle_dir).strip().lower().startswith("rev") else "M3"
            preamble_lines.append("{} S{}".format(m_code, spindle_speed))
            preamble_lines.append("G4 S3")

        args.append('--preamble="{}"'.format("\\n".join(preamble_lines)))

        extra = self.get_postamble_extra()
        if self.spindle_check.isChecked():
            deactivate = self.spindle_deactivate.text().strip()
            postamble_lines = [deactivate] if deactivate else []
            if extra:
                postamble_lines.append(extra)
        else:
            postamble_lines = ["M5"]
            if extra:
                postamble_lines.append(extra)

        if postamble_lines:
            args.append('--postamble="{}"'.format("\\n".join(postamble_lines)))

        return " ".join(args)


# =========================================================================
# Main
# =========================================================================

def main():
    job = get_active_job()
    if job is None:
        QtWidgets.QMessageBox.critical(
            None, "Smart Post-Process", "No CAM Job found in the active document."
        )
        return

    dialog = SmartPostDialog(Gui.getMainWindow())
    if dialog.exec_() != QtWidgets.QDialog.Accepted:
        return

    mode = dialog.get_export_mode()

    # Build the ordered list of top-level operations/dressups that:
    #   1. actually resolve to a Tool Controller (skips Model/Stock/etc.
    #      if they somehow ended up in Operations), and
    #   2. were ALREADY Active before we touched anything -- an operation
    #      deliberately switched off should never be swept into an
    #      export, forced Active for one file, then restored.
    all_items = []
    for item in job.Operations.Group:
        if resolve_tool_controller(item) is None:
            continue
        target = resolve_active_target(item)
        if target is None or not target.Active:
            continue
        all_items.append(item)

    if not all_items:
        QtWidgets.QMessageBox.critical(
            None, "Smart Post-Process",
            "No active operations with a resolvable Tool Controller were found."
        )
        return

    groups = build_groups(all_items, mode)
    if not groups:
        QtWidgets.QMessageBox.information(None, "Smart Post-Process", "Nothing to export.")
        return

    try:
        post_module = load_post_module(POST_SCRIPT_PATH)
    except Exception as e:
        QtWidgets.QMessageBox.critical(None, "Smart Post-Process", str(e))
        return

    if not os.path.isdir(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)

    # Map each top-level item to whatever object actually holds Active
    # (itself for normal operations, the wrapped base op for Dressups).
    active_targets = {item.Name: resolve_active_target(item) for item in all_items}
    original_active = {
        t.Name: t.Active for t in active_targets.values() if t is not None
    }

    doc = App.ActiveDocument
    exported_files = []

    try:
        for index, group in enumerate(groups, start=1):
            group_names = {item.Name for item in group}
            for item in all_items:
                target = active_targets[item.Name]
                if target is not None:
                    target.Active = item.Name in group_names
            doc.recompute()

            tc = resolve_tool_controller(group[0])
            tool_number = str(int(tc.ToolNumber)) if tc is not None else "1"
            spindle_speed = getattr(tc, "SpindleSpeed", 0) if tc is not None else 0
            spindle_dir = getattr(tc, "SpindleDir", "Forward") if tc is not None else "Forward"
            argstring = dialog.build_argstring(tool_number, spindle_speed, spindle_dir)

            filename = "{:02d}_{}.nc".format(index, group_label(group))
            filepath = os.path.join(OUTPUT_FOLDER, filename)

            post_module.export(list(job.Operations.Group), filepath, argstring)
            exported_files.append(filepath)

    finally:
        for t in active_targets.values():
            if t is not None:
                t.Active = original_active[t.Name]
        doc.recompute()

    QtWidgets.QMessageBox.information(
        None,
        "Smart Post-Process",
        "Exported {} file(s) to:\n{}\n\n{}\n\n"
        "⚠ Reminder: WCS (G54/G55/...) was not touched by this export -- "
        "double-check the correct one is selected on the machine before running.".format(
            len(exported_files), OUTPUT_FOLDER, "\n".join(os.path.basename(f) for f in exported_files)
        ),
    )


main()
