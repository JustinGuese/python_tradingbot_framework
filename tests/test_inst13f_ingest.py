from datetime import datetime

from tradingbot.utils.inst13f_ingest import (
    keep_latest_amendments,
    parse_quarter,
    select_effective,
)

SUB_H = "ACCESSION_NUMBER\tFILING_DATE\tSUBMISSIONTYPE\tCIK\tPERIODOFREPORT\n"
COV_H = "ACCESSION_NUMBER\tISAMENDMENT\tFILINGMANAGER_NAME\n"
INF_H = "ACCESSION_NUMBER\tCUSIP\tVALUE\tSSHPRNAMT\tPUTCALL\n"


def _parse(subs, covs, infs):
    return parse_quarter(
        SUB_H + "\n".join(subs) + "\n",
        COV_H + "\n".join(covs) + "\n",
        INF_H + "\n".join(infs) + "\n",
    )


CUSIPS = [("037833100", 150), ("594918104", 300), ("67066G104", 100)]


def _rows(acc, scale):
    return [f"{acc}\t{c}\t{int(p * 1000 * scale)}\t1000\t" for c, p in CUSIPS]


def test_unit_detection_mixed_units_same_quarter():
    # B reports thousands, A and C dollars, same quarter/CUSIPs. Date rule would
    # get it wrong (all filed after the cutover); the price comparison must not.
    _, h = _parse(
        [f"{a}\t14-FEB-2024\t13F-HR\t{i}\t31-DEC-2023" for i, a in enumerate("ABC", 1)],
        [f"{a}\tN\tF" for a in "ABC"],
        _rows("A", 1) + _rows("B", 0.001) + _rows("C", 1),
    )
    by = {(x["accession"], x["cusip"]): x["value_usd"] for x in h}
    assert by[("B", "037833100")] == 150_000.0
    assert by[("A", "037833100")] == 150_000.0
    assert by[("C", "594918104")] == 300_000.0


def test_unit_fallback_to_date_rule_when_no_overlap():
    _, h = _parse(
        ["A1\t15-NOV-2022\t13F-HR\t1\t30-SEP-2022", "A2\t14-FEB-2023\t13F-HR\t2\t31-DEC-2022"],
        ["A1\tN\tFund", "A2\tN\tFund"],
        ["A1\t037833100\t5\t10\t", "A2\t594918104\t5000\t10\t"],
    )
    by = {x["accession"]: x["value_usd"] for x in h}
    assert by["A1"] == 5000.0  # before cutover: thousands
    assert by["A2"] == 5000.0  # after: dollars


def test_amendment_replaces_original_only_from_its_filing_date():
    f, _ = _parse(
        [
            "O\t14-FEB-2022\t13F-HR\t7\t31-DEC-2021",
            "M1\t01-MAR-2022\t13F-HR/A\t7\t31-DEC-2021",
            "M2\t01-APR-2022\t13F-HR/A\t7\t31-DEC-2021",
        ],
        ["O\tN\tF", "M1\tY\tF", "M2\tY\tF"],
        [],
    )
    f = keep_latest_amendments(f)
    assert {x["accession"] for x in f} == {"O", "M2"}
    key = (7, f[0]["period_end"])
    assert select_effective(f, datetime(2022, 3, 15))[key]["accession"] == "O"
    assert select_effective(f, datetime(2022, 4, 1))[key]["accession"] == "M2"
    assert select_effective(f, datetime(2022, 2, 1)) == {}


def test_option_rows_dropped():
    _, h = _parse(
        ["A\t14-FEB-2024\t13F-HR\t1\t31-DEC-2023"],
        ["A\tN\tF"],
        ["A\t037833100\t100\t1\t", "A\t037833100\t900\t1\tCall", "A\t594918104\t50\t1\tPut"],
    )
    assert len(h) == 1 and h[0]["put_call"] is None and h[0]["value_usd"] == 100.0
