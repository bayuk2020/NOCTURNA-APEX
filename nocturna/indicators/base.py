"""Indicator engine core: plugin architecture, no Pine Script.

Every indicator is a Python class subclassing `Indicator`. It declares its
parameters, whether it draws as an overlay or in a separate subwindow, and its
plot lines/colors. `compute(df)` returns a dict of named pandas Series aligned to
the input OHLC frame's index.

The registry supports: create, delete, modify, enable/disable, overlay vs
separate window, custom colors, and multiple instances of the same indicator
(each instance carries its own params + colors + id).

Plugin loading: drop a *.py file into a plugins dir; any `Indicator` subclass
found is auto-registered. So "future indicators installable as plugins" is just
"add a file".
"""
from __future__ import annotations

import importlib.util
import itertools
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd


@dataclass
class Plot:
    """A single line/histogram this indicator outputs."""
    key: str                      # matches a key in compute()'s output dict
    color: str = "#2962FF"
    kind: str = "line"            # line | histogram | band
    width: int = 1


class Indicator:
    """Base class. Subclass and implement `compute`."""

    name: ClassVar[str] = "Indicator"
    overlay: ClassVar[bool] = True          # True: on price chart. False: subwindow.
    default_params: ClassVar[dict[str, Any]] = {}
    plots: ClassVar[list[Plot]] = []

    def __init__(self, params: dict | None = None,
                 colors: dict[str, str] | None = None,
                 instance_id: str | None = None,
                 enabled: bool = True):
        self.params = {**self.default_params, **(params or {})}
        self.instance_id = instance_id or uuid.uuid4().hex[:8]
        self.enabled = enabled
        # per-instance color overrides keyed by plot.key
        self.colors = {p.key: p.color for p in self.plots}
        if colors:
            self.colors.update(colors)

    def compute(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        raise NotImplementedError

    # convenience for strategies: latest value of a plot key
    def last(self, df: pd.DataFrame, key: str):
        return self.compute(df)[key].iloc[-1]

    def __repr__(self) -> str:
        return f"<{self.name}#{self.instance_id} params={self.params} enabled={self.enabled}>"


class IndicatorRegistry:
    """Holds indicator *classes* (types) and live *instances* on a chart."""

    def __init__(self):
        self._types: dict[str, type[Indicator]] = {}
        self._instances: dict[str, Indicator] = {}

    # --- type registration ---
    def register(self, cls: type[Indicator]) -> None:
        if not (isinstance(cls, type) and issubclass(cls, Indicator) and cls is not Indicator):
            raise TypeError("register expects an Indicator subclass")
        self._types[cls.name] = cls

    def available(self) -> list[str]:
        return sorted(self._types)

    def load_plugins(self, folder: str | Path) -> int:
        """Import every .py in `folder` and register Indicator subclasses found."""
        folder = Path(folder)
        count = 0
        for py in folder.glob("*.py"):
            spec = importlib.util.spec_from_file_location(py.stem, py)
            if not spec or not spec.loader:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for obj in vars(mod).values():
                if isinstance(obj, type) and issubclass(obj, Indicator) and obj is not Indicator:
                    self.register(obj)
                    count += 1
        return count

    # --- instance lifecycle (create / delete / modify / enable / disable) ---
    def create(self, type_name: str, params=None, colors=None, enabled=True) -> Indicator:
        if type_name not in self._types:
            raise KeyError(f"Unknown indicator type {type_name!r}. Available: {self.available()}")
        inst = self._types[type_name](params=params, colors=colors, enabled=enabled)
        self._instances[inst.instance_id] = inst
        return inst

    def delete(self, instance_id: str) -> None:
        self._instances.pop(instance_id, None)

    def modify(self, instance_id: str, params=None, colors=None) -> Indicator:
        inst = self._instances[instance_id]
        if params:
            inst.params.update(params)
        if colors:
            inst.colors.update(colors)
        return inst

    def enable(self, instance_id: str, on: bool = True) -> None:
        self._instances[instance_id].enabled = on

    def disable(self, instance_id: str) -> None:
        self.enable(instance_id, False)

    def instances(self, only_enabled: bool = False) -> list[Indicator]:
        vals = list(self._instances.values())
        return [i for i in vals if i.enabled] if only_enabled else vals

    def get(self, instance_id: str) -> Indicator:
        return self._instances[instance_id]

    def compute_all(self, df: pd.DataFrame, only_enabled: bool = True) -> dict[str, dict[str, pd.Series]]:
        out = {}
        for inst in self.instances(only_enabled=only_enabled):
            out[inst.instance_id] = inst.compute(df)
        return out
