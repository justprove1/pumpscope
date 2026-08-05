"""Techo empirico y probabilidades condicionadas al crecimiento actual.

Lo que se protege aqui es sobre todo lo que el modulo NO debe hacer: inventar un techo cuando
no hay poblacion comparable, y presentar como alcanzable una zona que la curva no llega a medir.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mit_worker.ingest import (
    BIG_CAP_SOL,
    BIRTH_CAP_SOL,
    EXPLODE_TARGET_SOL,
    MIN_CEILING_SAMPLE,
    GrowthOutlook,
    _load_corpus_seed,
    _load_growth_peaks,
)


def _cap_to_growth(cap_sol: float) -> float:
    return cap_sol / BIRTH_CAP_SOL


class TestSinMuestraNoHayCifra:
    def test_un_corpus_vacio_no_devuelve_techo(self) -> None:
        assert GrowthOutlook([]).outlook(5.0) is None

    def test_por_debajo_del_minimo_de_muestra_devuelve_none(self) -> None:
        # Justo un caso menos del minimo: no basta, y la respuesta correcta es None.
        outlook = GrowthOutlook([10.0] * (MIN_CEILING_SAMPLE - 1))
        assert outlook.outlook(2.0) is None

    def test_con_el_minimo_justo_ya_responde(self) -> None:
        outlook = GrowthOutlook([10.0] * MIN_CEILING_SAMPLE)
        result = outlook.outlook(2.0)
        assert result is not None
        assert result["ceiling_sample"] == MIN_CEILING_SAMPLE

    def test_un_crecimiento_por_encima_de_todo_el_corpus_no_inventa(self) -> None:
        """Nadie llego tan arriba: no hay con quien compararlo."""
        outlook = GrowthOutlook([2.0] * 100)
        assert outlook.outlook(50.0) is None

    @pytest.mark.parametrize("growth", [0.0, -1.0])
    def test_crecimiento_no_positivo_es_none(self, growth: float) -> None:
        assert GrowthOutlook([5.0] * 50).outlook(growth) is None


class TestCondicionamiento:
    def test_solo_cuentan_los_que_llegaron_al_menos_hasta_aqui(self) -> None:
        """La poblacion de referencia se recorta por abajo, no es el corpus entero."""
        peaks = [1.0] * 50 + [20.0] * 10
        result = GrowthOutlook(peaks).outlook(10.0)
        assert result is not None
        # Los 50 casos que se quedaron en x1 no describen a un token que ya va por x10.
        assert result["ceiling_sample"] == 10
        assert result["ceiling_sol"] == pytest.approx(20.0 * BIRTH_CAP_SOL, rel=1e-6)

    def test_el_techo_nunca_queda_por_debajo_de_donde_ya_esta(self) -> None:
        """Un techo inferior al precio actual seria un sinsentido que invita a vender solo."""
        peaks = [float(x) for x in range(3, 40)]
        for growth in (3.0, 8.0, 15.0, 30.0):
            result = GrowthOutlook(peaks).outlook(growth)
            if result is None:
                continue
            assert result["ceiling_sol"] >= growth * BIRTH_CAP_SOL

    def test_el_techo_optimista_no_es_menor_que_el_mediano(self) -> None:
        peaks = [float(x) for x in range(3, 60)]
        result = GrowthOutlook(peaks).outlook(5.0)
        assert result is not None
        assert result["ceiling_high_sol"] >= result["ceiling_sol"]

    def test_subir_el_listado_no_puede_bajar_la_probabilidad(self) -> None:
        """Cuanto mas arriba esta un token, mas cerca de graduar esta la poblacion que le queda."""
        peaks = [float(x) for x in range(3, 60)]
        outlook = GrowthOutlook(peaks)
        previous = -1.0
        for growth in (3.0, 6.0, 10.0, 14.0):
            result = outlook.outlook(growth)
            assert result is not None
            assert result["prob_grad"] >= previous
            previous = result["prob_grad"]


class TestProbabilidades:
    def test_prob_grad_cuenta_los_que_pasaron_el_umbral_de_graduacion(self) -> None:
        bajo = (EXPLODE_TARGET_SOL - 1) / BIRTH_CAP_SOL
        alto = (EXPLODE_TARGET_SOL + 1) / BIRTH_CAP_SOL
        result = GrowthOutlook([bajo] * 30 + [alto] * 10).outlook(2.0)
        assert result is not None
        assert result["prob_grad"] == pytest.approx(0.25)

    def test_prob_100k_es_cero_si_nadie_llego(self) -> None:
        """Cero medido no es lo mismo que None: hay muestra, y la respuesta es que no pasa."""
        casi = (BIG_CAP_SOL - 1) / BIRTH_CAP_SOL
        result = GrowthOutlook([casi] * 40).outlook(2.0)
        assert result is not None
        assert result["prob_100k"] == 0.0

    def test_prob_100k_no_supera_a_prob_grad(self) -> None:
        """100k esta por encima de la graduacion: no puede ser mas probable que ella."""
        peaks = [float(x) for x in range(3, 80)]
        for growth in (3.0, 10.0, 20.0):
            result = GrowthOutlook(peaks).outlook(growth)
            assert result is not None
            assert result["prob_100k"] <= result["prob_grad"]


class TestCargaDelCorpus:
    def test_un_corpus_ausente_no_tumba_el_arranque(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("mit_worker.ingest.TRAINING_CORPUS_DIR", str(tmp_path))
        assert _load_growth_peaks() == []
        seed = _load_corpus_seed()
        assert seed.peak_multiples == []
        assert sum(seed.band_reached) == 0

    def test_las_lineas_corruptas_se_saltan_sin_perder_las_buenas(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archivo = tmp_path / "winners.jsonl"
        archivo.write_text(
            "\n".join(
                [
                    json.dumps({"peak_growth": 4.0}),
                    "{esto no es json",
                    json.dumps({"sin_la_clave": 1}),
                    json.dumps({"peak_growth": "texto"}),
                    "",
                    json.dumps({"peak_growth": 9.0}),
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("mit_worker.ingest.TRAINING_CORPUS_DIR", str(tmp_path))
        assert _load_growth_peaks() == [4.0, 9.0]

    def test_la_siembra_reconstruye_las_bandas_desde_los_picos(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archivo = tmp_path / "stampedes.jsonl"
        archivo.write_text(
            "\n".join(
                [
                    json.dumps({"peak_market_cap_sol": 50.0, "peak_multiple": 1.2}),
                    json.dumps({"peak_market_cap_sol": 400.0, "peak_multiple": 5.0}),
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("mit_worker.ingest.TRAINING_CORPUS_DIR", str(tmp_path))
        seed = _load_corpus_seed()
        assert seed.peak_multiples == [1.2, 5.0]
        # Solo el de 400 SOL cruza las bandas altas, y ademas gradua.
        assert seed.band_reached[0] == 1
        assert seed.band_success[0] == 1
        # Ninguno llega a la zona de 100k: el conteo tiene que reflejarlo como cero.
        assert seed.band_big == [0, 0, 0, 0]


def test_la_zona_de_100k_esta_por_encima_de_la_graduacion() -> None:
    """Invariante que explica por que prob_100k medida en la curva tiende a cero.

    Si algun dia se recolocan estas constantes, este test avisa de que la advertencia que
    acompana a la cifra en la interfaz ha dejado de ser cierta.
    """
    assert BIG_CAP_SOL > EXPLODE_TARGET_SOL
    assert _cap_to_growth(BIG_CAP_SOL) > _cap_to_growth(EXPLODE_TARGET_SOL)


def test_el_umbral_de_las_series_sigue_atado_a_la_graduacion() -> None:
    """`SERIES_PUMP_CAP_SOL` duplica el valor de `EXPLODE_TARGET_SOL` por orden de definicion.

    Si alguien mueve la graduacion y no toca la otra, la pestana de series empezaria a admitir
    tokens que no graduaron —justo el ruido que el umbral se subio para eliminar— sin que nada
    fallase. Este test es el unico aviso.
    """
    from mit_worker.ingest import SERIES_PUMP_CAP_SOL

    assert SERIES_PUMP_CAP_SOL == EXPLODE_TARGET_SOL
