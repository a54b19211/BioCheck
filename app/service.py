"""HTTP service: uvicorn app.service:app --port 18562  (see README for environment variables)."""
import hmac
import os
import re
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.exceptions import HTTPException

from . import core, dimension_rules, item_rules, measurement_rules, unit_verify
from .llm import chat_json


class Evidence(BaseModel):
    id: str
    title: str = ""
    source: str = ""
    published_at: str | None = None
    source_kind: Literal["official", "research", "news", "social", "registry", "company"]
    verified: bool
    text: str = Field(min_length=1)
    origin_group: str | None = None  # Fabric's explicit same-origin group; absent = independence unknown


class AnalysisResult(BaseModel):
    id: str
    method: str = ""
    output: Any
    calculation_basis: dict = {}
    limitations: list[str] = []


class SourceUpdate(BaseModel):
    target: str
    replacement: str
    kind: Literal["correction", "retraction"]


class SourceRelation(BaseModel):
    """A repost or supplement Fabric stated between two records; it supersedes nothing."""

    kind: Literal["repost", "supplement"]
    source: str  # the evidence id of the reposting / supplementing record
    target: str | None = None  # the evidence id of the original, when it was read
    target_record: str = ""
    read: bool = True


class ResearchScope(BaseModel):
    checked: list[str] = []
    not_checked: list[str] = []
    failed: list[str] = []


class PreviousReport(BaseModel):
    draft: str
    date: str = ""


class HypothesisFact(BaseModel):
    ref: str
    text: str
    stance: Literal["supports", "refutes", "limits", "context"]


class Hypothesis(BaseModel):
    """A testable intelligence hypothesis the radar formed from a signal (written as 「情报假设」)."""

    id: str
    statement: str
    alternatives: list[str] = []
    confirm: list[str] = []
    refute: list[str] = []
    facts: list[HypothesisFact] = []


class MaterialsRequest(BaseModel):
    question: str = Field(min_length=1)
    evidence: list[Evidence] = []
    analysis_results: list[AnalysisResult] = []
    source_updates: list[SourceUpdate] = []
    source_relations: list[SourceRelation] = []
    research_scope: ResearchScope = ResearchScope()
    previous_report: PreviousReport | None = None
    hypotheses: list[Hypothesis] = []
    max_llm_calls: int = Field(20, ge=0)


class Slot(BaseModel):
    """A question the report must answer, numbered by the radar (J1 ...); the writer answers it by that number."""

    slot: str = Field(min_length=1, max_length=32)
    question: str = Field(min_length=1)


class GenerateRequest(MaterialsRequest):
    slots: list[Slot] = []  # given: the report is written, and answered, as sections


class VerifyRequest(MaterialsRequest):
    draft: str = Field(min_length=1)


class ReviewQuote(BaseModel):
    ref: str
    quote: str
    found: bool


class ReviewClaim(BaseModel):
    index: int
    text: str
    start: int
    end: int
    citations: list[str]
    verdict: Literal["supported", "contradicted", "insufficient", "no_claim"]
    labels: list[str] = []
    quotes: list[ReviewQuote] = []
    reason: str = ""
    scope_span: str | None = None

    @model_validator(mode="before")
    @classmethod
    def quotes_of_units(cls, data):
        """A /v2/verify claim keeps its short quotes in its units; the reviser reads them as the claim's quotes
        (a quote found once or several times is found)."""
        units = data.get("units") if isinstance(data, dict) else None
        if isinstance(units, list) and not data.get("quotes"):
            data = dict(data, quotes=[
                {"ref": q.get("ref"), "quote": q.get("quote"), "found": q.get("status") in ("found", "ambiguous")}
                for unit in units if isinstance(unit, dict)
                for q in unit.get("quotes", []) if isinstance(q, dict)])
        return data


class Review(BaseModel):
    status: Literal["PASS", "REVIEW", "FAIL"]
    claims: list[ReviewClaim]


class ReviseRequest(VerifyRequest):
    review: Review


# ---- extraction consistency (design v3 §6.2): strict on purpose - an unknown, misplaced or missing field is a 400,
# never a silently ignored field answered with 200 as if it had been checked. Field semantics follow Fabric's
# contracts/data_objects.py (Measurement: object, metric, value, unit, period, scope, denominator, measure_kind).

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


SHA256 = r"^[0-9a-f]{64}$"
PASSAGE_ID = r"^[A-Za-z0-9_.-]{1,32}$"


class ArtifactRef(Strict):
    processor_version: str = Field(min_length=1, max_length=120)
    text_sha256: str = Field(pattern=SHA256)


class Subject(Strict):
    """The fixed version and processing product the passages were cut from (Fabric's identities). The caller
    reads them from that fixed artifact; this service only checks the passages it is given."""

    source_id: str = Field(min_length=1, max_length=80)
    record_id: str = Field(min_length=1, max_length=2000)
    version_id: str = Field(min_length=1, max_length=128)
    raw_sha256: str = Field(pattern=SHA256)
    is_demo: bool
    artifact: ArtifactRef


class Passage(Strict):
    """A bounded slice [start, end) of the artifact in its own offsets. A table header and a data row are two
    passages (role context / primary), never one invented continuous range."""

    id: str = Field(pattern=PASSAGE_ID)
    role: Literal["primary", "context"]
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=20000)


class Span(Strict):
    passage: str = Field(pattern=PASSAGE_ID)
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class FieldValue(Strict):
    """A field as extracted: its value and where it is written, or explicitly not stated by the source."""

    state: Literal["stated", "unknown", "not_applicable"] = "stated"
    value: str | None = Field(None, min_length=1, max_length=300)
    span: Span | None = None

    @model_validator(mode="after")
    def stated_has_value_and_span(self):
        if self.state == "stated" and (self.value is None or self.span is None):
            raise ValueError("a stated field needs value and span")
        if self.state != "stated" and (self.value is not None or self.span is not None):
            raise ValueError("an unknown / not_applicable field has no value or span")
        return self


class Producer(Strict):
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=80)


class MeasurementFields(Strict):
    object: FieldValue
    metric: FieldValue
    value: FieldValue
    unit: FieldValue
    period: FieldValue
    scope: FieldValue
    denominator: FieldValue
    measure_kind: Literal["stock", "flow", "cumulative", "rate"]


class IdentifierFields(Strict):
    scheme: str = Field(min_length=1, max_length=40)
    identifier: FieldValue  # must be stated: an identifier item without its identifier checks nothing

    @model_validator(mode="after")
    def identifier_is_stated(self):
        if self.identifier.state != "stated":
            raise ValueError("an identifier item states its identifier (value and span)")
        return self


class ClaimFields(Strict):
    statement: FieldValue  # must be stated, as above
    polarity: Literal["affirmed", "negated", "uncertain"]
    qualifiers: list[str] = Field([], max_length=10)

    @model_validator(mode="after")
    def statement_is_stated(self):
        if self.statement.state != "stated":
            raise ValueError("a claim item states its statement (value and span)")
        return self


class AnnotationFields(Strict):
    label: str = Field(min_length=1, max_length=80)


class ItemBase(Strict):
    id: str = Field(min_length=1, max_length=64)
    producer: Producer
    locator: Span  # where the short quote is
    quote: str = Field(min_length=1, max_length=300)


class MeasurementItem(ItemBase):
    kind: Literal["measurement"]
    fields: MeasurementFields


class IdentifierItem(ItemBase):
    kind: Literal["identifier"]
    fields: IdentifierFields


class ClaimItem(ItemBase):
    kind: Literal["claim"]
    fields: ClaimFields


class AnnotationItem(ItemBase):
    kind: Literal["annotation"]
    fields: AnnotationFields


class ExtractionRequest(Strict):
    subject: Subject
    passages: list[Passage] = Field(min_length=1, max_length=8)
    items: list[Annotated[MeasurementItem | IdentifierItem | ClaimItem | AnnotationItem,
                          Field(discriminator="kind")]] = Field(min_length=1, max_length=50)


# ---- contract v2: measurements only. Numbers travel as canonical decimal strings, never as
# JSON numbers, so nothing passes through a float; a period is structured (type, precision, value | start..end).

DECIMAL = r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?$"
MONTH = r"^[0-9]{4}-(?:0[1-9]|1[0-2])$"
DAY = r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])$"
PERIOD_FORMS = {"day": DAY, "month": MONTH, "quarter": r"^[0-9]{4}Q[1-4]$", "half": r"^[0-9]{4}H[12]$",
                "year": r"^[0-9]{4}$"}


class PeriodValue(Strict):
    type: Literal["point", "interval"]
    precision: Literal["day", "month", "quarter", "half", "year"]
    value: str | None = None
    start: str | None = None
    end: str | None = None

    @model_validator(mode="after")
    def one_form(self):
        form = PERIOD_FORMS[self.precision]
        if self.value is not None:
            if self.start is not None or self.end is not None or not re.match(form, self.value):
                raise ValueError("a period has a value of its precision, or start and end, not both")
        elif (self.start is None or self.type != "interval" or self.precision not in ("day", "month")
              or not re.match(form, self.start) or not (self.end is None or re.match(form, self.end))):
            raise ValueError("a period range is an interval of day or month precision with a start and an end, "
                             "or only a start when it is open at its end (since ...)")
        return self


class PeriodField(Strict):
    state: Literal["stated", "unknown"] = "stated"
    value: PeriodValue | None = None
    span: Span | None = None

    @model_validator(mode="after")
    def stated_has_value_and_span(self):
        if (self.state == "stated") != (self.value is not None and self.span is not None):
            raise ValueError("a stated period needs value and span; an unknown one has neither")
        return self


def _decimal(field, name):
    if field.state == "stated":
        v = field.value or ""
        digits = sum(c.isdigit() for c in v)
        decimals = len(v.split(".")[1]) if "." in v else 0
        if (not re.match(DECIMAL, v) or digits > measurement_rules.MAX_DIGITS
                or decimals > measurement_rules.MAX_DECIMALS):
            raise ValueError(f"{name}: a canonical decimal string of at most 18 digits and 6 decimals")


class MeasurementFieldsV2(Strict):
    object: FieldValue
    metric: FieldValue
    value: FieldValue
    unit: FieldValue
    period: PeriodField
    scope: FieldValue
    scope_mode: Literal["stated", "object_metric_total", "unknown"]
    denominator: FieldValue
    measure_kind: Literal["stock", "flow", "cumulative", "rate"]
    qualifier: Literal["exact", "approximate", "at_least", "at_most", "more_than", "less_than"]
    modality: Literal["actual", "planned", "projected"]

    @model_validator(mode="after")
    def numbers_and_states(self):
        if self.value.state != "stated":
            raise ValueError("a measurement states its value")
        _decimal(self.value, "value")
        _decimal(self.denominator, "denominator")
        if self.scope_mode == "stated" and self.scope.state != "stated":
            raise ValueError("scope_mode stated needs a stated scope")
        if self.scope_mode != "stated" and self.scope.state == "stated":
            raise ValueError("a stated scope has scope_mode stated")
        return self


class MeasurementItemV2(ItemBase):
    kind: Literal["measurement"]
    fields: MeasurementFieldsV2


class Dim(Strict):
    """A business dimension the producer states for a measurement (contract v3): its kind, value and span."""

    kind: Literal["disease", "population", "date_basis", "indication", "product_or_class", "milestone_definition",
                  "entity", "stage", "denominator_definition"]
    state: Literal["stated"]
    value: str = Field(min_length=1, max_length=300)
    span: Span


class MeasurementItemV3(MeasurementItemV2):
    dims: list[Dim] = Field(default_factory=list, max_length=9)
    family: Literal["case_count", "milestone_count", "capacity", "efficacy", ""] = ""

    @model_validator(mode="after")
    def one_dimension_per_kind(self):
        if len({d.kind for d in self.dims}) != len(self.dims):
            raise ValueError("dims: one dimension per kind")
        return self


class ExtractionRequestV3(Strict):
    contract_version: Literal["extraction_consistency.v3"]
    subject: Subject
    passages: list[Passage] = Field(min_length=1, max_length=8)
    items: list[MeasurementItemV3] = Field(min_length=1, max_length=50)


class ExtractionRequestV2(Strict):
    contract_version: Literal["extraction_consistency.v2"]
    subject: Subject
    passages: list[Passage] = Field(min_length=1, max_length=8)
    items: list[MeasurementItemV2] = Field(min_length=1, max_length=50)


# ---- /v2/verify: strict like the extraction contracts, and the evidence carries the time facts the semantic check
# reads (design task book 06, section 3). rules_version is the segment-rule version the caller expects; sections
# say which report section each paragraph of the draft is (task book 08, section 2.2).

class SectionSpan(Strict):
    """One section of the report, where it lies in the draft: characters [start, end) of `draft`."""

    kind: Literal["question", "judgment", "evidence", "gaps", "scenarios", "uncertainty", "visibility", "revision",
                  "recommendation"]
    start: int = Field(ge=0)
    end: int = Field(ge=1)


class EvidenceV2(Evidence, Strict):
    first_seen_at: str | None = None
    proof_available_at: str | None = None
    replay_mode: Literal["current", "operational_replay", "public_history_reconstruction"] | None = None


class AnalysisResultV2(AnalysisResult, Strict):
    pass


class SourceUpdateV2(SourceUpdate, Strict):
    pass


class SourceRelationV2(SourceRelation, Strict):
    pass


class ResearchScopeV2(ResearchScope, Strict):
    pass


class PreviousReportV2(PreviousReport, Strict):
    pass


class HypothesisFactV2(HypothesisFact, Strict):
    pass


class HypothesisV2(Hypothesis, Strict):
    facts: list[HypothesisFactV2] = []


class VerifyRequestV2(VerifyRequest, Strict):
    evidence: list[EvidenceV2] = []
    analysis_results: list[AnalysisResultV2] = []
    source_updates: list[SourceUpdateV2] = []
    source_relations: list[SourceRelationV2] = []
    research_scope: ResearchScopeV2 = ResearchScopeV2()
    previous_report: PreviousReportV2 | None = None
    hypotheses: list[HypothesisV2] = []
    rules_version: str | None = None
    sections: list[SectionSpan] = []

    @model_validator(mode="after")
    def sections_lie_in_the_draft(self):
        edge = 0
        for section in sorted(self.sections, key=lambda s: s.start):
            if not edge <= section.start < section.end <= len(self.draft):
                raise ValueError("sections: each is a non-empty range inside the draft, none overlapping another")
            edge = section.end
        return self


def error(status, code, message, **extra):
    return JSONResponse({"error_code": code, "message": message, **extra}, status_code=status)


def create_app(llm=chat_json):
    app = FastAPI(title="BioCheck", version="2")

    @app.middleware("http")
    async def bearer_token(request: Request, call_next):
        if re.match(r"^/v[0-9]+/", request.url.path):  # every versioned endpoint, one token
            token = os.environ.get("SERVICE_TOKEN", "")
            supplied = request.headers.get("Authorization", "")
            if not token:
                return error(503, "service_token_missing", "服务未配置SERVICE_TOKEN")
            if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
                return error(401, "unauthorized", "缺少或错误的Bearer令牌")
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(x) for x in first.get("loc", ()))
        return error(400, "invalid_request", f"{where}: {first.get('msg', '请求格式错误')}")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(exc.status_code, {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error"),
                     str(exc.detail))

    @app.exception_handler(core.InputError)
    async def input_error(request, exc):
        extra = {"llm_calls": exc.llm_calls} if exc.llm_calls is not None else {}
        return error(exc.status, exc.code, exc.message, **extra)

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return error(500, "internal_error", type(exc).__name__)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/v1/generate")
    def generate(req: GenerateRequest):
        return core.generate(req.model_dump(), llm)

    @app.post("/v1/verify")
    def verify(req: VerifyRequest):
        return core.verify(req.model_dump(), llm)

    @app.post("/v1/revise")
    def revise(req: ReviseRequest):
        return core.revise(req.model_dump(), llm)

    @app.post("/v2/verify")
    def verify_units(req: VerifyRequestV2):
        return unit_verify.verify(req.model_dump(), llm)

    @app.post("/v2/extraction-consistency")
    def extraction_consistency_v2(req: ExtractionRequestV2):
        try:
            return measurement_rules.check(req.model_dump(exclude={"contract_version"}, exclude_none=True))
        except item_rules.ContractError as exc:
            return error(400, "invalid_request", str(exc))

    @app.post("/v3/extraction-consistency")
    def extraction_consistency_v3(req: ExtractionRequestV3):
        try:
            return dimension_rules.check(req.model_dump(exclude={"contract_version"}, exclude_none=True))
        except item_rules.ContractError as exc:
            return error(400, "invalid_request", str(exc))

    @app.post("/v1/extraction-consistency")
    def extraction_consistency(req: ExtractionRequest):
        try:
            return item_rules.check(req.model_dump())
        except item_rules.ContractError as exc:
            return error(400, "invalid_request", str(exc))

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "18562")))
