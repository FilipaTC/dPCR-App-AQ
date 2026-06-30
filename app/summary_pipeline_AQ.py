"""
summary_pipeline_AQ.py
========================
Convenience wrapper around final_results_AQ.Resultados that scans a
directory for detailed-analysis Excel files (produced by
analysis_pipeline_AQ.run_analysis) and produces the two summary outputs:
  - a simplified, wide-format, color-coded summary
  - a full-information export
"""

import os
from final_results_AQ import Resultados


def run_summary(input_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    results = Resultados()
    pattern = os.path.join(input_dir, "*.xlsx")
    df = results.results_together(pattern)

    if df is None:
        raise RuntimeError("No analysis result files found.")

    selected_df = results.select_information(df)

    output_file = os.path.join(output_dir, "Selected_Results.xlsx")
    output_file_full = os.path.join(output_dir, "Results_full_information.xlsx")

    results.save_selected_information(selected_df, output_file)
    results.save_full_information(df, output_file_full)

    return output_file, output_file_full
