"""HTTP service: uvicorn app.service:app --port 18562  (see README for environment variables)."""
import hmac
import os
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.exceptions import HTTPException

from . import core, extraction
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


class GenerateRequest(BaseModel):
    question: str = Field(min_length=1)
    evidence: list[Evidence] = []
    analysis_results: list[AnalysisResult] = []
    source_updates: list[SourceUpdate] = []
    source_relations: list[SourceRelation] = []
    research_scope: ResearchScope = ResearchScope()
    previous_report: PreviousReport | None = None
    hypotheses: list[Hypothesis] = []
    max_llm_calls: int = Field(20, ge=0)


class VerifyRequest(GenerateRequest):
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


class Review(BaseModel):
    status: Literal["PASS", "REVIEW", "FAIL"]
    claims: list[ReviewClaim]


class ReviseRequest(VerifyRequest):
    review: Review


# ---- extraction consistency (v3 6.2): strict on purpose - an unknown, misplaced or missing field is a 400,
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


def error(status, code, message, **extra):
    return JSONResponse({"error_code": code, "message": message, **extra}, status_code=status)


def create_app(llm=chat_json):
    app = FastAPI(title="BioCheck", version="2")

    @app.middleware("http")
    async def bearer_token(request: Request, call_next):
        if request.url.path.startswith("/v1/"):
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

    @app.post("/v1/extraction-consistency")
    def extraction_consistency(req: ExtractionRequest):
        try:
            return extraction.check(req.model_dump())
        except extraction.ContractError as exc:
            return error(400, "invalid_request", str(exc))

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "18562")))
