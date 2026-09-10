# Trust boundary

Candidate and submission are untrusted. The host runner packages only the case's explicit public allowlist and starts a fresh Candidate container. Private verifier, reference, solution, host credentials and Docker socket are never mounted into Candidate.

The model Gateway exposes a run-scoped connection; the upstream credential remains on the host. Candidate writes compute requests for the operator. SSH, cloud management and scheduler credentials remain operator-side.

After Candidate teardown, the runner quarantines and seals final submission files. Symlinks, hardlinks, special nodes, path traversal and excess sizes are rejected. A fresh Verifier container uses network-none, a read-only root filesystem and read-only submission/private-data mounts.

Content digests, signed admission and receipts detect inconsistent identities or changed bytes. They do not prove scientific correctness. The host operator and verifier author are trusted; a hostile host administrator is outside this isolation boundary. A public benchmark reference already published elsewhere can still contaminate a model's knowledge.
