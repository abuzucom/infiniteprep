# Security Policy

## Supported Versions

| Build | Supported |
|-------|-----------|
| Latest `main` branch build | Yes |
| Latest tagged release | Yes |
| Pre-releases | No |
| Earlier tagged releases | No |

The supported builds receive security fixes.

## Scope

Scope includes:

- Code in this repository.
- Configuration in this repository.
- Third-party defects caused by a selected version, a selected pin,
  configuration, permissions, or an integration.

Purely upstream defects route to the upstream maintainers.

Scope excludes social engineering and physical attacks.

## Application security

InfinitePrep reads and writes user-selected media and metadata. The surfaces
below handle input from outside the application.

- Audio and tag file parsing. Handles user-selected media and metadata.
- Tag writing to disk. Handles library files and exported media.
- Network requests to MusicBrainz, Cover Art Archive, iTunes Search,
  AcoustID, and Discogs. Handle query metadata.
- Local LLM endpoint. Handles user prompts and library metadata.
- Update downloads from GitHub releases. Handle executable artifacts.
- The `fpcalc` bootstrap. Handles a downloaded binary.

The AcoustID key and the Discogs token live in the OS credential store.

Reporters must exclude credentials, tokens, and personal data from report
attachments.

## Reporting a Vulnerability

Report vulnerabilities through GitHub private vulnerability reporting. Open
the Security tab. Select Report a vulnerability. Never open a public issue for
a vulnerability report.

Response timing: 7 days.

Remediation target: 90 days.

## Disclosure Policy

Coordinate disclosure with the maintainers. Keep the report private before a
disclosure event. A shipped fix defines one disclosure event. Passage of
90 days defines another disclosure event. The earlier event applies.
