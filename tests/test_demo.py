import argparse
import threading
import unittest

from bank_demo.customer_agent import ApiClient, parse_amount_cents
from bank_demo.mock_bank import (
    DEMO_AGENT_ID,
    DEMO_CUSTOMER_ID,
    DEMO_CUSTOMER_PASSWORD,
    make_server,
)


class DemoApiTests(unittest.TestCase):
    def setUp(self):
        self.server = make_server(port=0)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = "http://{}:{}".format(host, port)
        unauthenticated = ApiClient(self.base_url)
        status, result = unauthenticated.request(
            "POST",
            "/v1/auth/customer/sessions",
            {
                "customer_id": DEMO_CUSTOMER_ID,
                "password": DEMO_CUSTOMER_PASSWORD,
            },
        )
        self.assertEqual(status, 200)
        self.customer = ApiClient(self.base_url, result["access_token"])
        status, result = self.customer.request(
            "POST",
            "/v1/auth/agent-grants",
            {
                "agent_id": DEMO_AGENT_ID,
                "scopes": ["balance:read", "transfer:request"],
                "max_transfer_cents": 5000,
            },
        )
        self.assertEqual(status, 201)
        self.agent = ApiClient(self.base_url, result["access_token"])

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def test_agent_can_read_balance(self):
        status, result = self.agent.request("GET", "/v1/accounts/demo/balance")
        self.assertEqual(status, 200)
        self.assertEqual(result["balance_cents"], 250000)

    def test_customer_login_issues_new_opaque_tokens(self):
        client = ApiClient(self.base_url)
        credentials = {
            "customer_id": DEMO_CUSTOMER_ID,
            "password": DEMO_CUSTOMER_PASSWORD,
        }
        status, first = client.request("POST", "/v1/auth/customer/sessions", credentials)
        self.assertEqual(status, 200)
        status, second = client.request("POST", "/v1/auth/customer/sessions", credentials)
        self.assertEqual(status, 200)
        self.assertNotEqual(first["access_token"], second["access_token"])
        self.assertEqual(first["token_type"], "Bearer")
        self.assertGreater(first["expires_in"], 0)

    def test_invalid_customer_credentials_do_not_issue_token(self):
        status, result = ApiClient(self.base_url).request(
            "POST",
            "/v1/auth/customer/sessions",
            {"customer_id": DEMO_CUSTOMER_ID, "password": "wrong"},
        )
        self.assertEqual(status, 401)
        self.assertEqual(result["error"], "invalid_customer_credentials")

    def test_agent_cannot_approve_customer_action(self):
        status, _ = self.agent.request(
            "POST", "/v1/approvals/missing", {"decision": "approve"}
        )
        self.assertEqual(status, 403)

    def test_customer_cannot_use_agent_only_balance_endpoint(self):
        status, _ = self.customer.request("GET", "/v1/accounts/demo/balance")
        self.assertEqual(status, 403)

    def test_unknown_agent_cannot_receive_a_grant(self):
        status, result = self.customer.request(
            "POST",
            "/v1/auth/agent-grants",
            {
                "agent_id": "unregistered-agent",
                "scopes": ["balance:read"],
                "max_transfer_cents": 1000,
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(result["error"], "invalid_agent_grant")

    def test_over_limit_payment_is_blocked(self):
        status, result = self.agent.request(
            "POST",
            "/v1/transfers",
            {"recipient": "Coffee Shop", "amount_cents": 5001},
        )
        self.assertEqual(status, 403)
        self.assertEqual(result["error"], "agent_transfer_limit_exceeded")
        self.assertEqual(self.server.demo_bank.balance_cents, 250000)

    def test_new_payee_requires_customer_approval(self):
        status, result = self.agent.request(
            "POST",
            "/v1/transfers",
            {"recipient": "New Recipient", "amount_cents": 1800},
        )
        self.assertEqual(status, 202)
        approval_id = result["approval_id"]

        status, details = self.customer.request(
            "GET", "/v1/approvals/" + approval_id
        )
        self.assertEqual(status, 200)
        self.assertEqual(details["recipient"], "New Recipient")
        self.assertEqual(details["amount_cents"], 1800)

        status, result = self.customer.request(
            "POST",
            "/v1/approvals/" + approval_id,
            {"decision": "approve"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "approved")
        self.assertEqual(self.server.demo_bank.balance_cents, 248200)

    def test_payment_amount_uses_exact_cents(self):
        self.assertEqual(parse_amount_cents("12.34"), 1234)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_amount_cents("12.345")


if __name__ == "__main__":
    unittest.main()
