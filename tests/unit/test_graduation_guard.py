"""Una graduacion no puede darse por buena con una sola lectura de reservas.

Observado en vivo antes de este guardia: el token `quasi` figuraba como graduado con una
capitalizacion de 0,045 SOL y un progreso posterior de 0,38. Eso es imposible —un token que
gradua de verdad agota su reparto y no puede volver a operar en la curva—, asi que la marca
venia de un unico evento con reservas disparatadas que saturaba `min(1.0, ...)`.

El dano no era cosmetico: un falso graduado se lleva una suscripcion de PumpSwap (recurso
escaso, el RPC publico da 429) y entra en el denominador de P(100k | gradúa), hundiendo una
estadistica que precisamente se acaba de construir para poder medir ese tramo.
"""

from __future__ import annotations

from mit_worker.ingest import (
    BIRTH_CAP_SOL,
    GRADUATION_MIN_CAP_SOL,
    GRADUATION_RAISE_LAMPORTS,
    INITIAL_VIRTUAL_SOL_LAMPORTS,
    PUMPFUN_TOTAL_SUPPLY,
    _graduation_progress,
    _market_cap_sol,
)


class TestProgresoDeGraduacion:
    def test_al_nacer_el_progreso_es_cero(self) -> None:
        assert _graduation_progress(INITIAL_VIRTUAL_SOL_LAMPORTS) == 0.0

    def test_por_debajo_del_inicial_no_da_negativo(self) -> None:
        assert _graduation_progress(INITIAL_VIRTUAL_SOL_LAMPORTS // 2) == 0.0

    def test_al_recaudar_lo_exigido_llega_a_uno(self) -> None:
        reservas = INITIAL_VIRTUAL_SOL_LAMPORTS + GRADUATION_RAISE_LAMPORTS
        assert _graduation_progress(reservas) == 1.0

    def test_satura_en_uno_y_por_eso_hace_falta_el_guardia(self) -> None:
        """La saturacion es deliberada, pero convierte cualquier lectura absurda en un 1,0."""
        disparate = INITIAL_VIRTUAL_SOL_LAMPORTS + GRADUATION_RAISE_LAMPORTS * 10_000
        assert _graduation_progress(disparate) == 1.0


class TestGuardiaDeCapitalizacion:
    def test_el_umbral_deja_pasar_una_graduacion_real(self) -> None:
        """Los graduados reales rondan los 410 ◎; el umbral no puede excluirlos."""
        assert GRADUATION_MIN_CAP_SOL < 410.0

    def test_el_umbral_esta_muy_por_encima_del_nacimiento(self) -> None:
        assert GRADUATION_MIN_CAP_SOL > BIRTH_CAP_SOL * 5

    def test_el_caso_real_de_quasi_queda_fuera(self) -> None:
        """Reservas que dan progreso 1,0 y una capitalizacion ridicula a la vez.

        Se consigue con muchisimo SOL virtual y AUN MAS token virtual: el progreso solo mira el
        SOL, mientras que la capitalizacion mira el cociente. Es la forma exacta del fallo.
        """
        vsol = INITIAL_VIRTUAL_SOL_LAMPORTS + GRADUATION_RAISE_LAMPORTS
        vtok = 10**18
        assert _graduation_progress(vsol) == 1.0
        cap = _market_cap_sol(vsol, vtok, PUMPFUN_TOTAL_SUPPLY)
        assert cap < GRADUATION_MIN_CAP_SOL, "el guardia no atraparia el caso observado"

    def test_una_curva_en_graduacion_normal_supera_el_umbral(self) -> None:
        """Reservas coherentes al graduar: el guardia no debe estorbar."""
        vsol = INITIAL_VIRTUAL_SOL_LAMPORTS + GRADUATION_RAISE_LAMPORTS
        # Token virtual restante en una curva que se agota, del orden de 280e12 unidades base.
        vtok = 279_900_000_000_000
        cap = _market_cap_sol(vsol, vtok, PUMPFUN_TOTAL_SUPPLY)
        assert cap >= GRADUATION_MIN_CAP_SOL
