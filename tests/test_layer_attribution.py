"""#218: per-layer MACs join on the ORIGINAL tflite operator index.

The defect: AOT layer ids are post-compilation execution positions (helia-aot
skips ops without renumbering), while model analysis describes the original
graph — a positional join lands every MAC count on a plausible wrong layer.
These tests pin the contained resolver (`modelcost/layer_attribution.py`)
and both consumers (CSV writer, console table).
"""

from __future__ import annotations

from pathlib import Path

from helia_profiler.modelcost import LayerOps, ModelAnalysis
from helia_profiler.modelcost.layer_attribution import (
    LayerAttributor,
    manifest_source_map,
    source_index_from_op,
)
from helia_profiler.results import LayerResult


def _skewed_analysis() -> ModelAnalysis:
    # Original graph indices 0, 3, 5 — positions after skipping are 0, 1, 2.
    return ModelAnalysis(
        layers=[
            LayerOps(id=0, op="CONV_2D", macs=100_000, ops=200_000, original_id=0),
            LayerOps(id=1, op="FULLY_CONNECTED", macs=43_200, ops=86_400, original_id=3),
            LayerOps(id=2, op="SOFTMAX", macs=0, ops=500, original_id=5),
        ],
        total_macs=143_200,
        total_ops=286_900,
        num_parameters=1,
        engine="helia-aot",
    )


class TestSourceIndexFromOp:
    def test_integer_suffix_parses(self):
        assert source_index_from_op("FULLY_CONNECTED:43") == 43
        assert source_index_from_op("CONV_2D:0") == 0

    def test_plain_label_is_none(self):
        assert source_index_from_op("CONV_2D") is None

    def test_non_integer_suffix_is_none(self):
        # ExecuTorch labels name chain/instruction, not a tflite operator.
        assert source_index_from_op("OPERATOR_CALL:c3i12") is None
        assert source_index_from_op("OP:-1") is None
        assert source_index_from_op("OP:") is None

    def test_layer_result_derives_it(self):
        assert LayerResult(id=7, op="FULLY_CONNECTED:43").source_index == 43
        assert LayerResult(id=7, op="CONV_2D").source_index is None
        # An explicit value is never overwritten by derivation.
        assert LayerResult(id=7, op="FULLY_CONNECTED:43", source_index=9).source_index == 9


class TestManifestSourceMap:
    def test_maps_position_to_original(self):
        assert manifest_source_map([{"idx": 0, "id": 0}, {"idx": 1, "id": 3}]) == {0: 0, 1: 3}

    def test_malformed_entries_are_skipped_never_guessed(self):
        rows = [
            {"idx": "1", "id": 3},  # wrong-typed
            {"idx": 2},  # missing id
            {"id": 4},  # missing idx
            {"idx": True, "id": 1},  # bool is not a position
            "not-a-mapping",
            {"idx": 5, "id": 6},
        ]
        assert manifest_source_map(rows) == {5: 6}


class TestLayerAttributor:
    def test_manifest_join_beats_position(self):
        """The observed #218 shape: position 1 is FULLY_CONNECTED:3 — a
        positional join would hand it analysis.layers[1]... which here IS
        the right op, so the analysis list is ordered adversarially."""
        analysis = _skewed_analysis()
        att = LayerAttributor(analysis, [{"idx": 0, "id": 0}, {"idx": 1, "id": 5}, {"idx": 2, "id": 3}])
        # Position 1 executes the zero-mac SOFTMAX (original 5); positional
        # indexing would report 43,200 MACs for it.
        assert att.attribute(1, "SOFTMAX:5").macs == 0
        assert att.attribute(2, "FULLY_CONNECTED:3").macs == 43_200
        assert att.attribute(0, "CONV_2D:0").macs == 100_000

    def test_manifest_is_authoritative_over_the_suffix(self):
        analysis = _skewed_analysis()
        att = LayerAttributor(analysis, [{"idx": 0, "id": 5}])
        assert att.attribute(0, "CONV_2D:0").source_index == 5

    def test_manifest_miss_is_a_dash_not_a_guess(self):
        """A position the manifest does not name stays unresolved even when
        the label suffix would parse — disagreement is not a licence."""
        analysis = _skewed_analysis()
        att = LayerAttributor(analysis, [{"idx": 0, "id": 0}])
        result = att.attribute(1, "FULLY_CONNECTED:3")
        assert result.source_index is None
        assert result.macs is None

    def test_suffix_fallback_without_a_manifest(self):
        """Artifact-replay paths have no manifest; the label carries the key."""
        att = LayerAttributor(_skewed_analysis(), None)
        assert att.attribute(1, "FULLY_CONNECTED:3").macs == 43_200

    def test_sequential_engines_keep_the_identity_join(self):
        """TFLM/helia-rt: plain labels, position == original index."""
        analysis = ModelAnalysis(
            layers=[
                LayerOps(id=0, op="CONV_2D", macs=10),
                LayerOps(id=1, op="SOFTMAX", macs=0),
            ],
            total_macs=10,
            total_ops=20,
            num_parameters=1,
            engine="tflite",
        )
        att = LayerAttributor(analysis, None)
        result = att.attribute(0, "CONV_2D")
        assert result.macs == 10
        assert result.explicit is False  # not worth persisting: it IS the id

    def test_unmatchable_sources_resolve_nothing(self):
        att = LayerAttributor(_skewed_analysis(), None)
        # In-range id on purpose: an identity fallback for ":"-labelled ops
        # would positionally resolve id 0 to 100,000 macs (#222 lens).
        result = att.attribute(0, "OPERATOR_CALL:c3i12")
        assert result.source_index is None
        assert result.macs is None
        assert att.attribute("odd-id", "CONV_2D").macs is None

    def test_a_carried_source_index_outranks_the_label_but_not_the_manifest(self):
        analysis = _skewed_analysis()
        att = LayerAttributor(analysis, None)
        assert att.attribute(0, "CONV_2D:0", source_index=3).macs == 43_200
        att = LayerAttributor(analysis, [{"idx": 0, "id": 0}])
        assert att.attribute(0, "CONV_2D:0", source_index=3).macs == 100_000

    def test_an_aot_run_without_a_manifest_dashes_everything(self):
        """#222 lens: manifest extraction failed => degraded firmware labels
        layers with POSITIONS; an empty authoritative manifest ([]) must
        dash rather than let the suffix fallback join positionally."""
        att = LayerAttributor(_skewed_analysis(), [])
        result = att.attribute(1, "SOFTMAX:1")
        assert result.source_index is None
        assert result.macs is None

    def test_no_analysis_still_resolves_the_source_index(self):
        att = LayerAttributor(None, [{"idx": 0, "id": 4}])
        result = att.attribute(0, "CONV_2D:4")
        assert result.source_index == 4
        assert result.macs is None


class TestCsvWriterJoin:
    def test_csv_joins_on_the_manifest_and_dashes_misses(self, tmp_path: Path):
        import csv as csv_mod

        from helia_profiler.report.csv_writer import _write_csv
        from helia_profiler.results import FirmwareMeta, PmuResult

        pmu = PmuResult(
            meta=FirmwareMeta(),
            layers=[
                LayerResult(id=0, op="CONV_2D:0", cycles=1000.0, counters={"ARM_PMU_CPU_CYCLES": 1000.0}),
                LayerResult(id=1, op="SOFTMAX:5", cycles=500.0, counters={"ARM_PMU_CPU_CYCLES": 500.0}),
                LayerResult(id=2, op="FULLY_CONNECTED:3", cycles=800.0, counters={"ARM_PMU_CPU_CYCLES": 800.0}),
            ],
        )
        manifest = [{"idx": 0, "id": 0}, {"idx": 1, "id": 5}]  # position 2 absent
        out = _write_csv(pmu, tmp_path, _skewed_analysis(), aot_op_manifest=manifest)
        rows = list(csv_mod.DictReader(open(out)))

        assert [r["source_index"] for r in rows] == ["0", "5", ""]
        assert rows[0]["macs"] == "100000"
        assert rows[1]["macs"] == "0"  # softmax truth, not position 1's 43,200
        assert rows[2]["macs"] == ""  # manifest miss: dash, never positional

    def test_sequential_csv_shape_is_unchanged(self, tmp_path: Path):
        """No manifest, plain labels: no source_index column, macs as before."""
        import csv as csv_mod

        from helia_profiler.report.csv_writer import _write_csv
        from helia_profiler.results import FirmwareMeta, PmuResult

        analysis = ModelAnalysis(
            layers=[LayerOps(id=0, op="CONV_2D", macs=10, ops=20)],
            total_macs=10,
            total_ops=20,
            num_parameters=1,
            engine="tflite",
        )
        pmu = PmuResult(
            meta=FirmwareMeta(),
            layers=[LayerResult(id=0, op="CONV_2D", cycles=100.0, counters={"ARM_PMU_CPU_CYCLES": 100.0})],
        )
        out = _write_csv(pmu, tmp_path, analysis)
        rows = list(csv_mod.DictReader(open(out)))
        assert "source_index" not in rows[0]
        assert rows[0]["macs"] == "10"


class TestConsumerPlumbing:
    """#222 lens: the resolver's empty-manifest dash rule is only as good
    as the consumers that build the manifest argument — pin both."""

    def _aot_ctx(self, tmp_path: Path, manifest):
        from helia_profiler.config import load_config
        from helia_profiler.engines import EngineType
        from helia_profiler.engines.base import HeliaAotArtifacts
        from helia_profiler.pipeline import PipelineContext

        model_file = tmp_path / "test.tflite"
        model_file.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model_file)},
                "engine": {"type": "helia-aot"},
                "work_dir": str(tmp_path / "work"),
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.engine_artifacts = HeliaAotArtifacts(
            engine_type=EngineType.HELIA_AOT,
            engine_header="model_model.h",
            aot_prefix="model",
            aot_module_name="aot-model",
            aot_cmake_target="nsx::aot_model",
            helia_aot_version="0.18.4",
            aot_op_manifest=manifest,
        )
        return ctx

    def test_report_hands_a_manifestless_aot_run_an_empty_authoritative_manifest(
        self, tmp_path: Path
    ):
        from helia_profiler.report import _aot_manifest

        assert _aot_manifest(self._aot_ctx(tmp_path, None)) == []
        assert _aot_manifest(self._aot_ctx(tmp_path, [{"idx": 0, "id": 0}])) == [
            {"idx": 0, "id": 0}
        ]

    def test_report_hands_sequential_engines_no_manifest(self, tmp_path: Path):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.report import _aot_manifest

        model_file = tmp_path / "test.tflite"
        model_file.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model_file)},
                "engine": {"type": "helia-rt"},
                "work_dir": str(tmp_path / "work"),
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        assert _aot_manifest(ctx) is None

    def test_console_dashes_a_manifestless_aot_run(self, tmp_path: Path):
        """Degraded firmware labels layers with POSITIONS ("SOFTMAX:1") —
        the console must dash, not suffix-join positionally."""
        from rich.console import Console

        from helia_profiler.console import HpxConsole
        from helia_profiler.console.results import print_results
        from helia_profiler.results import FirmwareMeta, PmuResult
        from tests.pipeline_context_helpers import set_profile_result

        ctx = self._aot_ctx(tmp_path, None)
        set_profile_result(
            ctx,
            PmuResult(
                meta=FirmwareMeta(clean_infer_count=1, clean_infer_avg_us=1000),
                # Degraded label suffix = POSITION 3, which collides with
                # original id 3 (FULLY_CONNECTED) in the analysis.
                layers=[LayerResult(id=3, op="SOFTMAX:3", cycles=500.0)],
            ),
        )
        ctx.model_analysis = _skewed_analysis()
        hpx_console = HpxConsole(verbosity=0)
        recorder = Console(record=True, highlight=False, width=200)
        hpx_console._console = recorder
        print_results(hpx_console, ctx)
        softmax = next(
            line for line in recorder.export_text().splitlines() if "SOFTMAX:3" in line
        )
        # Suffix-joining the degraded label would land FULLY_CONNECTED's
        # 43,200 macs on this softmax; the dash rule forbids it.
        assert "43,200" not in softmax


class TestConsoleJoin:
    def test_top_layers_table_shows_manifest_joined_macs(self, tmp_path: Path):
        """The console's Top Layers table — where the #218 21x-wrong
        cycles/MAC was observed — must join on the manifest, not position."""
        from rich.console import Console

        from helia_profiler.config import load_config
        from helia_profiler.console import HpxConsole
        from helia_profiler.console.results import print_results
        from helia_profiler.engines.base import HeliaAotArtifacts
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.engines import EngineType
        from helia_profiler.results import FirmwareMeta, PmuResult
        from tests.pipeline_context_helpers import set_profile_result

        model_file = tmp_path / "test.tflite"
        model_file.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model_file)},
                "engine": {"type": "helia-aot"},
                "work_dir": str(tmp_path / "work"),
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        set_profile_result(
            ctx,
            PmuResult(
                meta=FirmwareMeta(clean_infer_count=1, clean_infer_avg_us=1000),
                layers=[
                    LayerResult(id=0, op="CONV_2D:0", cycles=1000.0),
                    LayerResult(id=1, op="SOFTMAX:5", cycles=500.0),
                ],
            ),
        )
        ctx.model_analysis = _skewed_analysis()
        ctx.engine_artifacts = HeliaAotArtifacts(
            engine_type=EngineType.HELIA_AOT,
            engine_header="model_model.h",
            aot_prefix="model",
            aot_module_name="aot-model",
            aot_cmake_target="nsx::aot_model",
            helia_aot_version="0.18.4",
            aot_op_manifest=[{"idx": 0, "id": 0}, {"idx": 1, "id": 5}],
        )
        hpx_console = HpxConsole(verbosity=0)
        recorder = Console(record=True, highlight=False, width=200)
        hpx_console._console = recorder
        print_results(hpx_console, ctx)
        text = recorder.export_text()

        conv = next(line for line in text.splitlines() if "CONV_2D:0" in line)
        softmax = next(line for line in text.splitlines() if "SOFTMAX:5" in line)
        assert "100,000" in conv
        # Position 1 is the zero-mac SOFTMAX: a positional join would print
        # analysis.layers[1]'s 43,200 here.
        assert "43,200" not in softmax
        assert " 0.5" not in softmax  # 500 cyc / 1000 macs would be the wrong quotient
