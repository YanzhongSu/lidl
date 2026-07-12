# Lidl receipt parser and CLI gotchas

## Current Lidl API response shape

Receipt detail responses from `https://www.lidl.co.uk/mre/api/v1/tickets/{id}` may be nested:

```json
{
  "ticket": {
    "id": "...",
    "date": "2026-06-23T09:22:46",
    "htmlPrintedReceipt": "<html>...</html>",
    "store": {
      "name": "Shepherd's Bush",
      "address": "...",
      "postalCode": "...",
      "locality": "..."
    }
  },
  "collectingModel": {"points": 12}
}
```

A robust parser should support both this nested shape and older/top-level shapes:

- `ticket.htmlPrintedReceipt` or `ticket.htmlReceipt`
- top-level `htmlPrintedReceipt` or `htmlReceipt`
- dates from `ticket.date`, then top-level `date`, then summary metadata
- store fields from `ticket.store`, then top-level `store`, then summary metadata

## Discount parsing pitfall

Do not parse arbitrary discount label text as a price. Labels like `£5 off £35 spend` must remain labels; a naive digit-stripping parser turns this into `535`.

Recommended pattern:

1. Parse only simple price strings (`-0.40`, `£5.99`, `5.25`) as amounts.
2. Treat non-price bold discount spans as labels.
3. Pair each discount label with the following amount span. If the same `data-promotion-id` repeats across line items, do **not** collapse by promotion id; each line may carry a different prorated discount amount.

## Weighted/continuation article rows

Lidl prints weighted products as two article spans with the same `data-art-id`: one line with the line total, then a continuation line such as `3.130 kg @ £0.79/kg`. Skip continuation rows whose visible text starts with whitespace, otherwise totals and item counts will double-count weighted items.

## Totals

Prefer the displayed `TOTAL` / payment amount when computed line totals differ materially. Use computed totals only when the displayed total is missing or the difference is within a small tolerance, because HTML article text can be truncated or wrapped.

## CLI argument order

The repo CLI defines global options on the root parser. Put auth/data options before the command:

```bash
python3 scripts/lidl_receipts.py --cookie-stdin all
python3 scripts/lidl_receipts.py --data-dir ~/data query --days 7
```

If a copied-cookie command errors with `unrecognized arguments: --cookie-stdin`, move `--cookie-stdin` before the subcommand.