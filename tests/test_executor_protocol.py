from collections.abc import Mapping
from typing import Any

import pytest

from coal_platform.domain.protocols.executor import (
    ExecutionOutcome,
    ExecutorDescriptor,
    ExecutorKind,
    ExecutorRegistry,
    ExecutorRequest,
    ExecutorResult,
    RuleExecutor,
)


class PassingExecutor(RuleExecutor):
    code = "passing"
    version = "1.0.0"

    def describe(self) -> ExecutorDescriptor:
        return ExecutorDescriptor(
            code=self.code,
            name="Passing executor",
            version=self.version,
            kind=ExecutorKind.BUILTIN,
            input_type="fact",
            output_type="result",
            parameter_schema={},
            result_schema={},
            default_timeout_seconds=5,
            supports_batch=False,
        )

    def validate_parameters(self, parameters: Mapping[str, Any]) -> None:
        return None

    def validate_input(self, request: ExecutorRequest) -> None:
        return None

    def execute(self, request: ExecutorRequest) -> ExecutorResult:
        return ExecutorResult(outcome=ExecutionOutcome.PASSED)


def test_registry_resolves_exact_snapshot_version() -> None:
    registry = ExecutorRegistry()
    executor = PassingExecutor()
    registry.register(executor)

    assert registry.get("passing", "1.0.0") is executor
    registry.validate_snapshot([executor.describe()])


def test_registry_rejects_duplicate_code_and_version() -> None:
    registry = ExecutorRegistry()
    registry.register(PassingExecutor())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(PassingExecutor())
