"""The notebook path: `open_platform`, a DataFrame by reference, and a failure that raises."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from quant_agent_os.config import get_settings
from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.runs import SqliteRunStore
from quant_agent_os.sdk import ToolCallError
from quant_platform import open_platform
from quant_research import open_research

pandas = pytest.importorskip("pandas")
pytest.importorskip("empyrical")

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "src" / "quant_platform" / "sample" / "corpus"
generator = random.Random(11)
FUND = [generator.gauss(0.0005, 0.01) for _ in range(300)]
DAYS = pandas.bdate_range("2020-01-01", periods=len(FUND))


def _series(dataset: str, column: str = "fund") -> dict[str, str]:
    return {"dataset": dataset, "column": column}


@pytest.fixture
def home() -> Path:
    """A home with the sample corpus ingested and one CSV dataset added."""
    settings = get_settings()
    Path(settings.store).parent.mkdir(parents=True)
    with open_research(settings.store) as research:
        research.ingest.ingest(CORPUS)
    source = Path(settings.store).parent / "fund.csv"
    pandas.DataFrame({"fund": FUND}, index=DAYS.rename("date")).to_csv(source)
    DirectoryDatasetStore(settings.datasets_dir).put("from-home", source)
    return Path(settings.store).parent


def test_a_notebook_session_from_open_to_a_raised_failure(home: Path) -> None:
    frame = pandas.DataFrame({"fund": FUND}, index=DAYS)

    with open_platform() as platform:
        platform.data.put("from-frame", frame)

        from_frame = platform.call("analyze_performance", returns=_series("from-frame")).result()
        from_home = platform.call("analyze_performance", returns=_series("from-home")).result()
        related = platform.call("find_related_signals", query="short-term reversal").result()
        listed = platform.call("list_datasets").result()
        failed = platform.call("analyze_performance", returns=_series("absent"))
        run_id = failed.run_id

        assert from_frame["metrics"] == from_home["metrics"]
        assert from_frame["periods_per_year"] == 252
        assert related["passages"]
        assert {item["id"] for item in listed["datasets"]} == {"from-frame", "from-home"}
        assert failed.status == "error"
        with pytest.raises(ToolCallError, match="absent") as caught:
            failed.result()
        assert caught.value.status == "error"
        assert caught.value.hint

    with SqliteRunStore(get_settings().runs_file) as audit:
        assert len(audit.list_runs(20)) == 5
        stored = audit.get_run(run_id or "")
    assert stored is not None
    assert stored.calls[0].status == "error"


def test_a_frame_is_aligned_by_date_with_a_dataset_in_the_home(home: Path) -> None:
    shifted = pandas.DataFrame({"fund": FUND[5:]}, index=DAYS[5:])

    with open_platform() as platform:
        platform.data.put("late", shifted)
        output = platform.call(
            "analyze_performance",
            returns=_series("late"),
            benchmark=_series("from-home"),
        ).result()

    assert output["metrics"]["beta"] == pytest.approx(1.0)
    assert any("left out 5 rows of 'from-home'" in warning for warning in output["warnings"])
    fingerprint = output["data_versions"]["dataset:late"]
    assert len(fingerprint) == 64


def test_a_parquet_dataset_gives_the_same_numbers_as_the_frame_it_came_from(home: Path) -> None:
    pytest.importorskip("pyarrow")
    source = home / "fund.parquet"
    frame = pandas.DataFrame({"fund": FUND}, index=DAYS.rename("date"))
    frame.to_parquet(source)
    DirectoryDatasetStore(get_settings().datasets_dir).put("from-parquet", source)

    with open_platform() as platform:
        platform.data.put("from-frame", frame)
        parquet = platform.call("analyze_performance", returns=_series("from-parquet")).result()
        direct = platform.call("analyze_performance", returns=_series("from-frame")).result()

    assert parquet["metrics"] == direct["metrics"]
    assert parquet["periods_per_year"] == 252


def test_a_frame_writes_nothing_to_the_home(home: Path) -> None:
    with open_platform() as platform:
        platform.data.put("session", pandas.DataFrame({"fund": FUND}, index=DAYS))

    with open_platform() as platform:
        listed = platform.call("list_datasets").result()["datasets"]
        assert [item["id"] for item in listed] == ["from-home"]


def test_a_home_without_a_store_still_opens_for_analytics() -> None:
    with pytest.warns(UserWarning, match="qaos ingest"), open_platform() as platform:
        platform.data.put("fund", pandas.DataFrame({"fund": FUND}, index=DAYS))

        assert platform.call("analyze_performance", returns=_series("fund")).status == "ok"
        unknown = platform.call("find_related_signals", query="momentum")

    assert unknown.status == "unknown_tool"
    with pytest.raises(ToolCallError) as caught:
        unknown.result()
    assert "analyze_performance" in str(caught.value)


def test_open_platform_never_grants_more_than_the_settings() -> None:
    with pytest.warns(UserWarning, match="qaos ingest"), open_platform() as platform:
        assert platform.policy.granted == get_settings().policy().granted
        assert not platform.policy.preapproved
