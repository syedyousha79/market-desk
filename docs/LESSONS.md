# Lessons

What went wrong while building Market Desk, why, the fix, and the rule for next time.
Newest at the bottom. Add an entry whenever something breaks or a test catches a real bug.

| # | What happened | Root cause | Fix | Rule |
|---|---|---|---|---|
| 1 | The borrowed day-trader-skill pack needed changes in every file | Built for the US: New York clock, US holidays, $ and US tickers in each script | Wrote a lean NSE-native desk reusing only its design | Check a borrowed codebase's assumptions before adapting it |
| 2 | TATAMOTORS returned 404 | Tata Motors demerged in 2025 (now TMPV and TMCV) | Watchlist uses TMPV; scans report skipped symbols | Validate every symbol against live data; corporate actions rename tickers |
| 3 | "Infosys" matched "Slone Infosystems" | Substring matching | NSE: exact legal name; headlines: whole-word, case-sensitive acronyms | Never match company names by substring |
| 4 | "Tata Motors CV" counted as TMPV | Bare "Tata Motors" is ambiguous after the demerger | Aliases name PV and CV explicitly | Re-check aliases after corporate actions |
| 5 | Second NSE filing of the day vanished | De-duplicated by title, and NSE titles are just the company name | NSE de-duplicates by link | Pick the duplicate key per source |
| 6 | A plan edited after the risk check still showed READY | Compared a fingerprint stored in the plan instead of recomputing it | Fingerprint recomputed at check and at use | Recompute integrity checks at use time |
| 7 | Printing ₹ crashed the script | Windows console cp1252 | stdout forced to UTF-8 in common.py | Force UTF-8 on Windows |
| 8 | Scripts could not import common under `python -I` | Isolated mode drops the script folder from the path | Run our own scripts without -I | -I is for untrusted code only |
| 9 | Python could not reach Telegram on this PC | Certificate check fails (likely antivirus HTTPS scanning) | Falls back to curl, which uses the Windows store | Keep a transport fallback for TLS-intercepting PCs |
| 10 | Volume filter favoured the busy open and close | Compared each bar with the whole-day average | Same time-of-day average from previous sessions | Compare like with like |
| 11 | A first live test sent about 10 Telegram alerts at once | The "already seen" state was empty, so the day's backlog went out | Pause switch added (`telegram_paused`) | Seed seen-state before the first live run; keep a pause switch |
| 12 | Moneycontrol feeds returned 2024 news | Their RSS stopped updating | Dropped; feeds checked for fresh dates | Check feed freshness, not just that it responds |
| 13 | A test could have sent a real Telegram message | Tests read the real .env | Tests stub load_env; Telegram refuses in offline/test runs | Tests never touch real secrets |
| 14 | A damaged journal crashed the risk check | No validation when reading the CSV | Journal validated; damage raises an error and the trade is blocked | Fail closed on damaged state |
| 15 | "yes buy 100" was accepted for a 14-share plan | The yes-check ignored numbers | A number in the reply must equal the planned quantity | Approvals must match the exact order |
| 16 | INFY exited at 15:25 instead of 15:15 | Yahoo was missing the 15:15 and 15:20 bars that day | A feed gap past flat-by exits at the last price at 15:15 | Exits must not depend on a bar existing |
| 18 | 20-session replay: +₹44.68 net, but charges were ₹47.13, about half the gross profit | Tiny positions (1-9 shares) pay near-fixed costs on every round trip | Results always shown after charges; risk gate requires net reward:risk ≥ 1 | At small size, charges decide the result; always judge net, never gross |
| 17 | One replay week looked like a result | 5 trades is noise | 20-session replay; judge on larger samples | Never trust a strategy on a week of data |
