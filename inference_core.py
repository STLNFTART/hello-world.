#!/usr/bin/env python3
"""SATELLITE PRIMAL LOGIC INFERENCE CORE (v1.1.0).

One-file executable module for a bounded, closed-loop inference simulation with:
1) gamma-field stabilization,
2) phase-lock PID control,
3) amplitude + coherence regulation,
4) semantic gate,
5) bounded inference manifold,
6) pandas rolling-window analytics and plot output.

Run:
    python3 inference_core.py --steps 40 --window 5 --plot inference_plot.png

Dependencies:
    numpy, pandas, matplotlib
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SimulationConfig:
    """Configuration for the closed-loop simulation.

    Assumptions: all variables are dimensionless normalized proxies.
    """

    steps: int = 40
    dt: float = 1.0
    rolling_window: int = 5


@dataclass
class RuntimeState:
    """Mutable runtime state for iterative loop."""

    gamma: float = 8.5e8
    B: float = 1.0
    state_norm: float = 1.52
    Pi: float = 1.0

    theta: float = 0.7
    theta_star: float = 1.0

    A: float = 0.5
    A_star: float = 1.0

    rho: float = 0.3
    rho_star: float = 0.9

    integral_theta: float = 0.0
    integral_A: float = 0.0
    prev_error_theta: float = 0.0

    external_control_damping: float = 0.3


def saturate(x: float) -> float:
    """Smooth saturator in [-1, 1]."""
    return float(np.tanh(x))


def gamma_eff(gamma: float) -> float:
    """Bound gamma gain to prevent runaway amplification."""
    return float(gamma / (1.0 + np.abs(gamma)))


def phase_lock(
    theta: float,
    theta_star: float,
    integral: float,
    prev_error: float,
    kp: float = 0.8,
    ki: float = 0.1,
    kd: float = 0.05,
    dt: float = 1.0,
) -> tuple[float, float, float]:
    """PID-like phase-lock block (section-style 6.9 behavior)."""
    if dt <= 0:
        raise ValueError("dt must be positive")

    error = theta_star - theta
    integral += error * dt
    derivative = (error - prev_error) / dt

    u = kp * error + ki * integral + kd * derivative
    return float(u), float(integral), float(error)


def amplitude_control(A: float, A_star: float, integral: float, dt: float = 1.0, GA: float = 0.6, HA: float = 0.2) -> tuple[float, float]:
    """Amplitude regulator (section-style 6.10 behavior)."""
    if dt <= 0:
        raise ValueError("dt must be positive")

    e = A_star - A
    integral += e * dt
    return float(GA * e + HA * integral), float(integral)


def coherence_control(rho: float, rho_star: float, G: float = 0.9) -> float:
    """Coherence regulator (section-style 6.11 behavior)."""
    e = rho_star - rho
    return float(G * max(e, 0.0))


def step(state: RuntimeState, t: int, dt: float = 1.0) -> Dict[str, float]:
    """Single closed-loop timestep with bounded inference output."""
        # Gamma stabilization as bounded gain (do not annihilate via (1-c)).
    g = gamma_eff(state.gamma)

    # control channels
    u_theta, state.integral_theta, state.prev_error_theta = phase_lock(
        state.theta,
        state.theta_star,
        state.integral_theta,
        state.prev_error_theta,
        dt=dt,
    )
    u_A, state.integral_A = amplitude_control(state.A, state.A_star, state.integral_A, dt=dt)
    u_rho = coherence_control(state.rho, state.rho_star)

    # unified control, clipped for safety
    # Smoothly bound control to preserve dynamic range and avoid hard clamping at ±1.
    c = float(np.tanh(u_theta + u_A + u_rho))

        # Non-destructive control modulation of gamma channel.
    gamma_field = g * np.exp(-0.2 * c)

    # Dynamic semantic gate (feedback-aware) instead of constant Pi=1.
    state.Pi = saturate(0.5 * state.B + 0.3 * state.Pi)

    # Bounded inference manifold.
    raw = gamma_field * state.B * state.state_norm * state.Pi
    inference = saturate(raw)

        # Toy evolution (satellite-style iterative dynamics) with low-amplitude excitation.
    excitation = 0.05 * np.sin(0.3 * t)
    state.B += excitation * dt
    state.theta += 0.01 * c
    state.A += 0.02 * (state.A_star - state.A)
    state.rho += 0.01 * (state.rho_star - state.rho)

    return {
        "control": c,
        "gamma_field": float(gamma_field),
        "raw": float(raw),
        "inference": inference,
        "theta": float(state.theta),
        "A": float(state.A),
        "rho": float(state.rho),
        "B": float(state.B),
        "Pi": float(state.Pi),
        "state_norm": float(state.state_norm),
    }


def run_simulation(config: SimulationConfig, runtime: RuntimeState) -> pd.DataFrame:
    """Run simulation loop and return DataFrame for analysis."""
    if config.steps <= 0:
        raise ValueError("steps must be > 0")
    if config.rolling_window <= 0:
        raise ValueError("rolling_window must be > 0")

    rows: List[Dict[str, float]] = []
    for t in range(1, config.steps + 1):
        out = step(runtime, t=t, dt=config.dt)
        out["t"] = t
        rows.append(out)
        LOGGER.info(
            "[t=%02d] B=%.3f | R=%.3f | γ=%.3e | inf=%.5f",
            t,
            out["B"],
            out["control"],
            out["gamma_field"],
            out["inference"],
        )

    df = pd.DataFrame(rows)

    # Required user-specific analytics: rolling window average of column X.
    # We define X := inference as primary scalar output.
    df["X"] = df["inference"]
    df["X_roll_mean"] = df["X"].rolling(window=config.rolling_window, min_periods=1).mean()

    # Basic sanity checks.
    if (df["inference"].abs() > 1.0).any():
        raise RuntimeError("Inference escaped bounded range [-1, 1]")
    if not np.isfinite(df["gamma_field"]).all():
        raise RuntimeError("Non-finite gamma_field detected")

    return df


def save_plot(df: pd.DataFrame, output_path: Path) -> None:
    """Save inference and rolling average plot to disk."""
    plt.figure(figsize=(10, 5))
    plt.plot(df["t"], df["X"], label="X (inference)", linewidth=1.8)
    plt.plot(df["t"], df["X_roll_mean"], label="Rolling mean(X)", linewidth=2.2)
    plt.xlabel("Timestep")
    plt.ylabel("Normalized scalar")
    plt.title("Bounded Inference and Rolling-Window Mean")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def parse_args() -> argparse.Namespace:
    """CLI argument parser."""
    parser = argparse.ArgumentParser(description="Run stabilized primal logic inference simulation.")
    parser.add_argument("--steps", type=int, default=40, help="Number of simulation timesteps.")
    parser.add_argument("--dt", type=float, default=1.0, help="Timestep duration (s, normalized).")
    parser.add_argument("--window", type=int, default=5, help="Rolling window size n for column X.")
    parser.add_argument("--plot", type=Path, default=Path("inference_plot.png"), help="Output PNG path.")
    parser.add_argument("--csv", type=Path, default=Path("inference_timeseries.csv"), help="Output CSV path.")
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
    LOGGER.info("Final inference=%.5f, rolling_mean=%.5f", df["X"].iloc[-1], df["X_roll_mean"].iloc[-1])


if __name__ == "__main__":
    main()
