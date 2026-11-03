# Code scanning

GitHub runs the code scanning analysis through the default setup of CodeQL.
The default setup owns the schedule, the language selection, and the runner.
The repository adds a configuration file that GitHub merges with the generated configuration.

The analysis covers two languages.
The Python database covers the application, the scripts, the hooks, the tests, and the CI adapters.
The actions database covers the workflow files.

## Configuration file

`.github/codeql/config.yml` holds the repository configuration.

- The `security-extended` query suite adds the lower severity and lower precision security queries to the queries the default setup already runs.
- The `paths` list names every directory that holds analyzed code.

The `paths` list keeps the analyzed scope explicit.
Code in a directory that the configuration omits stays unanalyzed.
A new source directory therefore needs a matching change to the configuration file.

`tests/test_codeql_configuration.py` checks the configuration against the repository.
The test reads the tracked files, because the tracked tree is the tree that CodeQL analyzes.
The test fails when a directory holds Python source and the `paths` list omits it.
The test fails when the configuration drops the extended query suite.
The test fails when the `paths` list holds a glob or a parent directory segment.

## Repository property

GitHub reads the configuration file when the `github-codeql-config-file` repository property names the path.
The property value is `remote=abuzucom/infiniteprep@main:.github/codeql/config.yml`.
The repository policy denies the agent every state-changing API request.
No GitHub CLI command sets a custom repository property.
The active human sets the property through the repository settings or the REST API.

The value names a remote address rather than a local path.
The remote form reads the file from the `main` branch through the API.
A local path reads the file from the checkout of the analyzed ref.
A local path therefore fails every analysis of a ref that predates the file.
That failure stops the analysis and reports a missing configuration file.
The remote form keeps the analysis working for every ref, including a pull request branch and a fork.

The `github-codeql-extra-queries` property offers the same query suite change without a file.
The configuration file carries more than the query suite. The file stays the single source.

## Verification

GitHub applies the configuration when the analysis next runs.
A pull request analysis reads the configuration from the `main` branch of the repository.
A default branch analysis reads the configuration from the `main` branch.
The configuration reaches a scan only after the file merges into `main`.

Confirm the change after the merge and the next scan.

- List the open alerts and compare the rule set with the previous scan.
- A rule that only the extended suite runs confirms that the configuration applies.
- The tool status page reports the percentage of files that the scan covered.

## Open options

The following changes need a separate decision.

- A ruleset can require the code scanning check before a pull request merges.
- A model pack can model the in-house audio container readers as taint sources.
  The Python analysis runs with the remote threat model. A file read stays outside the built-in source set.
  A model pack declares the readers that carry attacker-controlled bytes.
  The standard queries then follow those bytes to every sink.
- The advanced setup can replace the default setup.
  The advanced setup adds a workflow file, new action dependencies, and a hosted settings change.
  The advanced setup also consumes GitHub Actions minutes on every push and pull request.
