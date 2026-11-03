# shop-demo: test repo for GhostTrace AI

A tiny fake shop backend with deliberately planted hidden dependencies and a past-incident file.
The GhostTrace Action is bundled in `.ghosttrace/`, so this is the only repo you need.

## Setup
```
cd ghosttrace-test-repo
git init -b main
git add -A
git commit -m "initial"
# create an EMPTY repo on GitHub (e.g. ghosttrace-test), then:
git remote add origin https://github.com/YOUR-USERNAME/ghosttrace-test.git
git push -u origin main
```

## Scenario 1: HIGH risk (config change)
```
git checkout -b test-timeout
sed -i 's/PAYMENT_TIMEOUT = 30/PAYMENT_TIMEOUT = 90/' config/settings.py   # Mac: sed -i '' ...
git commit -am "Raise payment timeout"
git push -u origin test-timeout
```
Open a PR to `main`. Expect: the hidden `legacy/partner_sync.py` (env var, no import), the 6-hour
assumption in `jobs/nightly_reconcile.py`, the 2 AM crontab, incident INC-142, and missing tests.

## Scenario 2: database column rename
```
git checkout main && git checkout -b test-column
sed -i 's/customer_email/email_address/' db/schema.sql
git commit -am "Rename column"
git push -u origin test-column
```
Expect: `reports/weekly_report.sql` and `legacy/export_csv.py` (strings, not imports) and incident INC-207.

## Scenario 3: LOW risk (control)
```
git checkout main && git checkout -b test-safe
sed -i 's/Formats cents as a price string./Formats cents as a price label./' utils/format.py
git commit -am "Docstring tweak"
git push -u origin test-safe
```
Expect a low score and no scary findings. This proves it isn't crying wolf.

## Try it locally without GitHub
```
python .ghosttrace/ghosttrace.py analyze . --file config/settings.py --key PAYMENT_TIMEOUT
```
