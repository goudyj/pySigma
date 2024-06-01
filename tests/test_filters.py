from pathlib import Path

import pytest

from sigma.collection import SigmaCollection
from sigma.exceptions import SigmaLogsourceError, SigmaDetectionError, SigmaTitleError, SigmaConditionError
from sigma.filters import SigmaFilter, SigmaGlobalFilter
from sigma.processing.conditions import LogsourceCondition
from sigma.processing.pipeline import ProcessingPipeline, ProcessingItem
from sigma.processing.transformations import FieldMappingTransformation
from sigma.rule import SigmaLogSource
from .test_conversion_base import test_backend


@pytest.fixture
def rule_collection():
    return SigmaCollection.from_yaml(
        """
title: Failed login
id: 6f3e2987-db24-4c78-a860-b4f4095a7095
name: failed_login
logsource:
    category: process_creation
    product: windows
detection:
    selection:
        - EventID: 4625
        - EventID2: 4624
    condition: selection 
"""
    )


@pytest.fixture
def sigma_filter():
    return SigmaFilter.from_yaml(
        """
title: Filter Administrator account
description: The valid administrator account start with adm_
logsource:
    category: process_creation
    product: windows
global_filter:
  rules:
    - 6f3e2987-db24-4c78-a860-b4f4095a7095 # Data Compressed - rar.exe
    - df0841c0-9846-4e9f-ad8a-7df91571771b # Login on jump host
  selection:
      User|startswith: 'adm_'
  condition: not selection
  """
    )


def test_filter_valid_1(sigma_filter):
    assert isinstance(sigma_filter, SigmaFilter)
    assert sigma_filter.title == "Filter Administrator account"
    assert sigma_filter.description == "The valid administrator account start with adm_"
    assert sigma_filter.logsource == SigmaLogSource.from_dict(
        {"category": "process_creation", "product": "windows"}
    )
    assert sigma_filter.global_filter == SigmaGlobalFilter.from_dict(
        {
            "rules": [
                "6f3e2987-db24-4c78-a860-b4f4095a7095",
                "df0841c0-9846-4e9f-ad8a-7df91571771b",
            ],
            "selection": {"User|startswith": "adm_"},
            "condition": "not selection",
        }
    )


def test_basic_filter_application(sigma_filter, test_backend, rule_collection):
    rule_collection.rules += [sigma_filter]

    assert test_backend.convert(rule_collection) == [
        '(EventID=4625 or EventID2=4624) and not User startswith "adm_"'
    ]


def test_reducing_rule_collections(sigma_filter, test_backend, rule_collection):
    rule_collection.rules += [sigma_filter]

    assert len(rule_collection.rules) == 2

    # Applies / Flattens all the filters onto the rules in processing
    rule_collection.resolve_rule_references()

    assert len(rule_collection.rules) == 1


def test_filter_with_field_mapping_against_it(sigma_filter, test_backend, rule_collection):
    rule_collection.rules += [sigma_filter]

    # Field Mapping
    test_backend.processing_pipeline.items.append(
        ProcessingItem(
            FieldMappingTransformation({"User": "User123"}),
            rule_conditions=[
                LogsourceCondition(**sigma_filter.logsource.to_dict()),
            ],
        )
    )

    assert test_backend.convert(rule_collection) == [
        '(EventID=4625 or EventID2=4624) and not User123 startswith "adm_"'
    ]


def test_filter_sigma_collection_from_files(test_backend):
    rule_collection = SigmaCollection.load_ruleset([
        Path("tests/files/rule_valid"),
        Path("tests/files/filter_valid")
    ])

    assert len(rule_collection.rules) == 2

    assert test_backend.convert(rule_collection) == [
        'EventID=1234 and not ComputerName startswith "DC-"'
    ]


def test_filter_sigma_collection_from_files_duplicated(test_backend):
    rule_collection = SigmaCollection.load_ruleset([
        Path("tests/files/rule_valid"),
        Path("tests/files/filter_valid"),
        Path("tests/files/filter_valid")
    ])

    assert len(rule_collection.rules) == 3

    assert test_backend.convert(rule_collection) == [
        'EventID=1234 and not ComputerName startswith "DC-" and not ComputerName startswith "DC-"'
    ]


def test_invalid_rule_id_matching(sigma_filter, test_backend, rule_collection):
    # Change the rule id to something else
    rule_collection.rules += [sigma_filter]
    rule_collection.rules[0].id = "invalid-id"

    assert test_backend.convert(rule_collection) == [
        'EventID=4625 or EventID2=4624'
    ]


def test_no_rules_section(sigma_filter, test_backend, rule_collection):
    rule_collection.rules += [sigma_filter]
    rule_collection.rules[1].global_filter.rules = None

    assert test_backend.convert(rule_collection) == [
        'EventID=4625 or EventID2=4624'
    ]


# Validation Errors
@pytest.mark.parametrize('transformation,error', [
    [lambda sf: sf.update(logsource=None) or sf, SigmaLogsourceError],
    [lambda sf: sf.update(global_filter=None) or sf, SigmaDetectionError],
    [lambda sf: sf.update(title=None) or sf, SigmaTitleError],
    [lambda sf: sf.get('global_filter').update(condition=None) or sf, SigmaConditionError],
    [lambda sf: sf.get('global_filter').update(selection=None) or sf, SigmaConditionError],
])
def test_filter_validation_errors(transformation, error, sigma_filter):
    with pytest.raises(error):
        SigmaFilter.from_dict(
            transformation(sigma_filter.to_dict())
        )


