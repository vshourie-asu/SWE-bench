"""Change_Viz_Tooling package for the SWE-bench change visualization.

This package holds the data-generation pipeline components that build the
visualization of the agent ``gpt5_6_luna``'s code changes on the four target
SWE-bench Verified tasks:

  extract.py     Base_Commit_Extractor  -- dataset metadata + base commit per task
  clone.py       Repo_Cloner            -- cached local git clones at the base commit
  diffparse.py   unified-diff parser    -- per-file / per-line hunk model
  treebuild.py   Viz_Data_Builder       -- file tree + change annotations
  summarize.py   Summary_Generator      -- per-file natural-language summaries
  index.html     Viewer                 -- self-contained static HTML viewer

All modules are standard library plus ``datasets``, use UTF-8 ``pathlib`` I/O
with forward-slash paths, run on Windows/PowerShell in the ``trace-sims`` conda
environment, and never invoke Docker or commit secrets.
"""
