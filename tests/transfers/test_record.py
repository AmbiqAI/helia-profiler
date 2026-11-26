import pytest

from helia_profiler.transfers.matrix import REPETITIONS, Cell, matrix
from helia_profiler.transfers.record import read_rows

START = "HPX_XFER_START=96000000,96000000,8,8,2,8"


def test_matrix_covers_all_piece_counts_and_both_cpu_regimes():
    cells = matrix()
    assert len(cells) == 70
    for cell in cells:
        if cell.pieces and cell.size > 4096 * cell.pieces:
            assert not cell.lengths
            continue
        assert sum(cell.lengths) == cell.size
        if cell.pieces:
            assert len(cell.lengths) == cell.pieces
            start = 0
            for length in cell.lengths:
                assert length % 4 == 0
                assert start // 4096 == (start + length - 1) // 4096
                start += length


def test_signed_whole_ticket_loss_and_failed_cells():
    cells = (Cell(1024, 2, "none"), Cell(65536, 8, "none"))
    trial = "HPX_XFER_SAMPLE=0,{},20,90,71,22,4,300,220,217,233,251,50,80"
    lines = [
        START,
        *(trial.format(i) for i in range(REPETITIONS)),
        "HPX_XFER_FAIL=1,1,0",
        "HPX_XFER_END=2",
    ]
    rows, _ = read_rows(lines, cells)
    assert rows[0]["inflight_core_loss_cycles"]["mean"] == -3
    assert rows[0]["evidence"]["repetitions"][0]["callback_offsets"] == [50, 80]
    assert rows[1]["status"] == "not_run"
    assert rows[1]["issue_cycles"] is None
    for cutoff in (1, 8, len(lines) - 1):
        with pytest.raises(ValueError):
            read_rows(lines[:cutoff], cells)
    with pytest.raises(ValueError):
        read_rows([*lines[:2], *lines[1:]], cells)


@pytest.mark.parametrize("field", ["cold", "warm"])
def test_sync_copy_has_no_async_cost(field):
    lines = [
        START,
        *(f"HPX_XFER_SAMPLE=0,{i},0,25,0,0,0,0,0,0,0,0" for i in range(REPETITIONS)),
        "HPX_XFER_END=1",
    ]
    rows, _ = read_rows(lines, (Cell(1024, 0, field),))
    assert rows[0]["complete_cycles"]["mean"] == 25
    assert rows[0]["cache_regime"] == field
    assert all(
        rows[0][k] is None for k in ("wait_cycles", "inflight_core_loss_cycles", "issue_cycles")
    )


def test_converted_geometry_retains_both_offsets():
    from helia_profiler.transfers.matrix import schedule_cells

    plan = {
        "schedule": {
            "units": [
                {
                    "engine": "dma",
                    "source_memory": "PSRAM",
                    "bytes": 9600,
                    "pool_offset": 6592,
                    "source_offset": 4992,
                }
            ]
        }
    }
    (cell,) = schedule_cells(plan)
    assert cell.lengths == (1600, 4096, 3904)
    assert cell.source_offset == 4992
