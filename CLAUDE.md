# HMS / DataPro — working notes for Claude

## Conventions

### No backward compatibility
This project is in **active experimentation**. Do **not** add backward-compatibility
shims, deprecation paths, dual-support code, or migration fallbacks when changing an
API or contract. Change the thing cleanly, update every caller in the same pass, and
delete the old shape. There are no external consumers to protect yet — a clean break
is always preferred over a compatible one.
