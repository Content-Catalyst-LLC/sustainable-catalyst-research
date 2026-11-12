Synthetic sc.figure-bundle.v1 fixtures (no real research data):

- workspace-notebook.bundle.json — produced by Workspace PR #35 (SC-FIG-02B notebook figure package)
  from a synthetic regression run in its PostgreSQL integration test.
- lab-erw.bundle.json — Lab PR #7 synthetic ERW fixture (tests/fixtures/fig02b-vega-lite/erw.bundle.json).

The Workspace fixture's `project.environment` reads `production` only because Workspace's settings
default to it in the test process; the run and data are synthetic.
