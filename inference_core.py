#!/usr/bin/env python3
"""inference_core.py

v1.0.0 - Unified, bounded inference engine for satellite-coherence-style control.

This module implements a closed-loop inference stack with:
- physical state norm (x, v, B)
- semantic gate Π(t)
- control law c(t)
- bounded gamma-field modulation R_f(t)
- final stabilized inference scalar I(t)

Units/assumptions:
- x: normalized position state [dimensionless proxy]
- v: normalized velocity state [dimensionless proxy]
- B: normalized field magnitude [dimensionless proxy]
- gamma: sensitivity gain (dimensionless after normalization)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict

import numpy as np


logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")


@dataclass(frozen=True)
class State:
    """Physical state for one timestep."""

    x: float
    v: float
    B: float


@dataclass(frozen=True)
class Errors:
    """Control errors for phase/area/coherence channels."""

    theta: float
    A: float
    rho: float


@dataclass(frozen=True)
class SemanticConfig:
    """Semantic gate configuration."""

    dist: float
    prob_sum: float
    theta_d: float = 0.3
    theta_p: float = 1.0


@dataclass(frozen=True)
class GammaParams:
    """Gamma field parameters.

    gamma: raw gain-like sensitivity term (can be large, will be normalized)
    mu: centering term for B
    sigma: scale term for B (must be > 0 for z-score behavior)
    """

    gamma: float
    mu: float
    sigma: float
    max_gamma: float = 1e9


def state_norm(state: State) -> float:
    """Euclidean norm of (x, v, B)."""
    return float(np.sqrt(state.x**2 + state.v**2 + state.B**2))


def semantic_gate(cfg: SemanticConfig) -> float:
    """Binary robust semantic gate Π(t) in {0,1}."""
    return 1.0 if (cfg.dist < cfg.theta_d and cfg.prob_sum <= cfg.theta_p) else 0.0


def control(errors: Errors, kp: float = 0.8, ki: float = 0.1, kd: float = 0.05) -> float:
    """Simplified PID + coherence correction control law c(t)."""
    integral = errors.theta
    derivative = errors.theta

    u_theta = kp * errors.theta + ki * integral + kd * derivative
    u_A = 0.6 * errors.A
    u_rho = 0.7 * max(errors.rho, 0.0)
    return float(u_theta + u_A + u_rho)


def gamma_effective(gamma: float, max_gamma: float = 1e9) -> float:
    """Normalize gamma to bounded sensitivity weight.

    Returns value in approximately [-1, 1] for bounded behavior.
    """
    if max_gamma <= 0:
        raise ValueError("max_gamma must be > 0")
    return float(gamma / max_gamma)


def fractal_residual_gamma_stable(B: float, params: GammaParams) -> float:
    """Bounded gamma-modulated residual: tanh(gamma_eff * zscore(B))."""
    if params.sigma == 0:
        raise ValueError("sigma must be non-zero for stable z-score normalization")

    z = (B - params.mu) / params.sigma
    g_eff = gamma_effective(params.gamma, params.max_gamma)
    return float(np.tanh(g_eff * z))


def stabilized_inference(pi: float, c: float, rf: float, s_norm: float) -> float:
    """Final bounded inference scalar.

    Uses residual attenuation to avoid field domination:
    I = Π * c * (R_f / (1 + |R_f|)) * ||S||
    """
    return float(pi * c * (rf / (1.0 + abs(rf))) * s_norm)


def inference_step(
    state: State,
    errors: Errors,
    semantic: SemanticConfig,
    gamma_params: GammaParams,
) -> Dict[str, float]:
    """Run one inference timestep and return all intermediate scalars."""
    pi = semantic_gate(semantic)
    c = control(errors)
    rf = fractal_residual_gamma_stable(state.B, gamma_params)
    s_norm = state_norm(state)
    inference = stabilized_inference(pi, c, rf, s_norm)

    return {
        "Pi": pi,
        "control": c,
        "gamma_field": rf,
        "state_norm": s_norm,
        "inference": inference,
    }


def _sanity_checks(output: Dict[str, float]) -> None:
    """Basic runtime validations for physical/numerical limits."""
    if not (-1.0 <= output["gamma_field"] <= 1.0):
        raise RuntimeError("gamma_field out of bounded range [-1, 1]")
    if not np.isfinite(output["inference"]):
        raise RuntimeError("inference is not finite")


if __name__ == "__main__":
    test_state = State(x=0.5, v=1.2, B=0.8)
    test_errors = Errors(theta=0.25, A=0.4, rho=0.3)
    test_semantic = SemanticConfig(dist=0.2, prob_sum=0.7)
    test_gamma = GammaParams(gamma=2.675e8, mu=1.0, sigma=0.25)

    result = inference_step(test_state, test_errors, test_semantic, test_gamma)
    _sanity_checks(result)

    logging.info("=== INFERENCE OUTPUT (STABILIZED) ===")
    for key, val in result.items():
        logging.info("%s: %s", key, val)
