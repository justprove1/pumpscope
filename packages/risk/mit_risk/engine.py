"""RiskEngine DETERMINISTA (SPEC.md 14).

Mismos datos de entrada, misma decision, siempre. Sin ML, sin LLM, sin aleatoriedad y sin
estado oculto: el unico estado que guarda son los kill switches disparados a mano, y eso es
deliberado porque su reactivacion tiene que ser humana.

**El tamano es el MINIMO de todas las restricciones, nunca el maximo de ninguna.** Si fuera
al reves, bastaria una restriccion laxa para saltarse todas las demas.
"""

from __future__ import annotations

from mit_risk.types import (
    AccountState,
    KillSwitch,
    MarketSnapshot,
    RiskDecision,
    RiskLimits,
    SizingInputs,
    SizingResult,
)


class RiskEngine:
    """Decide cuanto se compromete y cuando hay que parar."""

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self._limits = limits or RiskLimits()
        # Switches disparados manualmente o por un incidente. NO se limpian solos.
        self._tripped: dict[KillSwitch, str] = {}

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    # --- Kill switches --------------------------------------------------------------------

    def trip(self, switch: KillSwitch, reason: str) -> None:
        """Dispara un switch. Queda activo hasta que un humano lo reponga."""
        self._tripped[switch] = reason

    def reset(self, switch: KillSwitch, operator: str) -> None:
        """Repone un switch. Exige `operator` porque la reactivacion es un acto humano."""
        if not operator:
            msg = "reponer un kill switch exige identificar al operador"
            raise ValueError(msg)
        self._tripped.pop(switch, None)

    @property
    def tripped(self) -> dict[KillSwitch, str]:
        return dict(self._tripped)

    def kill_switches(
        self, account: AccountState, market: MarketSnapshot
    ) -> tuple[KillSwitch, ...]:
        """Switches activos ahora mismo, por condicion o por disparo manual."""
        limits = self._limits
        active: list[KillSwitch] = list(self._tripped)

        equity = max(1, account.equity_lamports)
        daily_loss_pct = -account.realized_pnl_day_lamports / equity * 100
        if daily_loss_pct > limits.max_daily_loss_pct:
            active.append(KillSwitch.DAILY_LOSS)

        peak = max(1, account.peak_equity_lamports)
        drawdown_pct = (peak - account.equity_lamports) / peak * 100
        if drawdown_pct > limits.max_drawdown_pct:
            active.append(KillSwitch.DRAWDOWN)

        if account.consecutive_losses > limits.max_consecutive_losses:
            active.append(KillSwitch.CONSECUTIVE_LOSSES)
        if market.provider_down:
            active.append(KillSwitch.PROVIDER_DOWN)
        if market.latency_p95_ms > limits.max_latency_p95_ms:
            active.append(KillSwitch.LATENCY)
        if market.error_rate > limits.max_error_rate:
            active.append(KillSwitch.ERROR_RATE)
        if market.price_divergence_pct > limits.max_price_divergence_pct:
            active.append(KillSwitch.PRICE_DIVERGENCE)
        if market.balance_mismatch:
            active.append(KillSwitch.BALANCE_ANOMALY)
        if market.duplicate_transactions > 0:
            active.append(KillSwitch.DUPLICATE_TRANSACTIONS)
        if market.unexpected_exposure:
            active.append(KillSwitch.UNEXPECTED_EXPOSURE)
        if market.unauthorized_signature:
            active.append(KillSwitch.UNAUTHORIZED_SIGNATURE)
        if market.unapproved_config_change:
            active.append(KillSwitch.UNAPPROVED_CONFIG)

        # `dict.fromkeys` en vez de `set`: preserva el orden y hace el resultado determinista.
        return tuple(dict.fromkeys(active))

    # --- Sizing ---------------------------------------------------------------------------

    def size_position(self, account: AccountState, inputs: SizingInputs) -> SizingResult:
        """Tamano de la posicion: el MINIMO de todas las restricciones.

        Se devuelven todas las restricciones calculadas, no solo el resultado: es lo que
        permite explicar por que se opero con ese importe y detectar un limite mal puesto.
        """
        limits = self._limits
        equity = max(0, account.equity_lamports)

        # 1. Riesgo por operacion, ajustado por la distancia al stop.
        stop_distance = max(0.01, inputs.stop_distance_fraction)
        risk_budget = int(equity * limits.risk_per_trade_pct / 100 / stop_distance)

        # 2. Exposicion maxima por token.
        per_token = int(equity * limits.max_exposure_per_token_pct / 100)

        # 3. Margen que queda de exposicion total.
        remaining_total = max(0, limits.max_total_exposure_lamports - account.exposure_lamports)

        # 4. Fraccion maxima de la liquidez: ocupar mas haria imposible la salida.
        by_liquidity = int(inputs.liquidity_lamports * limits.max_liquidity_fraction)

        # 5. Saldo disponible, reservando fees y el coste estimado de SALIR.
        spendable = max(
            0,
            account.balance_lamports
            - limits.min_sol_fee_reserve_lamports
            - inputs.estimated_exit_cost_lamports,
        )

        # 6. Volatilidad y confianza modulan A LA BAJA, nunca al alza.
        confidence = min(1.0, max(0.0, inputs.confidence))
        volatility_factor = 1.0 / (1.0 + max(0.0, inputs.volatility))
        modulated = int(risk_budget * confidence * volatility_factor)

        # 7. La perdida diaria acumulada reduce el tamano ANTES de tocar el limite.
        loss_fraction = (
            -account.realized_pnl_day_lamports / max(1, equity)
            if account.realized_pnl_day_lamports < 0
            else 0.0
        )
        daily_factor = max(0.0, 1.0 - loss_fraction / (limits.max_daily_loss_pct / 100))
        after_losses = int(modulated * daily_factor)

        # 8. La exposicion correlacionada MODULA el presupuesto, no es una restriccion
        #    aparte. Como restriccion independiente casi nunca ataba —otra la superaba
        #    antes— y la correlacion quedaba de adorno. Dos posiciones en la misma
        #    narrativa o del mismo creador son UNA apuesta, y eso tiene que verse en el
        #    tamano, no en una comprobacion que nunca se activa.
        correlated = max(0, inputs.correlated_exposure_lamports)
        correlation_factor = 1.0 / (1.0 + correlated / max(1, per_token))
        after_correlation = int(after_losses * correlation_factor)

        constraints: dict[str, int] = {
            "risk_per_trade": max(0, after_correlation),
            "per_token_exposure": max(0, per_token),
            "total_exposure_remaining": remaining_total,
            "liquidity_fraction": max(0, by_liquidity),
            "spendable_balance": spendable,
            "max_order": limits.max_order_lamports,
        }

        binding = min(constraints, key=lambda name: constraints[name])
        size = constraints[binding]

        # Por debajo del minimo operable no se opera. Nunca se redondea hacia arriba.
        if size < limits.min_order_lamports:
            return SizingResult(lamports=0, binding_constraint=binding, constraints=constraints)
        return SizingResult(lamports=size, binding_constraint=binding, constraints=constraints)

    # --- Autorizacion ---------------------------------------------------------------------

    def can_open(
        self, account: AccountState, market: MarketSnapshot, inputs: SizingInputs
    ) -> RiskDecision:
        """Autoriza (o no) abrir una posicion. Devuelve TODAS las razones del rechazo."""
        reasons: list[str] = []

        switches = self.kill_switches(account, market)
        if switches:
            names = ", ".join(s.value for s in switches)
            reasons.append(f"kill switch activo: {names}")

        if account.open_positions >= self._limits.max_open_positions:
            reasons.append(
                f"ya hay {account.open_positions} posicion(es) abiertas, maximo "
                f"{self._limits.max_open_positions}"
            )

        size = self.size_position(account, inputs)
        if size.lamports <= 0:
            reasons.append(f"el tamano calculado es cero (restriccion: {size.binding_constraint})")

        return RiskDecision(allowed=not reasons, reasons=tuple(reasons))

    def can_add_to_position(self, account: AccountState) -> RiskDecision:
        """SIEMPRE deniega. SPEC.md 13: nada de averaging down automatico en esta version.

        Existe como metodo, y no como ausencia de metodo, para que el rechazo sea explicito y
        testeable: un dia alguien querra anadir a una posicion perdedora, y encontrara esto.
        """
        return RiskDecision(
            allowed=False,
            reasons=(
                "averaging down automatico prohibido (ADD_FORBIDDEN, SPEC.md 13): "
                "anadir a una posicion abierta no esta permitido en esta version",
            ),
        )
