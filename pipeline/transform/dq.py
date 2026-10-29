"""A simple accumulator for data-quality issues, turned into data_quality_issue rows at load time."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DQCollector:
    issues: list[dict] = field(default_factory=list)

    def log(self, rule_id: str, entity_type: str, entity_id: str, issue_type: str, details: str = "") -> None:
        self.issues.append(
            {
                "rule_id": rule_id,
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                "issue_type": issue_type,
                "details": details,
            }
        )

    def log_many(self, rule_id: str, entity_type: str, entity_ids, issue_type: str, details: str = "") -> None:
        for eid in entity_ids:
            self.log(rule_id, entity_type, eid, issue_type, details)

    def extend(self, raw_issues: list[dict]) -> None:
        self.issues.extend(raw_issues)
