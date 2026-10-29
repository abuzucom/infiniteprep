# infiniteprep

## Pull request review

Every pull request receives two model reviews:

- `abuzucom/foucault` security review through the `security-review` check.
- `abuzucom/euler` code quality review through the `quality-review` check.

Both reviews run after the `ci` workflow completes. A human approval remains
required. See [`docs/pr-review.md`](docs/pr-review.md) for pins, settings, and
upgrades.

## Checks

```console
python3 -m unittest discover -s tests -v
```
