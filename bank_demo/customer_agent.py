import argparse
import getpass
import json
import sys
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_API_URL = "http://127.0.0.1:8000"
DEMO_AGENT_ID = "demo-agent"
AGENT_SCOPES = ["balance:read", "transfer:request"]
AGENT_TRANSFER_LIMIT_CENTS = 5000


class ApiClient:
    def __init__(self, base_url, token=None):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token is not None:
            headers["Authorization"] = "Bearer " + self.token
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            payload = error.read().decode("utf-8")
            return error.code, json.loads(payload) if payload else {}
        except URLError as error:
            raise RuntimeError("Could not reach bank API: {}".format(error.reason))


def parse_amount_cents(value):
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError("amount must be a positive number, e.g. 12.50")
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        raise argparse.ArgumentTypeError("amount must be positive with at most two decimal places")
    return int(amount * 100)


def format_money(cents):
    return "${}.{:02d}".format(cents // 100, cents % 100)


def show_result(label, status, result):
    print("{} (HTTP {}): {}".format(label, status, json.dumps(result, sort_keys=True)))


def do_balance(client):
    status, result = client.request("GET", "/v1/accounts/demo/balance")
    show_result("Agent balance request", status, result)
    return status < 400


def do_payment(client, recipient, amount_cents):
    status, result = client.request(
        "POST",
        "/v1/transfers",
        {"recipient": recipient, "amount_cents": amount_cents},
    )
    show_result("Agent payment request", status, result)
    return status, result


def customer_approval(customer, approval_id):
    status, details = customer.request("GET", "/v1/approvals/" + approval_id)
    if status >= 400:
        show_result("Customer approval details", status, details)
        return False

    print(
        "\nCUSTOMER CONFIRMATION REQUIRED\n"
        "  Recipient: {}\n"
        "  Amount:    {}\n".format(
            details["recipient"], format_money(details["amount_cents"])
        )
    )
    try:
        answer = input("Approve this exact payment? [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    decision = "approve" if answer == "y" else "reject"
    status, result = customer.request(
        "POST",
        "/v1/approvals/" + approval_id,
        {"decision": decision},
    )
    show_result("Customer decision", status, result)
    return status < 400 and decision == "approve"


def authenticate_and_authorize(customer_id, password):
    unauthenticated = ApiClient(DEFAULT_API_URL)
    status, result = unauthenticated.request(
        "POST",
        "/v1/auth/customer/sessions",
        {"customer_id": customer_id, "password": password},
    )
    if status != 200:
        raise RuntimeError("Customer login failed: {}".format(result))
    customer = ApiClient(DEFAULT_API_URL, result["access_token"])

    print(
        "\nAgent access request\n"
        "  Agent:       {}\n"
        "  Permissions: {}\n"
        "  Payment cap: {}\n".format(
            DEMO_AGENT_ID,
            ", ".join(AGENT_SCOPES),
            format_money(AGENT_TRANSFER_LIMIT_CENTS),
        )
    )
    if input("Grant this agent access? [y/N] ").strip().lower() != "y":
        raise RuntimeError("Agent grant was not issued; customer declined consent.")

    status, grant = customer.request(
        "POST",
        "/v1/auth/agent-grants",
        {
            "agent_id": DEMO_AGENT_ID,
            "scopes": AGENT_SCOPES,
            "max_transfer_cents": AGENT_TRANSFER_LIMIT_CENTS,
        },
    )
    if status != 201:
        raise RuntimeError("Could not grant agent access: {}".format(grant))
    print(
        "Customer authenticated. Issued a separate agent token for {} "
        "with {} and a ${:.2f} payment limit.".format(
            grant["agent_id"],
            ", ".join(grant["scopes"]),
            grant["max_transfer_cents"] / 100.0,
        )
    )
    return ApiClient(DEFAULT_API_URL, grant["access_token"]), customer


def prompt_customer_credentials():
    customer_id = input("Customer ID [demo-customer]: ").strip() or "demo-customer"
    password = getpass.getpass("Demo password: ")
    return customer_id, password


def run_demo(agent, customer):
    print("Customer-agent demo: using mock data and demo-only tokens.\n")

    if not do_balance(agent):
        return 1

    print("\n1) Allowed transfer to a known recipient")
    status, _ = do_payment(agent, "Coffee Shop", 2500)
    if status >= 400:
        return 1

    print("\n2) Transfer above the agent's delegated limit")
    do_payment(agent, "Coffee Shop", 6000)

    print("\n3) New recipient; the bank requires customer approval")
    status, result = do_payment(agent, "New Recipient", 1800)
    if status == 202 and result.get("approval_id"):
        customer_approval(customer, result["approval_id"])
    elif status >= 400:
        return 1
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Simulate a customer-authorized agent calling a mock bank API."
    )
    commands = parser.add_subparsers(dest="command")
    commands.required = True
    commands.add_parser("demo", help="run the end-to-end interactive demo")
    commands.add_parser("balance", help="request the demo account balance")
    pay = commands.add_parser("pay", help="request a payment")
    pay.add_argument("--to", required=True, dest="recipient")
    pay.add_argument("--amount", required=True, type=parse_amount_cents)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        customer_id, password = prompt_customer_credentials()
        agent, customer = authenticate_and_authorize(customer_id, password)
        if args.command == "demo":
            return run_demo(agent, customer)
        if args.command == "balance":
            return 0 if do_balance(agent) else 1
        status, result = do_payment(agent, args.recipient, args.amount)
        if status == 202 and result.get("approval_id"):
            print("Run the demo flow to simulate customer confirmation.")
        return 0 if status < 400 else 1
    except (RuntimeError, ValueError, KeyError) as error:
        print("Error: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
