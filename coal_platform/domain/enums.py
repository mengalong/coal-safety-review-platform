from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    REVIEWER = "reviewer"


class TaskStatus(StrEnum):
    DRAFT = "draft"
    PARSING = "parsing"
    WAITING_BASIC_INFO = "waiting_basic_info"
    WAITING_STANDARDS = "waiting_standards"
    AUDITING = "auditing"
    WAITING_REVIEW = "waiting_review"
    WAITING_PUBLISH = "waiting_publish"
    PUBLISHED = "published"
    WAITING_RECTIFICATION = "waiting_rectification"
    IN_NEW_ROUND = "in_new_round"
    COMPLETED = "completed"
    FAILED = "failed"
    VOIDED = "voided"


class RoundStatus(StrEnum):
    DRAFT = "draft"
    WAITING_BASIC_INFO = "waiting_basic_info"
    WAITING_STANDARDS = "waiting_standards"
    SNAPSHOT_LOCKED = "snapshot_locked"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    WAITING_PUBLISH = "waiting_publish"
    PUBLISHED = "published"
    WAITING_RECTIFICATION = "waiting_rectification"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class FileStatus(StrEnum):
    UPLOADED = "uploaded"
    CLASSIFYING = "classifying"
    PARSING = "parsing"
    PARSED = "parsed"
    PARSE_FAILED = "parse_failed"
    RETRYING = "retrying"
    UNAVAILABLE = "unavailable"


class StandardStatus(StrEnum):
    DRAFT = "draft"
    PARSING = "parsing"
    WAITING_REVIEW = "waiting_review"
    WAITING_PUBLISH = "waiting_publish"
    ACTIVE = "active"
    EXPIRING = "expiring"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class RuleStatus(StrEnum):
    DRAFT = "draft"
    TESTING = "testing"
    PUBLISHED = "published"
    PAUSED = "paused"
    ARCHIVED = "archived"


class ExecutorStatus(StrEnum):
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    PAUSED = "paused"
    BANNED = "banned"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    WAITING_DEPENDENCY = "waiting_dependency"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    UNABLE_TO_DETERMINE = "unable_to_determine"
    EXCEPTION = "exception"
    CANCELED = "canceled"
    EXPIRED = "expired"


class IssueSeverity(StrEnum):
    SEVERE = "severe"
    NORMAL = "normal"
    HINT = "hint"


class CoverageStatus(StrEnum):
    EXECUTED_PASSED = "executed_passed"
    EXECUTED_FAILED = "executed_failed"
    MISSING_DATA = "missing_data"
    UNABLE_TO_DETERMINE = "unable_to_determine"
    NOT_APPLICABLE = "not_applicable"
    TO_CONFIRM = "to_confirm"
    UNSUPPORTED = "unsupported"
    EXECUTION_EXCEPTION = "execution_exception"
