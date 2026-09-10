# Test capability map

This map records the boundary after retiring the historical evaluator. Generic
coverage remains runnable; tests that asserted removed APIs are retained in the
pre-slim archive for historical replay.

| Former test capability | Current retained coverage | Historical-only portion |
| --- | --- | --- |
| `test_mvp_runner.py` public export, sealing, symlink/hardlink checks, compute-request validation | `tests/test_mvp_runner.py` (including pilot snapshot/resume/interruption budget behavior) | None; pilot tests use the local synthetic case fixture, not archived case 001 |
| Dockerfile `eval.apply_dockerfile_copies` and `eval.load_task` layouts | `tests/test_workspace_isolation.py` packager allowlist, drift, hidden-asset and credential boundary tests | Former G0.1–G0.4 Dockerfile/eval tests in `archive/20260911-pre-slim/retired-tree/tests/current-api-removed/` |
| transport EventStore/model_transport tests | Active packager credential-name test; receipt/evidence safety tests under `tests/evidence/` | Tests requiring deleted evaluator transport modules remain in the archive |
| dispatcher/coordinator/registry composition against removed core modules | `tests/hpc/test_dispatcher_facade.py`, `test_dispatcher_settlement.py`, `test_dispatcher_faults.py`, `test_gateway_lifecycle_events.py`, plus pilot cross-phase budget tests cover submit/logs/cancel/revoke/idempotency, lifecycle, fault handling and budget behavior | Former coordinator/registry source-scan and queue-wait tests remain historical-only |

No retired test is marked skipped to manufacture coverage. The archive is the
complete source for historical replay; the active suite tests only interfaces
that exist in the slim infra tree.
