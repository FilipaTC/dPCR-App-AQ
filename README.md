# ddPCR Analyzer AQ

Shiny (Python) app for analyzing AbsoluteQ digital PCR exports
(TERT-124, TERT-146, FGFR3-248, FGFR3-249, FGFR3-372, FGFR3-375 — 7
single-plex reactions, 6 mutations).

## Files

- `run_app_AQ.py` — entry point (run with `python run_app_AQ.py`, or
  `shiny run app/app_AQ.py` for local dev with autoreload).
- `app/formatter_AQ.py` — **Step 0**: parses/normalizes raw AbsoluteQ
  csv/xlsx exports (handles the channel header row, "Group" section
  rows, FAM/VIC duplicated columns for same-well Internal Controls, and
  drops unprocessed "ghost" wells) into one tidy long-format table.
- `app/analysis_pipeline_AQ.py` — **Step 1**: the business logic. NTC/NC
  normalization, validation thresholds, own Fractional Abundance
  calculation, and Positive/Negative/Inconclusive classification. Writes
  the detailed intermediate Excel file.
- `app/final_results_AQ.py` / `app/summary_pipeline_AQ.py` — **Step 2**:
  aggregates one or more detailed analysis files into a simplified,
  color-coded, wide-format summary (one row per sample) plus a full-detail
  export.
- `app/app_AQ.py` — the Shiny UI/server tying the three steps together.

## Business rules implemented

- **Internal Controls**: TERT-124/146 and FGFR3-372/375 carry their IC on
  the VIC channel of the *same well*. FGFR3-248 and FGFR3-249 share a
  single IC well (`FGFR3-IC`) that frequently runs in a *separate file*;
  it is matched back to the mutant wells purely by sample name, across
  every file given to the analysis step in one batch — **make sure to
  select the mutant file(s) AND the matching FGFR3-IC file together when
  running Step 1** if they come from a separate sequencing run.
- **NTC/NC normalization**: per assay/target, within the same run
  (source file), Positives are normalized against the well named `NTC`;
  if no `NTC` well exists, the `NC` well is used as a fallback instead.
  The subtraction is floored at 0 and applied to both the mutant and the
  IC target. All downstream thresholds and the Fractional Abundance use
  these normalized values.
- **Validation**: a sample/assay is only "valid" if Accepted Events
  (Total) ≥ 20,000 **and** normalized IC Positives ≥ 10.
  - If the IC itself is missing (e.g. no matching `FGFR3-IC` well was
    provided) → `Inconclusive`.
  - If IC Positives < 10 → `Inconclusive`.
  - If only the 20,000-event threshold fails (IC otherwise valid) →
    `Positive (<20k events)` / `Negative (<20k events)`.
- **Fractional Abundance** (independent of the instrument's own value,
  which is not present in the raw AbsoluteQ exports and is therefore left
  blank in the "Original Fractional Abundance" column):
  `FA = mut_positives_norm / (mut_positives_norm + ic_positives_norm) * 100`,
  only computed when the denominator > 0.
- **Positive call**: normalized mutant Positives ≥ 5 **and** FA ≥ 0.5%;
  otherwise Negative (within the validated population).
- Control wells (`PC`, `NC`, `NTC`) are excluded from every output
  report.

## Intermediate (Step 1) output columns

`Source_File, Sample description 1, Assay, Target, Conc (copies/µL),
Accepted Events, Positives, Positives (NTC-normalized), IC Target, IC
Positives (NTC-normalized), Original Fractional Abundance, Accepted
Events Valid?, Positive Events Valid (IC≥10)?, Fractional Abundance,
Fractional Abundance ≥ 0.5?, Result`

## Known gaps / things to double-check with real production data

- The 4 sample CSVs provided for development do not contain any
  `FGFR3-IC` well, so the FGFR3-248/249 logic was validated with
  synthetic data (see conversation) rather than the real shared-IC file;
  please confirm with a real paired run.
- The "Original Fractional Abundance" column (computed automatically by
  the AbsoluteQ system) does not exist as a field in the raw exports
  provided, so it is left blank — if your instrument software version
  does export it under a different column name, let me know and it can
  be wired in directly.
