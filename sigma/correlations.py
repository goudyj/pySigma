from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional
import sigma.exceptions as sigma_exceptions
from sigma.exceptions import SigmaRuleLocation, SigmaTimespanError
from sigma.rule import EnumLowercaseStringMixin, SigmaRule, SigmaRuleBase
import sigma


class SigmaCorrelationType(EnumLowercaseStringMixin, Enum):
    """
    Supported correlation types.
    """

    EVENT_COUNT = auto()
    VALUE_COUNT = auto()
    TEMPORAL = auto()


@dataclass(unsafe_hash=True)
class SigmaRuleReference:
    """
    Reference to a Sigma rule. Initially this only contains the plain reference as string that is
    then resolved into a rule reference.
    """

    reference: str
    rule: SigmaRule = field(init=False, repr=False, compare=False)

    def resolve(self, rule_collection: "sigma.collection.SigmaCollection"):
        """
        Resolves the reference to the actual Sigma rule.

        Raises:
            sigma_exceptions.SigmaRuleNotFoundError: If the referenced rule cannot be found in the given rule collection.
        """
        self.rule = rule_collection[self.reference]


class SigmaCorrelationConditionOperator(Enum):
    LT = auto()
    LTE = auto()
    GT = auto()
    GTE = auto()


@dataclass
class SigmaCorrelationCondition:
    op: SigmaCorrelationConditionOperator
    count: int
    source: Optional[SigmaRuleLocation] = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, d: dict, source: Optional[SigmaRuleLocation] = None
    ) -> "SigmaCorrelationCondition":
        if len(d) != 1:
            raise sigma_exceptions.SigmaCorrelationConditionError(
                f"Sigma correlation condition must have exactly one item"
            )

        cond_def = list(d.items())[0]
        try:
            cond_op = SigmaCorrelationConditionOperator[cond_def[0].upper()]
        except KeyError:
            raise sigma_exceptions.SigmaCorrelationConditionError(
                f"Sigma correlation condition operator '{ cond_def[0] }' is invalid"
            )

        try:
            cond_count = int(cond_def[1])
        except ValueError:
            raise sigma_exceptions.SigmaCorrelationConditionError(
                f"'{ cond_def[1] }' is no valid Sigma correlation condition count"
            )

        return cls(op=cond_op, count=cond_count)

    def to_dict(self) -> dict:
        return {self.op.name.lower(): self.count}


@dataclass
class SigmaCorrelationTimespan:
    spec: str = field(compare=False)
    seconds: int = field(init=False)
    count: int = field(init=False)
    unit: str = field(init=False)

    def __post_init__(self):
        """
        Parses a string representing a time span and stores the equivalent number of seconds.

        Raises:
            sigma_exceptions.SigmaTimespanError: If the given time span is invalid.
        """
        try:
            self.count = int(self.spec[:-1])
            self.unit = self.spec[-1]
            self.seconds = (
                self.count
                * {
                    "s": 1,
                    "m": 60,
                    "h": 3600,
                    "d": 86400,
                    "w": 604800,
                    "M": 2629746,
                    "y": 31556952,
                }[self.unit]
            )
        except (ValueError, KeyError):
            raise sigma_exceptions.SigmaTimespanError(f"Timespan '{ self.spec }' is invalid.")


@dataclass
class SigmaCorrelationFieldAlias:
    """
    The Sigma rules used in a correlation rule possibly match events that use different field names
    for the same information. An alias field definition maps a field name that can be used in the
    group-by definition of a correlation rule to their respective field names in the events matched
    by the Sigma rules.
    """

    alias: str
    mapping: Dict[SigmaRuleReference, str]

    def resolve_rule_references(self, rule_collection: "sigma.collection.SigmaCollection"):
        """
        Resolves all rule references in the mapping property to actual Sigma rules.

        Raises:
            sigma_exceptions.SigmaRuleNotFoundError: If a referenced rule cannot be found in the given rule collection.
        """
        for rule_ref in self.mapping.keys():
            rule_ref.resolve(rule_collection)


@dataclass
class SigmaCorrelationFieldAliases:
    aliases: Dict[str, SigmaCorrelationFieldAlias] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict):
        aliases = {}
        for alias, mapping in d.items():
            if not isinstance(mapping, dict):
                raise sigma_exceptions.SigmaCorrelationRuleError(
                    "Sigma correlation field alias mapping must be a dict"
                )

            aliases[alias] = SigmaCorrelationFieldAlias(
                alias=alias,
                mapping={
                    SigmaRuleReference(rule_ref): field_name
                    for rule_ref, field_name in mapping.items()
                },
            )

        return cls(aliases=aliases)

    def to_dict(self) -> dict:
        return {
            alias: {
                rule_ref.reference: field_name for rule_ref, field_name in alias_def.mapping.items()
            }
            for alias, alias_def in self.aliases.items()
        }

    def resolve_rule_references(self, rule_collection: "sigma.collection.SigmaCollection"):
        """
        Resolves all rule references in the aliases property to actual Sigma rules.

        Raises:
            sigma_exceptions.SigmaRuleNotFoundError: If a referenced rule cannot be found in the given rule collection.
        """
        for alias in self.aliases:
            alias.resolve_rule_reference(rule_collection)


@dataclass
class SigmaCorrelationRule(SigmaRuleBase):
    type: SigmaCorrelationType = None
    rules: List[SigmaRuleReference] = field(default_factory=list)
    timespan: SigmaCorrelationTimespan = field(default_factory=SigmaCorrelationTimespan)
    group_by: Optional[List[str]] = None
    ordered: bool = False
    aliases: SigmaCorrelationFieldAliases = field(default_factory=SigmaCorrelationFieldAliases)
    condition: Optional[SigmaCorrelationCondition] = None
    source: Optional[SigmaRuleLocation] = field(default=None, compare=False)

    def __post_init__(self):
        super().__post_init__()
        if self.type != SigmaCorrelationType.TEMPORAL and self.condition is None:
            raise sigma_exceptions.SigmaCorrelationRuleError(
                f"Non-temporal Sigma correlation rule without condition", source=self.source
            )

    @classmethod
    def from_dict(
        cls,
        rule: dict,
        collect_errors: bool = False,
        source: Optional[SigmaRuleLocation] = None,
    ) -> "SigmaCorrelationRule":
        kwargs, errors = super().from_dict(rule, collect_errors, source)
        correlation_rule = rule.get("correlation", dict())

        # Correlation type
        correlation_type = correlation_rule.get("type")
        if correlation_type is not None:
            try:
                correlation_type = SigmaCorrelationType[correlation_type.upper()]
            except KeyError:
                errors.append(
                    sigma_exceptions.SigmaCorrelationTypeError(
                        f"'{ correlation_type }' is no valid Sigma correlation type", source=source
                    )
                )
        else:  # no correlation type provided
            errors.append(
                sigma_exceptions.SigmaCorrelationTypeError(
                    f"Sigma correlation rule without type", source=source
                )
            )

        # Rules
        rules = correlation_rule.get("rules")
        if rules is not None:
            if isinstance(rules, str):
                rules = [SigmaRuleReference(rules)]
            elif isinstance(rules, list):
                rules = [SigmaRuleReference(rule) for rule in rules]
            else:
                errors.append(
                    sigma_exceptions.SigmaCorrelationRuleError(
                        f"Rule reference must be plain string or list.", source=source
                    )
                )
        else:
            errors.append(
                sigma_exceptions.SigmaCorrelationRuleError(
                    f"Sigma correlation rule without rule references", source=source
                )
            )

        # Group by
        group_by = correlation_rule.get("group-by")
        if group_by is not None:
            if isinstance(group_by, str):
                group_by = [group_by]
            if isinstance(group_by, list):
                group_by = [str(group) for group in group_by]
            else:
                errors.append(
                    sigma_exceptions.SigmaCorrelationRuleError(
                        f"Sigma correlation group-by definition must be string or list",
                        source=source,
                    )
                )

        # Time span
        timespan = correlation_rule.get("timespan")
        if timespan is not None:
            try:
                timespan = SigmaCorrelationTimespan(timespan)
            except SigmaTimespanError as e:
                errors.append(e)
        else:
            errors.append(
                sigma_exceptions.SigmaCorrelationRuleError(
                    f"Sigma correlation rule without timespan", source=source
                )
            )

        # Ordered
        ordered = correlation_rule.get("ordered")
        if ordered is not None:
            if not isinstance(ordered, bool):
                errors.append(
                    sigma_exceptions.SigmaCorrelationRuleError(
                        f"Sigma correlation ordered definition must be boolean", source=source
                    )
                )
        else:
            ordered = False

        # Aliases
        aliases = correlation_rule.get("aliases")
        if aliases is not None:
            if isinstance(aliases, dict):
                aliases = SigmaCorrelationFieldAliases.from_dict(aliases)
            else:
                errors.append(
                    sigma_exceptions.SigmaCorrelationRuleError(
                        f"Sigma correlation aliases definition must be a dict", source=source
                    )
                )
        else:
            aliases = SigmaCorrelationFieldAliases()

        # Condition
        condition = correlation_rule.get("condition")
        if condition is not None:
            if isinstance(condition, dict):
                condition = SigmaCorrelationCondition.from_dict(condition, source=source)
            else:
                errors.append(
                    sigma_exceptions.SigmaCorrelationRuleError(
                        f"Sigma correlation condition definition must be a dict", source=source
                    )
                )
        elif correlation_type != SigmaCorrelationType.TEMPORAL:
            errors.append(
                sigma_exceptions.SigmaCorrelationRuleError(
                    f"Non-temporal Sigma correlation rule without condition", source=source
                )
            )
        elif (
            correlation_type == SigmaCorrelationType.TEMPORAL
        ):  # default condition for temporal correlation rules: count >= number of rules
            condition = SigmaCorrelationCondition(
                op=SigmaCorrelationConditionOperator.GTE, count=len(rules), source=source
            )

        if not collect_errors and errors:
            raise errors[0]

        return cls(
            type=correlation_type,
            rules=rules,
            timespan=timespan,
            group_by=group_by,
            ordered=ordered,
            aliases=aliases,
            condition=condition,
            errors=errors,
            **kwargs,
        )

    def to_dict(self) -> dict:
        d = super().to_dict()
        dc = {
            "type": self.type.name.lower(),
            "rules": [rule.reference for rule in self.rules],
            "timespan": self.timespan.spec,
            "group-by": self.group_by,
            "ordered": self.ordered,
        }
        if self.aliases is not None:
            dc["aliases"] = self.aliases.to_dict()
        if self.condition is not None:
            dc["condition"] = self.condition.to_dict()
        d["correlation"] = dc

        return d

    def resolve_rule_references(self, rule_collection: "sigma.collection.SigmaCollection"):
        """
        Resolves all rule references in the rules property to actual Sigma rules.

        Raises:
            sigma_exceptions.SigmaRuleNotFoundError: If a referenced rule cannot be found in the given rule collection.
        """
        for rule_ref in self.rules:
            rule_ref.resolve(rule_collection)
            rule_ref.rule.add_backreference(self)

        self.aliases.resolve_rule_references(rule_collection)
