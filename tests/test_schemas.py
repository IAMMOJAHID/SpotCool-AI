import pytest

from app.spotcool_agent.schemas import CriticPolicyWire, SparkWire, extract_json, parse_wire, strict_json_schema


def objects(node):
    if isinstance(node, dict):
        if "properties" in node:
            yield node
        for value in node.values():
            yield from objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from objects(item)


@pytest.mark.parametrize("model", [SparkWire, CriticPolicyWire])
def test_strict_schema_requires_every_property_and_forbids_extras(model):
    found = list(objects(strict_json_schema(model)))
    assert found
    for node in found:
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])
        assert all("default" not in prop for prop in node["properties"].values())


def test_extract_json_handles_fences_and_prose():
    assert extract_json('Sure! ```json\n{"a": 1}\n``` done') == {"a": 1}
    assert extract_json('Result: {"a": {"b": "}"}} trailing') == {"a": {"b": "}"}}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_parse_wire_is_lenient_about_missing_and_extra_fields():
    wire = parse_wire(CriticPolicyWire, '{"manipulated_variables": {"target_temp_c": 24}, "unexpected": 1}')
    assert wire.manipulated_variables.target_temp_c == 24
    assert wire.manipulated_variables.fan_speed is None and wire.reward_evaluation.score is None
