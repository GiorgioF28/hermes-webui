# Command Bridge repository status

The sidebar reads the current Git checkout. It does not infer deployment state from commit history or from unrelated local branches.

- `da committare`: tracked or untracked files remain outside a commit, including staged changes. The tooltip lists counts and up to 20 paths; the total always covers all files. Git-ignored files are excluded.
- `da pushare`: the checkout is clean and local commits are ahead of the tracking branch.
- `da aggiornare` / `da sincronizzare`: tracking refs indicate remote-only commits / divergence. No network fetch or merge runs automatically; these counts use the last fetched refs.
- `in pari`: no pending files or tracking divergence. Without an upstream this means saved locally, not backed up remotely. Other unmerged local branches remain visible separately in the tooltip.

Manual refresh and returning to the window force a new read. Visible tabs poll every 15 seconds with a shared 10-second server cache. The refresh tooltip records the last successful check time. Out-of-order responses cannot replace a newer snapshot; a failed check shows unavailable instead of retaining a misleading green badge.

State layer: only the read-only Git snapshot cache and browser presentation change. No commits, push, merge or deployment run during status checks. Local delegation prompts in `.tmp/deleghe/` are temporary input and ignored; other untracked files still count.

Verification: backend cache/force tests, a temporary real Git repository through edit/stage/commit, endpoint tests and an executable JavaScript harness for badges, refresh and response ordering. Visual fixtures cover desktop, narrow and mobile widths.
