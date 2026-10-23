import json
import re
import subprocess
from pathlib import Path

import pytest

from app.services.fees import (
    FEE_SCHEDULE,
    batch_totals,
    best_platform,
    net_profit,
    platform_fee,
)

INDEX_HTML = Path(__file__).resolve().parents[2] / "index.html"


@pytest.mark.parametrize(
    "platform, price, fee",
    [
        ("ebay", 10.00, 1.66),  # 13.6% + $0.30 at the $10 boundary
        ("ebay", 10.01, 1.76),  # 13.6% + $0.40 just over
        ("ebay", 50.00, 7.20),
        ("poshmark", 14.99, 2.95),
        ("poshmark", 15.00, 3.00),
        ("poshmark", 40.00, 8.00),
        ("mercari", 30.00, 4.17),
    ],
)
def test_platform_fee(platform, price, fee):
    assert platform_fee(platform, price) == fee


def test_zero_price_has_no_fee():
    assert platform_fee("ebay", 0) == 0.0


def test_net_profit_subtracts_fee_shipping_and_cost():
    r = net_profit("ebay", 50.00, cost_paid=8.00, shipping=6.00)
    assert r["fee"] == 7.20
    assert r["net"] == 28.80
    assert r["margin_pct"] == 57.6


def test_net_profit_uses_default_shipping_when_unset():
    assert net_profit("poshmark", 40.00)["shipping"] == 0.0
    assert net_profit("mercari", 40.00)["shipping"] == FEE_SCHEDULE["mercari"]["default_shipping"]


def test_loss_is_negative_net():
    assert net_profit("ebay", 10.00, cost_paid=9.00, shipping=6.00)["net"] < 0


def test_best_platform_prefers_poshmark_when_buyer_pays_shipping():
    # Same $30 price on all three: Poshmark's 20% beats eBay/Mercari once the
    # seller-paid label is counted.
    assert best_platform({"ebay": 30, "poshmark": 30, "mercari": 30}) == "poshmark"


def test_batch_totals():
    rows = [net_profit("ebay", 50, 8, 6), net_profit("poshmark", 40, 5)]
    t = batch_totals(rows)
    assert t["revenue"] == 90.0
    assert t["net"] == round(rows[0]["net"] + rows[1]["net"], 2)


def _frontend_schedule() -> dict:
    html = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(r"/\* FEE_SCHEDULE:start \*/(.*?)/\* FEE_SCHEDULE:end \*/", html, re.S)
    assert m, "FEE_SCHEDULE markers missing from index.html"
    return json.loads(m.group(1))


def test_frontend_schedule_matches_backend():
    assert _frontend_schedule() == json.loads(json.dumps(FEE_SCHEDULE))
