"""
analysis_pipeline_AQ.py
========================
Core business logic for ddPCR AbsoluteQ analysis.

Takes the tidy long-format table produced by formatter_AQ.py (one row per
well/target, across one or more raw export files) and produces a detailed,
per-sample / per-assay result table, written to an intermediate Excel file.

Assays and Internal Controls
-----------------------------
Single-plex reactions, 6 possible mutations / 7 reactions total:

    Assay        Mutant target     IC target       IC location
    -----------  ----------------  --------------  --------------------------
    TERT-124     TERT-124-MUT      TERT-124-IC     same well (2nd channel)
    TERT-146     TERT-146-MUT      TERT-146-IC     same well (2nd channel)
    FGFR3-372    FGFR3-372-MUT     FGFR3-372-IC    same well (2nd channel)
    FGFR3-375    FGFR3-375-MUT     FGFR3-375-IC    same well (2nd channel)
    FGFR3-248    FGFR3-248-MUT     FGFR3-IC        separate well, shared with 249
    FGFR3-249    FGFR3-249-MUT     FGFR3-IC        separate well, shared with 248

For FGFR3-248 / FGFR3-249, the single shared 'FGFR3-IC' well may live in a
completely different raw file than the mutant well. It is matched to the
mutant row purely by Sample name within the data the user provided.

Business rules
---------------
1. NTC normalization: within the same run (Source_File/Run_name), find the
   well whose Sample is "NTC". If none exists, fall back to "NC". Subtract
   that well's Positives from every sample's Positives for the SAME Target
   in that run (applied to both mutant and IC targets). Result floored at 0.
2. Validation (computed on normalized Positives):
     - Total >= 20000  AND  IC Positives >= 10  -> validated
     - otherwise -> "Inconclusive" UNLESS only the Total condition fails
       (IC Positives still >= 10), in which case fall through to rule 4
       using the "<20k events" labels.
     - If the IC itself is missing entirely (e.g. FGFR3-248/249 sample with
       no matching FGFR3-IC well anywhere in the uploaded files) -> "Inconclusive".
3. Fractional Abundance (own calculation, independent from the instrument's
   "Original Fractional Abundance"):
       FA = [mut_positives_norm / (mut_positives_norm + ic_positives_norm)] * 100
   only computed when the denominator > 0.
4. Result:
     - mut_positives_norm >= 5  AND  FA >= 0.5   -> "Positive"
       (or "Positive (<20k events)" if Total < 20000 but IC was otherwise valid)
     - otherwise                                  -> "Negative"
       (or "Negative (<20k events)" if Total < 20000 but IC was otherwise valid)
     - if IC Positives < 10 (or IC missing)        -> "Inconclusive"

Output (intermediate Excel)
-----------------------------
One row per (Sample, Assay), columns:
    Source_File, Sample description 1, Target, Conc (copies/uL),
    Accepted Events, Positives, Original Fractional Abundance,
    Accepted Events Valid?, Positive Events Valid (IC>=10)?,
    Fractional Abundance, Fractional Abundance >= 0.5?, Result
"""

import os
import datetime
import numpy as np
import pandas as pd

from formatter_AQ import format_files

ASSAYS = {
    "TERT-124":  {"mut": "TERT-124-MUT",  "ic": "TERT-124-IC",  "ic_same_well": True},
    "TERT-146":  {"mut": "TERT-146-MUT",  "ic": "TERT-146-IC",  "ic_same_well": True},
    "FGFR3-372": {"mut": "FGFR3-372-MUT", "ic": "FGFR3-372-IC", "ic_same_well": True},
    "FGFR3-375": {"mut": "FGFR3-375-MUT", "ic": "FGFR3-375-IC", "ic_same_well": True},
    "FGFR3-248": {"mut": "FGFR3-248-MUT", "ic": "FGFR3-IC",     "ic_same_well": False},
    "FGFR3-249": {"mut": "FGFR3-249-MUT", "ic": "FGFR3-IC",     "ic_same_well": False},
}

CONTROL_SAMPLE_NAMES = {"PC", "NC", "NTC"}
MIN_TOTAL_EVENTS = 20000
MIN_IC_POSITIVES = 10
MIN_MUT_POSITIVES = 5
MIN_FRACTIONAL_ABUNDANCE = 0.5


def _normalize_by_ntc(df_long):
    """
    Subtract the NTC (or NC fallback) well's Positives from every other
    sample's Positives, per Target, within the same run (Source_File).
    Floors the result at 0. Adds a 'Positives_norm' column.
    """
    df = df_long.copy()
    df["Positives_norm"] = df["Positives"]

    for source_file, run_df in df.groupby("Source_File"):
        for target, target_df in run_df.groupby("Target"):
            ntc_rows = target_df[target_df["Sample"].str.upper() == "NTC"]
            if ntc_rows.empty:
                ntc_rows = target_df[target_df["Sample"].str.upper() == "NC"]

            if ntc_rows.empty:
                continue  # nothing to subtract

            baseline = ntc_rows["Positives"].iloc[0]
            if pd.isna(baseline):
                continue

            idx = target_df.index
            adjusted = (df.loc[idx, "Positives"] - baseline).clip(lower=0)
            df.loc[idx, "Positives_norm"] = adjusted

    return df


def _build_ic_lookup(df_norm, assay, ic_target):
    """
    Build a Sample -> (Positives_norm, Total, Source_File) lookup for the
    IC target of a given assay. For FGFR3-248/249 this IC ('FGFR3-IC') may
    appear in any uploaded file and is matched purely by Sample name.
    """
    ic_rows = df_norm[df_norm["Target"] == ic_target]
    lookup = {}
    for _, row in ic_rows.iterrows():
        # If the same sample/target combination appears more than once
        # (shouldn't normally happen) keep the first occurrence.
        if row["Sample"] not in lookup:
            lookup[row["Sample"]] = row
    return lookup


def _classify(total, ic_positives, mut_positives, fa):
    """
    Returns (accepted_events_valid, positive_events_valid, fa_ge_05, result)
    given already-normalized values. total/ic_positives/mut_positives/fa can
    be NaN if data is missing.
    """
    if pd.isna(ic_positives):
        return False, False, (fa is not None and not pd.isna(fa) and fa >= MIN_FRACTIONAL_ABUNDANCE), "Inconclusive"

    ic_valid = ic_positives >= MIN_IC_POSITIVES
    total_valid = (not pd.isna(total)) and (total >= MIN_TOTAL_EVENTS)

    fa_valid = (fa is not None) and (not pd.isna(fa)) and (fa >= MIN_FRACTIONAL_ABUNDANCE)
    mut_valid = (not pd.isna(mut_positives)) and (mut_positives >= MIN_MUT_POSITIVES)
    is_positive = mut_valid and fa_valid

    if not ic_valid:
        return total_valid, False, fa_valid, "Inconclusive"

    if not total_valid:
        # IC is valid, only the total-events threshold failed.
        suffix = " (<20k events)"
        return False, True, fa_valid, ("Positive" + suffix) if is_positive else ("Negative" + suffix)

    return True, True, fa_valid, ("Positive" if is_positive else "Negative")


def analyze(df_long):
    """
    Run the full business-logic pipeline on a tidy long-format DataFrame
    (as produced by formatter_AQ.format_files) and return the detailed
    per-(Sample, Assay) result DataFrame.
    """
    if df_long.empty:
        raise ValueError("No data rows to analyze.")

    df_norm = _normalize_by_ntc(df_long)

    # Pre-build IC lookups for the two "separate well" assays which share
    # a single FGFR3-IC pool across the whole uploaded dataset.
    shared_ic_lookup = _build_ic_lookup(df_norm, None, "FGFR3-IC")

    records = []

    for assay_name, cfg in ASSAYS.items():
        mut_rows = df_norm[df_norm["Target"] == cfg["mut"]]
        if mut_rows.empty:
            continue

        if cfg["ic_same_well"]:
            # IC lives in the same Source_File + Well as the mutant row;
            # easiest way to find it: same Sample + Source_File.
            ic_rows = df_norm[df_norm["Target"] == cfg["ic"]]
            ic_by_key = {(r["Source_File"], r["Sample"]): r for _, r in ic_rows.iterrows()}

        for _, mrow in mut_rows.iterrows():
            sample = mrow["Sample"]
            if sample.strip().upper() in CONTROL_SAMPLE_NAMES:
                continue  # controls excluded from results entirely

            if cfg["ic_same_well"]:
                ic_row = ic_by_key.get((mrow["Source_File"], sample))
            else:
                ic_row = shared_ic_lookup.get(sample)

            total = mrow["Total"]
            mut_pos_norm = mrow["Positives_norm"]
            ic_pos_norm = ic_row["Positives_norm"] if ic_row is not None else np.nan

            denom = (mut_pos_norm if not pd.isna(mut_pos_norm) else 0) + \
                    (ic_pos_norm if not pd.isna(ic_pos_norm) else 0)
            fa = (mut_pos_norm / denom * 100) if denom > 0 else np.nan

            accepted_valid, positive_ic_valid, fa_ge_05, result = _classify(
                total, ic_pos_norm, mut_pos_norm, fa
            )

            records.append({
                "Source_File": mrow["Source_File"],
                "Sample description 1": sample,
                "Assay": assay_name,
                "Target": cfg["mut"],
                "Conc (copies/\u00b5L)": mrow["Conc"],
                "Accepted Events": total,
                "Positives": mrow["Positives"],
                "Positives (NTC-normalized)": mut_pos_norm,
                "IC Target": cfg["ic"],
                "IC Positives (NTC-normalized)": ic_pos_norm,
                "Original Fractional Abundance": np.nan,  # see note below
                "Accepted Events Valid?": accepted_valid,
                "Positive Events Valid (IC\u226510)?": positive_ic_valid,
                "Fractional Abundance": fa,
                "Fractional Abundance \u2265 0.5?": fa_ge_05,
                "Result": result,
            })

    result_df = pd.DataFrame.from_records(records)
    return result_df


def run_analysis(input_path_or_dir, output_dir, filename=None):
    """
    Entry point used by the Shiny app.

    `input_path_or_dir` can be:
      - a single raw AbsoluteQ csv/xlsx file path, OR
      - a directory containing one or more raw csv/xlsx files (e.g. the
        concatenated set, including a separate FGFR3-IC run).

    Formats every input file, runs the full business-logic analysis, and
    writes a detailed intermediate Excel file to output_dir.

    Returns the output file path.
    """
    os.makedirs(output_dir, exist_ok=True)

    if os.path.isdir(input_path_or_dir):
        paths = [
            os.path.join(input_path_or_dir, f)
            for f in os.listdir(input_path_or_dir)
            if f.lower().endswith((".csv", ".xlsx", ".xls"))
        ]
    else:
        paths = [input_path_or_dir]

    if not paths:
        raise ValueError("No input files found to analyze.")

    df_long = format_files(paths)
    if df_long.empty:
        raise ValueError("No usable data rows found in the input file(s).")

    result_df = analyze(df_long)
    if result_df.empty:
        raise ValueError("No recognized assay targets found in the input file(s).")

    if filename is None:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"Resultados_Analise_{timestamp}.xlsx"

    output_file = os.path.join(output_dir, filename)
    result_df.to_excel(output_file, index=False)
    return output_file
