"""HTTP service: uvicorn app.service:app --port 18562  (see README for environment variables)."""
import hmac
import os
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException

from . import core
from .llm import chat_json


class Evidence(BaseModel):
    id: str
    title: str = ""
    source: str = ""
    published_at: str | None = None
    source_kind: Literal["official", "research", "news", "social", "registry", "company"]
    verified: bool
    text: str = Field(min_length=1)


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


class ResearchScope(BaseModel):
    checked: list[str] = []
    not_checked: list[str] = []
    failed: list[str] = []


class PreviousReport(BaseModel):
    draft: str
    date: str = ""


class GenerateRequest(BaseModel):
    question: str = Field(min_length=1)
    evidence: list[Evidence] = []
    analysis_results: list[AnalysisResult] = []
    source_updates: list[SourceUpdate] = []
    research_scope: ResearchScope = ResearchScope()
    previous_report: PreviousReport | None = None
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
    verdict: Literal["supported", "contradicted", "insufficient"]
    labels: list[str] = []
    quotes: list[ReviewQuote] = []
    reason: str = ""
    scope_span: str | None = None


class Review(BaseModel):
    status: Literal["PASS", "REVIEW", "FAIL"]
    claims: list[ReviewClaim]


class ReviseRequest(VerifyRequest):
    review: Review


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

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "18562")))
