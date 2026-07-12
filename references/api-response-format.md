# Lidl UK Receipt API — Response Format

## Detail endpoint

```
GET https://www.lidl.co.uk/mre/api/v1/tickets/{ticket_id}?country=GB&languageCode=en-GB
```

## Top-level keys

| Key | Type | Description |
|---|---|---|
| `type` | string | Always `"TICKET"` |
| `ticket` | object | The receipt container (see below) |
| `returnTickets` | array | Return/reversal tickets |
| `coupons` | array | Coupons applied |
| `languageCode` | string | e.g. `"en-GB"` |
| `logoUrl` | string | Lidl logo image URL |
| `showCopy` | bool | Whether "customer copy" flag is shown |
| `collectingModel` | string | e.g. `"DELIVERY"` or `"PICKUP"` |

## `ticket` object

| Key | Type | Description |
|---|---|---|
| `id` | string | Receipt ID (e.g. `130017517720260623337764`) |
| `date` | string | ISO 8601 datetime (e.g. `2026-06-23T20:15:30`) — **reliable, use this** |
| `totalAmount` | number | **Always `0`** — do not use; parse total from HTML instead |
| `storeNumber` | string | Store identifier |
| `store` | object | `{ id: string|null, name: string, address: string, postalCode: string, locality: string }` |
| `sequenceNumber` | string | Transaction sequence |
| `workstation` | string | Checkout workstation ID |
| `isDeleted` | bool | Whether the receipt is voided |
| `codes` | array | Barcode data for return info |
| `htmlPrintedReceipt` | string | **Full HTML receipt** — this is what gets parsed |

## Store field format

```json
"store": {
  "id": null,
  "name": "Shepherd's Bush",
  "address": "Shepherd's Bush Green",
  "postalCode": "W12 8PP",
  "locality": "Shepherd's Bush"
}
```

The `name` field from `ticket.store` is more reliable than the HTML `header_line_1` text (which can be `"Shepherds Bush"`, `"LON-Hammersmith"`, or empty on some receipts). Future improvements: populate `store_name` from `ticket.store.name` when HTML extraction fails.

## HTML receipt structure

The HTML is a `<pre>` layout with `<span>` elements. Key sections:

```
<span class="header">
  <span id="header_line_1">Store Name</span>
  ...

<span class="purchase_list">
  <span id="purchase_list_line_1" class="currency">...</span>
  <span id="purchase_list_line_2" class="article" data-art-id="..." ...>...</span>
  <span id="purchase_list_line_3" class="discount" data-promotion-id="..." ...>...</span>
  ...

<span class="purchase_summary">
  <span id="purchase_summary_1">...</span>
  <span id="purchase_summary_2" class="css_bold">TOTAL ... 28.73</span>
  <span id="purchase_summary_3" data-tender-description="CARD">CARD 28.73</span>

<span class="purchase_tender_information">
  <span id="purchase_tender_information_3">Date: 23/06/26 Time: 20:15:30</span>
  <span id="purchase_tender_information_6">AMEX CREDIT ***********0615</span>
  ...

<span class="vat_info">
  <span id="vat_info_line_2" data-tax-type="A" data-tax-percentage="0"
        data-tax-base-amount="28.73" data-tax-amount="0.00">...</span>
```

## Discount format

Each discount is a `<span class="discount">` group sharing the same `purchase_list_line_N` id. Within each group:

1. One `<span class="discount">` with whitespace only (opener)
2. One `<span class="discount css_bold">` with the promotion name (e.g. `"   £5 off £35 spend"`)
3. Several `<span class="discount">` with whitespace (padding)
4. One `<span class="discount css_bold">` with the amount (e.g. `"-1.23"`)

**Critical**: Group by `purchase_list_line` id, NOT by `data-promotion-id`. The same promotion id appears on every qualifying line item (e.g. "£5 off £35 spend" applied to 8 different items, each with its own line-level prorated discount).

## Summary endpoint

```
GET https://www.lidl.co.uk/mre/api/v1/tickets?country=GB&page={page}
```

Returns paginated JSON with `items[]`, `totalCount`, `size`, and `page`.
The summaries contain only header info (id, date, totalAmount).
