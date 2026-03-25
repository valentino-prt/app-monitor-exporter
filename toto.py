from __future__ import annotations

from dataclasses import dataclass
from abc import ABC, abstractmethod


@dataclass(slots=True)
class Instrument:
    position: float
    price: float
    spot: float


@dataclass(slots=True)
class FactorBase(Instrument, ABC):
    lev: float
    fixing: float

    def __post_init__(self) -> None:
        if self.price < 0:
            raise ValueError("price must be >= 0")
        if self.spot <= 0:
            raise ValueError("spot must be > 0")
        if self.fixing <= 0:
            raise ValueError("fixing must be > 0")
        if self.lev == 0:
            raise ValueError("lev must be non-zero")

    @property
    @abstractmethod
    def break_even(self) -> float:
        """
        Move de wipe-out / break-even théorique du produit.
        Exprimé en rendement du sous-jacent.
        Exemple:
        - bull x10  -> -0.10
        - bear x10  -> +0.10
        """
        raise NotImplementedError

    @property
    def break_even_yesterday(self) -> float:
        """
        Seuil break-even exprimé depuis le spot live,
        en repartant du fixing de la veille.
        """
        return (self.break_even * self.fixing - self.spot + self.fixing) / self.spot

    @property
    def delta_nominal(self) -> float:
        """
        Approximation simple du delta nominal.
        Convention:
        - short bull -> delta_nominal > 0
        - short bear -> delta_nominal < 0
        """
        return -self.position * self.price * self.lev

    def raw_pnl_at_scenario(self, scenario: float, threshold: float) -> float:
        """
        PnL brut linéarisé au scénario donné par rapport à un seuil.
        """
        return self.delta_nominal * (scenario - threshold)

    @staticmethod
    def loss_from_raw_pnl(raw_pnl: float) -> float:
        """
        Convertit un PnL signé en perte positive.
        """
        return max(-raw_pnl, 0.0)

    def loss_at_break_even(self, scenario: float) -> float:
        """
        Perte positive mesurée par rapport au break-even courant.
        """
        raw = self.raw_pnl_at_scenario(scenario, self.break_even)
        return self.loss_from_raw_pnl(raw)

    def loss_at_break_even_yesterday(self, scenario: float) -> float:
        """
        Perte positive mesurée par rapport au break-even ajusté veille.
        """
        raw = self.raw_pnl_at_scenario(scenario, self.break_even_yesterday)
        return self.loss_from_raw_pnl(raw)

    def gap_addon(self, scenario: float) -> float:
        """
        Add-on positif entre le seuil veille et le seuil courant.

        Idée métier:
        - on calcule la perte positive associée au seuil "yesterday"
        - on calcule la perte positive associée au seuil "today"
        - on prend l'excès de perte, borné à 0
        """
        loss_yesterday = self.loss_at_break_even_yesterday(scenario)
        loss_today = self.loss_at_break_even(scenario)
        return max(loss_yesterday - loss_today, 0.0)

    def explain(self, scenario: float) -> dict[str, float]:
        """
        Méthode utile pour debug / logs / tests.
        """
        raw_yesterday = self.raw_pnl_at_scenario(scenario, self.break_even_yesterday)
        raw_today = self.raw_pnl_at_scenario(scenario, self.break_even)

        loss_yesterday = self.loss_from_raw_pnl(raw_yesterday)
        loss_today = self.loss_from_raw_pnl(raw_today)
        addon = max(loss_yesterday - loss_today, 0.0)

        return {
            "scenario": scenario,
            "break_even": self.break_even,
            "break_even_yesterday": self.break_even_yesterday,
            "delta_nominal": self.delta_nominal,
            "raw_pnl_yesterday": raw_yesterday,
            "raw_pnl_today": raw_today,
            "loss_yesterday": loss_yesterday,
            "loss_today": loss_today,
            "gap_addon": addon,
        }


@dataclass(slots=True)
class BullFactor(FactorBase):
    @property
    def break_even(self) -> float:
        return -1.0 / self.lev


@dataclass(slots=True)
class BearFactor(FactorBase):
    @property
    def break_even(self) -> float:
        return -1.0 / self.lev