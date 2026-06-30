from shiny import App, ui, render, reactive
from shiny.ui import Progress
from formatter_AQ import run_concat
from analysis_pipeline_AQ import run_analysis
from summary_pipeline_AQ import run_summary
import tempfile
import shutil
import os
import pandas as pd

# ---------------- UI ----------------
app_ui = ui.page_fluid(
    ui.h2("ddPCR Analyzer AQ"),
    ui.p(
        "Workflow: 0) optionally concatenate/normalize raw AbsoluteQ exports "
        "into one tidy table; 1) run the individual analysis on one or more "
        "raw files (handles the shared FGFR3-IC across separate files "
        "automatically); 2) build the simplified, color-coded final summary."
    ),

    ui.h3("0\ufe0f\u20e3 Concatenate / format raw CSV files (optional)"),
    ui.input_file(
        "csv_files",
        "Select AbsoluteQ CSV/XLSX files",
        accept=[".csv", ".xlsx"],
        multiple=True
    ),
    ui.input_action_button("run_concat", "Concatenate & format"),
    ui.download_button("download_concat", "Download formatted table"),
    ui.hr(),

    ui.h3("1\ufe0f\u20e3 Individual Analysis"),
    ui.p(
        "Select one or more raw AbsoluteQ files for a single analysis batch. "
        "If FGFR3-248/249 and their shared FGFR3-IC well are in separate "
        "files, select them together here so the IC can be matched by "
        "sample name."
    ),
    ui.input_file(
        "file1",
        "Select CSV or Excel ddPCR file(s)",
        accept=[".csv", ".xlsx"],
        multiple=True
    ),
    ui.input_action_button("run_analysis", "Run analysis"),
    ui.download_button("download_analysis", "Download detailed result"),

    ui.hr(),

    ui.h3("2\ufe0f\u20e3 Final Summary"),
    ui.p("Aggregates every detailed-analysis result generated in step 1 above (within this session)."),
    ui.input_action_button("run_summary", "Run final summary"),
    ui.output_table("summary_table"),
    ui.download_button("download_summary", "Download final summary"),
    ui.download_button("download_full_summary", "Download full information summary")
)

# ---------------- SERVER ----------------
def server(input, output, session):

    # -------- Reactive values --------
    concat_file = reactive.Value(None)
    analysis_file = reactive.Value(None)
    summary_df = reactive.Value(None)
    summary_file = reactive.Value(None)
    full_summary_file = reactive.Value(None)

    # -------- Temporary directories --------
    tmpdir = tempfile.mkdtemp()
    analysis_dir = os.path.join(tmpdir, "analysis_results")
    os.makedirs(analysis_dir, exist_ok=True)
    print("\U0001F4C1 TMPDIR:", tmpdir)

    @session.on_ended
    def cleanup():
        shutil.rmtree(tmpdir, ignore_errors=True)
        print("\U0001F9F9 TMPDIR cleaned")

    # =========================================================
    # PART 0 — CONCATENATE / FORMAT CSV FILES
    # =========================================================
    @reactive.Effect
    @reactive.event(input.run_concat)
    def _run_concat():

        with Progress(min=0, max=3) as p:

            p.set(value=0, message="Concatenating CSV files", detail="Validating input files...")

            files = input.csv_files()
            if not files:
                ui.notification_show("\u274c No CSV files selected", type="error")
                concat_file.set(None)
                return

            p.set(value=1, detail="Copying files to temporary directory...")

            concat_input_dir = tempfile.mkdtemp(dir=tmpdir)
            for fileinfo in files:
                input_path = os.path.join(concat_input_dir, fileinfo["name"])
                shutil.copyfile(fileinfo["datapath"], input_path)

            try:
                p.set(value=2, detail="Running formatting/concatenation...")
                output_path = run_concat(input_dir=concat_input_dir, output_dir=tmpdir)
                concat_file.set(output_path)
                p.set(value=3, detail="Concatenation completed")
            except Exception as e:
                ui.notification_show(f"\u274c Error in concatenation: {e}", type="error", duration=10)
                concat_file.set(None)
                return

        ui.notification_show("\u2705 Concatenation completed successfully!", type="message")

    @output
    @render.download(filename=lambda: os.path.basename(concat_file() or "Concatenated.xlsx"))
    def download_concat():
        path = concat_file()
        return path if path and os.path.exists(path) else None

    # =========================================================
    # PART 1 — INDIVIDUAL ANALYSIS
    # =========================================================
    @reactive.Effect
    @reactive.event(input.run_analysis)
    def _run_analysis():

        with Progress(min=0, max=3) as p:

            p.set(value=0, message="Individual analysis", detail="Validating input file(s)...")

            files = input.file1()
            if not files:
                ui.notification_show("\u274c No file selected", type="error")
                analysis_file.set(None)
                return

            p.set(value=1, detail="Copying file(s) to temporary directory...")

            batch_dir = tempfile.mkdtemp(dir=tmpdir)
            for fileinfo in files:
                input_path = os.path.join(batch_dir, fileinfo["name"])
                shutil.copyfile(fileinfo["datapath"], input_path)

            try:
                p.set(value=2, detail="Running ddPCR analysis...")

                # write each analysis batch straight into analysis_dir so
                # the summary step (part 2) can pick up every batch run
                # so far in this session
                output_filename = f"Resultados_{len(os.listdir(analysis_dir))}.xlsx"
                output_path = run_analysis(
                    input_path_or_dir=batch_dir,
                    output_dir=analysis_dir,
                    filename=output_filename,
                )

                analysis_file.set(output_path)
                p.set(value=3, detail="Analysis completed")

            except Exception as e:
                ui.notification_show(f"\u274c Error in analysis: {e}", type="error", duration=10)
                analysis_file.set(None)
                return

        ui.notification_show("\u2705 Individual analysis completed successfully!", type="message")

    @output
    @render.download(filename=lambda: os.path.basename(analysis_file() or "Analysis_result.xlsx"))
    def download_analysis():
        path = analysis_file()
        return path if path and os.path.exists(path) else None

    # =========================================================
    # PART 2 — FINAL SUMMARY
    # =========================================================
    @reactive.Effect
    @reactive.event(input.run_summary)
    def _run_summary():

        with Progress(min=0, max=3) as p:

            p.set(value=0, message="Final summary", detail="Scanning analysis results...")

            try:
                p.set(value=1, detail="Running summary pipeline...")

                selected_path, full_path = run_summary(
                    input_dir=analysis_dir,
                    output_dir=tmpdir
                )

                p.set(value=2, detail="Loading summary table...")

                summary_file.set(selected_path)
                summary_df.set(pd.read_excel(selected_path))
                full_summary_file.set(full_path)

                p.set(value=3, detail="Summary completed")

            except Exception as e:
                ui.notification_show(f"\u274c Error creating summary: {e}", type="error", duration=10)
                summary_file.set(None)
                summary_df.set(None)
                full_summary_file.set(None)
                return

        ui.notification_show("\u2705 Final summary created successfully!", type="message")

    @output
    @render.table
    def summary_table():
        df = summary_df()
        return df if df is not None else pd.DataFrame()

    @output
    @render.download(filename=lambda: os.path.basename(summary_file() or "Summary.xlsx"))
    def download_summary():
        path = summary_file()
        return path if path and os.path.exists(path) else None

    @output
    @render.download(filename=lambda: os.path.basename(full_summary_file() or "Full_Summary.xlsx"))
    def download_full_summary():
        path = full_summary_file()
        return path if path and os.path.exists(path) else None


# ---------------- APP ----------------
app = App(app_ui, server)
