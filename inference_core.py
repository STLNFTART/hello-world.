#!/usr/bin/env python3
"""SATELLITE PRIMAL LOGIC INFERENCE CORE (v1.2.0).

Closed-loop bounded inference simulator with dynamic inference-state feedback.
Includes Pandas rolling-window analytics for column X and plotting.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SimulationConfig:
    """Simulation settings (normalized SI-like proxy units)."""

    steps: int = 50
    dt: float = 0.1
    rolling_window: int = 5


@dataclass
class RuntimeState:
    """Mutable dynamical state."""

    gamma_base: float = 8.5e8
    B: float = 1.0
    theta: float = 0.7
    theta_star: float = 1.0
    A: float = 0.5
    A_star: float = 1.0
    rho: float = 0.3
    rho_star: float = 0.9
    state_norm: float = 1.52
    Pi: float = 1.0

    # New inference state (dynamic, not static readout)
    I_state: float = 0.0

    # Integrators for control channels
    integral_theta: float = 0.0
    integral_A: float = 0.0
    prev_error_theta: float = 0.0


def saturate(x: float) -> float:
    """Smooth bounded nonlinearity in [-1, 1]."""
    return float(np.tanh(x))


def gamma_eff(gamma: float) -> float:
    """Bound potentially large gamma to a stable range."""
    return float(gamma / (1.0 + abs(gamma)))


def phase_lock(theta: float, theta_star: float, integral: float, prev_error: float, dt: float,
               kp: float = 0.8, ki: float = 0.1, kd: float = 0.05) -> tuple[float, float, float]:
    """PID-like phase-lock control."""
    if dt <= 0:
        raise ValueError("dt must be > 0")
    error = theta_star - theta
    integral += error * dt
    derivative = (error - prev_error) / dt
    u = kp * error + ki * integral + kd * derivative
    return float(u), float(integral), float(error)


def amplitude_control(A: float, A_star: float, integral: float, dt: float,
                      ga: float = 0.6, ha: float = 0.2) -> tuple[float, float]:
    """Amplitude correction channel."""
    if dt <= 0:
        raise ValueError("dt must be > 0")
    e = A_star - A
    integral += e * dt
    return float(ga * e + ha * integral), float(integral)


def coherence_control(rho: float, rho_star: float, g: float = 0.9) -> float:
    """Coherence correction channel (one-sided)."""
    return float(g * max(rho_star - rho, 0.0))


def update_inference_state(I_prev: float, R: float, gamma_field: float, B: float, dt: float,
                           alpha: float = 0.35, beta: float = 0.55, delta: float = 0.12) -> float:
    """Dynamic inference-state equation.

    dI/dt = alpha*R + beta*gamma_field*B - delta*I
    """
    dI = alpha * R + beta * gamma_field * B - delta * I_prev
    return float(I_prev + dI * dt)


def step(state: RuntimeState, t: int, dt: float = 0.1, kappa: float = 0.2) -> Dict[str, float]:
    """One simulation step with inference feedback into control."""
    # External excitation to prevent low-entropy fixed-point collapse.
    excitation = 0.05 * np.sin(0.3 * t)
    state.B += excitation * dt

    # Base bounded gamma channel.
    g = gamma_eff(state.gamma_base)

    # Control primitives.
    u_theta, state.integral_theta, state.prev_error_theta = phase_lock(
        state.theta, state.theta_star, state.integral_theta, state.prev_error_theta, dt
    )
    u_A, state.integral_A = amplitude_control(state.A, state.A_star, state.integral_A, dt)
    u_rho = coherence_control(state.rho, state.rho_star)

    # Dynamic semantic gate.
    state.Pi = saturate(0.5 * state.B + 0.3 * state.I_state)

    # Temporary regulator prior to inference feedback.
    r_pre = saturate(u_theta + u_A + u_rho)

    # Gamma modulation (non-destructive).
    gamma_field = g * np.exp(-0.2 * r_pre)

    # Inference state dynamics (new layer).
    state.I_state = update_inference_state(state.I_state, r_pre, gamma_field, state.B, dt)

    # Closed-loop control injection from inference state.
    c = saturate(u_theta + u_A + u_rho + kappa * state.I_state)

    # Final regulator and bounded inference readout.
    R = c
    raw = gamma_field * state.B * state.state_norm * state.Pi
    inference = saturate(raw + 0.25 * state.I_state)

    # Update remaining slow states.
    state.theta += 0.01 * c
    state.A += 0.02 * (state.A_star - state.A)
    state.rho += 0.01 * (state.rho_star - state.rho)

    return {
        "t": float(t),
        "B": float(state.B),
        "R": float(R),
        "gamma_field": float(gamma_field),
        "Pi": float(state.Pi),
        "I_state": float(state.I_state),
        "control": float(c),
        "raw": float(raw),
        "inference": float(inference),
        "theta": float(state.theta),
        "A": float(state.A),
        "rho": float(state.rho),
        "state_norm": float(state.state_norm),
    }


def run_simulation(config: SimulationConfig, runtime: RuntimeState) -> pd.DataFrame:
    """Run simulation and return DataFrame with rolling analytics."""
    if config.steps <= 0:
        raise ValueError("steps must be > 0")
    if config.dt <= 0:
        raise ValueError("dt must be > 0")
    if config.rolling_window <= 0:
        raise ValueError("rolling_window must be > 0")

    rows: List[Dict[str, float]] = []
    for t in range(1, config.steps + 1):
        out = step(runtime, t=t, dt=config.dt)
        rows.append(out)
        LOGGER.info("[t=%02d] B=%.3f R=%.3f γ=%.3f I=%.4f inf=%.4f", t, out["B"], out["R"], out["gamma_field"], out["I_state"], out["inference"])

    df = pd.DataFrame(rows)
    df["X"] = df["inference"]
    df["X_roll_mean"] = df["X"].rolling(window=config.rolling_window, min_periods=1).mean()

    # Safety checks.
    if (df["inference"].abs() > 1.0).any():
        raise RuntimeError("inference escaped [-1,1]")
    if not np.isfinite(df[["gamma_field", "I_state", "R"]].to_numpy()).all():
        raise RuntimeError("non-finite values detected")

    return df


def save_plot(df: pd.DataFrame, output_path: Path) -> None:
    """Plot X and rolling mean(X) versus time."""
    plt.figure(figsize=(10, 5))
    plt.plot(df["t"], df["X"], label="X (inference)", linewidth=1.8)
    plt.plot(df["t"], df["X_roll_mean"], label="rolling mean(X)", linewidth=2.2)
    plt.xlabel("timestep")
    plt.ylabel("normalized value")
    plt.title("Inference signal with rolling average")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="Closed-loop inference simulator")
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--dt", type=float, default=0.1)
    parser.add_argument("--window", type=int, default=5)
    parser.add_argument("--csv", type=Path, default=Path("inference_timeseries.csv"))
    parser.add_argument("--plot", type=Path, default=Path("inference_plot.png"))
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()
    config = SimulationConfig(steps=args.steps, dt=args.dt, rolling_window=args.window)
    runtime = RuntimeState()
    df = run_simulation(config, runtime)
    df.to_csv(args.csv, index=False)
    save_plot(df, args.plot)
    LOGGER.info("Saved CSV: %s", args.csv)
    LOGGER.info("Saved plot: %s", args.plot)


if __name__ == "__main__":
    main()
