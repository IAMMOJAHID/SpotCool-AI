"""Scenario files: find them, read their YAML with line numbers, validate them.

The loader builds the data itself from PyYAML's node tree, so it knows the line of every key and
list item, and it reads a plain ``HH:MM`` as text: YAML 1.1 would read an unquoted ``10:00`` as
the number 600 (base 60). Aliases (``*name``) are not supported: each one is reported at its
line and never followed, so a self-referencing or nested-alias file cannot recurse or blow up.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from spotcool_world.scenarios.schema import Scenario

LIBRARY_DIR = Path(__file__).with_name("library")
"""The bundled scenarios, one ``<name>.yaml`` each."""

Loc = tuple[str | int, ...]
"""Where a value sits in a scenario file, as keys and list indexes (``("events", 2, "to")``)."""

_CLOCK = re.compile(r"\d{1,2}:\d{2}")
_ALIAS = "!spotcool/alias"  # tag of the marker node composed in place of an alias


class _Loader(yaml.SafeLoader):
    """A SafeLoader that composes each alias as a marker node at the alias's own position."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            event = self.get_event()
            return yaml.ScalarNode(_ALIAS, "", event.start_mark, event.end_mark)
        return super().compose_node(parent, index)


class ScenarioFileError(ValueError):
    """A scenario file is not valid YAML or does not match the schema.

    Args:
        path: The file.
        problems: ``(line, message)`` for every problem; lines are 1-based.
    """

    def __init__(self, path: Path, problems: list[tuple[int, str]]) -> None:
        super().__init__("; ".join(f"{path}:{line}: {message}" for line, message in problems))
        self.path = path
        self.problems = problems


@dataclass(frozen=True)
class LoadedScenario:
    """A validated scenario and where each of its items sits in its file.

    Parameters:
        path: The file.
        scenario: The scenario.
        lines: The 1-based line of every key and list item, by location (``()`` is line 1).
    """

    path: Path
    scenario: Scenario
    lines: dict[Loc, int]

    def line(self, loc: Loc) -> int:
        """The line of ``loc``, or of the nearest item around it that the file has.

        Args:
            loc: Keys and list indexes.
        """
        return self.lines[_known(self.lines, loc)]


def library_names() -> list[str]:
    """The names of the bundled scenarios, sorted."""
    return sorted(path.stem for path in LIBRARY_DIR.glob("*.yaml"))


def scenario_path(source: str | Path) -> Path:
    """The file of a scenario given as a library name or a path to a ``.yaml`` file.

    Args:
        source: A library name (``hybrid_tuesday``) or a path ending in ``.yaml`` or ``.yml``.

    Raises:
        ValueError: If ``source`` is neither.
    """
    path = Path(source)
    if path.suffix in (".yaml", ".yml"):
        return path
    if str(source) in library_names():
        return LIBRARY_DIR / f"{source}.yaml"
    names = ", ".join(library_names())
    raise ValueError(f"unknown scenario {str(source)!r}: give a .yaml file or one of {names}")


def load_scenario(source: str | Path) -> LoadedScenario:
    """Read and validate a scenario.

    Args:
        source: A library name or a path to a ``.yaml`` file.

    Raises:
        ScenarioFileError: If the file is not valid YAML or does not match the schema.
        ValueError: If ``source`` names no scenario, or the file is not UTF-8.
        OSError: If the file cannot be read.
    """
    path = scenario_path(source)
    text = path.read_text(encoding="utf-8")
    try:
        data, lines, problems = _parse(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else 1
        raise ScenarioFileError(path, [(line, f"invalid YAML: {_problem(exc)}")]) from None
    except RecursionError:
        raise ScenarioFileError(path, [(1, "invalid YAML: nested too deeply")]) from None
    try:
        scenario = Scenario.model_validate(data)
    except ValidationError as exc:
        scenario = None
        for error in exc.errors():
            loc = tuple(error["loc"])
            message = error["msg"]
            if error["type"] == "value_error":  # our validators' own words, without "Value error, "
                message = str(error.get("ctx", {}).get("error", message))
            problems.append((lines[_known(lines, loc)], f"{_where(lines, loc)}{message}"))
    if scenario is None or problems:
        raise ScenarioFileError(path, sorted(problems, key=lambda problem: problem[0]))
    return LoadedScenario(path, scenario, lines)


def _parse(text: str) -> tuple[Any, dict[Loc, int], list[tuple[int, str]]]:
    """YAML text to plain data, the line of every key and list item, and the problems found."""
    loader = _Loader(text)
    try:
        root = loader.get_single_node()
        lines: dict[Loc, int] = {(): 1}
        problems: list[tuple[int, str]] = []
        data = None if root is None else _build(loader, root, (), lines, problems)
        return data, lines, problems
    finally:
        loader.dispose()


def _build(
    loader: yaml.SafeLoader,
    node: yaml.Node,
    loc: Loc,
    lines: dict[Loc, int],
    problems: list[tuple[int, str]],
) -> Any:
    if node.tag == _ALIAS:
        problems.append((node.start_mark.line + 1, "aliases are not supported"))
        return None
    if isinstance(node, yaml.MappingNode):
        mapping: dict[str, Any] = {}
        for key_node, value_node in node.value:
            line = key_node.start_mark.line + 1
            if key_node.tag == _ALIAS:
                problems.append((line, "aliases are not supported"))
                continue
            if not isinstance(key_node, yaml.ScalarNode):
                problems.append((line, "a key must be a plain name"))
                continue
            key = str(key_node.value)  # as written: YAML 1.1 would turn `on:` into True
            if key in mapping:
                problems.append((line, f"duplicate key {key!r}"))
                continue
            lines[(*loc, key)] = line
            mapping[key] = _build(loader, value_node, (*loc, key), lines, problems)
        return mapping
    if isinstance(node, yaml.SequenceNode):
        items: list[Any] = []
        for index, item_node in enumerate(node.value):
            lines[(*loc, index)] = item_node.start_mark.line + 1
            items.append(_build(loader, item_node, (*loc, index), lines, problems))
        return items
    if isinstance(node, yaml.ScalarNode) and node.style is None and _CLOCK.fullmatch(node.value):
        return node.value
    return loader.construct_object(node)


def _known(lines: dict[Loc, int], loc: Loc) -> Loc:
    """The part of ``loc`` the file has, skipping union tags and ``[key]`` markers."""
    known: Loc = ()
    for part in loc:
        if (*known, part) in lines:
            known = (*known, part)
    return known


def _where(lines: dict[Loc, int], loc: Loc) -> str:
    """``zones.cor[2].people: `` for a validation error at ``loc``; empty at the top."""
    known = _known(lines, loc)
    last = loc[-1] if loc else None
    if isinstance(last, str) and last != "[key]" and known[-1:] != (last,):
        known = (*known, last)  # a missing field: the file has no line for it
    text = "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in known)
    return f"{text.lstrip('.')}: " if text else ""


def _problem(exc: yaml.YAMLError) -> str:
    problem = getattr(exc, "problem", None)
    context = getattr(exc, "context", None)
    return f"{problem} ({context})" if problem and context else str(problem or exc)
