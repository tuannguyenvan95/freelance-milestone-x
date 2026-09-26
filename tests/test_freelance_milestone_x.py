import pytest
import json
from gltest import *


def _to_hex(addr) -> str:
    """Helper to convert test address to lowercase hex."""
    if hasattr(addr, "as_hex"):
        return addr.as_hex.lower()
    if isinstance(addr, bytes):
        return "0x" + addr.hex().lower()
    return str(addr).lower()


def setup_post_message_hook(direct_vm):
    """Intercept cross-contract calls / emit_transfer to track recipient balances in tests."""
    def post_message_hook(vm, request):
        if "PostMessage" in request:
            pm = request["PostMessage"]
            dest_addr = pm["address"]
            value = int(pm.get("value", 0))
            dest_bytes = vm._to_bytes(dest_addr)
            vm._balances[dest_bytes] = vm._balances.get(dest_bytes, 0) + value
            return {"ok": None}
        if "EthSend" in request:
            es = request["EthSend"]
            dest_addr = es.get("to") or es.get("address") or es.get("recipient")
            value = int(es.get("value", 0))
            dest_bytes = vm._to_bytes(dest_addr)
            vm._balances[dest_bytes] = vm._balances.get(dest_bytes, 0) + value
            return {"ok": None}
        return None

    direct_vm._gl_call_hook = post_message_hook


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("contracts/freelance_milestone_x.py")


def test_initial_state(contract):
    """Verify clean initial state on contract deployment."""
    assert contract.get_job_count() == 0


def test_create_job_success(contract, direct_vm, direct_alice, direct_bob):
    """Client creates an escrow job with funds and DoD."""
    direct_vm.sender = direct_alice
    direct_vm.value = 10000

    dod = "Deploy Next.js dApp to Vercel with Metamask wallet connect and transaction history table."
    job_id = contract.create_job(direct_bob, dod)

    assert str(job_id) == "1"
    assert contract.get_job_count() == 1

    job_data = json.loads(contract.get_job(job_id))
    assert job_data["job_id"] == "1"
    assert job_data["client"] == _to_hex(direct_alice)
    assert job_data["freelancer"] == _to_hex(direct_bob)
    assert job_data["definition_of_done"] == dod
    assert job_data["deliverable_url"] == ""
    assert job_data["escrow_amount"] == "10000"
    assert job_data["status"] == "CREATED"
    assert job_data["completion_percentage"] == "0"
    assert job_data["freelancer_payout"] == "0"
    assert job_data["client_refund"] == "0"


def test_create_job_validation_errors(contract, direct_vm, direct_alice, direct_bob):
    """Test validation errors on job creation."""
    direct_vm.sender = direct_alice

    # 1. Zero escrow deposit
    direct_vm.value = 0
    with pytest.raises(Exception) as exc:
        contract.create_job(direct_bob, "Detailed acceptance criteria goes here.")
    assert "Escrow funding must be greater than 0 GEN" in str(exc.value)

    # 2. Definition of Done too short (< 15 chars)
    direct_vm.value = 5000
    with pytest.raises(Exception) as exc:
        contract.create_job(direct_bob, "Do work")
    assert "definition_of_done must be detailed (min 15 chars)" in str(exc.value)

    # 3. Client cannot assign job to self
    direct_vm.value = 5000
    with pytest.raises(Exception) as exc:
        contract.create_job(direct_alice, "Build high frequency arbitrage trading bot on GenLayer.")
    assert "Client cannot assign job to self" in str(exc.value)


def test_submit_deliverable_success(contract, direct_vm, direct_alice, direct_bob):
    """Freelancer submits deliverable URL."""
    direct_vm.sender = direct_alice
    direct_vm.value = 8000
    dod = "Complete responsive landing page with Figma pixel-perfect fidelity."
    job_id = contract.create_job(direct_bob, dod)

    # Freelancer submits deliverable
    direct_vm.sender = direct_bob
    deliverable_url = "https://freelance-milestone-demo.vercel.app"
    contract.submit_deliverable(job_id, deliverable_url)

    job_data = json.loads(contract.get_job(job_id))
    assert job_data["status"] == "SUBMITTED"
    assert job_data["deliverable_url"] == deliverable_url


def test_submit_deliverable_permissions_and_validations(contract, direct_vm, direct_alice, direct_bob, direct_charlie):
    """Verify unauthorized users or invalid URLs are rejected."""
    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    job_id = contract.create_job(direct_bob, "Implement smart escrow with partial settlement.")

    # 1. Non-existent job
    direct_vm.sender = direct_bob
    with pytest.raises(Exception) as exc:
        contract.submit_deliverable("999", "https://valid-url.com")
    assert "Job contract not found" in str(exc.value)

    # 2. Unauthorized sender (Charlie or Alice instead of Bob)
    direct_vm.sender = direct_charlie
    with pytest.raises(Exception) as exc:
        contract.submit_deliverable(job_id, "https://valid-url.com")
    assert "Only the assigned freelancer can submit deliverable" in str(exc.value)

    # 3. Invalid URL scheme (missing http:// or https://)
    direct_vm.sender = direct_bob
    with pytest.raises(Exception) as exc:
        contract.submit_deliverable(job_id, "ftp://my-storage/demo.zip")
    assert "deliverable_url must begin with http:// or https://" in str(exc.value)


def test_adjudicate_full_completion_100_percent(contract, direct_vm, direct_alice, direct_bob):
    """
    Scenario 1: AI arbitrator scores 100% completion.
    Result: 100% payout to Freelancer, 0 refund to Client.
    """
    setup_post_message_hook(direct_vm)

    # Initial balance tracking
    alice_bytes = direct_vm._to_bytes(direct_alice)
    bob_bytes = direct_vm._to_bytes(direct_bob)
    direct_vm._balances[alice_bytes] = 20000
    direct_vm._balances[bob_bytes] = 0

    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    dod = "Deploy smart escrow dApp on Vercel with wallet connection, contract creation form, and audit summary."
    job_id = contract.create_job(direct_bob, dod)

    direct_vm.sender = direct_bob
    deliverable_url = "https://milestonex-preview.vercel.app"
    contract.submit_deliverable(job_id, deliverable_url)

    # Mock web rendering of deliverable
    web_body = """
    MilestoneX Live App:
    - Wallet Connect: Integrated with GenLayer testnet and MetaMask.
    - Contract Creation Form: Allows setting freelancer address and natural language DoD criteria.
    - Audit Summary: Live display of LLM adjudication and consensus breakdown.
    All requirements successfully fulfilled and deployed to production.
    """
    direct_vm.mock_web("milestonex-preview.vercel.app", {"status": 200, "body": web_body})

    # Mock LLM evaluation
    direct_vm.mock_llm(
        ".*",
        json.dumps({
            "completion_percentage": 100,
            "confidence": 98,
            "reason": "All 3 criteria (wallet connect, contract form, audit display) fully implemented and live."
        })
    )

    contract.adjudicate_job(job_id)

    job_data = json.loads(contract.get_job(job_id))
    assert job_data["status"] == "SETTLED"
    assert job_data["completion_percentage"] == "100"
    assert job_data["freelancer_payout"] == "10000"
    assert job_data["client_refund"] == "0"
    assert "fully implemented" in job_data["reason"]
    assert direct_vm._balances[bob_bytes] == 10000


def test_adjudicate_partial_payout_80_percent(contract, direct_vm, direct_alice, direct_bob):
    """
    Scenario 2: AI arbitrator scores 80% completion (Partial Payout).
    Result: 80% payout (8,000 GEN) to Freelancer, 20% refund (2,000 GEN) to Client.
    """
    setup_post_message_hook(direct_vm)

    alice_bytes = direct_vm._to_bytes(direct_alice)
    bob_bytes = direct_vm._to_bytes(direct_bob)
    direct_vm._balances[alice_bytes] = 0
    direct_vm._balances[bob_bytes] = 0

    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    dod = "Build landing page with hero section, pricing table, mobile responsive layout, and dark mode toggle."
    job_id = contract.create_job(direct_bob, dod)

    direct_vm.sender = direct_bob
    deliverable_url = "https://landing-page-milestone.vercel.app"
    contract.submit_deliverable(job_id, deliverable_url)

    web_body = """
    Landing Page Demo:
    - Hero section with CTA button: Completed.
    - Responsive mobile layout: Tested and working across viewports.
    - Pricing table: 3 tiers displayed.
    - Dark mode toggle: Under construction, toggle icon present but theme switch incomplete.
    """
    direct_vm.mock_web("landing-page-milestone.vercel.app", {"status": 200, "body": web_body})

    direct_vm.mock_llm(
        ".*",
        json.dumps({
            "completion_percentage": 80,
            "confidence": 92,
            "reason": "Hero, pricing, and responsive layout are solid. Dark mode toggle is only partially implemented."
        })
    )

    contract.adjudicate_job(job_id)

    job_data = json.loads(contract.get_job(job_id))
    assert job_data["status"] == "SETTLED"
    assert job_data["completion_percentage"] == "80"
    assert job_data["freelancer_payout"] == "8000"
    assert job_data["client_refund"] == "2000"
    assert "Dark mode" in job_data["reason"]

    # Verify automatic balance disbursement
    assert direct_vm._balances[bob_bytes] == 8000
    assert direct_vm._balances[alice_bytes] == 2000


def test_adjudicate_inaccessible_or_404_url(contract, direct_vm, direct_alice, direct_bob):
    """
    Scenario 3: Deliverable URL returns 404 or is blank.
    Result: 0% payout to Freelancer, 100% refund (10,000 GEN) to Client.
    """
    setup_post_message_hook(direct_vm)

    alice_bytes = direct_vm._to_bytes(direct_alice)
    bob_bytes = direct_vm._to_bytes(direct_bob)
    direct_vm._balances[alice_bytes] = 0
    direct_vm._balances[bob_bytes] = 0

    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    dod = "Deploy production e-commerce backend API with PostgreSQL and stripe webhooks."
    job_id = contract.create_job(direct_bob, dod)

    direct_vm.sender = direct_bob
    deliverable_url = "https://broken-or-offline-domain.com/api"
    contract.submit_deliverable(job_id, deliverable_url)

    # Mock 404 or empty response
    direct_vm.mock_web("broken-or-offline-domain.com/api", {"status": 404, "body": "404 Not Found"})

    contract.adjudicate_job(job_id)

    job_data = json.loads(contract.get_job(job_id))
    assert job_data["status"] == "SETTLED"
    assert job_data["completion_percentage"] == "0"
    assert job_data["freelancer_payout"] == "0"
    assert job_data["client_refund"] == "10000"
    assert "inaccessible, offline, 404, or blank" in job_data["reason"]

    assert direct_vm._balances[bob_bytes] == 0
    assert direct_vm._balances[alice_bytes] == 10000


def test_cannot_adjudicate_unsubmitted_or_settled_job(contract, direct_vm, direct_alice, direct_bob):
    """Verify adjudication guardrails prevent early execution or double spending."""
    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    job_id = contract.create_job(direct_bob, "Write unit tests covering 90% code coverage for auth service.")

    # 1. Attempt to adjudicate while status is still CREATED (no deliverable submitted)
    with pytest.raises(Exception) as exc:
        contract.adjudicate_job(job_id)
    assert "Job deliverable has not been submitted or already settled" in str(exc.value)

    # Freelancer submits
    direct_vm.sender = direct_bob
    contract.submit_deliverable(job_id, "https://github.com/org/repo/pull/1")

    direct_vm.mock_web("github.com/org/repo/pull/1", {"status": 200, "body": "100% test coverage implemented"})
    direct_vm.mock_llm(".*", json.dumps({"completion_percentage": 90, "confidence": 90, "reason": "90% coverage achieved"}))

    contract.adjudicate_job(job_id)

    # 2. Attempt to adjudicate again after already SETTLED
    with pytest.raises(Exception) as exc:
        contract.adjudicate_job(job_id)
    assert "Job deliverable has not been submitted or already settled" in str(exc.value)

    # 3. Freelancer cannot re-submit after settlement
    with pytest.raises(Exception) as exc:
        contract.submit_deliverable(job_id, "https://github.com/org/repo/pull/2")
    assert "Job is already settled" in str(exc.value)


def test_adjudicate_unrelated_submission_zero_percent(contract, direct_vm, direct_alice, direct_bob):
    """
    Scenario 4: Freelancer submits an unrelated website (e.g. a recipe blog instead of DeFi protocol).
    Result: AI arbitrator gives 0% score -> 100% refund to client, 0 to freelancer.
    """
    setup_post_message_hook(direct_vm)

    alice_bytes = direct_vm._to_bytes(direct_alice)
    bob_bytes = direct_vm._to_bytes(direct_bob)
    direct_vm._balances[alice_bytes] = 0
    direct_vm._balances[bob_bytes] = 0

    direct_vm.sender = direct_alice
    direct_vm.value = 15000
    dod = "Implement Uniswap v3 automated swap routing with slippage protection."
    job_id = contract.create_job(direct_bob, dod)

    direct_vm.sender = direct_bob
    deliverable_url = "https://my-unrelated-cooking-blog.com"
    contract.submit_deliverable(job_id, deliverable_url)

    direct_vm.mock_web("my-unrelated-cooking-blog.com", {
        "status": 200,
        "body": "Welcome to my Italian pasta recipes blog! Here are top 10 pasta sauces."
    })
    direct_vm.mock_llm(".*", json.dumps({
        "completion_percentage": 0,
        "confidence": 100,
        "reason": "Submitted URL is a cooking blog completely unrelated to Uniswap swap routing."
    }))

    contract.adjudicate_job(job_id)

    job_data = json.loads(contract.get_job(job_id))
    assert job_data["status"] == "SETTLED"
    assert job_data["completion_percentage"] == "0"
    assert job_data["freelancer_payout"] == "0"
    assert job_data["client_refund"] == "15000"
    assert direct_vm._balances[alice_bytes] == 15000
    assert direct_vm._balances[bob_bytes] == 0


def test_multiple_concurrent_jobs(contract, direct_vm, direct_alice, direct_bob, direct_charlie):
    """Verify independent escrow isolation across multiple simultaneous jobs."""
    setup_post_message_hook(direct_vm)

    # Job 1: Alice hires Bob for 5,000 GEN
    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    j1 = contract.create_job(direct_bob, "Build frontend UI components in Tailwind CSS.")

    # Job 2: Alice hires Charlie for 12,000 GEN
    direct_vm.sender = direct_alice
    direct_vm.value = 12000
    j2 = contract.create_job(direct_charlie, "Implement backend database migrations in PostgreSQL.")

    assert contract.get_job_count() == 2
    assert j1 == "1"
    assert j2 == "2"

    # Freelancers submit deliverables
    direct_vm.sender = direct_bob
    contract.submit_deliverable(j1, "https://tailwind-ui-components.vercel.app")

    direct_vm.sender = direct_charlie
    contract.submit_deliverable(j2, "https://github.com/db-org/migrations/pull/1")

    # Adjudicate Job 1 (100% complete)
    direct_vm.mock_web("tailwind-ui-components.vercel.app", {"status": 200, "body": "Tailwind UI component library complete"})
    direct_vm.mock_llm(r".*Tailwind.*", json.dumps({"completion_percentage": 100, "confidence": 95, "reason": "All UI components ready"}))
    contract.adjudicate_job(j1)

    # Adjudicate Job 2 (50% complete)
    direct_vm.mock_web("github.com/db-org/migrations/pull/1", {"status": 200, "body": "Partial DB migrations submitted"})
    direct_vm.mock_llm(r".*migrations.*", json.dumps({"completion_percentage": 50, "confidence": 90, "reason": "Half of migrations done"}))
    contract.adjudicate_job(j2)

    job1_data = json.loads(contract.get_job(j1))
    job2_data = json.loads(contract.get_job(j2))

    assert job1_data["freelancer_payout"] == "5000"
    assert job1_data["client_refund"] == "0"

    assert job2_data["freelancer_payout"] == "6000"
    assert job2_data["client_refund"] == "6000"


def test_get_nonexistent_job_raises_error(contract):
    """Querying a non-existent job must raise UserError."""
    with pytest.raises(Exception) as exc:
        contract.get_job("99999")
    assert "Job contract not found" in str(exc.value)

