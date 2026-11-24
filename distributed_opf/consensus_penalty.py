"""Numerically centered, algebraically equivalent quadratic ADMM penalties."""
import os
import gurobipy as gp
from gurobipy import GRB


def install_centered_penalties(model, specifications):
    if os.getenv("ADMM_CENTERED_PENALTY", "0") != "1":
        return
    # specifications may contain repeated occurrences of a shared variable.
    weights, variables = {}, {}
    for variable, weight in specifications:
        weights[variable.index] = weights.get(variable.index, 0.) + weight
        variables[variable.index] = variable
    objective = model.getObjective()
    entries = {}
    for index, weight in weights.items():
        if weight <= 0:
            raise ValueError("Centered consensus requires positive weights")
        variable = variables[index]
        deviation = model.addVar(lb=-GRB.INFINITY, name=f"consensus_delta_{index}")
        target = model.addConstr(variable - deviation == 0., name=f"consensus_target_{index}")
        objective += .5 * weight * (deviation * deviation - variable * variable)
        entries[index] = (variable, deviation, target, weight)
    model.setObjective(objective, GRB.MINIMIZE)
    # Substituting delta=x-target would recreate the cancellation we avoid.
    model.setParam("Aggregate", 0)
    model.update()
    model._ac_centered_penalties = entries


def set_consensus_linear(model, variable, coefficient):
    entry = getattr(model, "_ac_centered_penalties", {}).get(variable.index)
    if entry is None:
        variable.Obj = coefficient
        return
    _, _, target, weight = entry
    # .5*w*x^2+c*x == .5*w*(x+c/w)^2 - .5*c^2/w.
    # The omitted constant cannot affect the local minimizer.
    target.RHS = -coefficient / weight
    variable.Obj = 0.


def start_centered_penalties(model, previous):
    for variable, deviation, target, _ in getattr(model, "_ac_centered_penalties", {}).values():
        value = previous[variable.index] if previous is not None else variable.Start
        if abs(value) < 1e99:
            deviation.PStart = value - target.RHS
