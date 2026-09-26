# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from dataclasses import dataclass
import json

# Ensure gl.UserError compatibility across GenLayer Studio and gltest environments
if not hasattr(gl, "UserError"):
    try:
        gl.UserError = gl.vm.UserError
    except Exception:
        pass


def _addr_str(addr: Address) -> str:
    """Safely format an Address instance into a lowercase hex string."""
    try:
        return addr.as_hex.lower()
    except Exception:
        return str(addr).lower()


def _get_sender() -> Address:
    """Safely obtain transaction sender across GenVM runtime versions."""
    try:
        return gl.message.sender
    except Exception:
        try:
            return gl.message.sender_address
        except Exception:
            raise gl.UserError("Cannot resolve sender address.")


def _safe_transfer(recipient: Address, amount: bigint) -> None:
    """Safely disburse native GEN to an address using official GenLayer SDK pattern."""
    if amount <= bigint(0):
        return
    gl.get_contract_at(recipient).emit_transfer(value=u256(int(amount)))


@allow_storage
@dataclass
class JobContract:
    job_id: str
    client: Address
    freelancer: Address
    definition_of_done: str  # Clear criteria written in natural language
    deliverable_url: str     # Public URL of the deliverable (Vercel, GitHub, doc, etc.)
    escrow_amount: bigint
    status: str              # "CREATED", "SUBMITTED", "SETTLED"
    completion_percentage: bigint  # 0 to 100
    freelancer_payout: bigint
    client_refund: bigint
    reason: str              # AI consensus evaluation breakdown
    created_at: bigint
    resolved_at: bigint


class Contract(gl.Contract):
    """
    FreelanceMilestoneX: Autonomous Natural Language Escrow Arbiter for Freelancers
    Track: Future of Work / Onchain Justice
    """
    owner: Address
    job_count: bigint
    jobs: TreeMap[str, JobContract]

    def __init__(self):
        # GenVM automatically initializes TreeMap storage fields.
        self.owner = _get_sender()
        self.job_count = bigint(0)

    def _parse_llm_json(self, text: str) -> dict:
        """Safely parse LLM responses, stripping markdown wrappers if present."""
        try:
            cleaned = str(text).strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            return json.loads(cleaned.strip())
        except Exception as e:
            return {
                "completion_percentage": 0,
                "confidence": 0,
                "reason": f"Failed to parse LLM JSON: {str(e)[:100]}"
            }

    @gl.public.write.payable
    def create_job(self, freelancer: Address, definition_of_done: str) -> str:
        """
        Client creates a freelance milestone contract, locks GEN into escrow,
        and defines acceptance criteria in natural language.
        """
        deposit = bigint(gl.message.value)
        if deposit <= bigint(0):
            raise gl.UserError("Escrow funding must be greater than 0 GEN.")

        clean_dod = definition_of_done.strip()
        if len(clean_dod) < 15:
            raise gl.UserError("definition_of_done must be detailed (min 15 chars).")

        sender_hex = _addr_str(_get_sender())
        freelancer_hex = _addr_str(freelancer)
        if sender_hex == freelancer_hex:
            raise gl.UserError("Client cannot assign job to self.")

        self.job_count += bigint(1)
        jid = str(self.job_count)

        self.jobs[jid] = JobContract(
            job_id=jid,
            client=_get_sender(),
            freelancer=freelancer,
            definition_of_done=clean_dod,
            deliverable_url="",
            escrow_amount=deposit,
            status="CREATED",
            completion_percentage=bigint(0),
            freelancer_payout=bigint(0),
            client_refund=bigint(0),
            reason="Escrow locked. Waiting for freelancer submission.",
            created_at=self.job_count,
            resolved_at=bigint(0)
        )

        return jid

    @gl.public.write
    def submit_deliverable(self, job_id: str, deliverable_url: str) -> None:
        """
        Freelancer submits the proof or public URL of the completed deliverable.
        """
        if job_id not in self.jobs:
            raise gl.UserError("Job contract not found.")

        job = self.jobs[job_id]
        if _addr_str(_get_sender()) != _addr_str(job.freelancer):
            raise gl.UserError("Only the assigned freelancer can submit deliverable.")

        if job.status == "SETTLED":
            raise gl.UserError("Job is already settled.")

        clean_url = deliverable_url.strip()
        if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
            raise gl.UserError("deliverable_url must begin with http:// or https://")

        job.deliverable_url = clean_url
        job.status = "SUBMITTED"
        self.jobs[job_id] = job

    @gl.public.write
    def adjudicate_job(self, job_id: str) -> None:
        """
        Validators inspect the deliverable URL on-chain, cross-reference against
        the Definition of Done (DoD), reach consensus on completion %, and split the escrow.
        """
        if job_id not in self.jobs:
            raise gl.UserError("Job contract not found.")

        job = self.jobs[job_id]
        if job.status != "SUBMITTED":
            raise gl.UserError("Job deliverable has not been submitted or already settled.")

        if not job.deliverable_url:
            raise gl.UserError("Deliverable URL is missing.")

        # Extract values to local variables BEFORE nondet block
        dod_local = str(job.definition_of_done)
        url_local = str(job.deliverable_url)

        def leader_fn():
            web_content = ""
            try:
                res = gl.nondet.web.render(url_local, mode="text")
                if hasattr(res, "content"):
                    web_content = res.content
                elif isinstance(res, dict) and "body" in res:
                    web_content = res["body"]
                else:
                    web_content = str(res)
            except Exception:
                web_content = ""

            lower_web = web_content[:500].lower() if web_content else ""
            if len(web_content.strip()) < 15 or "404 not found" in lower_web or "access denied" in lower_web:
                return {
                    "completion_percentage": 0,
                    "confidence": 100,
                    "reason": "Deliverable URL is inaccessible, offline, 404, or blank."
                }

            snippet = web_content[:4000]

            prompt = f"""You are an Objective AI Arbitrator on GenLayer resolving a freelance deliverable dispute.
Evaluate the observed deliverable content against the agreed Definition of Done (DoD).

AGREED DEFINITION OF DONE:
\"\"\"
{dod_local}
\"\"\"

OBSERVED DELIVERABLE CONTENT (from {url_local}):
\"\"\"
{snippet}
\"\"\"

ARBITRATION INSTRUCTIONS:
1. Objectively evaluate which requirements were met, partially met, or missing.
2. Determine an overall integer completion score from 0 to 100:
   - 100: Flawlessly meets or exceeds all criteria.
   - 70-99: Core requirements met, minor non-blocking issues.
   - 30-69: Partially complete, several key DoD items missing.
   - 0-29: Unrelated, severely broken, or fail to deliver.

OUTPUT FORMAT:
Respond ONLY with a VALID JSON object (no markdown, no backticks):
{{
  "completion_percentage": <integer from 0 to 100>,
  "confidence": <integer from 0 to 100>,
  "reason": "<concise breakdown max 220 characters>"
}}"""

            try:
                raw_res = gl.nondet.exec_prompt(prompt, response_format="json")
                parsed = None
                if isinstance(raw_res, dict):
                    parsed = raw_res
                elif hasattr(raw_res, "content") and isinstance(raw_res.content, dict):
                    parsed = raw_res.content
                else:
                    text = raw_res.content if hasattr(raw_res, "content") else str(raw_res)
                    cleaned = str(text).strip()
                    if cleaned.startswith("```json"):
                        cleaned = cleaned[7:]
                    elif cleaned.startswith("```"):
                        cleaned = cleaned[3:]
                    if cleaned.endswith("```"):
                        cleaned = cleaned[:-3]
                    parsed = json.loads(cleaned.strip())

                try:
                    score = int(parsed.get("completion_percentage", 0))
                    score = max(0, min(100, score))
                except Exception:
                    score = 0

                try:
                    conf = int(parsed.get("confidence", 0))
                    conf = max(0, min(100, conf))
                except Exception:
                    conf = 50

                reason_str = str(parsed.get("reason", "Milestone evaluated by AI jury."))[:220]

                return {
                    "completion_percentage": score,
                    "confidence": conf,
                    "reason": reason_str
                }
            except Exception as e:
                return {
                    "completion_percentage": 0,
                    "confidence": 0,
                    "reason": f"Audit execution failed: {str(e)[:100]}"
                }

        def validator_fn(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            leader = leader_res.calldata
            if not isinstance(leader, dict) or "completion_percentage" not in leader:
                return False

            mine = leader_fn()

            try:
                l_score = int(leader.get("completion_percentage", 0))
                m_score = int(mine.get("completion_percentage", 0))
                return abs(l_score - m_score) <= 10
            except Exception:
                return False

        adjudication_res = gl.vm.run_nondet(leader_fn, validator_fn)
        if isinstance(adjudication_res, dict):
            final_res = adjudication_res
        else:
            final_res = self._parse_llm_json(str(adjudication_res))

        pct = int(final_res.get("completion_percentage", 0))
        pct = max(0, min(100, pct))
        reason = str(final_res.get("reason", "Consensus concluded."))

        escrow_total = job.escrow_amount
        freelancer_share = (escrow_total * bigint(pct)) // bigint(100)
        client_refund = escrow_total - freelancer_share

        job.status = "SETTLED"
        job.completion_percentage = bigint(pct)
        job.freelancer_payout = freelancer_share
        job.client_refund = client_refund
        job.reason = reason
        job.resolved_at = self.job_count
        self.jobs[job_id] = job

        # Automatic disbarment
        if freelancer_share > bigint(0):
            _safe_transfer(job.freelancer, freelancer_share)

        if client_refund > bigint(0):
            _safe_transfer(job.client, client_refund)

    @gl.public.view
    def get_job(self, job_id: str) -> str:
        """Retrieve details of a job milestone contract as a JSON string."""
        if job_id not in self.jobs:
            raise gl.UserError("Job contract not found.")
        j = self.jobs[job_id]
        return json.dumps({
            "job_id": j.job_id,
            "client": _addr_str(j.client),
            "freelancer": _addr_str(j.freelancer),
            "definition_of_done": j.definition_of_done,
            "deliverable_url": j.deliverable_url,
            "escrow_amount": str(j.escrow_amount),
            "status": j.status,
            "completion_percentage": str(j.completion_percentage),
            "freelancer_payout": str(j.freelancer_payout),
            "client_refund": str(j.client_refund),
            "reason": j.reason,
            "created_at": str(j.created_at),
            "resolved_at": str(j.resolved_at)
        })

    @gl.public.view
    def get_job_count(self) -> int:
        return int(self.job_count)
