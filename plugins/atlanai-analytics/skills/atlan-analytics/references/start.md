# Start with your existing context

For `start`, `setup`, `query-history` or an ordinary setup request, follow
[shared setup](setup.md). A supplied URL, folder, file or question is already an input;
do not ask the person to supply it again. With no source and no saved run, offer the demo
or the person's own data first ([start choice](setup/demo.md)); for own data, ask for a
dashboard link, local folder or BI export, or use the already selected source.

The workflow surveys the available evidence, combines relevant models and query history,
previews metrics and saved SQL, publishes the reviewed context, and suggests questions in
this harness. It waits for the person's question unless one was already supplied.

Explicit `metrics`, `dashboard`, `build`, `eval` and `improve` retain their documented routes
in the main skill. Setup creates no separate agent, eval dataset or chat application.
