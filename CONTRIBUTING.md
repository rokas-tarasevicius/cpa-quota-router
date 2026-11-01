# Contributing

Thanks for helping. Bug reports, fixes, new CLIProxyAPI releases to test against, and clearer docs
are all welcome.

## Ground rules

These keep the router easy to trust and easy to install. A change that breaks one needs a good
reason, given in the pull request.

- **One file, standard library only.** Installing the router means copying `quota_router.py`. No
  dependencies, no build step, Python 3.9 or newer.
- **Stay out of the request path.** The router only writes `priority` through the management API.
  It never proxies, queues or retries requests, and never writes account files itself.
- **When in doubt, change nothing.** If a read fails or a response looks wrong, leave priorities
  where they are and log it. A stale ranking is harmless; a wrong one isn't.
- **Never log secrets.** Tokens and the management key stay out of logs; addresses are masked.

The reasons are in [Design decisions](docs/design-decisions.md).

## Setting up

```sh
git clone https://github.com/rokas-tarasevicius/cpa-quota-router.git
cd cpa-quota-router
python3 -m unittest -v
```

There is nothing to install. The unit tests need no network.

The end-to-end test runs a real CLIProxyAPI binary on localhost, with dummy accounts and a mock
Anthropic server. Download a release for your platform from
[CLIProxyAPI's releases](https://github.com/router-for-me/CLIProxyAPI/releases), then:

```sh
python3 e2e/run_e2e.py /path/to/cli-proxy-api
```

It picks free ports and temporary directories, so it doesn't touch a proxy you already run.

## Making a change

- **The ranking** lives in `plan()` and `tier()`. Add a case to `PlanTest` in
  `test_quota_router.py` for the behaviour you change, and update section 5 of
  [How it works](docs/how-it-works.md#5-how-it-ranks).
- **Usage parsing** lives in `parse_usage()`. If Anthropic's response changed, add the new shape,
  trimmed and with nothing personal in it, to `ParseTest`.
- **A new CLIProxyAPI release.** Run the end-to-end test against it, then add it to the `e2e`
  matrix in `.github/workflows/test.yml` with its two hashes from the release's `checksums.txt`.
- **Options.** A new flag goes in `parse_args()`, the options table in
  [Running it](docs/running-it.md#options), and the README if most people need it.
- **Docs** are plain Markdown in `docs/`. Short sentences, concrete examples, and `example.com`
  addresses in sample output.
- **Diagrams.** Each `docs/diagrams/*.diagram.json` is the source of the two SVGs beside it. See
  [docs/diagrams/README.md](docs/diagrams/README.md).

Add a line to the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md) for anything a user would
notice.

## Pull requests

- Keep each pull request to one change, and say what it does and why.
- `python3 -m unittest` must pass. CI also runs the end-to-end test against the pinned
  CLIProxyAPI releases.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `fix:`,
  `feat:`, `docs:`, `test:`, `ci:`.

## Reporting bugs

Open an issue with:

- the output of `quota_router.py --once --dry-run --verbose` (addresses are masked, but check it
  before you paste);
- your CLIProxyAPI version and Python version;
- what you expected the ranking to be, and why.

Never paste access tokens, refresh tokens, the management key or unmasked addresses. For anything
security-related, see [SECURITY.md](SECURITY.md) instead.
