# HMS Raw Dataset Cache

Raw downloads collected on 2026-05-17. Files are intentionally left in their source formats.

## Size

Total footprint after decompression: about 49 GB, well below the 100 GB cap.

## Contents

### SEC EDGAR

Directory: `sec-edgar/`

- `company_tickers.json`
- `companyfacts.zip`
- `submissions.zip`
- `2026q1-financial-statement-data-sets.zip`
- `2026q1-form345-insider-transactions.zip`
- `2025dec-2026jan-feb-form13f.zip`

Decompressed copies are under `sec-edgar/extracted/`, one directory per archive.

### Binance Public Data

Directory: `binance/`

- April 2026 spot monthly 1-minute klines for major `USDT` symbols.
- April 2026 spot monthly trades and aggregate trades for `BTCUSDT` and `ETHUSDT`.
- April 2026 USD-M futures funding-rate and 1-minute kline slices for `BTCUSDT` and `ETHUSDT`.
- Binance checksum sidecar files were downloaded where available.

Decompressed copies are under `binance/extracted/`, mirroring the raw archive tree.

### GDELT

Directory: `gdelt/`

- Raw `gdeltv2-masterfilelist.txt`.
- Raw `gdeltv2-lastupdate.txt`.
- Latest 24 hours of GDELT v2 `export`, `mentions`, and `gkg` ZIP files available at download time.

Decompressed copies are under `gdelt/extracted/latest-24h/`.

### Stooq

Directory: `stooq/`

Direct automated downloads now require an API key obtained through a CAPTCHA flow. The failed direct-download response is preserved as `stooq-direct-download-requires-apikey.txt`.

## Skipped Sources

- Alpha Vantage, Financial Modeling Prep, Polygon, NASDAQ Data Link: require API keys for useful bulk access.
- Kaggle financial datasets and WRDS samples: require account/login flows.
- Pushshift Reddit: not downloaded in this pass because current public availability and bulk access are inconsistent, and the full corpus can be very large.
- ETF Database and Kaiko research exports: no stable no-key bulk endpoint was used in this pass.

## Verification

All downloaded ZIP files passed `unzip -tq` before decompression.

Archives were decompressed in place into source-specific `extracted/` directories. Original raw archives were retained.

Generated inventory: `manifests/file-inventory.txt`.
