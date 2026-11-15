# Project working guidance

- The user authorizes edits to any file within this project (`/Users/tyalevizos/Downloads/gensampledata2`) when carrying out requested work. Proceed with necessary implementation, documentation, packaging, and test changes without repeatedly asking for conversational permission. This authorization does not bypass the tool runtime's sandbox or native approval requirements.
- Deleting legacy code or original data requires the user's separate, explicit cleanup approval. Permission to edit files is not approval to perform that deferred cleanup.
- Preserve every original file in `support/`, including its complete contents, duplicates, ordering, and formatting. Never replace full data pools with subsets or discard data when retiring older code.
- Package all original support data and test its integrity against the source files. Respect the formats of line-oriented pools, wide CSV rows, and structured CSV records; commas inside company names or job titles are not field separators in line-oriented pools.
- Keep current application code in `src/sampledata/` and the workbench in `frontend/`. Use project-local `tmp/` for scratch files and `artifacts/` for generated output.
- Use `AGENTS.md` for durable guidance. Do not create Cursor-specific rules or directories.
