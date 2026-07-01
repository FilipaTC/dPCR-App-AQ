"""
formatter_AQ.py
================
Formats / normalizes raw AbsoluteQ digital PCR export files (.csv or .xlsx)
into a single tidy "long" table with one row per (Well, Target).

Raw AbsoluteQ export layout
----------------------------
Row 0   : channel header   (",,,,,,,FAM,,,,,,,,,,,,,,,," or with a second
          ",,,VIC,,,,,,,,,,,,,,," block when a second channel/target is read
          from the same well)
Row 1   : real column header. When a well is read on two channels (FAM +
          VIC) the header is duplicated with a ".1" pandas suffix
          (Target, Target.1, Positives, Positives.1, ...).
Then, repeating blocks:
    - a "Group" row, e.g. ",FGFR3 248,,,,,,,,,,,,,,,,,,,,,,,"
      (only the Group column is filled; marks the start of a section /
      assay for the rows that follow)
    - data rows: Run name, Sample, Well, DF, QC, Total, Target, ...,
      Positives, ... (and, if present, a second Target.1/Positives.1 block
      for the VIC channel = an Internal Control running in the SAME well)
    - occasionally a "ghost" row with only Sample/Well filled and every
      other field empty -> well was not processed / no run -> dropped.

Two physical layouts exist for the Internal Control (IC):
  * TERT-124, TERT-146, FGFR3-372, FGFR3-375: IC runs in the SAME well,
    on the VIC channel (Target.1 == '<assay>-IC').
  * FGFR3-248, FGFR3-249: IC runs in a SEPARATE well, often in a
    separate file entirely, and is reported as a single target
    'FGFR3-IC' shared between the 248 and 249 assays for the same
    sample (joined later by Sample name - see analysis_pipeline_AQ.py).

This module's only job is to turn any number of raw files into one tidy
long-format DataFrame:

    Source_File | Run_name | Sample | Well | Target | Total | Positives | Conc

ready to be consumed by analysis_pipeline_AQ.py.
"""

import os
import re
import pandas as pd
import numpy as np

# Columns we actually need downstream. AbsoluteQ sometimes spells
# "Conc." consistently; we keep it for reference even if not used in calcs.
_KEEP_COLS = ["Total", "Target", "Conc.", "Positives"]


_TIDY_COLUMNS = {"Source_File", "Sample", "Well", "Target", "Total", "Positives"}


def _read_tidy(path):
    """Try to read `path` as an already-formatted/tidy table (header on the
    very first row). Returns the DataFrame if it looks tidy, else None."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(path, header=0)
        else:
            df = pd.read_csv(path, header=0)
    except Exception:
        return None

    if _TIDY_COLUMNS.issubset(set(df.columns)):
        return df
    return None


def _read_raw(path):
    """Read a raw AbsoluteQ export (csv or xlsx) as a DataFrame, using the
    2nd row (index 1) as the header, matching the export layout."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        df = pd.read_excel(path, header=1)
    else:
        df = pd.read_csv(path, header=1)
    return df


def _channel_blocks(df):
    """Return a list of column-prefixes present for repeated per-channel
    blocks: '' for the first (FAM) block, '.1' for a second (VIC) block,
    etc. Detected from the presence of 'Target', 'Target.1', ..."""
    suffixes = [""]
    i = 1
    while f"Target.{i}" in df.columns:
        suffixes.append(f".{i}")
        i += 1
    return suffixes


def format_file(path):
    """
    Parse a single raw AbsoluteQ export file into a tidy long DataFrame.

    Returns
    -------
    pd.DataFrame with columns:
        Source_File, Run_name, Sample, Well, Target, Total, Positives, Conc
    One row per (well, target) -- a well read on two channels yields two
    rows (one per Target/Target.1 pair), which is exactly what we want
    for same-well Internal Controls.
    """
    source_name = os.path.basename(path)

    # If this file is already a tidy/formatted table (e.g. the output of
    # run_concat, or a previous format_file run saved and re-uploaded),
    # just normalize/return it as-is instead of trying to parse it as a
    # raw AbsoluteQ export.
    tidy_df = _read_tidy(path)
    if tidy_df is not None:
        result = tidy_df.copy()
        # Conc column may be named "Conc" already; keep optional columns safe
        for col in ["Run_name", "Conc"]:
            if col not in result.columns:
                result[col] = pd.NA
        result["Sample"] = result["Sample"].astype(str).str.strip()
        result["Target"] = result["Target"].astype(str).str.strip()
        result["Total"] = pd.to_numeric(result["Total"], errors="coerce")
        result["Positives"] = pd.to_numeric(result["Positives"], errors="coerce")
        keep = ["Source_File", "Run_name", "Sample", "Well", "Target", "Total", "Positives", "Conc"]
        return result[keep]

    df_raw = _read_raw(path)

    # Drop fully-empty helper rows where there's no Well at all (these are
    # either pure "Group" header rows or "ghost" unprocessed-well rows
    # with no Total/QC data).
    if "Well" not in df_raw.columns:
        raise ValueError(f"'{source_name}': unexpected file layout (no 'Well' column found).")

    # A genuine data row always has a Well AND a Total. Filter out:
    #  - Group-header rows (no Well, no Total)
    #  - "ghost" rows (Well present but no Total -> well not processed)
    #  - "Average" summary rows (Well == "Average", inserted between groups)
    #  - Placeholder rows where Sample is NaN or the literal "0"
    df_data = df_raw[
        df_raw["Well"].notna() &
        df_raw["Total"].notna() &
        (df_raw["Well"].astype(str).str.strip().str.upper() != "AVERAGE") &
        df_raw["Sample"].notna() &
        (df_raw["Sample"].astype(str).str.strip() != "0")
    ].copy()

    suffixes = _channel_blocks(df_raw)

    long_frames = []
    for suf in suffixes:
        target_col = f"Target{suf}"
        positives_col = f"Positives{suf}"
        conc_col = f"Conc.{suf}"

        if target_col not in df_data.columns:
            continue

        block = df_data[df_data[target_col].notna()].copy()
        if block.empty:
            continue

        # If this secondary channel has the same Target values as the
        # primary channel (e.g. FGFR3-IC reported on both FAM and VIC),
        # skip it to avoid duplicating rows.
        if suf != "" and "Target" in df_data.columns:
            primary = df_data.loc[block.index, "Target"].astype(str).values
            secondary = block[target_col].astype(str).values
            if list(primary) == list(secondary):
                continue

        out = pd.DataFrame({
            "Source_File": source_name,
            "Run_name": block.get("Run name"),
            "Sample": block["Sample"],
            "Well": block["Well"],
            "Target": block[target_col],
            "Total": pd.to_numeric(block["Total"], errors="coerce"),
            "Positives": pd.to_numeric(block[positives_col], errors="coerce"),
            "Conc": pd.to_numeric(block[conc_col], errors="coerce") if conc_col in block.columns else np.nan,
        })
        long_frames.append(out)

    if not long_frames:
        return pd.DataFrame(columns=["Source_File", "Run_name", "Sample", "Well", "Target", "Total", "Positives", "Conc"])

    result = pd.concat(long_frames, ignore_index=True)

    # Clean up sample / target strings
    result["Sample"] = result["Sample"].astype(str).str.strip()
    result["Target"] = result["Target"].astype(str).str.strip()

    return result


def format_files(paths):
    """Format multiple raw AbsoluteQ files and concatenate into a single
    tidy long DataFrame (see format_file)."""
    frames = [format_file(p) for p in paths]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=["Source_File", "Run_name", "Sample", "Well", "Target", "Total", "Positives", "Conc"])
    return pd.concat(frames, ignore_index=True)


def run_concat(input_dir, output_dir, filename="Concatenated_long.xlsx"):
    """
    Convenience wrapper used by the Shiny app's "Concatenate" step.
    Reads every .csv/.xlsx in input_dir, formats/normalizes them, and
    writes a single tidy long-format Excel file to output_dir.

    Returns the path to the written file.
    """
    os.makedirs(output_dir, exist_ok=True)

    candidates = [
        os.path.join(input_dir, f)
        for f in os.listdir(input_dir)
        if f.lower().endswith((".csv", ".xlsx", ".xls"))
    ]
    if not candidates:
        raise ValueError("No CSV/XLSX files found to concatenate.")

    df = format_files(candidates)
    if df.empty:
        raise ValueError("No usable data rows found in the selected files.")

    output_path = os.path.join(output_dir, filename)
    df.to_excel(output_path, index=False)
    return output_path
