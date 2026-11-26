# Policy security
The canonical policy is `AGENTS.md`. Supporting documents remain local to the
repository. The loader never fetches policy text from the network.

The loader rejects missing, malformed, non-ASCII, oversized, symlinked, and
special files. It rejects absolute paths and paths that escape the policy root.
It assembles deterministic output and fails closed.

Do not place secrets, credentials, tokens, private keys, or sensitive
vulnerability details in policy documents, examples, logs, handoffs, or
generated copies.

Secret categories include keys, tokens, passwords, private keys, and `.env`
files. The secret checker uses heuristics. It does not perform entropy
analysis or prove that a repository contains no secret.

Example cache use: `hashlib.md5(payload).hexdigest()` with a comment stating
that the digest is non-cryptographic.

A comment cannot convert a security-sensitive use into a use outside security.

For containers that run as root at runtime, prefer ports of 1024 or higher behind a reverse
proxy or port mapping. Prefer `COPY --chown` or build-time `chown`. Set
`user:` in Compose. Set `securityContext.runAsNonRoot: true` and `runAsUser`
in Kubernetes. After approval, add:

`# runtime-root: this container <reason> (Rule 12 exception).`

Flag unrelated findings of runtime root instead of fixing them under Rule 4.
`scripts/check_dockerfile_root.py` checks the rule.

Preserve license attribution and source metadata in every controlled
adoption and redistribution. Third-party mirrors remain responsible for their
own legal compliance. This repository cannot enforce or verify their local
practices.

Report vulnerabilities through the process in `SECURITY.md`. Do not
use public issues or pull requests for private vulnerability details.

The Claude file tool gate also denies protected file operations and broad
searches.

Injection examples:

- Bad: `cursor.execute(f"SELECT * FROM users WHERE name = '{name}'")`
- Good: `cursor.execute("SELECT * FROM users WHERE name = %s", (name,))`
- Bad: `subprocess.run(f"convert {filename} out.png", shell=True)`
- Good: `subprocess.run(["convert", filename, "out.png"])`

## Local identifier limits
Only the trusted CI scan blocks. CI sees only profile paths. Accounts with
placeholder names, double encoding, and homoglyphs pass.

## Denied command families
The denial covers AWS CLI, SAM, CDK, Azure CLI and PowerShell, Google Cloud
CLI, `gsutil`, `bq`, Terraform, OpenTofu, Terragrunt, Pulumi, Packer,
Kubernetes, Helm, Kustomize, OpenShift, Minikube, Kind, SSH clients, PuTTY,
FTP, TFTP, Telnet, iptables, nftables, UFW, firewalld, and Windows firewall
commands.

The Rule 15 Cloudflare Pages exception denies every other Wrangler operation,
including Workers, account, zone, DNS, WAF, Turnstile, KV, D1, R2, Queues,
Durable Objects, secret, configuration, inspection, and API operations.
Dashboard automation and infrastructure-as-code stay denied.

Protected content includes AWS, Azure, Google Cloud, SSH, Kubernetes,
Terraform, FTP, and Netrc credentials, Terraform source, variables, state,
locks and CLI configuration, plus Kubernetes, Helm, and Kustomize manifests
and project directories.
