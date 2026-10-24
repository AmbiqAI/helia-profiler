from copy import deepcopy

import pytest

from tools.docs.check_api_tiers import check_tiers


@pytest.mark.parametrize("change", ["none", "order", "tier", "count"])
def test_tier_parity_detects_runtime_disagreement(change):
    exports = ["first", "second"]
    stability = {"first": "stable", "second": "experimental"}
    document = {"all": exports, "stability": stability, "tiers": {"stable": 1, "experimental": 1}}
    document = deepcopy(document)
    if change == "none":
        check_tiers(document, exports, stability)
        return
    if change == "order":
        document["all"].reverse()
    elif change == "tier":
        document["stability"] = {"first": "experimental", "second": "stable"}
    else:
        document["tiers"]["stable"] = 2
    with pytest.raises(ValueError, match="differs|differ"):
        check_tiers(document, exports, stability)
