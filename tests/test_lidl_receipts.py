import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "lidl_receipts.py"
spec = importlib.util.spec_from_file_location("lidl_receipts", SCRIPT)
assert spec is not None
lidl_receipts = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(lidl_receipts)


# Realistic receipt HTML: two articles (apples + potatoes), one discount
# (£5 off £35 spend -0.40), weight continuation row (same art-id, no
# data-unit-price so parser skips it), CARD payment ending 0615, total 5.25.
RECEIPT_HTML = """<html><body><pre>
<span class="header" data-till-country="GB"><span id="header_line_1">             Shepherds Bush             </span></span>
<span class="purchase_list"><span id="purchase_list_line_1" class="currency" data-currency="\u00a3">                                      \u00a3</span>
<span id="purchase_list_line_2" class="article" data-art-id="0080218" data-art-quantity="2" data-unit-price="1.59" data-tax-type="A" data-art-description="Red Gala Apples 0080218">Red Gala Apples 00802 x &pound;1.59       3.18</span>
<span id="purchase_list_line_3" class="discount" data-promotion-id="promo-1">  </span><span id="purchase_list_line_3" class="discount css_bold" data-promotion-id="promo-1">   &pound;5 off &pound;35 spend</span><span id="purchase_list_line_3" class="discount" data-promotion-id="promo-1">     </span><span id="purchase_list_line_3" class="discount css_bold" data-promotion-id="promo-1">-0.40</span>
<span id="purchase_list_line_4" class="article" data-art-id="0083255" data-art-quantity="3.13" data-unit-price="0.79" data-tax-type="A" data-art-description="Loose Baking Potat. 0083255">Loose Baking Potat. 0083255        2.47 A</span>
<span id="purchase_list_line_5" class="article" data-art-id="0083255" data-tax-type="A" data-art-description="Loose Baking Potat. 0083255">  3.130 kg @ &pound; 0.79/kg      </span>
</span><span class="purchase_summary"><span id="purchase_summary_1">-----------------------------------------</span>
<span id="purchase_summary_2" class="css_bold">TOTAL</span><span id="purchase_summary_2">                 </span><span id="purchase_summary_2">       </span><span id="purchase_summary_2">     </span><span id="purchase_summary_2" class="css_bold">5.25</span>
<span id="purchase_summary_3" data-tender-description="CARD">CARD                               5.25</span>
</span><span class="purchase_tender_information"><span id="purchase_tender_information_3">Date: 23/06/26            Time: 09:22:46</span>
<span id="purchase_tender_information_6">AMEX CREDIT              ***********0615</span></span>
<span class="vat_info"><span id="vat_info_line_2" data-tax-type="A" data-tax-percentage="0" data-tax-base-amount="5.25" data-tax-amount="0.00">A    0 %             5.25          0.00</span></span>
</pre></body></html>"""


class LidlReceiptParserTests(unittest.TestCase):
    def test_parse_html_receipt_handles_discount_labels_and_weight_rows(self):
        parsed = lidl_receipts.parse_html_receipt(RECEIPT_HTML)

        # Two articles (weight continuation row with same art-id and no
        # data-unit-price is correctly skipped)
        self.assertEqual(len(parsed["articles"]), 2)
        self.assertEqual(len(parsed["discounts"]), 1)
        self.assertEqual(parsed["total_displayed"], 5.25)
        self.assertEqual(parsed["payment_method"], "CARD")
        self.assertEqual(parsed["card_last4"], "0615")
        self.assertAlmostEqual(parsed["vat_breakdown"][0]["base_amount"], 5.25)

        # Article 1: Red Gala Apples
        self.assertEqual(parsed["articles"][0]["name"], "Red Gala Apples")
        self.assertEqual(parsed["articles"][0]["quantity"], 2.0)
        self.assertEqual(parsed["articles"][0]["price"], 3.18)

        # Article 2: Loose Baking Potat. (weight continuation skipped)
        self.assertEqual(parsed["articles"][1]["name"], "Loose Baking Potat.")
        self.assertEqual(parsed["articles"][1]["price"], 2.47)

        # Discount: £5 off £35 spend
        self.assertEqual(parsed["discounts"][0]["name"], "£5 off £35 spend")
        self.assertEqual(parsed["discounts"][0]["amount"], -0.40)

    def test_parse_details_accepts_nested_ticket_api_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "receipts").mkdir()
            (data_dir / "receipts_summaries.json").write_text(
                json.dumps({"items": [{"id": "nested-1", "date": "2026-06-23T09:22:46"}]}),
                encoding="utf-8",
            )
            (data_dir / "receipts" / "nested-1.json").write_text(
                json.dumps(
                    {
                        "ticket": {
                            "date": "2026-06-23T09:22:46",
                            "htmlPrintedReceipt": RECEIPT_HTML,
                            "store": {
                                "name": "Shepherd's Bush",
                                "address": "Shepherd's Bush Green",
                                "postalCode": "W12 8PP",
                                "locality": "Shepherd's Bush",
                            },
                        },
                        "collectingModel": {"points": 12},
                    }
                ),
                encoding="utf-8",
            )

            lidl_receipts.parse_details(SimpleNamespace(data_dir=data_dir))
            output = json.loads((data_dir / "receipts_detail.json").read_text(encoding="utf-8"))

        self.assertEqual(output["total_receipts"], 1)
        receipt = output["receipts"][0]
        self.assertEqual(receipt["id"], "nested-1")
        # Store name is extracted from the receipt HTML, not the API JSON
        self.assertEqual(receipt["store_name"], "Shepherds Bush")
        self.assertEqual(receipt["total_displayed"], 5.25)
        self.assertEqual(len(receipt["articles"]), 2)
        self.assertEqual(len(receipt["discounts"]), 1)

    def test_parse_details_accepts_top_level_legacy_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "receipts").mkdir()
            (data_dir / "receipts_summaries.json").write_text(
                json.dumps({"items": [{"id": "legacy-1", "date": "2026-06-23T09:22:46"}]}),
                encoding="utf-8",
            )
            (data_dir / "receipts" / "legacy-1.json").write_text(
                json.dumps(
                    {
                        "date": "2026-06-23T09:22:46",
                        "htmlPrintedReceipt": RECEIPT_HTML,
                        "store": {"name": "Legacy Store"},
                        "totalAmount": 5.25,
                    }
                ),
                encoding="utf-8",
            )

            lidl_receipts.parse_details(SimpleNamespace(data_dir=data_dir))
            output = json.loads((data_dir / "receipts_detail.json").read_text(encoding="utf-8"))

        self.assertEqual(output["total_receipts"], 1)
        receipt = output["receipts"][0]
        # Store name always comes from receipt HTML, not API JSON
        self.assertEqual(receipt["store_name"], "Shepherds Bush")
        self.assertEqual(receipt["total_displayed"], 5.25)


if __name__ == "__main__":
    unittest.main()
