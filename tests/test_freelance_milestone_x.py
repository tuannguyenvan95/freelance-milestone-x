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
    """Client creates an escrow job with funds, DoD, and deadline."""
    direct_vm.sender = direct_alice
    direct_vm.value = 10000

    dod = "Deploy Next.js dApp to Vercel with Metamask wallet connect and transaction history table."
    deadline = 2000000000
    job_id = contract.create_job(direct_bob, dod, deadline)

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
    assert job_data["payout_tier"] == "NONE"
    assert job_data["completion_percentage"] == "0"
    assert job_data["freelancer_payout"] == "0"
    assert job_data["client_refund"] == "0"
    assert job_data["deadline"] == str(deadline)


def test_create_job_validation_errors(contract, direct_vm, direct_alice, direct_bob):
    """Test validation errors on job creation."""
    direct_vm.sender = direct_alice

    # 1. Zero escrow deposit
    direct_vm.value = 0
    with pytest.raises(Exception) as exc:
        contract.create_job(direct_bob, "Detailed acceptance criteria goes here.", 2000000000)
    assert "Escrow funding must be greater than 0 GEN" in str(exc.value)

    # 2. Definition of Done too short (< 15 chars)
    direct_vm.value = 5000
    with pytest.raises(Exception) as exc:
        contract.create_job(direct_bob, "Do work", 2000000000)
    assert "definition_of_done must be detailed (min 15 chars)" in str(exc.value)

    # 3. Client cannot assign job to self
    direct_vm.value = 5000
    with pytest.raises(Exception) as exc:
        contract.create_job(direct_alice, "Build high frequency arbitrage trading bot on GenLayer.", 2000000000)
    assert "Client cannot assign job to self" in str(exc.value)

    # 4. Zero or non-positive deadline
    with pytest.raises(Exception) as exc:
        contract.create_job(direct_bob, "Detailed acceptance criteria goes here.", 0)
    assert "deadline_timestamp must be greater than 0" in str(exc.value)


def test_submit_deliverable_success(contract, direct_vm, direct_alice, direct_bob):
    """Freelancer submits deliverable URL before deadline."""
    direct_vm.sender = direct_alice
    direct_vm.value = 8000
    dod = "Complete responsive landing page with Figma pixel-perfect fidelity."
    job_id = contract.create_job(direct_bob, dod, 2000000000)

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
    job_id = contract.create_job(direct_bob, "Implement smart escrow with partial settlement.", 2000000000)

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


def test_adjudicate_full_completion_tier(contract, direct_vm, direct_alice, direct_bob):
    """
    Scenario 1: AI arbitrator assigns FULL tier (100% completion).
    Result: 100% payout (10,000 GEN) to Freelancer, 0 refund to Client.
    """
    setup_post_message_hook(direct_vm)

    alice_bytes = direct_vm._to_bytes(direct_alice)
    bob_bytes = direct_vm._to_bytes(direct_bob)
    direct_vm._balances[alice_bytes] = 20000
    direct_vm._balances[bob_bytes] = 0

    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    dod = "Deploy smart escrow dApp on Vercel with wallet connection, contract creation form, and audit summary."
    job_id = contract.create_job(direct_bob, dod, 2000000000)

    direct_vm.sender = direct_bob
    deliverable_url = "https://milestonex-preview.vercel.app"
    contract.submit_deliverable(job_id, deliverable_url)

    web_body = """
    MilestoneX Live App:
    - Wallet Connect: Integrated with GenLayer testnet and MetaMask.
    - Contract Creation Form: Allows setting freelancer address and natural language DoD criteria.
    - Audit Summary: Live display of LLM adjudication and consensus breakdown.
    All requirements successfully fulfilled and deployed to production.
    """
    direct_vm.mock_web("milestonex-preview.vercel.app", {"status": 200, "body": web_body})

    direct_vm.mock_llm(
        ".*",
        json.dumps({
            "tier": "FULL",
            "confidence": 98,
            "reason": "All 3 criteria (wallet connect, contract form, audit display) fully implemented and live."
        })
    )

    contract.adjudicate_job(job_id)

    job_data = json.loads(contract.get_job(job_id))
    assert job_data["status"] == "SETTLED"
    assert job_data["payout_tier"] == "FULL"
    assert job_data["completion_percentage"] == "100"
    assert job_data["freelancer_payout"] == "10000"
    assert job_data["client_refund"] == "0"
    assert "fully implemented" in job_data["reason"]
    assert direct_vm._balances[bob_bytes] == 10000


def test_adjudicate_substantial_completion_tier(contract, direct_vm, direct_alice, direct_bob):
    """
    Scenario 2: AI arbitrator assigns SUBSTANTIAL tier (75% completion).
    Result: 75% payout (7,500 GEN) to Freelancer, 25% refund (2,500 GEN) to Client.
    """
    setup_post_message_hook(direct_vm)

    alice_bytes = direct_vm._to_bytes(direct_alice)
    bob_bytes = direct_vm._to_bytes(direct_bob)
    direct_vm._balances[alice_bytes] = 0
    direct_vm._balances[bob_bytes] = 0

    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    dod = "Build landing page with hero section, pricing table, mobile responsive layout, and dark mode toggle."
    job_id = contract.create_job(direct_bob, dod, 2000000000)

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
            "tier": "SUBSTANTIAL",
            "confidence": 92,
            "reason": "Hero, pricing, and responsive layout are solid. Dark mode toggle is only partially implemented."
        })
    )

    contract.adjudicate_job(job_id)

    job_data = json.loads(contract.get_job(job_id))
    assert job_data["status"] == "SETTLED"
    assert job_data["payout_tier"] == "SUBSTANTIAL"
    assert job_data["completion_percentage"] == "75"
    assert job_data["freelancer_payout"] == "7500"
    assert job_data["client_refund"] == "2500"
    assert "Dark mode" in job_data["reason"]

    assert direct_vm._balances[bob_bytes] == 7500
    assert direct_vm._balances[alice_bytes] == 2500


def test_adjudicate_partial_completion_tier(contract, direct_vm, direct_alice, direct_bob):
    """
    Scenario 3: AI arbitrator assigns PARTIAL tier (50% completion).
    Result: 50% payout (5,000 GEN) to Freelancer, 50% refund (5,000 GEN) to Client.
    """
    setup_post_message_hook(direct_vm)

    alice_bytes = direct_vm._to_bytes(direct_alice)
    bob_bytes = direct_vm._to_bytes(direct_bob)
    direct_vm._balances[alice_bytes] = 0
    direct_vm._balances[bob_bytes] = 0

    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    dod = "Develop user authentication, dashboard analytics, and CSV report export."
    job_id = contract.create_job(direct_bob, dod, 2000000000)

    direct_vm.sender = direct_bob
    deliverable_url = "https://analytics-dashboard-demo.vercel.app"
    contract.submit_deliverable(job_id, deliverable_url)

    web_body = "User Auth and Dashboard are live. CSV export is not implemented."
    direct_vm.mock_web("analytics-dashboard-demo.vercel.app", {"status": 200, "body": web_body})
    direct_vm.mock_llm(".*", json.dumps({
        "tier": "PARTIAL",
        "confidence": 90,
        "reason": "Auth and dashboard ready, but CSV report export missing."
    }))

    contract.adjudicate_job(job_id)

    job_data = json.loads(contract.get_job(job_id))
    assert job_data["status"] == "SETTLED"
    assert job_data["payout_tier"] == "PARTIAL"
    assert job_data["completion_percentage"] == "50"
    assert job_data["freelancer_payout"] == "5000"
    assert job_data["client_refund"] == "5000"
    assert direct_vm._balances[bob_bytes] == 5000
    assert direct_vm._balances[alice_bytes] == 5000


def test_adjudicate_minimal_completion_tier(contract, direct_vm, direct_alice, direct_bob):
    """
    Scenario 4: AI arbitrator assigns MINIMAL tier (25% completion).
    Result: 25% payout (2,500 GEN) to Freelancer, 75% refund (7,500 GEN) to Client.
    """
    setup_post_message_hook(direct_vm)

    alice_bytes = direct_vm._to_bytes(direct_alice)
    bob_bytes = direct_vm._to_bytes(direct_bob)
    direct_vm._balances[alice_bytes] = 0
    direct_vm._balances[bob_bytes] = 0

    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    dod = "Build complete fullstack e-commerce store with catalog, cart, and payment checkout."
    job_id = contract.create_job(direct_bob, dod, 2000000000)

    direct_vm.sender = direct_bob
    deliverable_url = "https://shop-prototype.vercel.app"
    contract.submit_deliverable(job_id, deliverable_url)

    web_body = "Static homepage mockup only. Cart and payment buttons are inactive placeholders."
    direct_vm.mock_web("shop-prototype.vercel.app", {"status": 200, "body": web_body})
    direct_vm.mock_llm(".*", json.dumps({
        "tier": "MINIMAL",
        "confidence": 85,
        "reason": "Only static landing page skeleton provided. Cart and checkout not implemented."
    }))

    contract.adjudicate_job(job_id)

    job_data = json.loads(contract.get_job(job_id))
    assert job_data["status"] == "SETTLED"
    assert job_data["payout_tier"] == "MINIMAL"
    assert job_data["completion_percentage"] == "25"
    assert job_data["freelancer_payout"] == "2500"
    assert job_data["client_refund"] == "7500"
    assert direct_vm._balances[bob_bytes] == 2500
    assert direct_vm._balances[alice_bytes] == 7500


def test_adjudicate_inaccessible_or_404_url(contract, direct_vm, direct_alice, direct_bob):
    """
    Scenario 5: Deliverable URL returns 404 or is blank.
    Result: REJECTED tier (0% payout), 100% refund (10,000 GEN) to Client.
    """
    setup_post_message_hook(direct_vm)

    alice_bytes = direct_vm._to_bytes(direct_alice)
    bob_bytes = direct_vm._to_bytes(direct_bob)
    direct_vm._balances[alice_bytes] = 0
    direct_vm._balances[bob_bytes] = 0

    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    dod = "Deploy production e-commerce backend API with PostgreSQL and stripe webhooks."
    job_id = contract.create_job(direct_bob, dod, 2000000000)

    direct_vm.sender = direct_bob
    deliverable_url = "https://broken-or-offline-domain.com/api"
    contract.submit_deliverable(job_id, deliverable_url)

    direct_vm.mock_web("broken-or-offline-domain.com/api", {"status": 404, "body": "404 Not Found"})

    contract.adjudicate_job(job_id)

    job_data = json.loads(contract.get_job(job_id))
    assert job_data["status"] == "SETTLED"
    assert job_data["payout_tier"] == "REJECTED"
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
    job_id = contract.create_job(direct_bob, "Write unit tests covering 90% code coverage for auth service.", 2000000000)

    # 1. Attempt to adjudicate while status is still CREATED (no deliverable submitted)
    with pytest.raises(Exception) as exc:
        contract.adjudicate_job(job_id)
    assert "Job deliverable has not been submitted or already settled" in str(exc.value)

    # Freelancer submits
    direct_vm.sender = direct_bob
    contract.submit_deliverable(job_id, "https://github.com/org/repo/pull/1")

    direct_vm.mock_web("github.com/org/repo/pull/1", {"status": 200, "body": "100% test coverage implemented"})
    direct_vm.mock_llm(".*", json.dumps({"tier": "FULL", "confidence": 95, "reason": "90% coverage achieved"}))

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
    Scenario 6: Freelancer submits an unrelated website (e.g. recipe blog instead of DeFi protocol).
    Result: AI assigns REJECTED tier -> 100% refund to client, 0 to freelancer.
    """
    setup_post_message_hook(direct_vm)

    alice_bytes = direct_vm._to_bytes(direct_alice)
    bob_bytes = direct_vm._to_bytes(direct_bob)
    direct_vm._balances[alice_bytes] = 0
    direct_vm._balances[bob_bytes] = 0

    direct_vm.sender = direct_alice
    direct_vm.value = 15000
    dod = "Implement Uniswap v3 automated swap routing with slippage protection."
    job_id = contract.create_job(direct_bob, dod, 2000000000)

    direct_vm.sender = direct_bob
    deliverable_url = "https://my-unrelated-cooking-blog.com"
    contract.submit_deliverable(job_id, deliverable_url)

    direct_vm.mock_web("my-unrelated-cooking-blog.com", {
        "status": 200,
        "body": "Welcome to my Italian pasta recipes blog! Here are top 10 pasta sauces."
    })
    direct_vm.mock_llm(".*", json.dumps({
        "tier": "REJECTED",
        "confidence": 100,
        "reason": "Submitted URL is a cooking blog completely unrelated to Uniswap swap routing."
    }))

    contract.adjudicate_job(job_id)

    job_data = json.loads(contract.get_job(job_id))
    assert job_data["status"] == "SETTLED"
    assert job_data["payout_tier"] == "REJECTED"
    assert job_data["completion_percentage"] == "0"
    assert job_data["freelancer_payout"] == "0"
    assert job_data["client_refund"] == "15000"
    assert direct_vm._balances[alice_bytes] == 15000
    assert direct_vm._balances[bob_bytes] == 0


def test_multiple_concurrent_jobs(contract, direct_vm, direct_alice, direct_bob, direct_charlie):
    """Verify independent escrow isolation across multiple simultaneous jobs."""
    setup_post_message_hook(direct_vm)

    # Job 1: Alice hires Bob for 8,000 GEN
    direct_vm.sender = direct_alice
    direct_vm.value = 8000
    j1 = contract.create_job(direct_bob, "Build frontend UI components in Tailwind CSS.", 2000000000)

    # Job 2: Alice hires Charlie for 12,000 GEN
    direct_vm.sender = direct_alice
    direct_vm.value = 12000
    j2 = contract.create_job(direct_charlie, "Implement backend database migrations in PostgreSQL.", 2000000000)

    assert contract.get_job_count() == 2
    assert j1 == "1"
    assert j2 == "2"

    # Freelancers submit deliverables
    direct_vm.sender = direct_bob
    contract.submit_deliverable(j1, "https://tailwind-ui-components.vercel.app")

    direct_vm.sender = direct_charlie
    contract.submit_deliverable(j2, "https://github.com/db-org/migrations/pull/1")

    # Adjudicate Job 1 (FULL tier: 100%)
    direct_vm.mock_web("tailwind-ui-components.vercel.app", {"status": 200, "body": "Tailwind UI component library complete"})
    direct_vm.mock_llm(r".*Tailwind.*", json.dumps({"tier": "FULL", "confidence": 95, "reason": "All UI components ready"}))
    contract.adjudicate_job(j1)

    # Adjudicate Job 2 (PARTIAL tier: 50%)
    direct_vm.mock_web("github.com/db-org/migrations/pull/1", {"status": 200, "body": "Partial DB migrations submitted"})
    direct_vm.mock_llm(r".*migrations.*", json.dumps({"tier": "PARTIAL", "confidence": 90, "reason": "Half of migrations done"}))
    contract.adjudicate_job(j2)

    job1_data = json.loads(contract.get_job(j1))
    job2_data = json.loads(contract.get_job(j2))

    assert job1_data["payout_tier"] == "FULL"
    assert job1_data["freelancer_payout"] == "8000"
    assert job1_data["client_refund"] == "0"

    assert job2_data["payout_tier"] == "PARTIAL"
    assert job2_data["freelancer_payout"] == "6000"
    assert job2_data["client_refund"] == "6000"


def test_get_nonexistent_job_raises_error(contract):
    """Querying a non-existent job must raise UserError."""
    with pytest.raises(Exception) as exc:
        contract.get_job("99999")
    assert "Job contract not found" in str(exc.value)


def test_consensus_rejects_divergent_payout_tiers(contract, direct_vm, direct_alice, direct_bob):
    """
    ECONOMIC DETERMINISM TEST:
    Demonstrates that if Leader proposes FULL (100%) but a Validator evaluates
    SUBSTANTIAL (75%), consensus REJECTS the leader proposal.
    """
    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    dod = "Build responsive web3 landing page with wallet connect button."
    job_id = contract.create_job(direct_bob, dod, 2000000000)

    direct_vm.sender = direct_bob
    deliverable_url = "https://milestone-test-consensus.vercel.app"
    contract.submit_deliverable(job_id, deliverable_url)

    direct_vm.mock_web("milestone-test-consensus.vercel.app", {"status": 200, "body": "Page live with partial buttons"})

    direct_vm.clear_validators()

    # Leader evaluates SUBSTANTIAL
    direct_vm.mock_llm(".*", json.dumps({"tier": "SUBSTANTIAL", "confidence": 90, "reason": "Partial items delivered"}))
    contract.adjudicate_job(job_id)

    from genlayer.gl.vm import Return
    divergent_leader_proposal = Return(calldata={"tier": "FULL", "confidence": 95, "reason": "Proposed full"})
    is_valid = direct_vm.run_validator(index=0, leader_result=divergent_leader_proposal.calldata)
    assert is_valid is False, "Validator must reject leader proposal with divergent payout tier!"


# ==============================================================================
# SAFE CLIENT RECOVERY PATH TESTS (DEADLINE EXPIRATION & FULL REFUND)
# ==============================================================================

def test_cancel_expired_job_success_full_refund(contract, direct_vm, direct_alice, direct_bob):
    """
    Client Recovery Path:
    Client funds a job with 10,000 GEN. Freelancer ghosts and never submits deliverable.
    Deadline passes. Client calls cancel_expired_job and recovers a 100% full refund.
    """
    setup_post_message_hook(direct_vm)

    alice_bytes = direct_vm._to_bytes(direct_alice)
    direct_vm._balances[alice_bytes] = 0

    # Start at 2026-06-01 10:00:00 UTC (timestamp 1780308000)
    direct_vm.warp("2026-06-01T10:00:00Z")
    now_ts = contract.get_current_time()
    deadline = now_ts + 7200  # 2 hour deadline

    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    job_id = contract.create_job(direct_bob, "Deploy fullstack application to production AWS cluster.", deadline)
    direct_vm.value = 0

    # Advance time 3 hours into future (past deadline)
    direct_vm.warp("2026-06-01T13:00:00Z")
    assert contract.get_current_time() > deadline

    # Client cancels expired job
    contract.cancel_expired_job(job_id)

    job_data = json.loads(contract.get_job(job_id))
    assert job_data["status"] == "CANCELLED"
    assert job_data["payout_tier"] == "CANCELLED"
    assert job_data["completion_percentage"] == "0"
    assert job_data["freelancer_payout"] == "0"
    assert job_data["client_refund"] == "10000"
    assert "Job cancelled by client after deadline expired" in job_data["reason"]

    # Client recovers 100% of escrow funds
    assert direct_vm._balances[alice_bytes] == 10000


def test_cancel_expired_job_before_deadline_fails(contract, direct_vm, direct_alice, direct_bob):
    """Client cannot cancel a job before the deadline has expired."""
    direct_vm.warp("2026-06-01T10:00:00Z")
    now_ts = contract.get_current_time()
    deadline = now_ts + 7200  # 2 hours

    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    job_id = contract.create_job(direct_bob, "Create marketing materials and Figma assets.", deadline)

    # Warp only 30 minutes forward (still before deadline)
    direct_vm.warp("2026-06-01T10:30:00Z")
    with pytest.raises(Exception) as exc:
        contract.cancel_expired_job(job_id)
    assert "Job deadline has not passed yet" in str(exc.value)


def test_cancel_expired_job_by_non_client_fails(contract, direct_vm, direct_alice, direct_bob, direct_charlie):
    """Non-clients (freelancer, stranger) cannot cancel an unsubmitted job."""
    direct_vm.warp("2026-06-01T10:00:00Z")
    now_ts = contract.get_current_time()
    deadline = now_ts + 3600

    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    job_id = contract.create_job(direct_bob, "Create marketing materials and Figma assets.", deadline)

    # Warp past deadline
    direct_vm.warp("2026-06-01T12:00:00Z")

    # Bob (freelancer) attempts to cancel
    direct_vm.sender = direct_bob
    with pytest.raises(Exception) as exc:
        contract.cancel_expired_job(job_id)
    assert "Only the client can cancel an unsubmitted job" in str(exc.value)

    # Charlie (third party) attempts to cancel
    direct_vm.sender = direct_charlie
    with pytest.raises(Exception) as exc:
        contract.cancel_expired_job(job_id)
    assert "Only the client can cancel an unsubmitted job" in str(exc.value)


def test_cannot_cancel_submitted_or_settled_job(contract, direct_vm, direct_alice, direct_bob):
    """Client cannot cancel a job once deliverable has been submitted."""
    direct_vm.warp("2026-06-01T10:00:00Z")
    now_ts = contract.get_current_time()
    deadline = now_ts + 7200

    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    job_id = contract.create_job(direct_bob, "Create marketing materials and Figma assets.", deadline)

    # Freelancer submits deliverable on time
    direct_vm.sender = direct_bob
    contract.submit_deliverable(job_id, "https://figma.com/file/my-design-assets")

    # Warp past deadline
    direct_vm.warp("2026-06-01T14:00:00Z")

    # Client attempts to cancel after submission
    direct_vm.sender = direct_alice
    with pytest.raises(Exception) as exc:
        contract.cancel_expired_job(job_id)
    assert "Cannot cancel job once deliverable has been submitted" in str(exc.value)


def test_cannot_submit_after_deadline_or_cancellation(contract, direct_vm, direct_alice, direct_bob):
    """Freelancer cannot submit a deliverable after the deadline has passed or job is cancelled."""
    direct_vm.warp("2026-06-01T10:00:00Z")
    now_ts = contract.get_current_time()
    deadline = now_ts + 3600

    direct_vm.sender = direct_alice
    direct_vm.value = 6000
    job_id = contract.create_job(direct_bob, "Build custom smart contract staking mechanism.", deadline)

    # Advance time past deadline
    direct_vm.warp("2026-06-01T11:30:00Z")

    # Freelancer attempts late submission
    direct_vm.sender = direct_bob
    with pytest.raises(Exception) as exc:
        contract.submit_deliverable(job_id, "https://github.com/org/late-submission")
    assert "Job deadline has passed; deliverable cannot be submitted" in str(exc.value)

    # Client cancels job
    direct_vm.sender = direct_alice
    contract.cancel_expired_job(job_id)

    # Freelancer attempts submission after cancellation
    direct_vm.sender = direct_bob
    with pytest.raises(Exception) as exc:
        contract.submit_deliverable(job_id, "https://github.com/org/late-submission")
    assert "Job has been cancelled" in str(exc.value)


def test_cannot_adjudicate_cancelled_job(contract, direct_vm, direct_alice, direct_bob):
    """Adjudication cannot be executed on a cancelled job."""
    direct_vm.warp("2026-06-01T10:00:00Z")
    deadline = contract.get_current_time() + 3600

    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    job_id = contract.create_job(direct_bob, "Build responsive landing page for protocol launch.", deadline)

    direct_vm.warp("2026-06-01T12:00:00Z")
    contract.cancel_expired_job(job_id)

    with pytest.raises(Exception) as exc:
        contract.adjudicate_job(job_id)
    assert "Cannot adjudicate a cancelled job" in str(exc.value)
