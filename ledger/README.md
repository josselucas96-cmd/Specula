# The ledger now lives on the `ledger` branch

Since 2026-10-23 the nightly export of the track record (CSV files, SHA256SUMS and
OpenTimestamps proofs) is committed to the **`ledger` branch** of this repository:
https://github.com/josselucas96-cmd/Specula/tree/ledger/ledger

Why: the public app redeploys on every commit to `main`, so the twice-daily exports
restarted the site. The `ledger` branch was cut from `main` on 2026-10-23; its history
contains every export committed to `main` before that date, then the new ones.
Verification instructions: `ledger/README.md` on that branch.
