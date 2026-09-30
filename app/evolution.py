from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from typing import Any

from .backtest import run_backtest
from .strategy import ParamSpec, Strategy


@dataclass
class Candidate:
    params: dict[str, float]
    train: dict[str, Any]
    validation: dict[str, Any]
    fitness: float
    generation: int
    parent_rank: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "train": _compact_metrics(self.train),
            "validation": _compact_metrics(self.validation),
            "fitness": round(self.fitness, 5),
            "generation": self.generation,
            "parent_rank": self.parent_rank,
        }


def _compact_metrics(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "return_pct": result.get("return_pct", 0.0),
        "max_drawdown_pct": result.get("max_drawdown_pct", 0.0),
        "profit_factor": result.get("profit_factor"),
        "win_rate_pct": result.get("win_rate_pct", 0.0),
        "round_trips": result.get("round_trips", 0),
        "end_equity": result.get("end_equity", 0.0),
    }


def _metric_score(result: dict[str, Any]) -> float:
    ret = float(result.get("return_pct") or 0.0)
    drawdown = abs(float(result.get("max_drawdown_pct") or 0.0))
    pf_raw = result.get("profit_factor")
    pf = min(float(pf_raw), 3.0) if pf_raw is not None else 0.0
    trades = int(result.get("round_trips") or 0)

    # Prefer return that did not require large drawdown, with a small
    # profit-factor bonus. Very low trade counts get discounted because one
    # lucky trade is not evidence of a stable strategy.
    score = ret - 0.65 * drawdown + 0.75 * max(0.0, pf - 1.0)
    if trades < 3:
        score -= (3 - trades) * 1.25
    return score


def _fitness(train: dict[str, Any], validation: dict[str, Any]) -> float:
    train_score = _metric_score(train)
    validation_score = _metric_score(validation)
    gap = abs(
        float(train.get("return_pct") or 0.0)
        - float(validation.get("return_pct") or 0.0)
    )

    # Validation carries more weight. The train/validation gap is explicitly
    # penalized so the search does not simply reward unstable spikes.
    return 0.35 * train_score + 0.65 * validation_score - 0.18 * gap


def _snap(value: float, spec: ParamSpec) -> float:
    value = max(spec.min, min(spec.max, value))
    if spec.kind == "int":
        return float(int(round(value)))
    if spec.step:
        steps = round((value - spec.min) / spec.step)
        value = spec.min + steps * spec.step
    return round(value, 8)


def mutate_params(
    strategy: Strategy,
    params: dict[str, float],
    rng: random.Random,
    mutation_rate: float = 0.35,
    scale: float = 0.12,
) -> dict[str, float]:
    child = copy.deepcopy(params)
    specs = [
        spec
        for spec in getattr(strategy, "specs", [])
        if getattr(spec, "tunable", True)
    ]
    touched = False

    for spec in specs:
        if rng.random() > mutation_rate:
            continue
        touched = True
        current = float(child.get(spec.name, spec.default))
        span = max(spec.max - spec.min, spec.step or 1e-9)
        sigma = span * scale
        proposed = current + rng.gauss(0.0, sigma)
        child[spec.name] = _snap(proposed, spec)

    # Every child must actually differ from its parent.
    if specs and not touched:
        spec = rng.choice(specs)
        current = float(child.get(spec.name, spec.default))
        direction = -1 if rng.random() < 0.5 else 1
        step = spec.step or max((spec.max - spec.min) * 0.03, 1e-6)
        child[spec.name] = _snap(current + direction * step, spec)

    # Keep fast EMA below slow EMA for the fusion strategy.
    if "trend_fast" in child and "trend_slow" in child:
        if child["trend_fast"] >= child["trend_slow"]:
            child["trend_fast"] = max(5.0, child["trend_slow"] - 5.0)

    return child


def _instantiate(template: Strategy, params: dict[str, float]) -> Strategy:
    cls = template.__class__
    return cls(params)


def _evaluate(
    template: Strategy,
    params: dict[str, float],
    train_candles: list[dict],
    validation_candles: list[dict],
    generation: int,
    parent_rank: int | None = None,
) -> Candidate:
    train_strategy = _instantiate(template, params)
    validation_strategy = _instantiate(template, params)
    train = run_backtest(train_candles, strategy=train_strategy)
    validation = run_backtest(validation_candles, strategy=validation_strategy)
    return Candidate(
        params=copy.deepcopy(params),
        train=train,
        validation=validation,
        fitness=_fitness(train, validation),
        generation=generation,
        parent_rank=parent_rank,
    )


def run_evolution(
    candles: list[dict],
    strategy: Strategy,
    *,
    generations: int = 5,
    population: int = 24,
    elite_fraction: float = 0.2,
    mutation_rate: float = 0.35,
    mutation_scale: float = 0.10,
    seed: int = 42,
) -> dict[str, Any]:
    if not getattr(strategy, "specs", None):
        raise ValueError("Strategy does not expose mutable parameters")

    min_candles = max(int(getattr(strategy, "min_candles", 30)), 30)
    if len(candles) < max(240, min_candles * 3):
        raise ValueError(
            f"Need at least {max(240, min_candles * 3)} candles for train/validation/test evolution"
        )

    generations = max(1, min(int(generations), 20))
    population = max(6, min(int(population), 100))
    elite_count = max(2, min(population, int(round(population * elite_fraction))))
    mutation_rate = max(0.05, min(float(mutation_rate), 0.9))
    mutation_scale = max(0.01, min(float(mutation_scale), 0.35))

    # Chronological 60/20/20 split. Test is never consulted during mutation.
    n = len(candles)
    train_end = int(n * 0.60)
    validation_end = int(n * 0.80)
    train = candles[:train_end]
    validation = candles[train_end:validation_end]
    test = candles[validation_end:]

    if min(len(train), len(validation), len(test)) < min_candles:
        raise ValueError("Each chronological split must contain enough candles")

    rng = random.Random(seed)
    base_params = {
        spec.name: float(strategy.params.get(spec.name, spec.default))
        for spec in strategy.specs
    }

    history: list[dict[str, Any]] = []
    population_params: list[tuple[dict[str, float], int | None]] = [(base_params, None)]
    while len(population_params) < population:
        population_params.append(
            (
                mutate_params(
                    strategy,
                    base_params,
                    rng,
                    mutation_rate=mutation_rate,
                    scale=mutation_scale,
                ),
                0,
            )
        )

    ranked: list[Candidate] = []
    for generation in range(generations):
        evaluated = [
            _evaluate(
                strategy,
                params,
                train,
                validation,
                generation=generation,
                parent_rank=parent_rank,
            )
            for params, parent_rank in population_params
        ]
        ranked = sorted(evaluated, key=lambda c: c.fitness, reverse=True)
        elites = ranked[:elite_count]

        history.append(
            {
                "generation": generation,
                "best_fitness": round(ranked[0].fitness, 5),
                "median_fitness": round(ranked[len(ranked) // 2].fitness, 5),
                "best_validation_return_pct": ranked[0].validation["return_pct"],
                "best_validation_drawdown_pct": ranked[0].validation[
                    "max_drawdown_pct"
                ],
                "best_params": ranked[0].params,
            }
        )

        if generation == generations - 1:
            break

        next_params: list[tuple[dict[str, float], int | None]] = [
            (copy.deepcopy(candidate.params), rank)
            for rank, candidate in enumerate(elites)
        ]
        while len(next_params) < population:
            parent_rank = rng.randrange(len(elites))
            parent = elites[parent_rank]
            child = mutate_params(
                strategy,
                parent.params,
                rng,
                mutation_rate=mutation_rate,
                scale=mutation_scale,
            )
            next_params.append((child, parent_rank))
        population_params = next_params

    champion = ranked[0]
    base_train = run_backtest(train, strategy=_instantiate(strategy, base_params))
    base_validation = run_backtest(
        validation, strategy=_instantiate(strategy, base_params)
    )

    # Only now reveal the holdout test.
    champion_test = run_backtest(test, strategy=_instantiate(strategy, champion.params))
    base_test = run_backtest(test, strategy=_instantiate(strategy, base_params))

    return {
        "strategy_id": strategy.id,
        "strategy_name": strategy.name,
        "seed": seed,
        "generations": generations,
        "population": population,
        "elite_count": elite_count,
        "mutation_rate": mutation_rate,
        "mutation_scale": mutation_scale,
        "split": {
            "train": len(train),
            "validation": len(validation),
            "test": len(test),
        },
        "base": {
            "params": base_params,
            "train": _compact_metrics(base_train),
            "validation": _compact_metrics(base_validation),
            "test": _compact_metrics(base_test),
        },
        "champion": {
            **champion.as_dict(),
            "test": _compact_metrics(champion_test),
        },
        "history": history,
        "top_candidates": [candidate.as_dict() for candidate in ranked[:5]],
        "note": (
            "Fitness used train + validation only. Holdout test metrics were "
            "computed once after the final champion was selected."
        ),
    }
