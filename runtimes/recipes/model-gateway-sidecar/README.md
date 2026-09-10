# Model gateway sidecar

The sidecar has two network attachments: Docker bridge for the host-side
ModelGatewayProxy and the run-scoped `--internal` network for the Candidate.
It only forwards TCP bytes to the proxy port; it has no API key or provider
client. Formal builds must replace the default base tag with a verified digest.

Build the profile-compatible tag with an explicit base digest:

```bash
./build.sh --base-image-digest sha256:<verified-python-digest>
```
