# Independent predeployment review

Initial snapshot 2026-09-18; parent implementing fixes concurrently. This is an
interim review, not production approval or a completed backend security scan.

## Confirmed findings

1. **High correctness: silent DOCX omissions.** At initial doc_engine.py:189-224,
   only direct paragraphs/tables are enumerated and table cells use cell.text.
   Body content controls and nested table text disappear while incomplete=false
   and next_start=null. Owner may then rely on an apparently complete extraction
   missing a material clause or amount. Synthetic regressions in
   test_extraction_integrity.py reproduce both cases. Recursively preserve text
   or explicitly mark unhandled containers incomplete with a rendering route.

2. **High correctness: stale XLSX dimensions hide actual cells.** At initial
   doc_engine.py:227-255 read-only sheet.max_row/max_column are trusted. A workbook
   with D8 containing a marker but dimension=A1:A1 returns only row 1, declaring
   completion. This is reachable through generated or imported spreadsheets.
   Derive bounds from actual cell records for both formula/value readers; regression
   is test_xlsx_stale_dimensions_do_not_hide_real_cells.

3. **Medium security: active formulas survive templates; IMAGE bypasses guard.**
   At _set_cell/create_xlsx, only explicitly changed cells are checked. A template
   with =WEBSERVICE(A1) survives creating a new workbook. Explicit =IMAGE(A1) also
   passes the denylist. Spreadsheet output can retain external-content behavior
   when the owner opens it in a capable client. Reproduction only inspects resulting
   formula strings, never executes them. Reject unsafe retained template formulas
   and cover indirect external-content functions. Tests: test_active_formulas.py.

## Confirmed checks and limits

Independent run of tests/test_auth_provider.py, test_nextcloud.py, test_server.py,
test_jobs.py: **34 passed**, one SDK annotation deprecation warning. Exact owner
matching, redirect allowlist, reference-token swapping, encrypted storage, path
traversal rejection, create-only PUT and readback checksum checks pass mocked tests.
No exposed delete/overwrite tool was found. Generic worker isolation concern was
sent to parent; parent reports full bwrap wrapper is being implemented.

Not validated: live Nextcloud OAuth exchange or ChatGPT client compatibility,
production service and tunnel/Caddy configuration (not yet supplied), arbitrary
native-parser exploit resistance, host filesystem alias mapping. Review fixtures
are synthetic and safe to run using `.venv/bin/python -m pytest -q docs/review`.

## Fix verification and deployment follow-up

The implementation owner repaired all three initial findings during this review.
Independent rerun of the five review regressions plus tests/test_jobs.py: **7
passed**. Updated DOCX recursion, XLSX actual bounds, retained-template and defined
name formula checks were inspected. Initial findings above are resolved for the
reproduced cases; they remain in this report as an audit trail.

Reviewed deployment/ service units, Caddy block and rollback: dedicated service
UID, loopback-only service/tunnel, strict host-key checking, private temp/home and
restricted writable state are appropriate. Parent specified a key restricted to
the single remote loopback listening port and disabled shell command; actual edge
authorized_keys was not read. New jobs.py bwrap wrapper exposes only system runtime,
three scripts, virtualenv, current job, private /tmp and isolated network/PID
namespaces. OAuth configuration and grant directory are not mounted.

The initial unbounded main-process download was also repaired. Nextcloud.read now
checks Content-Length and cumulative streamed bytes against a 256 MiB technical
input envelope, returning an explicit error rather than partial document success.
Independently verified the absent-Content-Length chunked-response path with a
synthetic stream. Worker execution is serialized by a semaphore while submissions
remain accepted; the worker keeps a 3 GiB address-space boundary. Service cgroup
settings are MemoryHigh=3G and MemoryMax=4G; Caddy limits request bodies to 32 MiB.
These bound this deployment's resource use and isolate host impact. Very large
files require splitting; concurrent downloads can still pressure the service
within its cgroup. No destructive exhaustion test was performed.

Final targeted verification after resource changes: tests/test_nextcloud.py,
tests/test_jobs.py and all independent docs/review tests: **20 passed**.

**Review status: ready for live smoke testing.** No reproduced code blocker remains
open in this bounded review. This status is not a production or ChatGPT acceptance
claim, nor a substitute for the parent's complete test run.

Follow-up sandbox compatibility review: Office conversion may reuse the existing
outer worker namespace only with the Jobs-controlled environment marker plus
read-only /app code mounts, expected cwd/HOME and current-job request file. Tool
options and document bytes cannot set that environment marker. The launcher still
unshares filesystem/network/PID namespaces and exposes no OAuth state; standalone
conversion still requires bwrap. This adjustment does not weaken the reviewed
host/credential isolation boundary. Independently ran the marker-forgery rejection,
missing-bwrap rejection and standalone Office rendering tests: **3 passed**.
Parent reports exact-service-UID Office conversion smoke passed; that live result
is parent evidence, not an independently executed check by this reviewer.

Production acceptance still needs actual dedicated-UID/systemd nested bwrap and
Office conversion smoke, auth-negative public routes and a real ChatGPT OAuth
round trip. These are not established by the passing local mocked tests.
