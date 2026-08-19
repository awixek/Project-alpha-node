"""AN-02 verification coordinator."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from shared.constants import AgentID, EventName, LogCategory
from shared.schemas import FactVerdict
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException
from shared.logger import AlphaLogger, get_agent_logger

from .analysis import extract_claims, verify_claim
from .models import FactAnalysisConfig, FactCheckRequest, FactVerificationReport, VerifiedClaim
from .providers import FactVerificationProviderRegistry


class FactVerificationCoordinator:
    """Coordinates claim extraction, evidence collection, and deterministic scoring."""

    def __init__(
        self,
        *,
        providers: FactVerificationProviderRegistry,
        config: FactAnalysisConfig | None = None,
        event_bus: EventBus | None = None,
        logger: AlphaLogger | None = None,
    ) -> None:
        self._providers = providers
        self._config = config or FactAnalysisConfig.from_shared_config()
        self._event_bus = event_bus or get_event_bus()
        self._logger = logger or get_agent_logger(AgentID.FACT_GUARDIAN)

    def run(self, request: FactCheckRequest) -> FactVerificationReport:
        started = datetime.now(timezone.utc)
        self._event_bus.emit(
            EventName.AGENT_STARTED,
            mission_id=request.mission_id,
            agent_id=AgentID.FACT_GUARDIAN,
            payload={"operation": "verification"},
        )
        self._logger.info(
            "Fact Guardian verification started.",
            category=LogCategory.AGENT,
            agent_id=AgentID.FACT_GUARDIAN,
            mission_id=request.mission_id,
        )
        try:
            claims, research_sources, research_id = extract_claims(
                request.research,
                max_claims=self._config.max_claims,
            )
            self._logger.info(
                "Fact Guardian claim extraction completed.",
                category=LogCategory.QUALITY,
                agent_id=AgentID.FACT_GUARDIAN,
                mission_id=request.mission_id,
                metadata={"claims_extracted": len(claims)},
            )

            verified: list[VerifiedClaim] = []
            all_failures: dict[str, str] = {}
            for claim in claims:
                self._logger.info(
                    "Fact Guardian requesting evidence.",
                    category=LogCategory.API,
                    agent_id=AgentID.FACT_GUARDIAN,
                    mission_id=request.mission_id,
                    metadata={"claim": claim[:200]},
                )
                responses, failures = self._providers.verify_all(claim)
                all_failures.update(failures)
                evidence = [item for response in responses.values() for item in response]
                self._logger.info(
                    "Fact Guardian evidence collected.",
                    category=LogCategory.QUALITY,
                    agent_id=AgentID.FACT_GUARDIAN,
                    mission_id=request.mission_id,
                    metadata={"evidence_count": len(evidence), "provider_failures": len(failures)},
                )
                result = verify_claim(claim, evidence, config=self._config)
                if result.conflicting_sources:
                    self._logger.warning(
                        "Fact Guardian contradiction detected.",
                        category=LogCategory.QUALITY,
                        agent_id=AgentID.FACT_GUARDIAN,
                        mission_id=request.mission_id,
                        metadata={"claim": claim[:200], "severity": result.contradiction_severity},
                    )
                self._logger.info(
                    "Fact Guardian confidence calculated.",
                    category=LogCategory.QUALITY,
                    agent_id=AgentID.FACT_GUARDIAN,
                    mission_id=request.mission_id,
                    metadata={
                        "confidence": result.confidence,
                        "reliability": result.reliability_score,
                        "status": result.verification_status.value,
                    },
                )
                verified.append(result)

            if not claims:
                report = FactVerificationReport(
                    mission_id=request.mission_id,
                    source_research_id=research_id,
                    claims=[],
                    overall_reliability_score=0.0,
                    verification_confidence=0.0,
                    overall_pass=False,
                    manual_review_required=True,
                    provider_failures=all_failures,
                    claims_extracted=0,
                    claims_verified=0,
                    score_breakdown={},
                )
            else:
                reliability = sum(item.reliability_score for item in verified) / len(verified)
                confidence = sum(item.confidence for item in verified) / len(verified)
                manual_review = any(item.manual_review_required for item in verified)
                passed = all(
                    item.verdict in {FactVerdict.VERIFIED_TRUE, FactVerdict.OPINION}
                    and not item.manual_review_required
                    for item in verified
                )
                if all_failures:
                    manual_review = True
                report = FactVerificationReport(
                    mission_id=request.mission_id,
                    source_research_id=research_id,
                    claims=verified,
                    overall_reliability_score=reliability,
                    verification_confidence=confidence,
                    overall_pass=passed,
                    manual_review_required=manual_review,
                    provider_failures=all_failures,
                    claims_extracted=len(claims),
                    claims_verified=sum(
                        item.verification_status.value in {"verified", "partially_verified"}
                        for item in verified
                    ),
                    checked_at=datetime.now(timezone.utc),
                    score_breakdown={
                        "average_reliability": reliability,
                        "average_confidence": confidence,
                        "provider_degradation": 1.0 if all_failures else 0.0,
                    },
                )

            self._event_bus.emit(
                EventName.AGENT_COMPLETED,
                mission_id=request.mission_id,
                agent_id=AgentID.FACT_GUARDIAN,
                payload={"claims": str(len(report.claims)), "overall_pass": str(report.overall_pass)},
            )
            self._logger.info(
                "Fact Guardian verification completed.",
                category=LogCategory.AGENT,
                agent_id=AgentID.FACT_GUARDIAN,
                mission_id=request.mission_id,
                metadata={
                    "claims": len(report.claims),
                    "overall_pass": report.overall_pass,
                    "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
                },
            )
            return report
        except AlphaBaseException:
            raise
        except Exception as exc:  # noqa: BLE001 - coordinator failure boundary
            self._logger.exception(
                "Unexpected Fact Guardian verification failure.",
                category=LogCategory.ERROR,
                agent_id=AgentID.FACT_GUARDIAN,
                mission_id=request.mission_id,
            )
            self._event_bus.emit(
                EventName.AGENT_FAILED,
                mission_id=request.mission_id,
                agent_id=AgentID.FACT_GUARDIAN,
                payload={"operation": "verification"},
            )
            raise AgentExecutionError(
                "Fact Guardian verification failed unexpectedly.",
                agent_id=AgentID.FACT_GUARDIAN,
                mission_id=request.mission_id,
                retryable=True,
                context={"operation": "run"},
                cause=exc,
            ) from exc
