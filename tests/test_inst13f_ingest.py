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


def test_value_units_normalised_across_2023_change():
    _, h = _parse(
        ["A1\t15-NOV-2022\t13F-HR\t1\t30-SEP-2022", "A2\t14-FEB-2023\t13F-HR\t1\t31-DEC-2022"],
        ["A1\tN\tFund", "A2\tN\tFund"],
        ["A1\t037833100\t5\t10\t", "A2\t037833100\t5000\t10\t"],
    )
    by = {x["accession"]: x["value_usd"] for x in h}
    assert by["A1"] == 5000.0  # thousands -> dollars
    assert by["A2"] == 5000.0  # already dollars


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
